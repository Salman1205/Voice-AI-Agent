"""Deepgram streaming STT over WebSocket.

We receive mu-law 8 kHz audio frames from Twilio and forward them to
Deepgram Nova-3. Final transcripts are yielded back to the conversation
engine; interim results are surfaced only as VAD signals for barge-in.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import websockets
from websockets.legacy.client import WebSocketClientProtocol

from app.core.logging import get_logger
from app.providers.base import STTProvider, Transcript


log = get_logger(__name__)

DEEPGRAM_WS_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?encoding=mulaw"
    "&sample_rate=8000"
    "&channels=1"
    "&model={model}"
    "&punctuate=true"
    "&smart_format=true"
    "&interim_results=true"
    # 1100ms endpointing = more natural conversation pacing. 800ms was
    # cutting off slow speakers mid-sentence. Deepgram themselves warn
    # that UtteranceEnd can fire prematurely; we rely on this + our own
    # silence watchdog as the safety net.
    "&endpointing=1100"
    "&vad_events=true"
    "&utterance_end_ms=1500"
)


class DeepgramSTT(STTProvider):
    def __init__(self, *, api_key: str, model: str = "nova-3") -> None:
        self._api_key = api_key
        self._model = model
        self._ws: WebSocketClientProtocol | None = None
        self._closed = False

    async def _connect(self) -> WebSocketClientProtocol:
        url = DEEPGRAM_WS_URL.format(model=self._model)
        headers = {"Authorization": f"Token {self._api_key}"}
        ws = await websockets.connect(url, extra_headers=headers, ping_interval=20)
        log.info("deepgram.stt.connected", model=self._model)
        return ws

    async def stream(
        self, audio_in: AsyncIterator[bytes]
    ) -> AsyncIterator[Transcript]:
        self._ws = await self._connect()
        queue: asyncio.Queue[Transcript | None] = asyncio.Queue()

        async def pump_audio() -> None:
            try:
                async for chunk in audio_in:
                    if self._closed or self._ws is None or self._ws.closed:
                        break
                    await self._ws.send(chunk)
            except Exception as exc:  # noqa: BLE001
                log.warning("deepgram.stt.pump_error", error=str(exc))
            finally:
                if self._ws and not self._ws.closed:
                    try:
                        await self._ws.send(json.dumps({"type": "CloseStream"}))
                    except Exception:  # noqa: BLE001
                        pass

        async def pump_transcripts() -> None:
            assert self._ws is not None
            try:
                async for raw in self._ws:
                    if not raw:
                        continue
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    msg_type = msg.get("type")
                    if msg_type == "Results":
                        alt = (
                            msg.get("channel", {})
                            .get("alternatives", [{}])[0]
                        )
                        text = (alt.get("transcript") or "").strip()
                        if not text:
                            continue
                        is_final = bool(msg.get("is_final"))
                        confidence = float(alt.get("confidence", 0.0))
                        await queue.put(
                            Transcript(
                                text=text, is_final=is_final, confidence=confidence
                            )
                        )
                    elif msg_type == "SpeechStarted":
                        # VAD detected voice — fires ~50ms after speech onset,
                        # well before any transcript. Emit as a sentinel
                        # (empty interim) so the bridge can trigger barge-in
                        # immediately.
                        await queue.put(
                            Transcript(text="", is_final=False, confidence=1.0)
                        )
                    elif msg_type == "UtteranceEnd":
                        await queue.put(
                            Transcript(text="", is_final=True, confidence=1.0)
                        )
            except websockets.ConnectionClosed:
                log.info("deepgram.stt.closed")
            except Exception as exc:  # noqa: BLE001
                log.warning("deepgram.stt.recv_error", error=str(exc))
            finally:
                await queue.put(None)

        audio_task = asyncio.create_task(pump_audio())
        transcript_task = asyncio.create_task(pump_transcripts())

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            self._closed = True
            audio_task.cancel()
            transcript_task.cancel()
            await self.close()

    async def close(self) -> None:
        self._closed = True
        if self._ws and not self._ws.closed:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._ws = None
