from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal, overload
from uuid import uuid4

from redis import Redis
from redis.exceptions import ResponseError

_ACQUIRE_LOCK_SCRIPT = """
if redis.call("SET", KEYS[1], ARGV[1], "NX", "EX", ARGV[2]) then
    return redis.call("INCR", KEYS[2])
end
return nil
"""

_RELEASE_LOCK_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
end
return 0
"""

_EXTEND_LOCK_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("EXPIRE", KEYS[1], ARGV[2])
end
return 0
"""


@dataclass
class RedisLock:
    redis: Redis[str]
    key: str
    ttl_seconds: int
    token: str = field(default_factory=lambda: uuid4().hex)
    fencing_key: str | None = None
    fencing_token: int | None = field(default=None, init=False)
    acquired: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.fencing_key is None:
            self.fencing_key = f"{self.key}:fencing"

    def acquire(self) -> bool:
        fencing_key = self._fencing_key()
        try:
            fencing_token = self.redis.eval(
                _ACQUIRE_LOCK_SCRIPT,
                2,
                self.key,
                fencing_key,
                self.token,
                self.ttl_seconds,
            )
        except ResponseError as exc:
            if not _eval_unsupported(exc):
                raise
            fencing_token = self._acquire_without_eval()

        self.acquired = fencing_token is not None
        self.fencing_token = int(fencing_token) if fencing_token is not None else None
        return self.acquired

    def release(self) -> bool:
        try:
            released = bool(self.redis.eval(_RELEASE_LOCK_SCRIPT, 1, self.key, self.token))
        except ResponseError as exc:
            if not _eval_unsupported(exc):
                raise
            if self.redis.get(self.key) != self.token:
                self.acquired = False
                return False
            released = bool(self.redis.delete(self.key))

        self.acquired = False
        return released

    def extend(self, ttl_seconds: int | None = None) -> bool:
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl_seconds
        try:
            extended = bool(self.redis.eval(_EXTEND_LOCK_SCRIPT, 1, self.key, self.token, ttl))
        except ResponseError as exc:
            if not _eval_unsupported(exc):
                raise
            if self.redis.get(self.key) != self.token:
                self.acquired = False
                return False
            extended = bool(self.redis.expire(self.key, ttl))

        if not extended:
            self.acquired = False
        return extended

    def refresh(self, ttl_seconds: int | None = None) -> bool:
        return self.extend(ttl_seconds)

    def _acquire_without_eval(self) -> int | None:
        if not self.redis.set(self.key, self.token, nx=True, ex=self.ttl_seconds):
            return None
        return int(self.redis.incr(self._fencing_key()))

    def _fencing_key(self) -> str:
        if self.fencing_key is None:
            self.fencing_key = f"{self.key}:fencing"
        return self.fencing_key


@overload
def redis_lock(
    redis: Redis[str],
    key: str,
    ttl_seconds: int,
    *,
    yield_lock: Literal[False] = False,
) -> Iterator[bool]: ...


@overload
def redis_lock(
    redis: Redis[str],
    key: str,
    ttl_seconds: int,
    *,
    yield_lock: Literal[True],
) -> Iterator[RedisLock]: ...


@contextmanager
def redis_lock(
    redis: Redis[str],
    key: str,
    ttl_seconds: int,
    *,
    yield_lock: bool = False,
) -> Iterator[bool | RedisLock]:
    lock = RedisLock(redis=redis, key=key, ttl_seconds=ttl_seconds)
    acquired = lock.acquire()
    try:
        yield lock if yield_lock else acquired
    finally:
        if acquired:
            lock.release()


def _eval_unsupported(exc: ResponseError) -> bool:
    message = str(exc).lower()
    return "unknown command" in message and "eval" in message
