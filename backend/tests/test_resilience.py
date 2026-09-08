from __future__ import annotations

import pytest

from backend.app.core.resilience import (
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitOpenError,
    retry_with_circuit,
)


def test_retry_with_circuit_retries_within_budget_and_records_success() -> None:
    registry = CircuitBreakerRegistry()
    calls = 0

    def flaky_call() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary")
        return "ok"

    result = retry_with_circuit(
        key="provider:test",
        func=flaky_call,
        max_attempts=2,
        circuit_config=CircuitBreakerConfig(failure_threshold=3, reset_after_seconds=60),
        registry=registry,
    )

    assert result == "ok"
    assert calls == 2
    assert registry.get(
        "provider:test",
        CircuitBreakerConfig(failure_threshold=3, reset_after_seconds=60),
    ).state == "closed"


def test_retry_with_circuit_opens_and_recovers_after_reset_window() -> None:
    now = 0.0
    registry = CircuitBreakerRegistry()
    config = CircuitBreakerConfig(failure_threshold=2, reset_after_seconds=30)

    def monotonic() -> float:
        return now

    breaker = registry.get("provider:down", config, monotonic=monotonic)

    for _ in range(2):
        with pytest.raises(RuntimeError):
            retry_with_circuit(
                key="provider:down",
                func=lambda: (_ for _ in ()).throw(RuntimeError("down")),
                max_attempts=1,
                circuit_config=config,
                registry=registry,
            )

    assert breaker.state == "open"
    with pytest.raises(CircuitOpenError):
        retry_with_circuit(
            key="provider:down",
            func=lambda: "should-not-run",
            max_attempts=1,
            circuit_config=config,
            registry=registry,
        )

    now = 31.0
    assert breaker.state == "half_open"
    assert retry_with_circuit(
        key="provider:down",
        func=lambda: "recovered",
        max_attempts=1,
        circuit_config=config,
        registry=registry,
    ) == "recovered"
    assert breaker.state == "closed"


def test_non_retryable_failure_does_not_penalize_circuit() -> None:
    registry = CircuitBreakerRegistry()
    config = CircuitBreakerConfig(failure_threshold=1, reset_after_seconds=60)

    with pytest.raises(ValueError, match="policy rejected"):
        retry_with_circuit(
            key="provider:policy",
            func=lambda: (_ for _ in ()).throw(ValueError("policy rejected")),
            max_attempts=3,
            circuit_config=config,
            should_retry=lambda exc: False,
            registry=registry,
        )

    breaker = registry.get("provider:policy", config)
    assert breaker.failures == 0
    assert breaker.state == "closed"
