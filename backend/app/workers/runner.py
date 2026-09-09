from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Event
from typing import Protocol

from opentelemetry.trace import SpanKind
from sqlalchemy.orm import Session

from backend.app.admin.updates.service import maintenance_enabled
from backend.app.agent_runtime.contracts import AgentRuntimeExecutor
from backend.app.capabilities.mcp_execution_adapters import (
    McpToolAdapter,
    McpToolAdapterResolver,
)
from backend.app.core.config import Settings
from backend.app.core.request_context import log_context
from backend.app.core.trace_context import (
    current_trace_context,
    new_trace_context,
    telemetry_span,
)
from backend.app.operations.worker_capacity_snapshot import WorkerCapacitySnapshotService
from backend.app.operations.worker_heartbeats import WorkerHeartbeatOperationsService
from backend.app.workers.capacity import worker_can_run_job
from backend.app.workers.handlers import WorkerJobHandler
from backend.app.workers.heartbeat import worker_status_for_failures
from backend.app.workers.jobs import JobPayload
from backend.app.workers.lease_reporting import WorkerLeaseReporter
from backend.app.workers.maintenance import (
    WorkerMaintenanceConfig,
    WorkerMaintenanceService,
    WorkerMaintenanceSummary,
)
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workers.run_state import WorkerRunState
from backend.app.workers.runner_models import WorkerRunnerConfig, WorkerRunSummary

logger = logging.getLogger(__name__)


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


