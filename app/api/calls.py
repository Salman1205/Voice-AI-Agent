"""Outbound call dispatch + status endpoints."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sse_starlette.sse import EventSourceResponse
from twilio.base.exceptions import TwilioRestException

from app.api.deps import (
    get_app_settings,
    get_outcome_recorder,
    get_preset_scenario,
    get_store,
    get_telephony,
)
from app.api.schemas import (
    CallStateResponse,
    CreateCallRequest,
    CreateCallResponse,
)
from app.conversation.outcome import OutcomeRecorder
from app.conversation.state import CallSession, CallStatus
from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.base import TelephonyProvider
from app.scenarios.loader import ScenarioConfig, build_custom
from app.store.sessions import SessionStore


log = get_logger(__name__)
router = APIRouter(prefix="/calls", tags=["calls"])

# ANSI escape codes injected by Twilio's exception __str__ when stderr is a TTY.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Friendly explanations for common Twilio error codes so the UI can show
# an actionable hint instead of raw provider output.
_TWILIO_ERROR_HINTS: dict[int, str] = {
    21219: (
        "This number isn't verified on your Twilio trial account. "
        "Verify it at https://console.twilio.com/us1/develop/phone-numbers/manage/verified, "
        "or upgrade your Twilio account to call any number."
    ),
    13227: (
        "Geo permissions block calls to this destination. Enable the "
        "destination country in Twilio Console → Voice → Settings → Geo Permissions."
    ),
    21211: "The 'To' number is invalid. Use E.164 format (e.g. +14155552671).",
    21210: "The 'From' number is not a Twilio number on this account.",
    21214: "The 'To' number cannot be reached from this Twilio account.",
    20003: "Twilio authentication failed. Check TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN.",
    20404: "Twilio resource not found. Check the SID or endpoint being used.",
}


def _format_telephony_error(exc: Exception) -> str:
    """Build a clean, user-facing message from a telephony provider exception.

    Why: TwilioRestException.__str__ embeds ANSI color escapes when stderr is
    a TTY, which leak into HTTP responses and render as garbage in the browser.
    Use the structured fields instead, and promote known codes to actionable
    hints so trial-account issues are self-service.
    """
    if isinstance(exc, TwilioRestException):
        code = exc.code
        hint = _TWILIO_ERROR_HINTS.get(code) if code is not None else None
        if hint:
            return f"Twilio error {code}: {exc.msg.strip()} — {hint}"
        docs = (
            f"https://www.twilio.com/docs/errors/{code}"
            if code is not None
            else "https://www.twilio.com/docs/errors"
        )
        prefix = f"Twilio error {code}" if code is not None else f"Twilio HTTP {exc.status}"
        return f"{prefix}: {exc.msg.strip()} (see {docs})"
    return _ANSI_RE.sub("", str(exc)).strip()


@router.post(
    "",
    response_model=CreateCallResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_call(
    payload: CreateCallRequest,
    preset: ScenarioConfig = Depends(get_preset_scenario),
    settings: Settings = Depends(get_app_settings),
    store: SessionStore = Depends(get_store),
    telephony: TelephonyProvider = Depends(get_telephony),
) -> CreateCallResponse:
    # Build the scenario for THIS call (preset or custom).
    if payload.mode == "custom":
        if not payload.custom:
            raise HTTPException(
                status_code=422,
                detail="mode=custom requires 'custom' object with custom_prompt.",
            )
        scenario = build_custom(
            custom_prompt=payload.custom.custom_prompt,
            opening_line=payload.custom.opening_line,
            name=payload.custom.name,
        )
    else:
        # Preset is self-contained — any context_variables are optional
        # extras (e.g. when integrating with a real CRM that has patient
        # data). For a bare API call (only phone + scenario), the agent
        # gathers what it needs during the conversation.
        scenario = preset

    # Enforce per-hour cap.
    if await _exceeds_hourly_cap(store, settings.max_calls_per_hour):
        raise HTTPException(
            status_code=429,
            detail=(
                f"Rate limit reached: {settings.max_calls_per_hour} calls/hour. "
                "Wait or raise MAX_CALLS_PER_HOUR."
            ),
        )

    session = CallSession.new(
        to=payload.to,
        scenario=scenario,
        context_variables=payload.context_variables,
    )
    await store.put(session)

    voice_url = f"{settings.public_base_url.rstrip('/')}/twilio/voice/{session.call_id}"
    status_url = f"{settings.public_base_url.rstrip('/')}/twilio/status/{session.call_id}"

    try:
        handle = await telephony.place_call(
            to=payload.to,
            callback_url=voice_url,
            status_callback_url=status_url,
            machine_detection=settings.answering_machine_detection,
        )
    except Exception as exc:  # noqa: BLE001
        clean = _format_telephony_error(exc)
        session.mark_status(CallStatus.FAILED, reason=f"dispatch_error: {clean}")
        await store.put(session)
        log.warning(
            "call.dispatch_error",
            call_id=session.call_id,
            error=clean,
            twilio_code=getattr(exc, "code", None),
            twilio_status=getattr(exc, "status", None),
        )
        raise HTTPException(
            status_code=502,
            detail=f"Telephony provider failed to place call: {clean}",
        ) from exc

    session.provider_call_id = handle.provider_call_id
    session.mark_status(CallStatus.DIALING)
    await store.put(session)

    return CreateCallResponse(
        call_id=session.call_id,
        provider_call_id=handle.provider_call_id,
        status=session.status.value,
        to=session.to,
        scenario_id=session.scenario.id,
    )


@router.get("/{call_id}", response_model=CallStateResponse)
async def get_call(
    call_id: str,
    store: SessionStore = Depends(get_store),
    outcome_recorder: OutcomeRecorder = Depends(get_outcome_recorder),
) -> CallStateResponse:
    session = await store.get(call_id)
    if not session:
        raise HTTPException(status_code=404, detail="Call not found.")

    # If call is over but we never finalised (e.g. WS bridge crashed),
    # do it lazily so the API always returns the structured outcome.
    if (
        session.status
        in {
            CallStatus.COMPLETED,
            CallStatus.NO_ANSWER,
            CallStatus.VOICEMAIL,
            CallStatus.BUSY,
            CallStatus.FAILED,
            CallStatus.TIMED_OUT,
            CallStatus.ABANDONED,
            CallStatus.HOSTILE_CALLER,
        }
        and session.final_outcome is None
    ):
        try:
            await outcome_recorder.finalize(session)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "call.lazy_finalize_failed",
                call_id=call_id,
                error=str(exc),
            )

    return CallStateResponse(
        call_id=session.call_id,
        provider_call_id=session.provider_call_id,
        to=session.to,
        scenario_id=session.scenario.id,
        status=session.status.value,
        turn_count=session.turn_count,
        started_at=session.started_at,
        ended_at=session.ended_at,
        extracted_data=session.extracted_data,
        transcript=[
            {"role": e.role, "text": e.text, "ts": e.ts}
            for e in session.transcript_events
        ],
        final_outcome=session.final_outcome,
    )


@router.get("/{call_id}/stream")
async def stream_call(
    call_id: str,
    request: Request,
    store: SessionStore = Depends(get_store),
) -> EventSourceResponse:
    """Server-Sent Events feed for live UI updates."""
    session = await store.get(call_id)
    if not session:
        raise HTTPException(status_code=404, detail="Call not found.")

    async def event_gen() -> AsyncIterator[dict]:
        last_n_events = 0
        last_status = ""
        while True:
            if await request.is_disconnected():
                break
            current = await store.get(call_id)
            if not current:
                yield {
                    "event": "error",
                    "data": json.dumps({"error": "session lost"}),
                }
                break

            # Emit status if changed.
            if current.status.value != last_status:
                last_status = current.status.value
                yield {
                    "event": "status",
                    "data": json.dumps({"status": last_status}),
                }

            # Emit new transcript events.
            new_events = current.transcript_events[last_n_events:]
            if new_events:
                last_n_events = len(current.transcript_events)
                yield {
                    "event": "transcript",
                    "data": json.dumps(
                        [{"role": e.role, "text": e.text} for e in new_events]
                    ),
                }

            # Emit outcome when ready.
            if current.final_outcome is not None:
                yield {
                    "event": "outcome",
                    "data": json.dumps(current.final_outcome),
                }
                break

            await asyncio.sleep(0.5)

    return EventSourceResponse(event_gen())


async def _exceeds_hourly_cap(store: SessionStore, cap: int) -> bool:
    cutoff = time.time() - 3600
    count = 0
    for s in await store.list_all():
        if s.started_at >= cutoff:
            count += 1
    return count >= cap
