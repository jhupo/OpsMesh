from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

T = TypeVar("T")

logger = logging.getLogger(__name__)

RETRYABLE_SQLSTATES = {
    "40001",  # serialization_failure
    "40P01",  # deadlock_detected
    "55P03",  # lock_not_available
    "57014",  # query_canceled, commonly statement_timeout / lock_timeout
}


class TransactionRetryExhaustedError(Exception):
    def __init__(self, *, attempts: int, last_error: Exception) -> None:
        super().__init__(f"Transaction failed after {attempts} attempts")
        self.attempts = attempts
        self.last_error = last_error


@dataclass(frozen=True)
class TransactionRetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.05
    max_delay_seconds: float = 1.0
    backoff_multiplier: float = 2.0

    def delay_for_attempt(self, attempt: int) -> float:
        raw_delay = self.base_delay_seconds * (self.backoff_multiplier ** max(0, attempt - 1))
        return min(raw_delay, self.max_delay_seconds)


def run_in_transaction(
    session: Session,
    operation: Callable[[], T],
    *,
    retry_policy: TransactionRetryPolicy | None = None,
    operation_name: str = "db.transaction",
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    policy = retry_policy or TransactionRetryPolicy()
    attempts = max(1, policy.max_attempts)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = operation()
            session.commit()
            return result
        except Exception as exc:
            session.rollback()
            last_error = exc
            if not _is_retryable_db_error(exc) or attempt >= attempts:
                if _is_retryable_db_error(exc):
                    raise TransactionRetryExhaustedError(
                        attempts=attempt,
                        last_error=exc,
                    ) from exc
                raise
            delay = policy.delay_for_attempt(attempt)
            logger.warning(
                "Retrying database transaction",
                extra={
                    "operation_name": operation_name,
                    "attempt": attempt,
                    "next_attempt": attempt + 1,
                    "max_attempts": attempts,
                    "delay_seconds": delay,
                    "sqlstate": sqlstate_from_error(exc),
                },
            )
            sleep(delay)
    raise TransactionRetryExhaustedError(
        attempts=attempts,
        last_error=last_error or RuntimeError("unknown transaction failure"),
    )


def sqlstate_from_error(exc: Exception) -> str | None:
    original = getattr(exc, "orig", None)
    for attr in ("sqlstate", "pgcode"):
        value = getattr(original, attr, None)
        if isinstance(value, str) and value:
            return value
    return None


def _is_retryable_db_error(exc: Exception) -> bool:
    if not isinstance(exc, OperationalError | DBAPIError):
        return False
    sqlstate = sqlstate_from_error(exc)
    return sqlstate in RETRYABLE_SQLSTATES
