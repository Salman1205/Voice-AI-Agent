"""Outbound call dispatch + status endpoints."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sse_starlette.sse import EventSourceResponse

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
        session.mark_status(CallStatus.FAILED, reason=f"dispatch_error: {exc}")
        await store.put(session)
        log.warning("call.dispatch_error", call_id=session.call_id, error=str(exc))
        raise HTTPException(
            status_code=502,
            detail=(
                f"Telephony provider failed to place call: {exc}. "
                "If on Twilio trial, ensure the number is verified."
            ),
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
    import time

    cutoff = time.time() - 3600
    count = 0
    for s in await store.list_all():
        if s.started_at >= cutoff:
            count += 1
    return count >= cap
