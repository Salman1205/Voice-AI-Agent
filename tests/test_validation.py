"""Phone validation + prompt sanitization."""

from __future__ import annotations

import pytest

from app.core.validation import normalize_phone, sanitize_prompt


class TestNormalizePhone:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("+14155552671", "+14155552671"),
            (" +14155552671 ", "+14155552671"),
            ("+447911123456", "+447911123456"),
        ],
    )
    def test_valid(self, raw: str, expected: str) -> None:
        assert normalize_phone(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "abcdef", "12345", "+1", "+1234567890123456"])
    def test_invalid(self, raw: str) -> None:
        with pytest.raises(ValueError):
            normalize_phone(raw)

    def test_national_format_rejected(self) -> None:
        with pytest.raises(ValueError):
            normalize_phone("4155552671")  # no + prefix → not parseable region-free


class TestSanitizePrompt:
    def test_strips_control_chars(self) -> None:
        assert sanitize_prompt("hello\x00world\x07") == "helloworld"

    def test_preserves_newlines_and_tabs(self) -> None:
        assert sanitize_prompt("line1\nline2\tend") == "line1\nline2\tend"

    def test_clamps_length(self) -> None:
        big = "a" * 9000
        assert len(sanitize_prompt(big, max_chars=4000)) == 4000

    def test_none_input(self) -> None:
        assert sanitize_prompt(None) == ""  # type: ignore[arg-type]

    def test_empty_string(self) -> None:
        assert sanitize_prompt("") == ""
