from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event
from typing import Protocol

from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunner
from backend.app.core.config import Settings
from backend.app.operations.service import OperationsService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.workers.handlers import WorkerJobHandler
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue import RedisQueue

logger = logging.getLogger(__name__)


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


@dataclass(frozen=True)
class WorkerRunnerConfig:
    worker_id: str
    worker_type: str = "cloud"
    queue_name: str = "agent_runs"
    max_jobs: int = 1
    heartbeat_interval_seconds: float = 30.0
    idle_sleep_seconds: float = 1.0
    maintenance_interval_seconds: float = 60.0
    run_lease_seconds: int = 900
    recovery_batch_size: int = 100


@dataclass(frozen=True)
class WorkerRunSummary:
    processed: int
    failed: int
    idle_polls: int
    recovered_runs: int
    stopped: bool


class WorkerRunner:
    def __init__(
        self,
        *,
        queue: RedisQueue,
        session_factory: SessionFactory,
        config: WorkerRunnerConfig,
        agent_runner: AgentRunner | None = None,
        settings: Settings | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._queue = queue
        self._session_factory = session_factory
        self._config = config
        self._agent_runner = agent_runner
        self._settings = settings
        self._monotonic = monotonic
        self._sleep = sleep

    def run_once(self) -> bool:
        with self._session_scope() as session:
            operations = OperationsService(session)
            capacity = operations.worker_capacity_snapshot(
                self._config.worker_id,
                default_max_jobs=self._config.max_jobs,
            )
            if not capacity.accepting:
                return False
        job = self._queue.dequeue()
        if job is None:
            return False
        self._start_lease(job)
        try:
            self._handle_job(job)
        except Exception as exc:
            self._finish_lease(
                job,
                status="retrying" if job.can_retry else "failed",
                metadata={"error": str(exc)},
            )
            self._queue.retry_or_dead_letter(job)
            raise
        self._finish_lease(job, status="completed")
        return True

    def _handle_job(self, job: JobPayload) -> None:
        with self._session_scope() as session:
            handler = WorkerJobHandler(
                session,
                self._queue,
                self._agent_runner,
                self._settings,
            )
            handler.handle(job)

    def run(
        self,
        *,
        max_jobs: int | None = None,
        stop_event: Event | None = None,
    ) -> WorkerRunSummary:
        processed = 0
        failed = 0
        idle_polls = 0
        recovered_runs = 0
        last_error: str | None = None
        next_heartbeat_at = 0.0
        next_maintenance_at = 0.0

        while not self._is_stopped(stop_event):
            now = self._monotonic()
            if now >= next_heartbeat_at:
                self.record_heartbeat(
                    self._status_for(failed),
                    self._heartbeat_details(
                        processed=processed,
                        failed=failed,
                        idle_polls=idle_polls,
                        recovered_runs=recovered_runs,
                        last_error=last_error,
                    ),
                )
                next_heartbeat_at = now + self._config.heartbeat_interval_seconds
            if now >= next_maintenance_at:
                recovered_runs += self.run_maintenance()
                next_maintenance_at = now + self._config.maintenance_interval_seconds

            try:
                handled = self.run_once()
            except Exception as exc:
                failed += 1
                last_error = str(exc)
                logger.exception("Worker job failed")
                self.record_heartbeat(
                    "degraded",
                    self._heartbeat_details(
                        processed=processed,
                        failed=failed,
                        idle_polls=idle_polls,
                        recovered_runs=recovered_runs,
                        last_error=last_error,
                    ),
                )
                if max_jobs is not None and processed + failed >= max_jobs:
                    break
                continue
            if handled:
                processed += 1
                if max_jobs is not None and processed + failed >= max_jobs:
                    break
                continue

            idle_polls += 1
            self._sleep(self._config.idle_sleep_seconds)

        status = "stopping" if self._is_stopped(stop_event) else self._status_for(failed)
        self.record_heartbeat(
            status,
            self._heartbeat_details(
                processed=processed,
                failed=failed,
                idle_polls=idle_polls,
                recovered_runs=recovered_runs,
                last_error=last_error,
            ),
        )
        return WorkerRunSummary(
            processed=processed,
            failed=failed,
            idle_polls=idle_polls,
            recovered_runs=recovered_runs,
            stopped=self._is_stopped(stop_event),
        )

    def record_heartbeat(self, status: str, details: dict[str, object]) -> None:
        try:
            with self._session_scope() as session:
                OperationsService(session).record_worker_heartbeat(
                    worker_id=self._config.worker_id,
                    worker_type=self._config.worker_type,
                    status=status,
                    queue_name=self._config.queue_name,
                    details=details,
                    capacity={"max_jobs": self._config.max_jobs},
                )
        except Exception:
            logger.exception("Failed to record worker heartbeat")

    @contextmanager
    def _session_scope(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _is_stopped(self, stop_event: Event | None) -> bool:
        return stop_event is not None and stop_event.is_set()

    def run_maintenance(self) -> int:
        try:
            with self._session_scope() as session:
                summary = RunOrchestrationService(session).recover_stale_running_runs(
                    stale_after_seconds=self._config.run_lease_seconds,
                    limit=self._config.recovery_batch_size,
                )
                return summary.recovered_runs
        except Exception:
            logger.exception("Failed to run worker maintenance")
            return 0

    def _status_for(self, failed: int) -> str:
        return "degraded" if failed > 0 else "online"

    def _heartbeat_details(
        self,
        *,
        processed: int,
        failed: int,
        idle_polls: int,
        recovered_runs: int,
        last_error: str | None,
    ) -> dict[str, object]:
        details: dict[str, object] = {
            "processed": processed,
            "failed": failed,
            "idle_polls": idle_polls,
            "recovered_runs": recovered_runs,
            "capacity": {
                "max_jobs": self._config.max_jobs,
            },
        }
        if last_error is not None:
            details["last_error"] = last_error
        return details

    def _start_lease(self, job: JobPayload) -> None:
        try:
            with self._session_scope() as session:
                OperationsService(session).start_worker_lease(
                    worker_id=self._config.worker_id,
                    queue_name=self._config.queue_name,
                    job=job,
                    metadata={
                        "requested_by_user_id": str(job.requested_by_user_id)
                        if job.requested_by_user_id is not None
                        else None,
                        "requested_by_agent_run_id": str(job.requested_by_agent_run_id)
                        if job.requested_by_agent_run_id is not None
                        else None,
                    },
                )
        except Exception:
            logger.exception("Failed to start worker lease")

    def _finish_lease(
        self,
        job: JobPayload,
        *,
        status: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        try:
            with self._session_scope() as session:
                OperationsService(session).finish_worker_lease(
                    job_id=job.job_id,
                    status=status,
                    metadata=metadata,
                )
        except Exception:
            logger.exception("Failed to finish worker lease")
