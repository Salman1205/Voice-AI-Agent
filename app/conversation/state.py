"""Per-call session state.

Holds everything we need to drive the conversation and produce a final
structured outcome: scenario config, message history, extracted data,
status, timing, and event timeline.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.providers.base import Message
from app.scenarios.loader import ScenarioConfig


class CallStatus(str, Enum):
    QUEUED = "queued"
    DIALING = "dialing"
    RINGING = "ringing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NO_ANSWER = "no_answer"
    VOICEMAIL = "voicemail"
    BUSY = "busy"
    TIMED_OUT = "timed_out"
    ABANDONED = "abandoned"
    HOSTILE_CALLER = "hostile_caller"


@dataclass
class TurnEvent:
    role: str  # "user" | "assistant" | "system" | "tool"
    text: str
    ts: float = field(default_factory=time.time)


@dataclass
class CallSession:
    call_id: str
    to: str
    scenario: ScenarioConfig
    context_variables: dict[str, str] = field(default_factory=dict)
    provider_call_id: str | None = None
    status: CallStatus = CallStatus.QUEUED
    history: list[Message] = field(default_factory=list)
    transcript_events: list[TurnEvent] = field(default_factory=list)
    extracted_data: dict[str, Any] = field(default_factory=dict)
    turn_count: int = 0
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    end_reason: str | None = None
    interrupt_count: int = 0
    final_outcome: dict[str, Any] | None = None

    @classmethod
    def new(
        cls,
        *,
        to: str,
        scenario: ScenarioConfig,
        context_variables: dict[str, str] | None = None,
    ) -> "CallSession":
        return cls(
            call_id=str(uuid.uuid4()),
            to=to,
            scenario=scenario,
            context_variables=context_variables or {},
        )

    def record_user(self, text: str) -> None:
        self.history.append(Message(role="user", content=text))
        self.transcript_events.append(TurnEvent(role="user", text=text))

    def record_assistant(self, text: str, tool_calls: list[dict] | None = None) -> None:
        self.history.append(
            Message(role="assistant", content=text, tool_calls=tool_calls)
        )
        if text:
            self.transcript_events.append(TurnEvent(role="assistant", text=text))
        self.turn_count += 1

    def record_tool_result(self, tool_call_id: str, name: str, result: str) -> None:
        self.history.append(
            Message(
                role="tool",
                content=result,
                name=name,
                tool_call_id=tool_call_id,
            )
        )

    def is_over_turn_limit(self) -> bool:
        return self.turn_count >= self.scenario.max_turns

    def render_history_for_summary(self) -> str:
        lines = []
        for e in self.transcript_events:
            speaker = "Caller" if e.role == "user" else "Agent"
            lines.append(f"{speaker}: {e.text}")
        return "\n".join(lines)

    def mark_status(self, status: CallStatus, reason: str | None = None) -> None:
        self.status = status
        if status in {
            CallStatus.COMPLETED,
            CallStatus.FAILED,
            CallStatus.NO_ANSWER,
            CallStatus.VOICEMAIL,
            CallStatus.BUSY,
            CallStatus.TIMED_OUT,
            CallStatus.ABANDONED,
            CallStatus.HOSTILE_CALLER,
        } and self.ended_at is None:
            self.ended_at = time.time()
            self.end_reason = reason
