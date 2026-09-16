from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.domains.agents.memory.embedding_scheduler import WorkspaceMemoryEmbeddingScheduler
from backend.app.domains.agents.memory.lifecycle import WorkspaceMemoryLifecycleService
from backend.app.domains.integrations.webhooks.scheduler import WebhookDeliveryScheduler
from backend.app.domains.orchestration.approvals.lifecycle import AgentToolApprovalLifecycleService
from backend.app.domains.orchestration.runs.control import RunControlService
from backend.app.domains.orchestration.runs.service import RunOrchestrationService
from backend.app.domains.orchestration.tasks.event_outbox import TaskEventOutboxPublisher
from backend.app.domains.orchestration.tasks.events import RedisTaskEventBus
from backend.app.domains.workspace.data_lifecycle.service import WorkspaceDataLifecycleService
from backend.app.domains.workspace.storage.storage import create_storage
from backend.app.domains.workspace.teams.execution.loop import TeamExecutionLoopQueueService
from backend.app.domains.workspace.tenants.health.service import WorkspaceHealthService
from backend.app.observability.audit.integrity import AuditIntegrityService
from backend.app.runtime.environment.backends.factory import build_runtime_backend_registry
from backend.app.runtime.environment.cleanup_jobs import RuntimeCleanupService
from backend.app.runtime.environment.contracts import DockerRuntimeClient
from backend.app.runtime.workers.contracts import JobPayload
from backend.app.runtime.workers.leases import WorkerLeaseMaintenanceService
from backend.app.runtime.workers.queue import RedisQueue
from backend.app.runtime.workers.recovery.rehydration import QueueRehydrationService
from backend.app.runtime.workers.scheduling.service import WorkspaceScheduledJobService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerMaintenanceConfig:
    run_lease_seconds: int
    recovery_batch_size: int


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
    audit_integrity_workspaces_checked: int = 0
    audit_integrity_workspaces_invalid: int = 0
    expired_tool_approvals: int = 0
    memory_embedding_jobs_enqueued: int = 0
    memory_embedding_jobs_already_queued: int = 0
    memory_embedding_jobs_recovered: int = 0
    memory_episodes_archived: int = 0
    memory_semantic_archived: int = 0
    memory_episodes_promoted: int = 0
    workspace_health_snapshots_created: int = 0
    workspace_health_snapshots_skipped: int = 0
    workspace_health_snapshots_disabled: int = 0
    project_runtime_workspaces_cleaned: int = 0
    project_runtime_workspaces_failed: int = 0
    run_runtime_environments_cleaned: int = 0
    run_runtime_environments_failed: int = 0
    queue_rehydrated_runs: int = 0
    queue_recovery_failures: int = 0
    last_error: str | None = None


