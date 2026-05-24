"""FastAPI dependency providers — wire shared singletons into request handlers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import Request

from app.conversation.engine import ConversationEngine
from app.conversation.outcome import OutcomeRecorder
from app.core.config import Settings, get_settings
from app.providers.base import (
    LLMProvider,
    STTProvider,
    TelephonyProvider,
    TTSProvider,
)
from app.providers.factory import (
    build_llm,
    build_stt,
    build_telephony,
    build_tts,
)
from app.scenarios.loader import ScenarioConfig, load_preset
from app.store.sessions import InMemorySessionStore, SessionStore


PRESET_PATH = Path(__file__).resolve().parent.parent / "scenarios" / "appointment_reminder.yaml"


@lru_cache
def get_preset_scenario() -> ScenarioConfig:
    return load_preset(PRESET_PATH)


def get_app_settings() -> Settings:
    return get_settings()


def get_store(request: Request) -> SessionStore:
    return request.app.state.session_store


def get_telephony(request: Request) -> TelephonyProvider:
    return request.app.state.telephony


def get_stt(request: Request) -> STTProvider:
    # Each call gets its own STT instance (stateful WS), so build per-request.
    return build_stt(request.app.state.settings)


def get_tts(request: Request) -> TTSProvider:
    return build_tts(request.app.state.settings)


def get_llm(request: Request) -> LLMProvider:
    return request.app.state.llm


def get_conversation_engine(request: Request) -> ConversationEngine:
    return request.app.state.engine


def get_outcome_recorder(request: Request) -> OutcomeRecorder:
    return request.app.state.outcome_recorder


def install_singletons(app, settings: Settings) -> None:
    """Build long-lived singletons at startup and attach to app.state."""
    app.state.settings = settings
    app.state.session_store = InMemorySessionStore()
    app.state.telephony = build_telephony(settings)
    app.state.llm = build_llm(settings)
    app.state.engine = ConversationEngine(
        llm=app.state.llm,
        settings_max_turns=settings.max_turns_per_call,
    )
    app.state.outcome_recorder = OutcomeRecorder(llm=app.state.llm)
