"""CallSession state machine + history accumulation."""

from __future__ import annotations

from app.conversation.state import CallSession, CallStatus, TurnEvent
from app.scenarios.loader import build_custom


def _make_session() -> CallSession:
    scenario = build_custom(custom_prompt="You are testing things, calling to verify.")
    return CallSession.new(
        to="+14155552671",
        scenario=scenario,
        context_variables={"foo": "bar"},
    )


class TestCallSession:
    def test_starts_queued(self) -> None:
        s = _make_session()
        assert s.status == CallStatus.QUEUED
        assert s.turn_count == 0
        assert s.history == []
        assert s.ended_at is None

    def test_record_user_then_assistant(self) -> None:
        s = _make_session()
        s.record_user("Hello?")
        s.record_assistant("Hi there!")
        assert s.turn_count == 1
        assert s.history[0].role == "user"
        assert s.history[1].role == "assistant"
        assert len(s.transcript_events) == 2

    def test_turn_limit(self) -> None:
        s = _make_session()
        # The custom scenario defaults to max_turns=12.
        for i in range(12):
            s.record_assistant(f"turn {i}")
        assert s.is_over_turn_limit()

    def test_mark_terminal_sets_ended_at(self) -> None:
        s = _make_session()
        s.mark_status(CallStatus.COMPLETED, reason="done")
        assert s.ended_at is not None
        assert s.end_reason == "done"

    def test_mark_non_terminal_does_not_set_ended(self) -> None:
        s = _make_session()
        s.mark_status(CallStatus.IN_PROGRESS)
        assert s.ended_at is None

    def test_render_history_for_summary(self) -> None:
        s = _make_session()
        s.record_user("can you call me Tuesday?")
        s.record_assistant("sure thing, Tuesday it is")
        rendered = s.render_history_for_summary()
        assert "Caller" in rendered
        assert "Agent" in rendered
        assert "Tuesday" in rendered

    def test_tool_call_recorded(self) -> None:
        s = _make_session()
        s.record_assistant(
            "Got it.",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "update_extracted_data", "arguments": "{}"},
                }
            ],
        )
        assert s.history[-1].tool_calls is not None

    def test_silence_event(self) -> None:
        s = _make_session()
        s.transcript_events.append(TurnEvent(role="system", text="[silence]"))
        assert s.transcript_events[-1].role == "system"