class WorkerRunner:
    def __init__(
        self,
        *,
        queue: RedisQueue,
        session_factory: SessionFactory,
        config: WorkerRunnerConfig,
        agent_runner: AgentRuntimeExecutor | None = None,
        mcp_adapter: McpToolAdapter | McpToolAdapterResolver | None = None,
        settings: Settings | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._queue = queue
        self._session_factory = session_factory
        self._config = config
        self._agent_runner = agent_runner
        self._mcp_adapter = mcp_adapter
        self._settings = settings
        self._monotonic = monotonic
        self._sleep = sleep
        self._lease_reporter = WorkerLeaseReporter(
            config=config,
            session_scope=self._session_scope,
        )

    def run_once(self) -> bool:
        with self._session_scope() as session:
            # Hold the shared installation lock until the lease exists. The updater's exclusive
            # maintenance transition must not race a dequeued-but-not-yet-leased job.
            if maintenance_enabled(session):
                return False
            capacity = WorkerCapacitySnapshotService(session).worker_capacity_snapshot(
                self._config.worker_id,
                default_max_jobs=self._config.max_jobs,
            )
            if not capacity.accepting:
                return False
            worker_capacity = capacity.capacity or {}
            job = self._queue.dequeue_matching(
                lambda candidate: worker_can_run_job(candidate, worker_capacity),
                scan_limit=self._config.job_scan_limit,
            )
            if job is None:
                return False
            with self._job_log_context(job):
                self._lease_reporter.start_lease(job)
                session.commit()  # release the admission lock before long-running execution
                try:
                    self._handle_job(job)
                except Exception as exc:
                    failure_status = "retrying" if job.can_retry else "failed"
                    self._lease_reporter.finish_lease(
                        job,
                        status=failure_status,
                        metadata={"error": str(exc)},
                    )
                    self._lease_reporter.record_team_execution_loop_failure(
                        job,
                        status=failure_status,
                        error=exc,
                    )
                    self._queue.retry_or_dead_letter(
                        job,
                        error=exc,
                        delay_seconds=self._retry_delay(job),
                    )
                    raise
                self._lease_reporter.finish_lease(job, status="completed")
                self._queue.ack(job)
        return True

    def _handle_job(self, job: JobPayload) -> None:
        with self._session_scope() as session:
            handler = WorkerJobHandler(
                session=session,
                queue=self._queue,
                agent_runner=self._agent_runner,
                settings=self._settings,
                mcp_adapter=self._mcp_adapter,
            )
            handler.handle(job)

    @contextmanager
    def _job_log_context(self, job: JobPayload) -> Iterator[None]:
        run_id = (
            job.resource_id if job.job_type.value in {"agent.run", "mcp.tool_execution"} else None
        )
        parent_trace = job.trace_context()
        if not self._tracing_enabled():
            with log_context(
                worker_id=self._config.worker_id,
                workspace_id=job.workspace_id,
                run_id=run_id,
            ):
                yield
            return
        with (
            telemetry_span(
                "opsmesh.worker.process_job",
                parent=parent_trace,
                kind=SpanKind.CONSUMER,
                attributes={
                    "messaging.destination.name": self._config.queue_name,
                    "messaging.operation.name": "process",
                    "messaging.system": "redis",
                    "opsmesh.job.attempt": job.attempt,
                    "opsmesh.job.type": job.job_type.value,
                    "opsmesh.workspace.id": str(job.workspace_id),
                },
            ) as active_trace,
            log_context(
                worker_id=self._config.worker_id,
                workspace_id=job.workspace_id,
                run_id=run_id,
                **active_trace.metadata(),
            ),
        ):
            yield

    def run(
        self,
        *,
        max_jobs: int | None = None,
        stop_event: Event | None = None,
    ) -> WorkerRunSummary:
        state = WorkerRunState()
        next_heartbeat_at = 0.0
        next_maintenance_at = 0.0

        while not self._is_stopped(stop_event):
            now = self._monotonic()
            if now >= next_heartbeat_at:
                self.record_heartbeat(
                    worker_status_for_failures(state.failed),
                    state.heartbeat_details(self._config),
                )
                next_heartbeat_at = now + self._config.heartbeat_interval_seconds
            if now >= next_maintenance_at:
                state.record_maintenance(self.run_maintenance())
                next_maintenance_at = now + self._config.maintenance_interval_seconds

            try:
                handled = self.run_once()
            except Exception as exc:
                state.failed += 1
                state.last_error = str(exc)
                logger.exception("Worker job failed")
                self.record_heartbeat(
                    "degraded",
                    state.heartbeat_details(self._config),
                )
                if max_jobs is not None and state.attempts >= max_jobs:
                    break
                continue
            if handled:
                state.processed += 1
                if max_jobs is not None and state.attempts >= max_jobs:
                    break
                continue

            state.idle_polls += 1
            self._sleep(self._config.idle_sleep_seconds)

        stopped = self._is_stopped(stop_event)
        status = "stopping" if stopped else worker_status_for_failures(state.failed)
        self.record_heartbeat(
            status,
            state.heartbeat_details(self._config),
        )
        return state.summary(stopped=stopped)

    def record_heartbeat(self, status: str, details: dict[str, object]) -> None:
        details = self._heartbeat_trace_details(details)
        try:
            with self._session_scope() as session:
                WorkerHeartbeatOperationsService(session).record_worker_heartbeat(
                    worker_id=self._config.worker_id,
                    worker_type=self._config.worker_type,
                    status=status,
                    queue_name=self._config.queue_name,
                    details=details,
                    capacity={"max_jobs": self._config.max_jobs},
                )
        except Exception:
            logger.exception("Failed to record worker heartbeat")

    def _heartbeat_trace_details(self, details: dict[str, object]) -> dict[str, object]:
        if not self._tracing_enabled():
            return details
        context = current_trace_context() or new_trace_context()
        return dict(details) | context.metadata()

    @contextmanager
    def _session_scope(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _is_stopped(self, stop_event: Event | None) -> bool:
        return stop_event is not None and stop_event.is_set()

    def run_maintenance(self) -> WorkerMaintenanceSummary:
        return WorkerMaintenanceService(
            queue=self._queue,
            session_factory=self._session_factory,
            config=WorkerMaintenanceConfig(
                run_lease_seconds=self._config.run_lease_seconds,
                recovery_batch_size=self._config.recovery_batch_size,
            ),
            settings=self._settings,
        ).run()

    def _retry_delay(self, job: JobPayload) -> float:
        if not job.can_retry:
            return 0.0
        base_delay = max(0.0, self._config.retry_base_delay_seconds)
        if base_delay <= 0:
            return 0.0
        delay = base_delay * (2 ** max(0, job.attempt))
        max_delay = self._config.retry_max_delay_seconds
        return min(delay, max_delay) if max_delay > 0 else delay

    def _tracing_enabled(self) -> bool:
        return self._settings.tracing_enabled if self._settings is not None else True
