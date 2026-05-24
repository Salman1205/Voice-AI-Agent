"""Provider smoke tests — verify each API key works in isolation.

Run BEFORE attempting a real Twilio call so you know which provider is
broken if anything fails. Costs ~$0.001 total (a tiny Groq completion
and ~1KB of Deepgram TTS audio).

Usage:
    python -m scripts.smoke_providers
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow running as `python scripts/smoke_providers.py` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.providers.factory import build_llm, build_telephony, build_tts
from app.providers.base import Message


GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"{GREEN}[OK]{RESET}    {msg}")


def fail(msg: str) -> None:
    print(f"{RED}[FAIL]{RESET}  {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}[WARN]{RESET}  {msg}")


async def test_groq(settings) -> bool:
    print("\n--- Groq LLM ---")
    try:
        llm = build_llm(settings)
        chunks: list[str] = []
        async for chunk in llm.generate(
            system="You are a test bot. Reply with exactly the word 'pong'.",
            messages=[Message(role="user", content="ping")],
            max_tokens=10,
            temperature=0.0,
        ):
            if chunk.text:
                chunks.append(chunk.text)
        response = "".join(chunks).strip()
        if response:
            ok(f"Groq replied: {response!r}")
            return True
        fail("Groq returned empty response")
        return False
    except Exception as exc:
        fail(f"Groq error: {exc}")
        return False


async def test_deepgram_tts(settings) -> bool:
    print("\n--- Deepgram TTS (Aura) ---")
    try:
        tts = build_tts(settings)
        total = 0
        async for chunk in tts.synthesize("Hello, this is a smoke test."):
            total += len(chunk)
        await tts.close()
        if total > 0:
            ok(f"Deepgram TTS returned {total} bytes of audio")
            return True
        fail("Deepgram TTS returned no audio — check the key or model name")
        return False
    except Exception as exc:
        fail(f"Deepgram TTS error: {exc}")
        return False


async def test_twilio(settings) -> bool:
    """Read-only check: list account info via Twilio REST without placing a call."""
    print("\n--- Twilio (read-only) ---")
    if not settings.twilio_account_sid or settings.twilio_account_sid.startswith("ACxxx"):
        fail("TWILIO_ACCOUNT_SID looks like a placeholder")
        return False
    try:
        tel = build_telephony(settings)
        # twilio-python is sync; call account.fetch in a thread
        client = tel._client  # noqa: SLF001
        account = await asyncio.to_thread(client.api.accounts(settings.twilio_account_sid).fetch)
        ok(f"Twilio account: {account.friendly_name} (status={account.status})")
        return True
    except Exception as exc:
        fail(f"Twilio error: {exc}")
        return False


def check_config(settings) -> bool:
    print("--- Config ---")
    missing = settings.validate_required_keys()
    if missing:
        fail(f"Missing env keys: {missing}")
        return False
    ok("All required keys present in .env")
    if settings.public_base_url.startswith("https://your-"):
        warn(
            "PUBLIC_BASE_URL is still the placeholder. "
            "For real calls, start ngrok and paste the URL into .env."
        )
    else:
        ok(f"PUBLIC_BASE_URL = {settings.public_base_url}")
    return True


async def main() -> int:
    settings = get_settings()
    print(f"Loaded .env from {Path('.env').resolve()}\n")

    cfg_ok = check_config(settings)
    if not cfg_ok:
        return 1

    results = await asyncio.gather(
        test_groq(settings),
        test_deepgram_tts(settings),
        test_twilio(settings),
    )

    print("\n--- Summary ---")
    labels = ["Groq LLM", "Deepgram TTS", "Twilio REST"]
    for label, result in zip(labels, results):
        (ok if result else fail)(label)

    if all(results):
        print(f"\n{GREEN}All providers healthy.{RESET} Safe to attempt a real call.")
        return 0
    print(
        f"\n{RED}One or more providers failed.{RESET} "
        "Fix them before launching a real call."
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
