"""Twilio webhook endpoints.

- POST /twilio/voice/{call_id}: Twilio fetches this when the callee picks up.
  We return TwiML that connects the call to our WS media bridge — unless
  AMD reports a machine, in which case we leave a voicemail and hang up.
- POST /twilio/status/{call_id}: Twilio posts lifecycle updates here.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response

from app.api.deps import get_app_settings, get_store, get_telephony
from app.conversation.state import CallStatus
from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.telephony_twilio import TwilioTelephony
from app.store.sessions import SessionStore


log = get_logger(__name__)
router = APIRouter(prefix="/twilio", tags=["twilio"])


@router.post("/voice/{call_id}", response_class=Response)
async def voice_webhook(
    call_id: str,
    request: Request,
    AnsweredBy: str | None = Form(default=None),
    store: SessionStore = Depends(get_store),
    settings: Settings = Depends(get_app_settings),
    telephony: TwilioTelephony = Depends(get_telephony),
) -> Response:
    session = await store.get(call_id)
    if not session:
        raise HTTPException(status_code=404, detail="Unknown call_id")

    await _verify_signature(request, telephony, settings)

    # Voicemail / machine detected → leave a message and hang up.
    if AnsweredBy and AnsweredBy.startswith("machine"):
        log.info(
            "twilio.voice.machine_detected",
            call_id=call_id,
            answered_by=AnsweredBy,
        )
        session.mark_status(CallStatus.VOICEMAIL, reason=f"amd:{AnsweredBy}")
        await store.put(session)
        twiml = telephony.build_voicemail_response(session.scenario.voicemail_message)
        return Response(content=twiml, media_type="application/xml")

    base = settings.public_base_url.rstrip("/")
    ws_url = f"{base}/ws/media/{call_id}"
    twiml = telephony.build_stream_response(ws_url)
    log.info("twilio.voice.connect_stream", call_id=call_id, ws_url=ws_url)
    return Response(content=twiml, media_type="application/xml")


@router.post("/status/{call_id}", response_class=Response)
async def status_webhook(
    call_id: str,
    request: Request,
    CallStatus: str = Form(default=""),
    CallDuration: str | None = Form(default=None),
    store: SessionStore = Depends(get_store),
    settings: Settings = Depends(get_app_settings),
    telephony: TwilioTelephony = Depends(get_telephony),
) -> Response:
    session = await store.get(call_id)
    if not session:
        return Response(status_code=204)

    await _verify_signature(request, telephony, settings)

    log.info(
        "twilio.status.update",
        call_id=call_id,
        twilio_status=CallStatus,
        duration=CallDuration,
    )

    # Map Twilio statuses → our enum.
    from app.conversation.state import CallStatus as Status

    mapping = {
        "queued": Status.QUEUED,
        "initiated": Status.DIALING,
        "ringing": Status.RINGING,
        "in-progress": Status.IN_PROGRESS,
        "answered": Status.IN_PROGRESS,
        "completed": Status.COMPLETED,
        "busy": Status.BUSY,
        "no-answer": Status.NO_ANSWER,
        "failed": Status.FAILED,
        "canceled": Status.ABANDONED,
    }
    mapped = mapping.get(CallStatus.lower())
    if mapped:
        # Don't downgrade from a final state.
        terminal = {
            Status.COMPLETED,
            Status.FAILED,
            Status.NO_ANSWER,
            Status.VOICEMAIL,
            Status.BUSY,
            Status.TIMED_OUT,
            Status.ABANDONED,
            Status.HOSTILE_CALLER,
        }
        if session.status not in terminal:
            session.mark_status(mapped, reason=f"twilio:{CallStatus}")
            await store.put(session)

    return Response(status_code=204)


async def _verify_signature(
    request: Request,
    telephony: TwilioTelephony,
    settings: Settings,
) -> None:
    if not settings.twilio_auth_token:
        return
    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        if settings.enforce_twilio_signature:
            log.warning("twilio.signature.missing", path=str(request.url))
            raise HTTPException(status_code=403, detail="Missing Twilio signature")
        log.warning("twilio.signature.missing", path=str(request.url))
        return
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    full_url = str(request.url)
    if not telephony.verify_webhook_signature(full_url, params, signature):
        log.warning(
            "twilio.signature.invalid",
            url=full_url,
            qs=urlencode(params),
        )
        if settings.enforce_twilio_signature:
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")
