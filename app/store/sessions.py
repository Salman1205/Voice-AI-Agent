"""In-memory session store with TTL eviction.

Behind a small interface so a Redis/DB-backed store can replace it in
the next round with no caller changes.
"""

from __future__ import annotations

import asyncio
import time
from typing import Iterable, Protocol

from app.conversation.state import CallSession


class SessionStore(Protocol):
    async def put(self, session: CallSession) -> None: ...
    async def get(self, call_id: str) -> CallSession | None: ...
    async def find_by_provider_id(self, provider_id: str) -> CallSession | None: ...
    async def list_all(self) -> Iterable[CallSession]: ...
    async def evict_expired(self, max_age_seconds: int = 3600) -> int: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._by_id: dict[str, CallSession] = {}
        self._lock = asyncio.Lock()

    async def put(self, session: CallSession) -> None:
        async with self._lock:
            self._by_id[session.call_id] = session

    async def get(self, call_id: str) -> CallSession | None:
        return self._by_id.get(call_id)

    async def find_by_provider_id(self, provider_id: str) -> CallSession | None:
        for s in self._by_id.values():
            if s.provider_call_id == provider_id:
                return s
        return None

    async def list_all(self) -> Iterable[CallSession]:
        return list(self._by_id.values())

    async def evict_expired(self, max_age_seconds: int = 3600) -> int:
        cutoff = time.time() - max_age_seconds
        async with self._lock:
            stale = [
                cid for cid, s in self._by_id.items() if s.started_at < cutoff
            ]
            for cid in stale:
                del self._by_id[cid]
            return len(stale)
