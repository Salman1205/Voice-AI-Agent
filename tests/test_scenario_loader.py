"""ScenarioConfig YAML loader + custom builder."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.scenarios.loader import (
    ScenarioConfig,
    build_custom,
    load_preset,
)


PRESET = Path(__file__).resolve().parent.parent / "app" / "scenarios" / "appointment_reminder.yaml"


class TestLoadPreset:
    def test_loads_appointment_reminder(self) -> None:
        scenario = load_preset(PRESET)
        assert isinstance(scenario, ScenarioConfig)
        assert scenario.id == "appointment_reminder"
        # Self-contained scenario — no required context variables.
        assert scenario.context_variables == []
        assert scenario.max_turns >= 1
        assert len(scenario.guardrails) > 0

    def test_renders_system_prompt_with_baked_defaults(self) -> None:
        scenario = load_preset(PRESET)
        prompt = scenario.render_system_prompt()
        # The preset bakes Dr. Khan + tomorrow 3 PM into the goal text
        # rather than requiring runtime context.
        assert "Dr. Khan" in prompt
        assert "3 PM" in prompt or "3PM" in prompt
        assert "Guardrails" in prompt
        assert "Conversation rules" in prompt

    def test_renders_system_prompt_accepts_optional_context(self) -> None:
        # Even though the preset is self-contained, the engine still accepts
        # extra context variables for CRM-style integrations.
        scenario = load_preset(PRESET)
        prompt = scenario.render_system_prompt({"customer_id": "ABC-123"})
        assert "ABC-123" in prompt

    def test_renders_opening_line(self) -> None:
        scenario = load_preset(PRESET)
        opening = scenario.render_opening_line()
        assert "Sara" in opening
        assert "MediCare" in opening


class TestBuildCustom:
    def test_builds_with_prompt_only(self) -> None:
        scenario = build_custom(
            custom_prompt="You are a helpful assistant calling to test things."
        )
        assert scenario.id == "custom"
        assert scenario.persona.startswith("You are a helpful assistant")
        assert scenario.opening_line  # has default

    def test_custom_opening_line(self) -> None:
        scenario = build_custom(
            custom_prompt="A polite agent.",
            opening_line="Quick question for you.",
        )
        assert scenario.opening_line == "Quick question for you."


class TestValidation:
    def test_id_must_be_slug(self) -> None:
        with pytest.raises(ValidationError):
            ScenarioConfig(
                id="bad id with spaces",
                name="x",
                persona="a" * 20,
                goal="a" * 20,
                opening_line="hello",
            )

    def test_persona_too_short(self) -> None:
        with pytest.raises(ValidationError):
            ScenarioConfig(
                id="ok",
                name="x",
                persona="x",
                goal="a" * 20,
                opening_line="hello",
            )

    def test_max_turns_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ScenarioConfig(
                id="ok",
                name="x",
                persona="a" * 20,
                goal="a" * 20,
                opening_line="hello",
                max_turns=999,
            )
