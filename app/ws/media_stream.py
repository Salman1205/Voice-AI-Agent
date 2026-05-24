"""WebSocket bridge between Twilio Media Streams and the conversation engine."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any, AsyncIterator

from fastapi import WebSocket, WebSocketDisconnect

from app.conversation.engine import ConversationEngine, TurnResult
from app.conversation.outcome import OutcomeRecorder
from app.conversation.state import CallSession, CallStatus
from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.base import STTProvider, TTSProvider
from app.store.sessions import SessionStore


log = get_logger(__name__)

SILENCE_TIMEOUT_SECONDS = 10.0
CALL_HARD_TIMEOUT_SECONDS_DEFAULT = 300


class MediaStreamBridge:
    """One instance per active call."""

    def __init__(
        self,
        *,
        ws: WebSocket,
        session: CallSession,
        store: SessionStore,
        stt: STTProvider,
        tts: TTSProvider,
        engine: ConversationEngine,
        outcome_recorder: OutcomeRecorder,
        settings: Settings,
    ) -> None:
        self._ws = ws
        self._session = session
        self._store = store
        self._stt = stt
        self._tts = tts
        self._engine = engine
        self._outcome = outcome_recorder
        self._settings = settings

        self._stream_sid: str | None = None
        self._stream_ready = asyncio.Event()
        self._inbound_audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._last_user_speech_at: float = time.time()
        self._last_final_transcript: str = ""
        # Serialises turn execution so a barge-in arriving while the previous
        # LLM call is still in flight does not produce overlapping responses
        # or interleaved writes to session.history.
        self._turn_lock = asyncio.Lock()
        self._agent_speaking = False
        self._agent_speak_started_at: float = 0.0
        self._agent_stopped_at: float = 0.0
        self._tts_cancel_event = asyncio.Event()
        self._end_requested = False
        self._end_reason: str | None = None
        self._final_farewell: str | None = None

    async def run(self) -> None:
        await self._ws.accept()
        self._session.mark_status(CallStatus.IN_PROGRESS)
        await self._store.put(self._session)

        log.info("ws.bridge.started", call_id=self._session.call_id)

        twilio_task = asyncio.create_task(self._read_twilio_events())
        stt_task = asyncio.create_task(self._consume_stt())
        watchdog_task = asyncio.create_task(self._watchdog())

        try:
            # Wait for Twilio's start event before speaking — without the
            # stream_sid set, outbound media frames are silently dropped.
            try:
                await asyncio.wait_for(self._stream_ready.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                log.warning(
                    "ws.bridge.no_stream_sid",
                    call_id=self._session.call_id,
                )
                return

            opening = await self._engine.first_turn(self._session)
            await self._speak(opening)

            await asyncio.wait(
                [twilio_task, stt_task, watchdog_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "ws.bridge.error", call_id=self._session.call_id, error=str(exc)
            )
        finally:
            for t in (twilio_task, stt_task, watchdog_task):
                t.cancel()
            await self._shutdown()

    # --- Twilio inbound (audio + lifecycle events) -----------------------

    async def _read_twilio_events(self) -> None:
        try:
            while True:
                raw = await self._ws.receive_text()
                msg = json.loads(raw)
                event = msg.get("event")

                if event == "start":
                    self._stream_sid = msg["start"]["streamSid"]
                    self._stream_ready.set()
                    log.info(
                        "ws.twilio.start",
                        call_id=self._session.call_id,
                        stream_sid=self._stream_sid,
                    )

                elif event == "media":
                    payload_b64 = msg["media"]["payload"]
                    audio = base64.b64decode(payload_b64)
                    await self._inbound_audio_queue.put(audio)

                elif event == "stop":
                    log.info(
                        "ws.twilio.stop",
                        call_id=self._session.call_id,
                    )
                    self._session.mark_status(
                        CallStatus.ABANDONED
                        if self._session.status == CallStatus.IN_PROGRESS
                        else self._session.status,
                        reason="twilio_stop",
                    )
                    await self._inbound_audio_queue.put(None)
                    return

        except WebSocketDisconnect:
            log.info("ws.twilio.disconnected", call_id=self._session.call_id)
            await self._inbound_audio_queue.put(None)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "ws.twilio.read_error",
                call_id=self._session.call_id,
                error=str(exc),
            )
            await self._inbound_audio_queue.put(None)

    async def _audio_generator(self) -> AsyncIterator[bytes]:
        while True:
            chunk = await self._inbound_audio_queue.get()
            if chunk is None:
                return
            yield chunk

    # --- STT consumer -----------------------------------------------------

    async def _consume_stt(self) -> None:
        try:
            async for transcript in self._stt.stream(self._audio_generator()):
                if not transcript.is_final:
                    # Phone-line echo of the agent's own voice round-trips back
                    # within 100-300ms and Deepgram can transcribe it as if the
                    # caller spoke. Ignore interim signals for the first 800ms
                    # of each TTS utterance so we don't cut ourselves off.
                    if self._agent_speaking:
                        speak_age = time.time() - self._agent_speak_started_at
                        if speak_age < 0.8:
                            continue
                        if transcript.text:
                            self._session.interrupt_count += 1
                        self._tts_cancel_event.set()
                        await self._send_clear()
                    self._last_user_speech_at = time.time()
                    continue

                text = transcript.text.strip()
                if not text:
                    # Empty final = utterance-end signal; treat as turn boundary.
                    if self._last_final_transcript:
                        await self._run_turn(self._last_final_transcript)
                        self._last_final_transcript = ""
                    continue

                # Echo guard: drop short transcripts that arrive while the
                # agent is still speaking OR within 500ms of it finishing.
                # Phone-line echo of the agent's own voice is the #1 source
                # of phantom transcripts. Real barge-in is already handled
                # by the SpeechStarted VAD signal above.
                in_echo_window = (
                    self._agent_speaking
                    or (time.time() - self._agent_stopped_at) < 0.5
                )
                if in_echo_window and len(text) <= 12:
                    log.info(
                        "ws.echo_guard.dropped",
                        call_id=self._session.call_id,
                        text=text,
                    )
                    continue

                # Accumulate finals until utterance-end OR a complete sentence.
                self._last_final_transcript = (
                    f"{self._last_final_transcript} {text}".strip()
                )
                self._last_user_speech_at = time.time()

                # If we get a complete-looking sentence, fire immediately so
                # the agent feels responsive.
                if text.endswith((".", "?", "!")):
                    utterance = self._last_final_transcript
                    self._last_final_transcript = ""
                    await self._run_turn(utterance)

        except Exception as exc:  # noqa: BLE001
            log.warning(
                "ws.stt.consume_error",
                call_id=self._session.call_id,
                error=str(exc),
            )

    # --- Turn execution ---------------------------------------------------

    async def _run_turn(self, user_text: str) -> None:
        async with self._turn_lock:
            log.info(
                "ws.user_utterance",
                call_id=self._session.call_id,
                text=user_text[:200],
            )
            result = await self._engine.respond(self._session, user_text)
            await self._speak(result.text)
            if result.end_call:
                self._end_reason = result.end_reason
                self._final_farewell = result.farewell
                self._end_requested = True
                # Let the audio play out before we tear down the call.
                await asyncio.sleep(1.0)
                await self._inbound_audio_queue.put(None)

    # --- TTS playback -----------------------------------------------------

    async def _speak(self, text: str) -> None:
        if not text or self._stream_sid is None:
            return
        self._tts_cancel_event.clear()
        self._agent_speaking = True
        self._agent_speak_started_at = time.time()
        try:
            async for audio_chunk in self._tts.synthesize(text):
                if self._tts_cancel_event.is_set():
                    log.info("ws.tts.barge_in", call_id=self._session.call_id)
                    break
                await self._send_media(audio_chunk)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "ws.tts.error", call_id=self._session.call_id, error=str(exc)
            )
        finally:
            self._agent_speaking = False
            self._agent_stopped_at = time.time()

    async def _send_media(self, audio: bytes) -> None:
        if not audio or self._stream_sid is None:
            return
        try:
            await self._ws.send_text(
                json.dumps(
                    {
                        "event": "media",
                        "streamSid": self._stream_sid,
                        "media": {"payload": base64.b64encode(audio).decode("ascii")},
                    }
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "ws.send_media.error",
                call_id=self._session.call_id,
                error=str(exc),
            )

    async def _send_clear(self) -> None:
        """Tell Twilio to flush any buffered TTS audio (true barge-in)."""
        if self._stream_sid is None:
            return
        try:
            await self._ws.send_text(
                json.dumps({"event": "clear", "streamSid": self._stream_sid})
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "ws.send_clear.error",
                call_id=self._session.call_id,
                error=str(exc),
            )

    # --- Watchdog: silence + duration --------------------------------------

    async def _watchdog(self) -> None:
        hard_timeout = self._settings.call_max_duration_seconds or CALL_HARD_TIMEOUT_SECONDS_DEFAULT
        start = time.time()
        silence_warned = False
        while not self._end_requested:
            await asyncio.sleep(1.0)
            now = time.time()

            if (now - start) > hard_timeout:
                log.info(
                    "ws.watchdog.hard_timeout",
                    call_id=self._session.call_id,
                )
                self._session.mark_status(CallStatus.TIMED_OUT, reason="hard_timeout")
                farewell = "I've kept you long enough — thank you and goodbye."
                await self._speak(farewell)
                self._end_requested = True
                await self._inbound_audio_queue.put(None)
                return

            silent_for = now - self._last_user_speech_at
            if not self._agent_speaking and silent_for > SILENCE_TIMEOUT_SECONDS:
                if not silence_warned:
                    silence_warned = True
                    result = await self._engine.handle_silence(self._session)
                    await self._speak(result.text)
                    if result.end_call:
                        self._end_requested = True
                        await asyncio.sleep(1.0)
                        await self._inbound_audio_queue.put(None)
                        return
                    # Reset silence clock so we don't immediately retrigger.
                    self._last_user_speech_at = now
                else:
                    # Already warned once — end.
                    farewell = (
                        self._final_farewell
                        or "I'll try again later. Goodbye."
                    )
                    await self._speak(farewell)
                    self._session.mark_status(
                        CallStatus.NO_ANSWER, reason="double_silence"
                    )
                    self._end_requested = True
                    await self._inbound_audio_queue.put(None)
                    return

    # --- Teardown ---------------------------------------------------------

    async def _shutdown(self) -> None:
        if self._session.status == CallStatus.IN_PROGRESS:
            self._session.mark_status(
                CallStatus.COMPLETED, reason=self._end_reason or "ws_closed"
            )
        try:
            await self._stt.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            await self._tts.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            await self._ws.close()
        except Exception:  # noqa: BLE001
            pass

        # Always record an outcome, even on partial transcripts.
        try:
            await self._outcome.finalize(self._session)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "ws.outcome.finalize_error",
                call_id=self._session.call_id,
                error=str(exc),
            )

        log.info(
            "ws.bridge.closed",
            call_id=self._session.call_id,
            status=self._session.status.value,
            turn_count=self._session.turn_count,
        )
