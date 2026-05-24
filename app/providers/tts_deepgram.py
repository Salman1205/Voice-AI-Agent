"""Deepgram Aura-2 streaming TTS via HTTP.

We use the synchronous HTTP streaming endpoint (not the WS speak interface
which is in beta). The audio is returned as raw mulaw at 8 kHz so it can
be sent straight into Twilio Media Streams without resampling.
"""

from __future__ import annotations

from typing import AsyncIterator

import httpx

from app.core.logging import get_logger
from app.providers.base import TTSProvider


log = get_logger(__name__)

DEEPGRAM_TTS_URL = (
    "https://api.deepgram.com/v1/speak"
    "?model={model}"
    "&encoding=mulaw"
    "&sample_rate=8000"
    "&container=none"
)


class DeepgramTTS(TTSProvider):
    def __init__(self, *, api_key: str, model: str = "aura-2-thalia-en") -> None:
        self._api_key = api_key
        self._model = model
        self._client = httpx.AsyncClient(timeout=30.0)

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        text = (text or "").strip()
        if not text:
            return
        url = DEEPGRAM_TTS_URL.format(model=self._model)
        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with self._client.stream(
                "POST",
                url,
                headers=headers,
                json={"text": text},
            ) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    log.warning(
                        "deepgram.tts.error",
                        status=resp.status_code,
                        body=body[:500].decode("utf-8", "replace"),
                    )
                    return
                async for chunk in resp.aiter_bytes(chunk_size=320):
                    if chunk:
                        yield chunk
        except httpx.HTTPError as exc:
            log.warning("deepgram.tts.http_error", error=str(exc))

    async def close(self) -> None:
        await self._client.aclose()
