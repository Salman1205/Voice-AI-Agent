"""Scenario read-only API.

Exposes the single bundled preset so the UI can render the context-
variable form. No registry / no POST: custom scenarios come via the
call payload itself.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.scenarios.loader import ScenarioConfig
from app.api.deps import get_preset_scenario


router = APIRouter(prefix="/scenarios", tags=["scenarios"])


@router.get("/preset", response_model=ScenarioConfig)
async def get_preset(scenario: ScenarioConfig = Depends(get_preset_scenario)) -> ScenarioConfig:
    return scenario
