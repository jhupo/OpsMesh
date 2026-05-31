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
from backend.app.capabilities.execution import McpToolAdapter, McpToolAdapterResolver
from backend.app.core.config import Settings
from backend.app.core.request_context import log_context
from backend.app.files.storage import LocalStorage
from backend.app.operations.service import OperationsService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.workers.handlers import WorkerJobHandler
from backend.app.workers.jobs import JobPayload
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
        return log_context(
            worker_id=self._config.worker_id,
            workspace_id=job.workspace_id,
            run_id=run_id,
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

    def run_maintenance(self) -> WorkerMaintenanceSummary:
        try:
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
                        LocalStorage(self._settings.storage_root)
                        if self._settings is not None
                        else None
                    ),
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
