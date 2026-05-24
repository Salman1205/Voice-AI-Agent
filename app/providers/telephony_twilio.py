"""Twilio telephony implementation."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

from twilio.request_validator import RequestValidator
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, Stream, VoiceResponse

from app.core.logging import get_logger
from app.providers.base import CallHandle, TelephonyProvider


log = get_logger(__name__)


class TwilioTelephony(TelephonyProvider):
    def __init__(self, *, account_sid: str, auth_token: str, from_number: str) -> None:
        self._client = Client(account_sid, auth_token)
        self._from = from_number
        self._validator = RequestValidator(auth_token)

    async def place_call(
        self,
        *,
        to: str,
        callback_url: str,
        status_callback_url: str,
        machine_detection: bool = True,
    ) -> CallHandle:
        kwargs: dict[str, Any] = {
            "to": to,
            "from_": self._from,
            "url": callback_url,
            "status_callback": status_callback_url,
            "status_callback_event": ["initiated", "ringing", "answered", "completed"],
            "status_callback_method": "POST",
        }
        if machine_detection:
            kwargs["machine_detection"] = "DetectMessageEnd"
            kwargs["machine_detection_timeout"] = 5
        # twilio-python is sync; run in thread to keep FastAPI loop unblocked
        call = await asyncio.to_thread(self._client.calls.create, **kwargs)
        log.info(
            "twilio.call.placed",
            sid=call.sid,
            to=to,
            from_=self._from,
            status=call.status,
        )
        return CallHandle(
            provider_call_id=call.sid,
            to=to,
            from_=self._from,
            status=call.status or "queued",
        )

    def build_stream_response(self, ws_url: str) -> str:
        """TwiML that connects the call to our WS media bridge."""
        # Twilio requires wss:// for media streams.
        parsed = urlparse(ws_url)
        if parsed.scheme == "http":
            ws_url = ws_url.replace("http://", "ws://", 1)
        elif parsed.scheme == "https":
            ws_url = ws_url.replace("https://", "wss://", 1)
        response = VoiceResponse()
        connect = Connect()
        stream = Stream(url=ws_url)
        connect.append(stream)
        response.append(connect)
        return str(response)

    def build_voicemail_response(self, message: str) -> str:
        """Spoken message + hangup, used when AMD detects a machine."""
        response = VoiceResponse()
        response.say(message, voice="Polly.Joanna-Neural")
        response.hangup()
        return str(response)

    def build_say_and_hangup(self, message: str) -> str:
        """Used as graceful degradation when streaming media fails."""
        return self.build_voicemail_response(message)

    def verify_webhook_signature(
        self, url: str, params: dict[str, str], signature: str
    ) -> bool:
        try:
            return self._validator.validate(url, params, signature)
        except Exception as exc:  # noqa: BLE001
            log.warning("twilio.signature.verify_error", error=str(exc))
            return False

    async def end_call(self, call_sid: str) -> None:
        """Force-terminate a call from the server side."""
        try:
            await asyncio.to_thread(
                self._client.calls(call_sid).update, status="completed"
            )
            log.info("twilio.call.ended", sid=call_sid)
        except Exception as exc:  # noqa: BLE001
            log.warning("twilio.call.end_error", sid=call_sid, error=str(exc))
