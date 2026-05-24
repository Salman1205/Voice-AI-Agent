"""ConversationEngine turn orchestration with a stub LLM."""

from __future__ import annotations

from typing import AsyncIterator

import pytest

from app.conversation.engine import ConversationEngine
from app.conversation.state import CallSession
from app.providers.base import LLMChunk, LLMProvider, Message
from app.scenarios.loader import build_custom


class ScriptedLLM(LLMProvider):
    """Yields chunks from a pre-baked sequence; supports text + tool_calls."""

    def __init__(self, script: list[LLMChunk]) -> None:
        self._script = script

    async def generate(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict] | None = None,
        max_tokens: int = 512,
        temperature: float = 0.4,
    ) -> AsyncIterator[LLMChunk]:
        for chunk in self._script:
            yield chunk


def _new_session() -> CallSession:
    scenario = build_custom(
        custom_prompt="You are a friendly tester verifying conversation orchestration.",
        opening_line="Hi there!",
    )
    return CallSession.new(to="+14155552671", scenario=scenario)


@pytest.mark.asyncio
async def test_first_turn_returns_opening_line() -> None:
    s = _new_session()
    engine = ConversationEngine(llm=ScriptedLLM([]))
    opening = await engine.first_turn(s)
    assert opening == "Hi there!"
    # opening was recorded as assistant turn
    assert s.history[-1].role == "assistant"


@pytest.mark.asyncio
async def test_text_only_turn() -> None:
    s = _new_session()
    engine = ConversationEngine(
        llm=ScriptedLLM([LLMChunk(text="Of course!"), LLMChunk(finish_reason="stop")])
    )
    result = await engine.respond(s, "Are you free?")
    assert result.text == "Of course!"
    assert not result.end_call
    assert s.turn_count == 1


@pytest.mark.asyncio
async def test_end_call_tool_terminates() -> None:
    s = _new_session()
    end_tool = {
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "end_call",
            "arguments": '{"reason": "done", "farewell": "Bye!"}',
        },
    }
    engine = ConversationEngine(
        llm=ScriptedLLM(
            [
                LLMChunk(text="Thanks. "),
                LLMChunk(tool_calls=[end_tool], finish_reason="tool_calls"),
            ]
        )
    )
    result = await engine.respond(s, "I'm all set")
    assert result.end_call
    assert result.farewell == "Bye!"
    assert result.text  # something was said


@pytest.mark.asyncio
async def test_max_turns_forces_graceful_end() -> None:
    s = _new_session()
    # Push turn_count up to the cap.
    for _ in range(s.scenario.max_turns):
        s.record_assistant("filler")
    engine = ConversationEngine(llm=ScriptedLLM([]))
    result = await engine.respond(s, "anything")
    assert result.end_call
    assert result.end_reason == "max_turns_reached"


@pytest.mark.asyncio
async def test_llm_failure_returns_safe_farewell() -> None:
    class BoomLLM(LLMProvider):
        async def generate(self, **kwargs):  # type: ignore[override]
            raise RuntimeError("network down")
            yield  # pragma: no cover

    s = _new_session()
    engine = ConversationEngine(llm=BoomLLM())
    result = await engine.respond(s, "Hello?")
    assert result.end_call
    assert result.end_reason == "llm_failure"
    assert "sorry" in result.text.lower() or "trouble" in result.text.lower()


@pytest.mark.asyncio
async def test_silence_handler_first_warns_then_ends() -> None:
    s = _new_session()
    engine = ConversationEngine(llm=ScriptedLLM([]))
    first = await engine.handle_silence(s)
    assert not first.end_call
    assert "still there" in first.text.lower()
    second = await engine.handle_silence(s)
    assert second.end_call
