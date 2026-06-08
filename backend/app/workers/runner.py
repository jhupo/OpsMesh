from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Event
from typing import Protocol

from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunner
from backend.app.capabilities.execution import McpToolAdapter, McpToolAdapterResolver
from backend.app.core.config import Settings
from backend.app.core.request_context import log_context
from backend.app.core.trace_context import (
    current_trace_context,
    current_trace_metadata,
    new_trace_context,
)
from backend.app.files.storage import create_storage
from backend.app.operations.service import OperationsService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.scheduled_jobs.service import WorkspaceScheduledJobService
from backend.app.tasks.event_outbox import TaskEventOutboxPublisher
from backend.app.tasks.events import RedisTaskEventBus
from backend.app.teams.execution_loop import TeamExecutionLoopQueueService
from backend.app.teams.runtime import TeamRuntimeService
from backend.app.webhooks.service import WebhookDeliveryScheduler
from backend.app.workers.handlers import WorkerJobHandler
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.data_lifecycle import WorkspaceDataLifecycleService

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
    job_scan_limit: int = 50
    retry_base_delay_seconds: float = 5.0
    retry_max_delay_seconds: float = 300.0


@dataclass(frozen=True)
class WorkerRunSummary:
    processed: int
    failed: int
    idle_polls: int
    recovered_runs: int
    expired_leases: int
    stale_runtimes: int
    deleted_runtime_records: int
    lifecycle_backup_jobs_enqueued: int
    lifecycle_backup_jobs_skipped: int
    lifecycle_retention_runs_applied: int
    lifecycle_retention_runs_skipped: int
    lifecycle_restore_drills_completed: int
    lifecycle_restore_drills_skipped: int
    team_execution_loop_jobs_enqueued: int
    team_execution_loop_jobs_skipped: int
    team_execution_loop_skip_reasons: dict[str, int]
    task_events_published: int
    task_event_publish_failures: int
    webhook_delivery_jobs_enqueued: int
    webhook_delivery_jobs_skipped: int
    scheduled_job_actions_enqueued: int
    scheduled_job_actions_recorded: int
    scheduled_job_actions_skipped: int
    scheduled_job_actions_enqueued_by_job_type: dict[str, int]
    scheduled_job_actions_recorded_by_job_type: dict[str, int]
    scheduled_job_actions_skipped_by_job_type: dict[str, int]
    stopped: bool


