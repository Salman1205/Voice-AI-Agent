"""Conversation engine.

Per-turn orchestration: take the latest user transcript, build the full
LLM context (persona + goal + context vars + full history + extracted
data), stream the response, apply tool calls, decide whether to keep
going or end.

Returns a `TurnResult` so the media bridge knows what to TTS and whether
to terminate the call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.conversation.state import CallSession, TurnEvent
from app.conversation.tools import conversation_tools, dispatch_tool
from app.core.logging import get_logger
from app.providers.base import LLMProvider, Message


log = get_logger(__name__)


@dataclass
class TurnResult:
    text: str
    end_call: bool = False
    end_reason: str | None = None
    farewell: str | None = None
    tool_results: list[dict] = field(default_factory=list)


class ConversationEngine:
    def __init__(self, *, llm: LLMProvider, settings_max_turns: int = 12) -> None:
        self._llm = llm
        self._max_turns = settings_max_turns

    async def first_turn(self, session: CallSession) -> str:
        """Return the rendered opening line for the assistant to say first."""
        opening = session.scenario.render_opening_line(session.context_variables)
        session.record_assistant(opening)
        return opening

    async def respond(self, session: CallSession, user_utterance: str) -> TurnResult:
        """Drive one full turn: user said X → agent says Y (and maybe ends)."""
        if user_utterance:
            session.record_user(user_utterance)

        # Honor hard turn cap.
        if session.is_over_turn_limit():
            farewell = "Thanks for your time, I'll have a colleague follow up. Goodbye."
            session.record_assistant(farewell)
            return TurnResult(
                text=farewell,
                end_call=True,
                end_reason="max_turns_reached",
                farewell=farewell,
            )

        system_prompt = self._build_system_prompt(session)
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        finish_reason: str | None = None

        try:
            async for chunk in self._llm.generate(
                system=system_prompt,
                messages=session.history,
                tools=conversation_tools(),
                max_tokens=256,
                temperature=0.5,
            ):
                if chunk.text:
                    text_parts.append(chunk.text)
                if chunk.tool_calls:
                    tool_calls.extend(chunk.tool_calls)
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "conversation.llm_error",
                call_id=session.call_id,
                error=str(exc),
            )
            fallback = (
                "I'm sorry, I'm having a bit of trouble. Let me have someone "
                "call you back shortly. Goodbye."
            )
            session.record_assistant(fallback)
            return TurnResult(
                text=fallback,
                end_call=True,
                end_reason="llm_failure",
                farewell=fallback,
            )

        assistant_text = "".join(text_parts).strip()
        end_call = False
        end_reason: str | None = None
        farewell: str | None = None
        tool_results: list[dict] = []

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = dispatch_tool(name, args)
            tool_results.append({"name": name, "arguments": args, "result": result})
            if name == "end_call":
                end_call = True
                end_reason = args.get("reason", "agent_ended")
                farewell = args.get("farewell") or "Thank you, goodbye."

        if not assistant_text and farewell:
            assistant_text = farewell
        if not assistant_text and not end_call:
            assistant_text = "Sorry, could you repeat that?"

        session.record_assistant(assistant_text, tool_calls=tool_calls or None)

        log.info(
            "conversation.turn",
            call_id=session.call_id,
            turn=session.turn_count,
            user_len=len(user_utterance),
            assistant_len=len(assistant_text),
            end_call=end_call,
            finish_reason=finish_reason,
        )

        return TurnResult(
            text=assistant_text,
            end_call=end_call,
            end_reason=end_reason,
            farewell=farewell,
            tool_results=tool_results,
        )

    async def handle_silence(self, session: CallSession) -> TurnResult:
        silences = sum(
            1
            for e in reversed(session.transcript_events)
            if e.role == "system" and e.text.startswith("[silence]")
        )
        if silences >= 1:
            farewell = "I'll go ahead and have someone follow up. Goodbye."
            session.record_assistant(farewell)
            return TurnResult(
                text=farewell, end_call=True, end_reason="silence_timeout", farewell=farewell
            )
        prompt = "Are you still there?"
        session.transcript_events.append(TurnEvent(role="system", text="[silence]"))
        session.record_assistant(prompt)
        return TurnResult(text=prompt)

    def _build_system_prompt(self, session: CallSession) -> str:
        base = session.scenario.render_system_prompt(session.context_variables)
        if session.extracted_data:
            data_block = json.dumps(session.extracted_data, ensure_ascii=False, indent=2)
            base += f"\n\n# Data captured so far\n```json\n{data_block}\n```\n"
        return base
