"""Build concrete provider instances from current settings."""

from __future__ import annotations

from app.core.config import Settings
from app.providers.base import (
    LLMProvider,
    STTProvider,
    TelephonyProvider,
    TTSProvider,
)
from app.providers.llm_groq import GroqLLM
from app.providers.stt_deepgram import DeepgramSTT
from app.providers.telephony_twilio import TwilioTelephony
from app.providers.tts_deepgram import DeepgramTTS


def build_stt(settings: Settings) -> STTProvider:
    if settings.stt_provider == "deepgram":
        return DeepgramSTT(
            api_key=settings.deepgram_api_key, model=settings.deepgram_stt_model
        )
    raise ValueError(f"Unsupported STT_PROVIDER: {settings.stt_provider!r}")


def build_llm(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "groq":
        return GroqLLM(api_key=settings.groq_api_key, model=settings.groq_model)
    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider!r}")


def build_tts(settings: Settings) -> TTSProvider:
    if settings.tts_provider == "deepgram":
        return DeepgramTTS(
            api_key=settings.deepgram_api_key, model=settings.deepgram_tts_model
        )
    raise ValueError(f"Unsupported TTS_PROVIDER: {settings.tts_provider!r}")


def build_telephony(settings: Settings) -> TelephonyProvider:
    if settings.telephony_provider == "twilio":
        return TwilioTelephony(
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
            from_number=settings.twilio_phone_number,
        )
    raise ValueError(f"Unsupported TELEPHONY_PROVIDER: {settings.telephony_provider!r}")
