from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from time import time

from redis import Redis

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    backend_available: bool
    limit: int
    remaining: int
    reset_epoch_seconds: int


class RedisFixedWindowRateLimiter:
    def __init__(
        self,
        redis: Redis[str],
        *,
        key_prefix: str,
        clock: Callable[[], float] = time,
    ) -> None:
        self._redis = redis
        self._key_prefix = key_prefix
        self._clock = clock

    def check(
        self,
        *,
        identifier: str,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        now = int(self._clock())
        window_id = now // window_seconds
        reset = (window_id + 1) * window_seconds
        key = f"{self._key_prefix}:rate-limit:{identifier}:{window_id}"

        try:
            count = int(self._redis.incr(key))
            if count == 1:
                self._redis.expire(key, window_seconds + 1)
        except Exception:
            logger.warning("Rate limiter failed open", exc_info=True)
            return RateLimitDecision(
                allowed=True,
                backend_available=False,
                limit=limit,
                remaining=limit,
                reset_epoch_seconds=reset,
            )

        remaining = max(limit - count, 0)
        return RateLimitDecision(
            allowed=count <= limit,
            backend_available=True,
            limit=limit,
            remaining=remaining,
            reset_epoch_seconds=reset,
        )
