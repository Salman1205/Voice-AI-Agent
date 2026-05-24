"""In-memory session store contract."""

from __future__ import annotations

import pytest

from app.conversation.state import CallSession
from app.scenarios.loader import build_custom
from app.store.sessions import InMemorySessionStore


def _session(provider_id: str | None = None) -> CallSession:
    s = CallSession.new(
        to="+14155552671",
        scenario=build_custom(custom_prompt="testing the session store contract"),
    )
    s.provider_call_id = provider_id
    return s


@pytest.mark.asyncio
async def test_put_and_get() -> None:
    store = InMemorySessionStore()
    s = _session()
    await store.put(s)
    assert (await store.get(s.call_id)) is s


@pytest.mark.asyncio
async def test_find_by_provider_id() -> None:
    store = InMemorySessionStore()
    s = _session(provider_id="CA123")
    await store.put(s)
    found = await store.find_by_provider_id("CA123")
    assert found is s


@pytest.mark.asyncio
async def test_missing_returns_none() -> None:
    store = InMemorySessionStore()
    assert await store.get("nope") is None
    assert await store.find_by_provider_id("nope") is None


@pytest.mark.asyncio
async def test_evict_expired() -> None:
    store = InMemorySessionStore()
    s = _session()
    s.started_at -= 7200  # 2 hours ago
    await store.put(s)
    evicted = await store.evict_expired(max_age_seconds=3600)
    assert evicted == 1
    assert await store.get(s.call_id) is None
