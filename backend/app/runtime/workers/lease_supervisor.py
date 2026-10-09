"""Keep execution leases alive independently of admission, SDK and maintenance work."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Lock, Thread
from types import TracebackType
from uuid import UUID

from backend.app.runtime.queues.execution_control import ExecutionControl
from backend.app.runtime.queues.service import QueueLease


@dataclass(frozen=True)
class ActiveExecution:
    lease: QueueLease
    control: ExecutionControl


class LeaseSupervisor:
    def __init__(
        self,
        interval: float,
        pulse: Callable[[list[ActiveExecution]], None],
    ) -> None:
        self._interval = interval
        self._pulse = pulse
        self._active: dict[UUID, ActiveExecution] = {}
        self._lock = Lock()
        self._stopped = Event()
        self._thread = Thread(target=self._run, name="opsmesh-leases", daemon=True)

    def add(self, execution: ActiveExecution) -> None:
        with self._lock:
            self._active[execution.lease.job.job_id] = execution

    def remove(self, job_id: UUID) -> None:
        with self._lock:
            self._active.pop(job_id, None)

    def _run(self) -> None:
        while not self._stopped.wait(self._interval):
            with self._lock:
                snapshot = list(self._active.values())
            self._pulse(snapshot)

    def __enter__(self) -> LeaseSupervisor:
        if self._interval > 0:
            self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stopped.set()
        if self._thread.is_alive():
            self._thread.join(timeout=max(1.0, self._interval))
