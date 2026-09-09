from __future__ import annotations

import logging
from dataclasses import dataclass
from math import ceil
from time import time

from limits import RateLimitItemPerSecond
from limits.storage import RedisStorage, Storage
from limits.strategies import FixedWindowRateLimiter as LimitsFixedWindowRateLimiter
from redis import Redis

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    backend_available: bool
    limit: int
    remaining: int
    reset_epoch_seconds: int


class FixedWindowRateLimiter:
    def __init__(self, storage: Storage, *, namespace: str = "opsmesh:rate-limit") -> None:
        self._strategy = LimitsFixedWindowRateLimiter(storage)
        self._namespace = namespace

    @classmethod
    def from_redis(
        cls,
        redis: Redis[str],
        *,
        key_prefix: str,
    ) -> FixedWindowRateLimiter:
        storage = RedisStorage(
            "redis://",
            connection_pool=redis.connection_pool,
            key_prefix=f"{key_prefix}:rate-limit",
            wrap_exceptions=True,
        )
        return cls(storage, namespace="opsmesh")

    def check(
        self,
        *,
        identifier: str,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        item = RateLimitItemPerSecond(
            limit,
            multiples=window_seconds,
            namespace=self._namespace,
        )
        try:
            allowed = self._strategy.hit(item, identifier)
            window = self._strategy.get_window_stats(item, identifier)
        except Exception:
            logger.warning("Rate limiter failed open", exc_info=True)
            return RateLimitDecision(
                allowed=True,
                backend_available=False,
                limit=limit,
                remaining=limit,
                reset_epoch_seconds=int(time()) + window_seconds,
            )
        return RateLimitDecision(
            allowed=allowed,
            backend_available=True,
            limit=limit,
            remaining=max(window.remaining, 0),
            reset_epoch_seconds=ceil(window.reset_time),
        )


__all__ = ["FixedWindowRateLimiter", "RateLimitDecision"]
