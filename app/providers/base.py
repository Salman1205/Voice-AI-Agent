"""Provider Protocols for STT, LLM, TTS, and telephony.

Audio is mu-law G.711 at 8 kHz mono throughout (the Twilio Media Streams format).
STT and TTS expose async iterators. LLM yields plain text tokens; tool dispatch
lives in the conversation engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Literal, Protocol


@dataclass(frozen=True)
class Transcript:
    text: str
    is_final: bool
    confidence: float = 1.0
    speaker: int | None = None


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict] | None = None


@dataclass(frozen=True)
class LLMChunk:
    """A streaming chunk from the LLM. Either text or a finalized tool call."""

    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str | None = None


@dataclass(frozen=True)
class CallHandle:
    """Reference to an outbound call placed via the telephony provider."""

    provider_call_id: str
    to: str
    from_: str
    status: str


class STTProvider(Protocol):
    """Streaming speech-to-text."""

    async def stream(
        self, audio_in: AsyncIterator[bytes]
    ) -> AsyncIterator[Transcript]: ...

    async def close(self) -> None: ...


class LLMProvider(Protocol):
    """Streaming chat completion with tool-calling support."""

    async def generate(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict] | None = None,
        max_tokens: int = 512,
        temperature: float = 0.4,
    ) -> AsyncIterator[LLMChunk]: ...


class TTSProvider(Protocol):
    """Streaming text-to-speech. Yields mu-law 8 kHz audio frames."""

    async def synthesize(self, text: str) -> AsyncIterator[bytes]: ...

    async def close(self) -> None: ...


class TelephonyProvider(Protocol):
    """Outbound telephony + TwiML-equivalent response generation."""

    async def place_call(
        self,
        *,
        to: str,
        callback_url: str,
        status_callback_url: str,
        machine_detection: bool = True,
    ) -> CallHandle: ...

    def build_stream_response(self, ws_url: str) -> str:
        """Return the XML/TwiML payload that tells the carrier to open a media stream."""
        ...

    def verify_webhook_signature(
        self, url: str, params: dict[str, str], signature: str
    ) -> bool: ...
