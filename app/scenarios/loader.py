"""Scenario configuration model and YAML loader.

A scenario fully describes how the agent behaves on one call: persona,
goal, opening line, guardrails, and the schema for the structured
outcome extracted at call end.

The same model is constructed in-memory when the UI submits a custom
prompt — no registry, no persistence (per spec §2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class ScenarioConfig(BaseModel):
    id: str
    name: str
    persona: str = Field(..., min_length=10, max_length=4000)
    goal: str = Field(..., min_length=10, max_length=2000)
    opening_line: str = Field(..., min_length=5, max_length=500)
    context_variables: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    extraction_schema: dict[str, Any] = Field(default_factory=dict)
    max_turns: int = Field(default=12, ge=1, le=40)
    guardrails: list[str] = Field(default_factory=list)
    voicemail_message: str = Field(
        default="Hi, this is calling to follow up. Please give us a call back at your convenience. Thank you.",
        max_length=600,
    )

    @field_validator("id")
    @classmethod
    def _id_is_slug(cls, v: str) -> str:
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("id must be a slug (alphanumeric, _ or -)")
        return v

    def render_system_prompt(self, context: dict[str, str] | None = None) -> str:
        """Build the full system prompt sent to the LLM each turn."""
        context = context or {}
        rendered_persona = _safe_interpolate(self.persona, context)
        rendered_goal = _safe_interpolate(self.goal, context)
        guardrails_block = (
            "\n".join(f"- {g}" for g in self.guardrails)
            if self.guardrails
            else "- Stay on topic. Be polite."
        )
        ctx_block = (
            "\n".join(f"- {k}: {v}" for k, v in context.items())
            if context
            else "(no additional context)"
        )
        return (
            f"# Role\n{rendered_persona}\n\n"
            f"# Objective\n{rendered_goal}\n\n"
            f"# Context\n{ctx_block}\n\n"
            f"# Guardrails\n{guardrails_block}\n\n"
            "# Conversation rules\n"
            "- Speak in short, natural sentences (max ~25 words per turn).\n"
            "- Acknowledge what the caller said (even a brief 'hello' or short reply) "
            "before steering the conversation forward; do not repeat the same question back-to-back.\n"
            "- If the caller's reply is unclear or partial (e.g. 'yes', 'hmm', 'what?'), "
            "interpret it generously in context and either confirm understanding or ask one short clarifier.\n"
            "- Never invent facts not in the context.\n"
            "- If asked whether you are an AI, answer honestly and briefly, then continue.\n"
            "- If the caller asks for something out of scope, offer a human callback and end politely.\n"
            "- Track what the caller has already told you across the full transcript. "
            "Do not ask for a fact (name, time, confirmation, etc.) you have already heard.\n"
            "- When the objective is achieved (or clearly cannot be), call the `end_call` tool.\n"
        )

    def render_opening_line(self, context: dict[str, str] | None = None) -> str:
        return _safe_interpolate(self.opening_line, context or {})


def load_preset(path: str | Path) -> ScenarioConfig:
    """Load a scenario YAML file from disk."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return ScenarioConfig(**data)


def build_custom(
    *,
    custom_prompt: str,
    opening_line: str | None = None,
    name: str = "Custom Scenario",
) -> ScenarioConfig:
    """Build a ScenarioConfig from a free-form prompt supplied via the UI."""
    return ScenarioConfig(
        id="custom",
        name=name,
        persona=custom_prompt,
        goal="Follow the persona's instructions and accomplish the implied objective.",
        opening_line=opening_line or "Hello, do you have a moment to talk?",
        context_variables=[],
        success_criteria=["call ends with a clear outcome"],
        extraction_schema={"status": "string", "summary": "string"},
        max_turns=12,
        guardrails=[
            "Honesty about being an AI assistant.",
            "Politely refuse anything illegal or harmful.",
        ],
    )


def _safe_interpolate(template: str, context: dict[str, str]) -> str:
    """Substitute {variable} placeholders with values; leave unknown vars intact."""
    result = template
    for key, value in context.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result
