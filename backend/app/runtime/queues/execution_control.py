"""Cooperative control for a claimed execution, independent of its SDK event loop."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Event, Lock


class ExecutionOwnershipLostError(RuntimeError):
    """An expired owner must neither publish a result nor retry its execution."""


@dataclass
class ExecutionControl:
    cancel_requested: Event = field(default_factory=Event)
    ownership_lost: Event = field(default_factory=Event)
    settled: Event = field(default_factory=Event)
    lease_transition: Lock = field(default_factory=Lock)

    def check_ownership(self) -> None:
        if self.ownership_lost.is_set():
            raise ExecutionOwnershipLostError("Execution lease ownership was lost")


_control: ContextVar[ExecutionControl | None] = ContextVar("execution_control", default=None)


def current_execution_control() -> ExecutionControl | None:
    return _control.get()


@contextmanager
def execution_control(control: ExecutionControl) -> Iterator[None]:
    token = _control.set(control)
    try:
        yield
    finally:
        _control.reset(token)