@dataclass(frozen=True)
class WorkerMaintenanceSummary:
    recovered_runs: int
    expired_leases: int
    stale_runtimes: int = 0
    deleted_runtime_records: int = 0
    lifecycle_backup_jobs_enqueued: int = 0
    lifecycle_backup_jobs_skipped: int = 0
    lifecycle_retention_runs_applied: int = 0
    lifecycle_retention_runs_skipped: int = 0
    lifecycle_restore_drills_completed: int = 0
    lifecycle_restore_drills_skipped: int = 0
    team_execution_loop_jobs_enqueued: int = 0
    team_execution_loop_jobs_skipped: int = 0
    team_execution_loop_skip_reasons: dict[str, int] = field(default_factory=dict)
    task_events_published: int = 0
    task_event_publish_failures: int = 0
    webhook_delivery_jobs_enqueued: int = 0
    webhook_delivery_jobs_skipped: int = 0
    scheduled_job_actions_enqueued: int = 0
    scheduled_job_actions_recorded: int = 0
    scheduled_job_actions_skipped: int = 0
    scheduled_job_actions_enqueued_by_job_type: dict[str, int] = field(default_factory=dict)
    scheduled_job_actions_recorded_by_job_type: dict[str, int] = field(default_factory=dict)
    scheduled_job_actions_skipped_by_job_type: dict[str, int] = field(default_factory=dict)


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

    def run_once(self) -> bool:
        with self._session_scope() as session:
            operations = OperationsService(session)
            capacity = operations.worker_capacity_snapshot(
                self._config.worker_id,
                default_max_jobs=self._config.max_jobs,
            )
            if not capacity.accepting:
                return False
            worker_capacity = capacity.capacity or {}
        job = self._queue.dequeue_matching(
            lambda candidate: _worker_can_run_job(candidate, worker_capacity),
            scan_limit=self._config.job_scan_limit,
        )
        if job is None:
            return False
        with self._job_log_context(job):
            self._start_lease(job)
            try:
                self._handle_job(job)
            except Exception as exc:
                failure_status = "retrying" if job.can_retry else "failed"
                self._finish_lease(
                    job,
                    status=failure_status,
                    metadata={"error": str(exc)},
                )
                self._record_team_execution_loop_failure(
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
            self._finish_lease(job, status="completed")
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
            job.resource_id
            if job.job_type.value in {"agent.run", "mcp.tool_execution"}
            else None
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
                    self._status_for(failed),
                    self._heartbeat_details(
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
                        lifecycle_restore_drills_completed=(
                            lifecycle_restore_drills_completed
                        ),
                        lifecycle_restore_drills_skipped=lifecycle_restore_drills_skipped,
                        team_execution_loop_jobs_enqueued=(
                            team_execution_loop_jobs_enqueued
                        ),
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
                lifecycle_backup_jobs_enqueued += (
                    maintenance.lifecycle_backup_jobs_enqueued
                )
                lifecycle_backup_jobs_skipped += (
                    maintenance.lifecycle_backup_jobs_skipped
                )
                lifecycle_retention_runs_applied += (
                    maintenance.lifecycle_retention_runs_applied
                )
                lifecycle_retention_runs_skipped += (
                    maintenance.lifecycle_retention_runs_skipped
                )
                lifecycle_restore_drills_completed += (
                    maintenance.lifecycle_restore_drills_completed
                )
                lifecycle_restore_drills_skipped += (
                    maintenance.lifecycle_restore_drills_skipped
                )
                team_execution_loop_jobs_enqueued += (
                    maintenance.team_execution_loop_jobs_enqueued
                )
                team_execution_loop_jobs_skipped += (
                    maintenance.team_execution_loop_jobs_skipped
                )
                _merge_counts(
                    team_execution_loop_skip_reasons,
                    maintenance.team_execution_loop_skip_reasons,
                )
                task_events_published += maintenance.task_events_published
                task_event_publish_failures += maintenance.task_event_publish_failures
                webhook_delivery_jobs_enqueued += maintenance.webhook_delivery_jobs_enqueued
                webhook_delivery_jobs_skipped += maintenance.webhook_delivery_jobs_skipped
                scheduled_job_actions_enqueued += (
                    maintenance.scheduled_job_actions_enqueued
                )
                scheduled_job_actions_recorded += (
                    maintenance.scheduled_job_actions_recorded
                )
                scheduled_job_actions_skipped += maintenance.scheduled_job_actions_skipped
                _merge_counts(
                    scheduled_job_actions_enqueued_by_job_type,
                    maintenance.scheduled_job_actions_enqueued_by_job_type,
                )
                _merge_counts(
                    scheduled_job_actions_recorded_by_job_type,
                    maintenance.scheduled_job_actions_recorded_by_job_type,
                )
                _merge_counts(
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
                    self._heartbeat_details(
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
                        lifecycle_restore_drills_completed=(
                            lifecycle_restore_drills_completed
                        ),
                        lifecycle_restore_drills_skipped=lifecycle_restore_drills_skipped,
                        team_execution_loop_jobs_enqueued=(
                            team_execution_loop_jobs_enqueued
                        ),
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

        status = "stopping" if self._is_stopped(stop_event) else self._status_for(failed)
        self.record_heartbeat(
            status,
            self._heartbeat_details(
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
            scheduled_job_actions_enqueued_by_job_type=(
                scheduled_job_actions_enqueued_by_job_type
            ),
            scheduled_job_actions_recorded_by_job_type=(
                scheduled_job_actions_recorded_by_job_type
            ),
            scheduled_job_actions_skipped_by_job_type=(
                scheduled_job_actions_skipped_by_job_type
            ),
            stopped=self._is_stopped(stop_event),
        )

    def record_heartbeat(self, status: str, details: dict[str, object]) -> None:
        details = self._heartbeat_trace_details(details)
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
        try:
            self._queue.reclaim_due_retries(limit=self._config.recovery_batch_size)
            self._queue.reclaim_expired(limit=self._config.recovery_batch_size)
            with self._session_scope() as session:
                summary = RunOrchestrationService(
                    session,
                    queue=self._queue,
                ).recover_stale_worker_runs(
                    stale_after_seconds=self._config.run_lease_seconds,
                    limit=self._config.recovery_batch_size,
                    reason="worker_maintenance",
                )
                expired_leases = OperationsService(session).expire_stale_worker_leases(
                    stale_after_seconds=self._config.run_lease_seconds,
                )
                stale_runtimes, deleted_runtime_records = OperationsService(
                    session
                ).cleanup_stale_runtimes_across_workspaces(
                    stale_after_seconds=self._config.run_lease_seconds,
                )
                lifecycle_summary = WorkspaceDataLifecycleService(
                    session
                ).run_scheduled_lifecycle(
                    queue=self._queue,
                    storage=(
                        create_storage(self._settings)
                        if self._settings is not None
                        else None
                    ),
                    limit=self._config.recovery_batch_size,
                )
                team_loop_summary = TeamExecutionLoopQueueService(
                    session
                ).enqueue_active_team_iterations(
                    queue=self._queue,
                    limit=self._config.recovery_batch_size,
                )
                task_event_summary = TaskEventOutboxPublisher(
                    session,
                    RedisTaskEventBus(
                        redis=self._queue.redis,
                        key_prefix=self._queue.keys.prefix,
                    ),
                ).publish_pending(limit=self._config.recovery_batch_size)
                webhook_delivery_summary = WebhookDeliveryScheduler(session).enqueue_due(
                    queue=self._queue,
                    limit=self._config.recovery_batch_size,
                )
                scheduled_job_summary = WorkspaceScheduledJobService(session).enqueue_due(
                    queue=self._queue,
                    limit=self._config.recovery_batch_size,
                )
                return WorkerMaintenanceSummary(
                    recovered_runs=summary.recovered_runs,
                    expired_leases=expired_leases,
                    stale_runtimes=stale_runtimes,
                    deleted_runtime_records=deleted_runtime_records,
                    lifecycle_backup_jobs_enqueued=(
                        lifecycle_summary.backup_jobs_enqueued
                    ),
                    lifecycle_backup_jobs_skipped=(
                        lifecycle_summary.backup_jobs_skipped
                    ),
                    lifecycle_retention_runs_applied=(
                        lifecycle_summary.retention_runs_applied
                    ),
                    lifecycle_retention_runs_skipped=(
                        lifecycle_summary.retention_runs_skipped
                    ),
                    lifecycle_restore_drills_completed=(
                        lifecycle_summary.restore_drills_completed
                    ),
                    lifecycle_restore_drills_skipped=(
                        lifecycle_summary.restore_drills_skipped
                    ),
                    team_execution_loop_jobs_enqueued=team_loop_summary.enqueued,
                    team_execution_loop_jobs_skipped=team_loop_summary.skipped,
                    team_execution_loop_skip_reasons=(
                        team_loop_summary.skipped_reasons
                    ),
                    task_events_published=task_event_summary.published,
                    task_event_publish_failures=task_event_summary.failed,
                    webhook_delivery_jobs_enqueued=(
                        webhook_delivery_summary.enqueued
                    ),
                    webhook_delivery_jobs_skipped=webhook_delivery_summary.skipped,
                    scheduled_job_actions_enqueued=scheduled_job_summary.enqueued,
                    scheduled_job_actions_recorded=scheduled_job_summary.recorded,
                    scheduled_job_actions_skipped=scheduled_job_summary.skipped,
                    scheduled_job_actions_enqueued_by_job_type=(
                        scheduled_job_summary.enqueued_by_job_type or {}
                    ),
                    scheduled_job_actions_recorded_by_job_type=(
                        scheduled_job_summary.recorded_by_job_type or {}
                    ),
                    scheduled_job_actions_skipped_by_job_type=(
                        scheduled_job_summary.skipped_by_job_type or {}
                    ),
                )
        except Exception:
            logger.exception("Failed to run worker maintenance")
            return WorkerMaintenanceSummary(recovered_runs=0, expired_leases=0)

    def _status_for(self, failed: int) -> str:
        return "degraded" if failed > 0 else "online"

    def _heartbeat_details(
        self,
        *,
        processed: int,
        failed: int,
        idle_polls: int,
        recovered_runs: int,
        expired_leases: int,
        stale_runtimes: int,
        deleted_runtime_records: int,
        lifecycle_backup_jobs_enqueued: int,
        lifecycle_backup_jobs_skipped: int,
        lifecycle_retention_runs_applied: int,
        lifecycle_retention_runs_skipped: int,
        lifecycle_restore_drills_completed: int,
        lifecycle_restore_drills_skipped: int,
        team_execution_loop_jobs_enqueued: int,
        team_execution_loop_jobs_skipped: int,
        team_execution_loop_skip_reasons: dict[str, int],
        task_events_published: int,
        task_event_publish_failures: int,
        webhook_delivery_jobs_enqueued: int,
        webhook_delivery_jobs_skipped: int,
        scheduled_job_actions_enqueued: int,
        scheduled_job_actions_recorded: int,
        scheduled_job_actions_skipped: int,
        scheduled_job_actions_enqueued_by_job_type: dict[str, int],
        scheduled_job_actions_recorded_by_job_type: dict[str, int],
        scheduled_job_actions_skipped_by_job_type: dict[str, int],
        last_error: str | None,
    ) -> dict[str, object]:
        details: dict[str, object] = {
            "processed": processed,
            "failed": failed,
            "idle_polls": idle_polls,
            "recovered_runs": recovered_runs,
            "expired_leases": expired_leases,
            "stale_runtimes": stale_runtimes,
            "deleted_runtime_records": deleted_runtime_records,
            "lifecycle_backup_jobs_enqueued": lifecycle_backup_jobs_enqueued,
            "lifecycle_backup_jobs_skipped": lifecycle_backup_jobs_skipped,
            "lifecycle_retention_runs_applied": lifecycle_retention_runs_applied,
            "lifecycle_retention_runs_skipped": lifecycle_retention_runs_skipped,
            "lifecycle_restore_drills_completed": lifecycle_restore_drills_completed,
            "lifecycle_restore_drills_skipped": lifecycle_restore_drills_skipped,
            "team_execution_loop_jobs_enqueued": team_execution_loop_jobs_enqueued,
            "team_execution_loop_jobs_skipped": team_execution_loop_jobs_skipped,
            "team_execution_loop_skip_reasons": dict(team_execution_loop_skip_reasons),
            "task_events_published": task_events_published,
            "task_event_publish_failures": task_event_publish_failures,
            "webhook_delivery_jobs_enqueued": webhook_delivery_jobs_enqueued,
            "webhook_delivery_jobs_skipped": webhook_delivery_jobs_skipped,
            "scheduled_job_actions_enqueued": scheduled_job_actions_enqueued,
            "scheduled_job_actions_recorded": scheduled_job_actions_recorded,
            "scheduled_job_actions_skipped": scheduled_job_actions_skipped,
            "scheduled_job_actions_enqueued_by_job_type": dict(
                scheduled_job_actions_enqueued_by_job_type
            ),
            "scheduled_job_actions_recorded_by_job_type": dict(
                scheduled_job_actions_recorded_by_job_type
            ),
            "scheduled_job_actions_skipped_by_job_type": dict(
                scheduled_job_actions_skipped_by_job_type
            ),
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
                        "priority": job.priority,
                        "requested_by_user_id": str(job.requested_by_user_id)
                        if job.requested_by_user_id is not None
                        else None,
                        "requested_by_agent_run_id": str(job.requested_by_agent_run_id)
                        if job.requested_by_agent_run_id is not None
                        else None,
                        "routing": dict(job.routing),
                        **(job.trace_metadata() or current_trace_metadata()),
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
            lease_metadata = job.trace_metadata() or current_trace_metadata()
            lease_metadata.update(metadata or {})
            with self._session_scope() as session:
                OperationsService(session).finish_worker_lease(
                    job_id=job.job_id,
                    status=status,
                    metadata=lease_metadata,
                )
        except Exception:
            logger.exception("Failed to finish worker lease")

    def _record_team_execution_loop_failure(
        self,
        job: JobPayload,
        *,
        status: str,
        error: BaseException,
    ) -> None:
        if job.job_type != JobType.TEAM_EXECUTION_LOOP:
            return
        try:
            with self._session_scope() as session:
                TeamRuntimeService(session).record_worker_failure(
                    workspace_id=job.workspace_id,
                    team_id=job.resource_id,
                    worker_id=self._config.worker_id,
                    queue_name=self._config.queue_name,
                    job_id=job.job_id,
                    status=status,
                    attempt=job.attempt,
                    max_attempts=job.max_attempts,
                    will_retry=job.can_retry,
                    error=error,
                    actor_user_id=job.requested_by_user_id,
                    routing=job.routing,
                    trace_metadata=job.trace_metadata() or current_trace_metadata(),
                )
        except Exception:
            logger.exception("Failed to record team execution loop worker failure")

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


def _worker_can_run_job(job: JobPayload, capacity: dict[str, object]) -> bool:
    routing = job.routing
    if not routing:
        return True
    required_worker_types = _string_set(routing.get("worker_types"))
    worker_type = _capacity_string(capacity, "worker_type")
    if required_worker_types and worker_type not in required_worker_types:
        return False
    required_capabilities = _string_set(routing.get("capabilities"))
    worker_capabilities = _string_set(capacity.get("capabilities"))
    if required_capabilities and not required_capabilities <= worker_capabilities:
        return False
    required_runtime_modes = _string_set(routing.get("runtime_modes"))
    worker_runtime_modes = _string_set(capacity.get("runtime_modes"))
    if required_runtime_modes and not required_runtime_modes <= worker_runtime_modes:
        return False
    resource_requirements = _dict(routing.get("resource_requirements"))
    for key, required_value in resource_requirements.items():
        if _positive_number(capacity.get(key)) < _positive_number(required_value):
            return False
    return True


def _merge_counts(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        if value <= 0:
            continue
        target[key] = target.get(key, 0) + value


def _capacity_string(capacity: dict[str, object], key: str) -> str:
    value = capacity.get(key)
    return value if isinstance(value, str) else ""


def _dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _positive_number(value: object) -> float:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float) and value > 0:
        return float(value)
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return 0
        return parsed if parsed > 0 else 0
    return 0


def _string_set(value: object) -> set[str]:
    if isinstance(value, str) and value:
        return {value}
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str) and item}
