from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from uuid import uuid4

from redis import Redis


@dataclass(frozen=True)
class RedisLock:
    redis: Redis[str]
    key: str
    ttl_seconds: int
    token: str = field(default_factory=lambda: uuid4().hex)

    def acquire(self) -> bool:
        return bool(self.redis.set(self.key, self.token, nx=True, ex=self.ttl_seconds))

    def release(self) -> bool:
        current_value = self.redis.get(self.key)
        if current_value != self.token:
            return False
        return bool(self.redis.delete(self.key))


@contextmanager
def redis_lock(redis: Redis[str], key: str, ttl_seconds: int) -> Iterator[bool]:
    lock = RedisLock(redis=redis, key=key, ttl_seconds=ttl_seconds)
    acquired = lock.acquire()
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()
