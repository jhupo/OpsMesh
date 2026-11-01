"""Explicit local or Redis coordination; never the sole source of business facts."""

import asyncio
import time
from uuid import uuid4

from redis.asyncio import Redis

from opsmesh.framework.errors import OpsMeshError


class RequestRateLimiter:
    def __init__(self, redis_url: str | None = None) -> None:
        self._redis = (
            Redis.from_url(redis_url, decode_responses=True, protocol=2) if redis_url else None
        )
        self._counts: dict[str, tuple[int, float]] = {}
        self._lock = asyncio.Lock()

    async def check(self, key: str, *, limit: int, window_seconds: int = 60) -> None:
        if self._redis is not None:
            script = (
                "local n=redis.call('INCR',KEYS[1]); "
                "if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n"
            )
            count = int(await self._redis.eval(script, 1, f"opsmesh:rate:{key}", window_seconds))
        else:
            async with self._lock:
                now = time.monotonic()
                count, expires = self._counts.get(key, (0, now + window_seconds))
                if expires <= now:
                    count, expires = 0, now + window_seconds
                count += 1
                self._counts[key] = (count, expires)
                if len(self._counts) > 10000:
                    self._counts = {k: v for k, v in self._counts.items() if v[1] > now}
        if count > limit:
            raise OpsMeshError("RATE_LIMITED", "Request rate limit exceeded", retryable=True)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()


class RedisLeaseCoordinator:
    def __init__(self, redis_url: str) -> None:
        self._redis = Redis.from_url(redis_url, decode_responses=True, protocol=2)

    async def acquire(self, name: str, ttl_seconds: int) -> str | None:
        token = uuid4().hex
        accepted = await self._redis.set(f"opsmesh:lease:{name}", token, ex=ttl_seconds, nx=True)
        return token if accepted else None

    async def renew(self, name: str, token: str, ttl_seconds: int) -> bool:
        script = (
            "if redis.call('GET',KEYS[1])==ARGV[1] then "
            "return redis.call('EXPIRE',KEYS[1],ARGV[2]) else return 0 end"
        )
        return bool(await self._redis.eval(script, 1, f"opsmesh:lease:{name}", token, ttl_seconds))

    async def release(self, name: str, token: str) -> bool:
        script = (
            "if redis.call('GET',KEYS[1])==ARGV[1] then "
            "return redis.call('DEL',KEYS[1]) else return 0 end"
        )
        return bool(await self._redis.eval(script, 1, f"opsmesh:lease:{name}", token))

    async def close(self) -> None:
        await self._redis.aclose()
