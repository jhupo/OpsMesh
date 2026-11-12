"""Per-dispatch failures, isolated across concurrent and nested events."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class DispatchResult:
    """Processing errors, excluding ordinary denied or unmatched events.

    An empty result does not imply that any handler ran or that external
    effects are durable. Callers retain responsibility for idempotency.
    """

    errors: tuple[Exception, ...] = ()
    matched: int = 0
    handled: int = 0
    suspended: int = 0
    ignored: bool = False

    def raise_for_errors(self) -> None:
        """Propagate failures to callers that require retry or rejection."""
        if self.errors:
            raise ExceptionGroup("Event dispatch failed", self.errors)


_errors: ContextVar[list[Exception] | None] = ContextVar(
    "dispatch_errors", default=None
)


@dataclass
class DispatchProgress:
    """Count matcher outcomes inside one dispatch's child tasks."""

    matched: int = 0
    handled: int = 0
    suspended: int = 0
    ignored: bool = False


_progress: ContextVar[DispatchProgress | None] = ContextVar(
    "dispatch_progress", default=None
)


@contextmanager
def collect_progress() -> Iterator[DispatchProgress]:
    """Isolate outcome counters across concurrent and nested dispatches."""
    progress = DispatchProgress()
    token = _progress.set(progress)
    try:
        yield progress
    finally:
        _progress.reset(token)


def record_outcome(outcome: str) -> None:
    """Record routing facts without interpreting business success."""
    if (progress := _progress.get()) is None:
        return
    if outcome == "ignored":
        progress.ignored = True
    elif outcome in {"matched", "handled", "suspended"}:
        setattr(progress, outcome, getattr(progress, outcome) + 1)
    else:
        raise ValueError("Unknown dispatch outcome")


@contextmanager
def collect_errors() -> Iterator[list[Exception]]:
    """Give one dispatch an error collector inherited by its child tasks."""
    errors: list[Exception] = []
    token = _errors.set(errors)
    try:
        yield errors
    finally:
        _errors.reset(token)


def record_error(error: Exception) -> None:
    """Record a caught failure without changing legacy standalone calls."""
    if (errors := _errors.get()) is not None:
        errors.append(error)
