"""Groq LLM provider with streaming + tool calling.

Uses the official `groq` SDK in async mode. Returns LLMChunks so the
conversation engine can stream partial text to TTS while still
recognising tool calls when they arrive at the end of a turn.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from groq import AsyncGroq

from app.core.logging import get_logger
from app.providers.base import LLMChunk, LLMProvider, Message


log = get_logger(__name__)

# Transient errors (429 rate limit, 5xx) get one retry with backoff before
# the conversation engine sees the failure. Groq free tier is 30 req/min so
# this happens during multi-call testing sessions.
_RETRY_BACKOFF_SECONDS = 0.8


class GroqLLM(LLMProvider):
    def __init__(self, *, api_key: str, model: str = "llama-3.3-70b-versatile") -> None:
        self._client = AsyncGroq(api_key=api_key)
        self._model = model

    async def generate(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict] | None = None,
        max_tokens: int = 512,
        temperature: float = 0.4,
    ) -> AsyncIterator[LLMChunk]:
        payload_messages: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            entry: dict = {"role": m.role, "content": m.content}
            if m.name:
                entry["name"] = m.name
            if m.tool_call_id:
                entry["tool_call_id"] = m.tool_call_id
            if m.tool_calls:
                entry["tool_calls"] = m.tool_calls
            payload_messages.append(entry)

        kwargs: dict = {
            "model": self._model,
            "messages": payload_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        tool_accumulator: dict[int, dict] = {}
        finish_reason: str | None = None

        stream = None
        last_error: Exception | None = None
        for attempt in range(2):  # initial attempt + 1 retry
            try:
                stream = await self._client.chat.completions.create(**kwargs)
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt == 0:
                    log.info(
                        "groq.transient_error.retrying",
                        error=str(exc)[:200],
                        backoff_s=_RETRY_BACKOFF_SECONDS,
                    )
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                    continue

        if stream is None:
            log.warning("groq.create_failed", error=str(last_error))
            yield LLMChunk(
                text="Sorry, could you repeat that?",
                finish_reason="error",
            )
            return

        try:
            async for chunk in stream:
                choice = chunk.choices[0] if chunk.choices else None
                if choice is None:
                    continue
                delta = choice.delta
                text = getattr(delta, "content", None) or ""
                tool_deltas = getattr(delta, "tool_calls", None) or []

                for td in tool_deltas:
                    idx = td.index
                    bucket = tool_accumulator.setdefault(
                        idx,
                        {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    if td.id:
                        bucket["id"] = td.id
                    if td.function and td.function.name:
                        bucket["function"]["name"] = td.function.name
                    if td.function and td.function.arguments:
                        bucket["function"]["arguments"] += td.function.arguments

                if text:
                    yield LLMChunk(text=text)

                if choice.finish_reason:
                    finish_reason = choice.finish_reason

        except Exception as exc:  # noqa: BLE001
            log.warning("groq.stream_error", error=str(exc))
            # Soft fallback: don't end the call — ask the caller to repeat.
            # The conversation engine treats this as a normal turn and keeps
            # going.
            yield LLMChunk(
                text="Sorry, could you say that again?",
                finish_reason="error",
            )
            return

        completed_tools: list[dict] = []
        for bucket in tool_accumulator.values():
            args = bucket["function"]["arguments"] or "{}"
            try:
                json.loads(args)  # validate
            except json.JSONDecodeError:
                args = "{}"
            bucket["function"]["arguments"] = args
            completed_tools.append(bucket)

        yield LLMChunk(tool_calls=completed_tools, finish_reason=finish_reason)
