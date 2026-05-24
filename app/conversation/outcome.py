"""Post-call structured outcome extraction."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from app.conversation.state import CallSession, CallStatus
from app.core.logging import get_logger
from app.providers.base import LLMProvider, Message


log = get_logger(__name__)

OUTCOMES_DIR = Path("outcomes")


class OutcomeRecorder:
    def __init__(self, *, llm: LLMProvider) -> None:
        self._llm = llm

    async def finalize(self, session: CallSession) -> dict[str, Any]:
        """Build a structured outcome dict, attach it to the session, and persist."""
        if session.final_outcome is not None:
            return session.final_outcome

        extracted = dict(session.extracted_data)
        # If the LLM already produced enough structure during the call, trust it
        # and only top up missing fields.
        try:
            llm_extracted = await self._llm_extract(session)
            for k, v in llm_extracted.items():
                extracted.setdefault(k, v)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "outcome.llm_extract_failed",
                call_id=session.call_id,
                error=str(exc),
            )

        outcome: dict[str, Any] = {
            "call_id": session.call_id,
            "to": session.to,
            "scenario": session.scenario.id,
            "scenario_name": session.scenario.name,
            "status": session.status.value,
            "end_reason": session.end_reason,
            "started_at": session.started_at,
            "ended_at": session.ended_at or time.time(),
            "duration_seconds": (
                (session.ended_at or time.time()) - session.started_at
            ),
            "turn_count": session.turn_count,
            "interrupt_count": session.interrupt_count,
            "context_variables": session.context_variables,
            "extracted_data": extracted,
            "transcript": [
                {"role": e.role, "text": e.text, "ts": e.ts}
                for e in session.transcript_events
            ],
        }

        session.final_outcome = outcome
        self._persist(outcome)
        return outcome

    async def _llm_extract(self, session: CallSession) -> dict[str, Any]:
        transcript = session.render_history_for_summary() or "(no speech)"
        schema_hint = json.dumps(
            session.scenario.extraction_schema, ensure_ascii=False, indent=2
        )
        system = (
            "You extract structured outcome data from a phone call transcript. "
            "Return ONLY a single JSON object that conforms to the schema. "
            "Do not include code fences, prose, or commentary.\n\n"
            f"# Target schema\n```json\n{schema_hint}\n```"
        )
        user = (
            f"# Transcript\n{transcript}\n\n"
            "Output the JSON object now."
        )
        text_parts: list[str] = []
        async for chunk in self._llm.generate(
            system=system,
            messages=[Message(role="user", content=user)],
            max_tokens=400,
            temperature=0.0,
        ):
            if chunk.text:
                text_parts.append(chunk.text)
        raw = "".join(text_parts).strip()
        return _parse_json_lenient(raw)

    def _persist(self, outcome: dict[str, Any]) -> None:
        OUTCOMES_DIR.mkdir(exist_ok=True)
        path = OUTCOMES_DIR / f"{outcome['call_id']}.json"
        try:
            path.write_text(json.dumps(outcome, indent=2), encoding="utf-8")
            log.info(
                "outcome.persisted",
                call_id=outcome["call_id"],
                path=str(path),
                status=outcome["status"],
            )
        except OSError as exc:
            log.warning(
                "outcome.persist_failed",
                call_id=outcome["call_id"],
                error=str(exc),
            )


def _parse_json_lenient(raw: str) -> dict[str, Any]:
    """Try to extract a JSON object even if the model wrapped it in prose."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return {}


def status_from_extracted(data: dict[str, Any]) -> CallStatus | None:
    """Map an LLM-reported status string back to our internal enum."""
    status = (data.get("status") or "").lower().strip()
    mapping = {
        "confirmed": CallStatus.COMPLETED,
        "rescheduled": CallStatus.COMPLETED,
        "cancelled": CallStatus.COMPLETED,
        "voicemail": CallStatus.VOICEMAIL,
        "no_response": CallStatus.NO_ANSWER,
        "hostile_caller": CallStatus.HOSTILE_CALLER,
        "wrong_number": CallStatus.COMPLETED,
    }
    return mapping.get(status)
