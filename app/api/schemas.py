"""Pydantic request/response shapes for the REST API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.validation import normalize_phone, sanitize_prompt


class CustomScenarioPayload(BaseModel):
    name: str = Field(default="Custom Scenario", max_length=120)
    custom_prompt: str = Field(..., min_length=10, max_length=4000)
    opening_line: str | None = Field(default=None, max_length=500)

    @field_validator("custom_prompt")
    @classmethod
    def _clean_prompt(cls, v: str) -> str:
        return sanitize_prompt(v)

    @field_validator("opening_line")
    @classmethod
    def _clean_opening(cls, v: str | None) -> str | None:
        return sanitize_prompt(v, max_chars=500) if v else None


class CreateCallRequest(BaseModel):
    to: str = Field(..., description="Destination phone number in E.164 format.")
    mode: Literal["preset", "custom"] = "preset"
    context_variables: dict[str, str] = Field(default_factory=dict)
    custom: CustomScenarioPayload | None = None

    @field_validator("to")
    @classmethod
    def _validate_phone(cls, v: str) -> str:
        return normalize_phone(v)


class CreateCallResponse(BaseModel):
    call_id: str
    provider_call_id: str
    status: str
    to: str
    scenario_id: str


class CallStateResponse(BaseModel):
    call_id: str
    provider_call_id: str | None
    to: str
    scenario_id: str
    status: str
    turn_count: int
    started_at: float
    ended_at: float | None
    extracted_data: dict[str, Any]
    transcript: list[dict[str, Any]]
    final_outcome: dict[str, Any] | None
