"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    twilio_account_sid: str = Field(default="")
    twilio_auth_token: str = Field(default="")
    twilio_phone_number: str = Field(default="")
    enforce_twilio_signature: bool = False

    deepgram_api_key: str = Field(default="")
    groq_api_key: str = Field(default="")

    # Plain str (not Literal) so a new provider added via factory.py works
    # immediately without also editing this class. The factory raises a clear
    # error for unsupported values.
    stt_provider: str = "deepgram"
    llm_provider: str = "groq"
    tts_provider: str = "deepgram"
    telephony_provider: str = "twilio"

    groq_model: str = "llama-3.3-70b-versatile"
    deepgram_stt_model: str = "nova-3"
    deepgram_tts_model: str = "aura-2-thalia-en"

    public_base_url: str = Field(default="http://localhost:8000")

    max_calls_per_hour: int = 20
    max_turns_per_call: int = 12
    call_max_duration_seconds: int = 300

    streaming_mode: bool = True
    answering_machine_detection: bool = True
    log_level: str = "INFO"

    def validate_required_keys(self) -> list[str]:
        missing: list[str] = []
        if not self.twilio_account_sid:
            missing.append("TWILIO_ACCOUNT_SID")
        if not self.twilio_auth_token:
            missing.append("TWILIO_AUTH_TOKEN")
        if not self.twilio_phone_number:
            missing.append("TWILIO_PHONE_NUMBER")
        if not self.deepgram_api_key:
            missing.append("DEEPGRAM_API_KEY")
        if not self.groq_api_key:
            missing.append("GROQ_API_KEY")
        return missing


@lru_cache
def get_settings() -> Settings:
    return Settings()
