"""Outcome extraction + lenient JSON parsing."""

from __future__ import annotations

from typing import AsyncIterator

import pytest

from app.conversation.outcome import OutcomeRecorder, _parse_json_lenient
from app.conversation.state import CallSession, CallStatus
from app.providers.base import LLMChunk, LLMProvider, Message
from app.scenarios.loader import build_custom


class StubLLM(LLMProvider):
    def __init__(self, scripted: str) -> None:
        self._scripted = scripted

    async def generate(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict] | None = None,
        max_tokens: int = 512,
        temperature: float = 0.4,
    ) -> AsyncIterator[LLMChunk]:
        yield LLMChunk(text=self._scripted)
        yield LLMChunk(finish_reason="stop")


@pytest.mark.asyncio
async def test_outcome_finalize_produces_required_fields(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    # Recreate outcomes dir inside the temp cwd.
    (tmp_path / "outcomes").mkdir(exist_ok=True)

    scenario = build_custom(custom_prompt="You are testing the outcome flow end to end.")
    s = CallSession.new(to="+14155552671", scenario=scenario)
    s.record_user("Yes I'd like to confirm")
    s.record_assistant("Great, you're confirmed.")
    s.mark_status(CallStatus.COMPLETED, reason="agent_ended")

    llm = StubLLM('{"status": "confirmed", "summary": "patient confirmed"}')
    recorder = OutcomeRecorder(llm=llm)

    outcome = await recorder.finalize(s)
    assert outcome["call_id"] == s.call_id
    assert outcome["status"] == "completed"
    assert outcome["scenario"] == "custom"
    assert outcome["extracted_data"]["status"] == "confirmed"
    assert isinstance(outcome["transcript"], list)
    assert outcome["duration_seconds"] >= 0


@pytest.mark.asyncio
async def test_outcome_handles_llm_failure_gracefully() -> None:
    class BoomLLM(LLMProvider):
        async def generate(self, **kwargs):  # type: ignore[override]
            raise RuntimeError("boom")
            yield  # pragma: no cover

    scenario = build_custom(custom_prompt="Testing failure paths in outcome extraction.")
    s = CallSession.new(to="+14155552671", scenario=scenario)
    s.mark_status(CallStatus.COMPLETED)
    recorder = OutcomeRecorder(llm=BoomLLM())
    outcome = await recorder.finalize(s)
    # Should still produce a basic outcome from session state.
    assert outcome["status"] == "completed"


class TestParseJsonLenient:
    def test_plain_json(self) -> None:
        assert _parse_json_lenient('{"a": 1}') == {"a": 1}

    def test_with_prose(self) -> None:
        text = 'Sure thing! {"a": 1, "b": "x"} hope that helps.'
        assert _parse_json_lenient(text) == {"a": 1, "b": "x"}

    def test_with_code_fence(self) -> None:
        text = '```json\n{"x": 2}\n```'
        assert _parse_json_lenient(text) == {"x": 2}

    def test_garbage(self) -> None:
        assert _parse_json_lenient("nothing useful here") == {}

    def test_empty(self) -> None:
        assert _parse_json_lenient("") == {}
