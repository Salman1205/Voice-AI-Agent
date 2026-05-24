"""Interactive CLI for the conversation engine. No Twilio, no audio.

Runs the real LLM, the real scenario loader, the real state machine, and the
real OutcomeRecorder. The only things stubbed out are the STT and TTS layers:
you type the caller's utterances, and you read the agent's replies on stdout.

Useful for verifying conversation flow, system prompt quality, and the
remember/end_call tools without needing a phone or a verified Twilio number.

Usage
-----
    python -m scripts.chat                       # appointment_reminder preset
    python -m scripts.chat --custom              # paste a custom prompt
    python -m scripts.chat --turns 6             # tighten the turn cap
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow running as `python scripts/chat.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.conversation.engine import ConversationEngine
from app.conversation.outcome import OutcomeRecorder
from app.conversation.state import CallSession, CallStatus
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.providers.factory import build_llm
from app.scenarios.loader import build_custom, load_preset


PRESET_PATH = Path("app/scenarios/appointment_reminder.yaml")

# Light ANSI styling. Falls back gracefully on terminals that ignore escapes.
DIM = "\033[2m"
BOLD = "\033[1m"
GREEN = "\033[32m"
BLUE = "\033[34m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def _print_agent(text: str) -> None:
    print(f"{BOLD}{GREEN}agent{RESET}  {text}")


def _print_caller_prompt() -> None:
    print(f"{BOLD}{BLUE}you{RESET}    ", end="", flush=True)


def _print_event(text: str) -> None:
    print(f"{DIM}       [{text}]{RESET}")


def _print_remembered(extracted_before: dict, extracted_after: dict) -> None:
    new_keys = {
        k: v
        for k, v in extracted_after.items()
        if k not in extracted_before or extracted_before[k] != v
    }
    for key, value in new_keys.items():
        _print_event(f"remembered {key}={value!r}")


def _print_outcome(outcome: dict) -> None:
    print()
    print(f"{BOLD}{YELLOW}final outcome.json{RESET}")
    print(json.dumps(outcome, indent=2))


async def _build_custom_scenario_from_stdin():
    print("Paste a custom persona+goal prompt, end with a blank line:")
    lines: list[str] = []
    while True:
        line = input()
        if not line:
            break
        lines.append(line)
    return build_custom(custom_prompt="\n".join(lines))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--custom",
        action="store_true",
        help="Use a custom scenario typed at stdin instead of the preset.",
    )
    parser.add_argument(
        "--turns",
        type=int,
        default=None,
        help="Override the scenario's max_turns cap.",
    )
    args = parser.parse_args()

    # Keep stdout clean of structured log noise during the interactive loop.
    configure_logging(level="WARNING")

    settings = get_settings()
    missing = settings.validate_required_keys()
    # Only the LLM key is strictly required for chat-mode; warn rather than
    # exit if Twilio/Deepgram keys are absent so smoke usage stays smooth.
    if "GROQ_API_KEY" in missing:
        print("GROQ_API_KEY is missing in .env. Cannot run the LLM.", file=sys.stderr)
        return 1

    if args.custom:
        scenario = await _build_custom_scenario_from_stdin()
    else:
        if not PRESET_PATH.exists():
            print(f"Preset not found at {PRESET_PATH}", file=sys.stderr)
            return 1
        scenario = load_preset(PRESET_PATH)

    if args.turns is not None:
        scenario = scenario.model_copy(update={"max_turns": args.turns})

    llm = build_llm(settings)
    engine = ConversationEngine(llm=llm, settings_max_turns=scenario.max_turns)
    outcome_recorder = OutcomeRecorder(llm=llm)

    session = CallSession.new(
        to="+10000000000",  # placeholder; not used in chat mode
        scenario=scenario,
        context_variables={},
    )
    session.mark_status(CallStatus.IN_PROGRESS)

    print(f"{DIM}---{RESET}")
    print(f"{DIM}scenario:{RESET} {scenario.name}")
    print(f"{DIM}max turns:{RESET} {scenario.max_turns}")
    print(f"{DIM}type your reply and press enter. ctrl+c to abort.{RESET}")
    print(f"{DIM}---{RESET}")

    opening = await engine.first_turn(session)
    _print_agent(opening)

    try:
        while True:
            _print_caller_prompt()
            try:
                user_text = input().strip()
            except EOFError:
                print()
                break
            if not user_text:
                continue

            extracted_before = dict(session.extracted_data)
            result = await engine.respond(session, user_text)
            _print_remembered(extracted_before, session.extracted_data)
            _print_agent(result.text)

            if result.end_call:
                _print_event(f"end_call reason={result.end_reason}")
                session.mark_status(CallStatus.COMPLETED, reason=result.end_reason)
                break
    except KeyboardInterrupt:
        print()
        _print_event("aborted by user")
        session.mark_status(CallStatus.ABANDONED, reason="cli_abort")

    # Run the same post-call extractor that the real WS bridge uses, so the
    # final JSON is identical to what HR would see at the end of a real call.
    try:
        await outcome_recorder.finalize(session)
    except Exception as exc:  # noqa: BLE001
        _print_event(f"outcome extraction failed: {exc}")

    if session.final_outcome:
        _print_outcome(session.final_outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
