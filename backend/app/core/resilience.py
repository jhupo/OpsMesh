from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from threading import Lock
from typing import TypeVar

T = TypeVar("T")


class CircuitOpenError(RuntimeError):
    def __init__(self, key: str) -> None:
        super().__init__(f"Circuit breaker is open for {key}")
        self.key = key


@dataclass(frozen=True)
class CircuitBreakerConfig:
    failure_threshold: int = 5
    reset_after_seconds: float = 60.0


class CircuitBreaker:
    def __init__(
        self,
        key: str,
        config: CircuitBreakerConfig,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._key = key
        self._config = config
        self._monotonic = monotonic
        self._lock = Lock()
        self._state = "closed"
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        with self._lock:
            self._maybe_half_open()
            return self._state

    @property
    def failures(self) -> int:
        with self._lock:
            return self._failures

    def before_call(self) -> None:
        with self._lock:
            self._maybe_half_open()
            if self._state == "open":
                raise CircuitOpenError(self._key)

    def record_success(self) -> None:
        with self._lock:
            self._state = "closed"
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._config.failure_threshold:
                self._state = "open"
                self._opened_at = self._monotonic()

    def _maybe_half_open(self) -> None:
        if self._state != "open" or self._opened_at is None:
            return
        if self._monotonic() - self._opened_at >= self._config.reset_after_seconds:
            self._state = "half_open"


class CircuitBreakerRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(
        self,
        key: str,
        config: CircuitBreakerConfig,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> CircuitBreaker:
        with self._lock:
            breaker = self._breakers.get(key)
            if breaker is None:
                breaker = CircuitBreaker(key, config, monotonic=monotonic)
                self._breakers[key] = breaker
            return breaker


_GLOBAL_REGISTRY = CircuitBreakerRegistry()


def retry_with_circuit(
    *,
    key: str,
    func: Callable[[], T],
    max_attempts: int,
    circuit_config: CircuitBreakerConfig,
    should_retry: Callable[[Exception], bool] | None = None,
    registry: CircuitBreakerRegistry = _GLOBAL_REGISTRY,
) -> T:
    breaker = registry.get(key, circuit_config)
    attempts = max(1, max_attempts)
    for attempt in range(attempts):
        breaker.before_call()
        try:
            result = func()
        except Exception as exc:
            retryable = _should_retry(exc, should_retry)
            if retryable:
                breaker.record_failure()
            if attempt + 1 >= attempts or not retryable:
                raise
            continue
        breaker.record_success()
        return result
    raise RuntimeError("retry loop exhausted")


async def async_retry_with_circuit(
    *,
    key: str,
    func: Callable[[], Awaitable[T]],
    max_attempts: int,
    circuit_config: CircuitBreakerConfig,
    should_retry: Callable[[Exception], bool] | None = None,
    registry: CircuitBreakerRegistry = _GLOBAL_REGISTRY,
) -> T:
    breaker = registry.get(key, circuit_config)
    attempts = max(1, max_attempts)
    for attempt in range(attempts):
        breaker.before_call()
        try:
            result = await func()
        except Exception as exc:
            retryable = _should_retry(exc, should_retry)
            if retryable:
                breaker.record_failure()
            if attempt + 1 >= attempts or not retryable:
                raise
            await asyncio.sleep(0)
            continue
        breaker.record_success()
        return result
    raise RuntimeError("retry loop exhausted")


def _should_retry(
    exc: Exception,
    should_retry: Callable[[Exception], bool] | None,
) -> bool:
    if isinstance(exc, CircuitOpenError):
        return False
    if should_retry is None:
        return True
    return should_retry(exc)
