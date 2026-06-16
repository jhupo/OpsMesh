from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Event
from typing import Protocol

from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunner
from backend.app.capabilities.mcp_execution_adapters import (
    McpToolAdapter,
    McpToolAdapterResolver,
)
from backend.app.core.config import Settings
from backend.app.core.request_context import log_context
from backend.app.core.trace_context import (
    current_trace_context,
    new_trace_context,
)
from backend.app.operations.worker_capacity_snapshot import WorkerCapacitySnapshotService
from backend.app.operations.worker_heartbeats import WorkerHeartbeatOperationsService
from backend.app.workers.capacity import merge_counts, worker_can_run_job
from backend.app.workers.handlers import WorkerJobHandler
from backend.app.workers.heartbeat import worker_heartbeat_details, worker_status_for_failures
from backend.app.workers.jobs import JobPayload
from backend.app.workers.lease_reporting import WorkerLeaseReporter
from backend.app.workers.maintenance import (
    WorkerMaintenanceConfig,
    WorkerMaintenanceService,
    WorkerMaintenanceSummary,
)
from backend.app.workers.queue import RedisQueue
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
        agent_runner: AgentRunner | None = None,
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

    def _job_log_context(self, job: JobPayload) -> Iterator[None]:
        run_id = (
            job.resource_id if job.job_type.value in {"agent.run", "mcp.tool_execution"} else None
        )
        trace = job.trace_context()
        trace_metadata = {}
        if trace is not None:
            trace_metadata = trace.metadata()
        elif self._tracing_enabled():
            trace_metadata = new_trace_context().metadata()
        return log_context(
            worker_id=self._config.worker_id,
            workspace_id=job.workspace_id,
            run_id=run_id,
            **trace_metadata,
        )

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
        expired_leases = 0
        stale_runtimes = 0
        deleted_runtime_records = 0
        lifecycle_backup_jobs_enqueued = 0
        lifecycle_backup_jobs_skipped = 0
        lifecycle_retention_runs_applied = 0
        lifecycle_retention_runs_skipped = 0
        lifecycle_restore_drills_completed = 0
        lifecycle_restore_drills_skipped = 0
        team_execution_loop_jobs_enqueued = 0
        team_execution_loop_jobs_skipped = 0
        team_execution_loop_skip_reasons: dict[str, int] = {}
        task_events_published = 0
        task_event_publish_failures = 0
        webhook_delivery_jobs_enqueued = 0
        webhook_delivery_jobs_skipped = 0
        scheduled_job_actions_enqueued = 0
        scheduled_job_actions_recorded = 0
        scheduled_job_actions_skipped = 0
        scheduled_job_actions_enqueued_by_job_type: dict[str, int] = {}
        scheduled_job_actions_recorded_by_job_type: dict[str, int] = {}
        scheduled_job_actions_skipped_by_job_type: dict[str, int] = {}
        last_error: str | None = None
        next_heartbeat_at = 0.0
        next_maintenance_at = 0.0

        while not self._is_stopped(stop_event):
            now = self._monotonic()
            if now >= next_heartbeat_at:
                self.record_heartbeat(
                    worker_status_for_failures(failed),
                    worker_heartbeat_details(
                        config=self._config,
                        processed=processed,
                        failed=failed,
                        idle_polls=idle_polls,
                        recovered_runs=recovered_runs,
                        expired_leases=expired_leases,
                        stale_runtimes=stale_runtimes,
                        deleted_runtime_records=deleted_runtime_records,
                        lifecycle_backup_jobs_enqueued=lifecycle_backup_jobs_enqueued,
                        lifecycle_backup_jobs_skipped=lifecycle_backup_jobs_skipped,
                        lifecycle_retention_runs_applied=lifecycle_retention_runs_applied,
                        lifecycle_retention_runs_skipped=lifecycle_retention_runs_skipped,
                        lifecycle_restore_drills_completed=(lifecycle_restore_drills_completed),
                        lifecycle_restore_drills_skipped=lifecycle_restore_drills_skipped,
                        team_execution_loop_jobs_enqueued=(team_execution_loop_jobs_enqueued),
                        team_execution_loop_jobs_skipped=team_execution_loop_jobs_skipped,
                        team_execution_loop_skip_reasons=team_execution_loop_skip_reasons,
                        task_events_published=task_events_published,
                        task_event_publish_failures=task_event_publish_failures,
                        webhook_delivery_jobs_enqueued=webhook_delivery_jobs_enqueued,
                        webhook_delivery_jobs_skipped=webhook_delivery_jobs_skipped,
                        scheduled_job_actions_enqueued=scheduled_job_actions_enqueued,
                        scheduled_job_actions_recorded=scheduled_job_actions_recorded,
                        scheduled_job_actions_skipped=scheduled_job_actions_skipped,
                        scheduled_job_actions_enqueued_by_job_type=(
                            scheduled_job_actions_enqueued_by_job_type
                        ),
                        scheduled_job_actions_recorded_by_job_type=(
                            scheduled_job_actions_recorded_by_job_type
                        ),
                        scheduled_job_actions_skipped_by_job_type=(
                            scheduled_job_actions_skipped_by_job_type
                        ),
                        last_error=last_error,
                    ),
                )
                next_heartbeat_at = now + self._config.heartbeat_interval_seconds
            if now >= next_maintenance_at:
                maintenance = self.run_maintenance()
                recovered_runs += maintenance.recovered_runs
                expired_leases += maintenance.expired_leases
                stale_runtimes += maintenance.stale_runtimes
                deleted_runtime_records += maintenance.deleted_runtime_records
                lifecycle_backup_jobs_enqueued += maintenance.lifecycle_backup_jobs_enqueued
                lifecycle_backup_jobs_skipped += maintenance.lifecycle_backup_jobs_skipped
                lifecycle_retention_runs_applied += maintenance.lifecycle_retention_runs_applied
                lifecycle_retention_runs_skipped += maintenance.lifecycle_retention_runs_skipped
                lifecycle_restore_drills_completed += maintenance.lifecycle_restore_drills_completed
                lifecycle_restore_drills_skipped += maintenance.lifecycle_restore_drills_skipped
                team_execution_loop_jobs_enqueued += maintenance.team_execution_loop_jobs_enqueued
                team_execution_loop_jobs_skipped += maintenance.team_execution_loop_jobs_skipped
                merge_counts(
                    team_execution_loop_skip_reasons,
                    maintenance.team_execution_loop_skip_reasons,
                )
                task_events_published += maintenance.task_events_published
                task_event_publish_failures += maintenance.task_event_publish_failures
                webhook_delivery_jobs_enqueued += maintenance.webhook_delivery_jobs_enqueued
                webhook_delivery_jobs_skipped += maintenance.webhook_delivery_jobs_skipped
                scheduled_job_actions_enqueued += maintenance.scheduled_job_actions_enqueued
                scheduled_job_actions_recorded += maintenance.scheduled_job_actions_recorded
                scheduled_job_actions_skipped += maintenance.scheduled_job_actions_skipped
                merge_counts(
                    scheduled_job_actions_enqueued_by_job_type,
                    maintenance.scheduled_job_actions_enqueued_by_job_type,
                )
                merge_counts(
                    scheduled_job_actions_recorded_by_job_type,
                    maintenance.scheduled_job_actions_recorded_by_job_type,
                )
                merge_counts(
                    scheduled_job_actions_skipped_by_job_type,
                    maintenance.scheduled_job_actions_skipped_by_job_type,
                )
                next_maintenance_at = now + self._config.maintenance_interval_seconds

            try:
                handled = self.run_once()
            except Exception as exc:
                failed += 1
                last_error = str(exc)
                logger.exception("Worker job failed")
                self.record_heartbeat(
                    "degraded",
                    worker_heartbeat_details(
                        config=self._config,
                        processed=processed,
                        failed=failed,
                        idle_polls=idle_polls,
                        recovered_runs=recovered_runs,
                        expired_leases=expired_leases,
                        stale_runtimes=stale_runtimes,
                        deleted_runtime_records=deleted_runtime_records,
                        lifecycle_backup_jobs_enqueued=lifecycle_backup_jobs_enqueued,
                        lifecycle_backup_jobs_skipped=lifecycle_backup_jobs_skipped,
                        lifecycle_retention_runs_applied=lifecycle_retention_runs_applied,
                        lifecycle_retention_runs_skipped=lifecycle_retention_runs_skipped,
                        lifecycle_restore_drills_completed=(lifecycle_restore_drills_completed),
                        lifecycle_restore_drills_skipped=lifecycle_restore_drills_skipped,
                        team_execution_loop_jobs_enqueued=(team_execution_loop_jobs_enqueued),
                        team_execution_loop_jobs_skipped=team_execution_loop_jobs_skipped,
                        team_execution_loop_skip_reasons=team_execution_loop_skip_reasons,
                        task_events_published=task_events_published,
                        task_event_publish_failures=task_event_publish_failures,
                        webhook_delivery_jobs_enqueued=webhook_delivery_jobs_enqueued,
                        webhook_delivery_jobs_skipped=webhook_delivery_jobs_skipped,
                        scheduled_job_actions_enqueued=scheduled_job_actions_enqueued,
                        scheduled_job_actions_recorded=scheduled_job_actions_recorded,
                        scheduled_job_actions_skipped=scheduled_job_actions_skipped,
                        scheduled_job_actions_enqueued_by_job_type=(
                            scheduled_job_actions_enqueued_by_job_type
                        ),
                        scheduled_job_actions_recorded_by_job_type=(
                            scheduled_job_actions_recorded_by_job_type
                        ),
                        scheduled_job_actions_skipped_by_job_type=(
                            scheduled_job_actions_skipped_by_job_type
                        ),
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

        status = "stopping" if self._is_stopped(stop_event) else worker_status_for_failures(failed)
        self.record_heartbeat(
            status,
            worker_heartbeat_details(
                config=self._config,
                processed=processed,
                failed=failed,
                idle_polls=idle_polls,
                recovered_runs=recovered_runs,
                expired_leases=expired_leases,
                stale_runtimes=stale_runtimes,
                deleted_runtime_records=deleted_runtime_records,
                lifecycle_backup_jobs_enqueued=lifecycle_backup_jobs_enqueued,
                lifecycle_backup_jobs_skipped=lifecycle_backup_jobs_skipped,
                lifecycle_retention_runs_applied=lifecycle_retention_runs_applied,
                lifecycle_retention_runs_skipped=lifecycle_retention_runs_skipped,
                lifecycle_restore_drills_completed=lifecycle_restore_drills_completed,
                lifecycle_restore_drills_skipped=lifecycle_restore_drills_skipped,
                team_execution_loop_jobs_enqueued=team_execution_loop_jobs_enqueued,
                team_execution_loop_jobs_skipped=team_execution_loop_jobs_skipped,
                team_execution_loop_skip_reasons=team_execution_loop_skip_reasons,
                task_events_published=task_events_published,
                task_event_publish_failures=task_event_publish_failures,
                webhook_delivery_jobs_enqueued=webhook_delivery_jobs_enqueued,
                webhook_delivery_jobs_skipped=webhook_delivery_jobs_skipped,
                scheduled_job_actions_enqueued=scheduled_job_actions_enqueued,
                scheduled_job_actions_recorded=scheduled_job_actions_recorded,
                scheduled_job_actions_skipped=scheduled_job_actions_skipped,
                scheduled_job_actions_enqueued_by_job_type=(
                    scheduled_job_actions_enqueued_by_job_type
                ),
                scheduled_job_actions_recorded_by_job_type=(
                    scheduled_job_actions_recorded_by_job_type
                ),
                scheduled_job_actions_skipped_by_job_type=(
                    scheduled_job_actions_skipped_by_job_type
                ),
                last_error=last_error,
            ),
        )
        return WorkerRunSummary(
            processed=processed,
            failed=failed,
            idle_polls=idle_polls,
            recovered_runs=recovered_runs,
            expired_leases=expired_leases,
            stale_runtimes=stale_runtimes,
            deleted_runtime_records=deleted_runtime_records,
            lifecycle_backup_jobs_enqueued=lifecycle_backup_jobs_enqueued,
            lifecycle_backup_jobs_skipped=lifecycle_backup_jobs_skipped,
            lifecycle_retention_runs_applied=lifecycle_retention_runs_applied,
            lifecycle_retention_runs_skipped=lifecycle_retention_runs_skipped,
            lifecycle_restore_drills_completed=lifecycle_restore_drills_completed,
            lifecycle_restore_drills_skipped=lifecycle_restore_drills_skipped,
            team_execution_loop_jobs_enqueued=team_execution_loop_jobs_enqueued,
            team_execution_loop_jobs_skipped=team_execution_loop_jobs_skipped,
            team_execution_loop_skip_reasons=team_execution_loop_skip_reasons,
            task_events_published=task_events_published,
            task_event_publish_failures=task_event_publish_failures,
            webhook_delivery_jobs_enqueued=webhook_delivery_jobs_enqueued,
            webhook_delivery_jobs_skipped=webhook_delivery_jobs_skipped,
            scheduled_job_actions_enqueued=scheduled_job_actions_enqueued,
            scheduled_job_actions_recorded=scheduled_job_actions_recorded,
            scheduled_job_actions_skipped=scheduled_job_actions_skipped,
            scheduled_job_actions_enqueued_by_job_type=(scheduled_job_actions_enqueued_by_job_type),
            scheduled_job_actions_recorded_by_job_type=(scheduled_job_actions_recorded_by_job_type),
            scheduled_job_actions_skipped_by_job_type=(scheduled_job_actions_skipped_by_job_type),
            stopped=self._is_stopped(stop_event),
        )

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
