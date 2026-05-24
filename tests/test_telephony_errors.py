"""Tests for the telephony error formatter in app.api.calls.

These cover the regression where TwilioRestException.__str__ embedded ANSI
color escapes when stderr was a TTY, leaking raw escape sequences into the
HTTP response and rendering as garbage in the browser.
"""

from __future__ import annotations

import pytest
from twilio.base.exceptions import TwilioRestException

from app.api.calls import _format_telephony_error


class TestFormatTelephonyError:
    def test_known_code_promotes_to_hint(self) -> None:
        exc = TwilioRestException(
            status=400,
            uri="/2010-04-01/Accounts/AC.../Calls.json",
            msg="The number +923250427314 is unverified. Trial accounts may only make calls to verified numbers.",
            code=21219,
            method="POST",
        )
        out = _format_telephony_error(exc)
        assert "Twilio error 21219" in out
        assert "isn't verified" in out
        assert "console.twilio.com" in out
        # No ANSI escapes leak through.
        assert "\x1b[" not in out
        assert "[31m" not in out

    def test_unknown_code_falls_back_to_docs_url(self) -> None:
        exc = TwilioRestException(
            status=400,
            uri="/some/uri",
            msg="Some unusual error",
            code=99999,
            method="POST",
        )
        out = _format_telephony_error(exc)
        assert "Twilio error 99999" in out
        assert "Some unusual error" in out
        assert "https://www.twilio.com/docs/errors/99999" in out
        assert "\x1b[" not in out

    def test_missing_code_uses_http_status(self) -> None:
        exc = TwilioRestException(
            status=500,
            uri="/some/uri",
            msg="Server error",
            code=None,
            method="POST",
        )
        out = _format_telephony_error(exc)
        assert "Twilio HTTP 500" in out
        assert "Server error" in out
        assert "\x1b[" not in out

    def test_non_twilio_exception_strips_ansi(self) -> None:
        # Simulate an exception whose str() contains ANSI escapes.
        class Colored(Exception):
            def __str__(self) -> str:
                return "\x1b[31m\x1b[49mboom\x1b[0m \x1b[34mblue\x1b[0m"

        out = _format_telephony_error(Colored())
        assert out == "boom blue"
        assert "\x1b[" not in out

    @pytest.mark.parametrize("code", [21219, 13227, 21211, 21210, 21214, 20003, 20404])
    def test_known_codes_have_hints(self, code: int) -> None:
        exc = TwilioRestException(
            status=400, uri="/u", msg="x", code=code, method="POST"
        )
        out = _format_telephony_error(exc)
        assert f"Twilio error {code}" in out
        # Hint branch always contains an em-dash separator.
        assert " — " in out
