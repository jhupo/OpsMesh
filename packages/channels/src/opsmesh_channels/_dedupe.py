"""Bounded event-identifier deduplication for channel callbacks."""

from __future__ import annotations

import time
from collections import OrderedDict


class EventDeduplicator:
    """Remember event identifiers for a bounded time window.

    This suppresses repeated vendor callbacks in one process. It is not a
    cross-restart exactly-once guarantee.

    Args:
        ttl: Seconds an identifier remains remembered.
        capacity: Maximum identifiers kept; the oldest are evicted first.
    """

    def __init__(self, *, ttl: float, capacity: int) -> None:
        if ttl <= 0:
            raise ValueError("Deduplication TTL must be positive")
        if capacity <= 0:
            raise ValueError("Deduplication capacity must be positive")
        self._ttl = ttl
        self._capacity = capacity
        self._seen: OrderedDict[str, float] = OrderedDict()

    def seen(self, event_id: str, *, now: float | None = None) -> bool:
        """Record an identifier and report whether it was seen recently."""
        current = time.monotonic() if now is None else now
        self._evict(current)
        if event_id in self._seen:
            return True
        self._seen[event_id] = current
        while len(self._seen) > self._capacity:
            self._seen.popitem(last=False)
        return False

    def forget(self, event_id: str) -> None:
        """Release an identifier whose event could not be accepted."""
        self._seen.pop(event_id, None)

    def _evict(self, now: float) -> None:
        while self._seen:
            event_id, recorded = next(iter(self._seen.items()))
            if now - recorded < self._ttl:
                break
            del self._seen[event_id]
