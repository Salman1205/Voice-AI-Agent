"""Input validation utilities — phone numbers, scenario payloads, etc."""

from __future__ import annotations

import re

import phonenumbers
from phonenumbers import NumberParseException


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def normalize_phone(raw: str) -> str:
    """Return E.164 phone string. Raises ValueError with a user-friendly message."""
    if not raw or not raw.strip():
        raise ValueError("Phone number is required.")
    cleaned = raw.strip()
    try:
        parsed = phonenumbers.parse(cleaned, None)
    except NumberParseException as exc:
        raise ValueError(
            "Phone number must be in international E.164 format (e.g. +14155552671)."
        ) from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError(
            "That phone number doesn't look valid. Use E.164 format like +14155552671."
        )
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def sanitize_prompt(text: str | None, max_chars: int = 4000) -> str:
    """Strip control characters and clamp length on user-supplied prompt text."""
    if text is None:
        return ""
    cleaned = _CONTROL_CHARS.sub("", text).strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars]
    return cleaned