class WorkerMaintenanceService:
    def __init__(
        self,
        *,
        queue: RedisQueue,
        session_factory: Callable[[], Session],
        config: WorkerMaintenanceConfig,
        settings: Settings | None = None,
        runtime_docker_client: DockerRuntimeClient | None = None,
    ) -> None:
        self._queue = queue
        self._session_factory = session_factory
        self._config = config
        self._settings = settings
        self._runtime_docker_client = runtime_docker_client
        self._runtime_backends = build_runtime_backend_registry(runtime_docker_client)

    def run(self) -> WorkerMaintenanceSummary:
        try:
            self._queue.reclaim_due_retries(limit=self._config.recovery_batch_size)
            reclaimed_jobs = self._queue.reclaim_expired(limit=self._config.recovery_batch_size)
            with self._session_scope() as session:
                return self._run_database_maintenance(session, reclaimed_jobs=reclaimed_jobs)
        except Exception as exc:
            logger.exception("Failed to run worker maintenance")
            return WorkerMaintenanceSummary(
                recovered_runs=0,
                expired_leases=0,
                last_error=str(exc),
            )

    def _run_database_maintenance(
        self,
        session: Session,
        *,
        reclaimed_jobs: list[JobPayload] | None = None,
    ) -> WorkerMaintenanceSummary:
        reclaimed_job_ids = [job.job_id for job in (reclaimed_jobs or [])]
        reclaimed_expired_leases = WorkerLeaseMaintenanceService(
            session
        ).expire_worker_leases_for_jobs(job_ids=reclaimed_job_ids)
        run_orchestration = RunOrchestrationService(session, queue=self._queue)
        recovery = RunControlService(
            session=session,
            enqueue_run=run_orchestration.enqueue_run,
        ).recover_stale_worker_runs(
            stale_after_seconds=self._config.run_lease_seconds,
            limit=self._config.recovery_batch_size,
            reason="worker_maintenance",
        )
        queue_rehydration = QueueRehydrationService(session, self._queue).rehydrate_queued_runs(
            limit=self._config.recovery_batch_size,
        )
        expired_leases = WorkerLeaseMaintenanceService(session).expire_stale_worker_leases(
            stale_after_seconds=self._config.run_lease_seconds,
        )
        expired_tool_approvals = AgentToolApprovalLifecycleService(session).expire_pending(
            timeout_seconds=(
                self._settings.agent_tool_approval_timeout_seconds
                if self._settings is not None
                else 86_400
            ),
            limit=self._config.recovery_batch_size,
        )
        stale_runtimes, deleted_runtime_records = RuntimeCleanupService(
            session,
        ).cleanup_stale_runtimes_across_workspaces(
            stale_after_seconds=self._config.run_lease_seconds,
        )
        project_runtime_workspaces_cleaned, project_runtime_workspaces_failed = (
            RuntimeCleanupService(session).cleanup_terminal_run_workspaces(
                settings=self._settings,
                docker_client=self._runtime_docker_client,
                runtime_backends=self._runtime_backends,
                limit=self._config.recovery_batch_size,
            )
        )
        run_runtime_environments_cleaned, run_runtime_environments_failed = (
            RuntimeCleanupService(session).cleanup_terminal_run_environments(
                docker_client=self._runtime_docker_client,
                limit=self._config.recovery_batch_size,
            )
        )
        lifecycle_summary = WorkspaceDataLifecycleService(session).run_scheduled_lifecycle(
            queue=self._queue,
            storage=create_storage(self._settings) if self._settings is not None else None,
            limit=self._config.recovery_batch_size,
        )
        team_loop_summary = TeamExecutionLoopQueueService(session).enqueue_active_team_iterations(
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
        audit_integrity = AuditIntegrityService(session).run_due(
            interval_seconds=(
                self._settings.audit_integrity_check_interval_seconds
                if self._settings is not None
                else 3_600
            ),
            limit=self._config.recovery_batch_size,
        )
        memory_lifecycle = WorkspaceMemoryLifecycleService(session).run_due(
            limit=self._config.recovery_batch_size,
        )
        memory_embeddings = WorkspaceMemoryEmbeddingScheduler(session).enqueue_pending(
            queue=self._queue,
            limit=self._config.recovery_batch_size,
        )
        health_snapshots = WorkspaceHealthService(session).run_scheduled_snapshots(
            limit=self._config.recovery_batch_size,
        )
        return WorkerMaintenanceSummary(
            recovered_runs=recovery.recovered_runs,
            expired_leases=expired_leases + reclaimed_expired_leases,
            stale_runtimes=stale_runtimes,
            deleted_runtime_records=deleted_runtime_records,
            lifecycle_backup_jobs_enqueued=lifecycle_summary.backup_jobs_enqueued,
            lifecycle_backup_jobs_skipped=lifecycle_summary.backup_jobs_skipped,
            lifecycle_retention_runs_applied=lifecycle_summary.retention_runs_applied,
            lifecycle_retention_runs_skipped=lifecycle_summary.retention_runs_skipped,
            lifecycle_restore_drills_completed=lifecycle_summary.restore_drills_completed,
            lifecycle_restore_drills_skipped=lifecycle_summary.restore_drills_skipped,
            team_execution_loop_jobs_enqueued=team_loop_summary.enqueued,
            team_execution_loop_jobs_skipped=team_loop_summary.skipped,
            team_execution_loop_skip_reasons=team_loop_summary.skipped_reasons,
            task_events_published=task_event_summary.published,
            task_event_publish_failures=task_event_summary.failed,
            webhook_delivery_jobs_enqueued=webhook_delivery_summary.enqueued,
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
            audit_integrity_workspaces_checked=audit_integrity.checked_workspaces,
            audit_integrity_workspaces_invalid=audit_integrity.invalid_workspaces,
            expired_tool_approvals=expired_tool_approvals.expired,
            memory_embedding_jobs_enqueued=memory_embeddings.enqueued,
            memory_embedding_jobs_already_queued=memory_embeddings.already_queued,
            memory_embedding_jobs_recovered=memory_embeddings.recovered,
            memory_episodes_archived=memory_lifecycle.archived_episodes,
            memory_semantic_archived=memory_lifecycle.archived_semantic,
            memory_episodes_promoted=memory_lifecycle.promoted_episodes,
            workspace_health_snapshots_created=health_snapshots.snapshots_created,
            workspace_health_snapshots_skipped=health_snapshots.snapshots_skipped,
            workspace_health_snapshots_disabled=health_snapshots.snapshots_disabled,
            project_runtime_workspaces_cleaned=project_runtime_workspaces_cleaned,
            project_runtime_workspaces_failed=project_runtime_workspaces_failed,
            run_runtime_environments_cleaned=run_runtime_environments_cleaned,
            run_runtime_environments_failed=run_runtime_environments_failed,
            queue_rehydrated_runs=queue_rehydration.requeued_runs,
            queue_recovery_failures=queue_rehydration.failed_runs,
        )

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
