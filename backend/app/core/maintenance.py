from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MaintenanceJob:
    name: str
    run: Callable[[], object]
    interval_seconds: float = 60.0
    run_on_start: bool = True


@dataclass(frozen=True)
class MaintenanceJobResult:
    name: str
    status: str
    duration_ms: int
    result: object | None = None
    error: str | None = None


@dataclass(frozen=True)
class MaintenanceTickResult:
    ran: tuple[MaintenanceJobResult, ...]
    skipped: tuple[str, ...]


class MaintenanceRunner:
    def __init__(
        self,
        jobs: list[MaintenanceJob] | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        logger_: logging.Logger | None = None,
    ) -> None:
        self._jobs: dict[str, MaintenanceJob] = {}
        self._next_run_at: dict[str, float] = {}
        self._monotonic = monotonic
        self._logger = logger_ or logger
        for job in jobs or []:
            self.register(job)

    def register(self, job: MaintenanceJob) -> None:
        if not job.name:
            raise ValueError("Maintenance job name is required")
        if job.interval_seconds <= 0:
            raise ValueError("Maintenance job interval must be positive")
        if job.name in self._jobs:
            raise ValueError(f"Maintenance job {job.name} is already registered")
        self._jobs[job.name] = job
        self._next_run_at[job.name] = (
            0.0 if job.run_on_start else self._monotonic() + job.interval_seconds
        )

    def tick(self) -> MaintenanceTickResult:
        now = self._monotonic()
        ran: list[MaintenanceJobResult] = []
        skipped: list[str] = []
        for job in self._jobs.values():
            if now < self._next_run_at[job.name]:
                skipped.append(job.name)
                continue
            result = self._run_job(job)
            ran.append(result)
            self._next_run_at[job.name] = self._monotonic() + job.interval_seconds
        return MaintenanceTickResult(ran=tuple(ran), skipped=tuple(skipped))

    def due_jobs(self) -> tuple[str, ...]:
        now = self._monotonic()
        return tuple(
            job.name for job in self._jobs.values() if now >= self._next_run_at[job.name]
        )

    def _run_job(self, job: MaintenanceJob) -> MaintenanceJobResult:
        started_at = self._monotonic()
        try:
            result = job.run()
        except Exception as exc:
            duration_ms = _duration_ms(started_at, self._monotonic())
            self._logger.exception(
                "Maintenance job failed",
                extra={
                    "maintenance_job": job.name,
                    "duration_ms": duration_ms,
                },
            )
            return MaintenanceJobResult(
                name=job.name,
                status="failed",
                duration_ms=duration_ms,
                error=str(exc),
            )
        duration_ms = _duration_ms(started_at, self._monotonic())
        self._logger.info(
            "Maintenance job completed",
            extra={
                "maintenance_job": job.name,
                "duration_ms": duration_ms,
            },
        )
        return MaintenanceJobResult(
            name=job.name,
            status="completed",
            duration_ms=duration_ms,
            result=result,
        )


def _duration_ms(started_at: float, finished_at: float) -> int:
    return max(0, int((finished_at - started_at) * 1000))
