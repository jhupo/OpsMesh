from __future__ import annotations

from dataclasses import dataclass, field

from backend.app.workers.capacity import merge_counts
from backend.app.workers.heartbeat import worker_heartbeat_details
from backend.app.workers.maintenance import WorkerMaintenanceSummary
from backend.app.workers.runner_models import WorkerRunnerConfig, WorkerRunSummary


@dataclass
class WorkerRunState:
    processed: int = 0
    failed: int = 0
    idle_polls: int = 0
    recovered_runs: int = 0
    expired_leases: int = 0
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
    last_error: str | None = None

    @property
    def attempts(self) -> int:
        return self.processed + self.failed

    def record_maintenance(self, maintenance: WorkerMaintenanceSummary) -> None:
        self.recovered_runs += maintenance.recovered_runs
        self.expired_leases += maintenance.expired_leases
        self.stale_runtimes += maintenance.stale_runtimes
        self.deleted_runtime_records += maintenance.deleted_runtime_records
        self.lifecycle_backup_jobs_enqueued += maintenance.lifecycle_backup_jobs_enqueued
        self.lifecycle_backup_jobs_skipped += maintenance.lifecycle_backup_jobs_skipped
        self.lifecycle_retention_runs_applied += maintenance.lifecycle_retention_runs_applied
        self.lifecycle_retention_runs_skipped += maintenance.lifecycle_retention_runs_skipped
        self.lifecycle_restore_drills_completed += maintenance.lifecycle_restore_drills_completed
        self.lifecycle_restore_drills_skipped += maintenance.lifecycle_restore_drills_skipped
        self.team_execution_loop_jobs_enqueued += maintenance.team_execution_loop_jobs_enqueued
        self.team_execution_loop_jobs_skipped += maintenance.team_execution_loop_jobs_skipped
        merge_counts(
            self.team_execution_loop_skip_reasons,
            maintenance.team_execution_loop_skip_reasons,
        )
        self.task_events_published += maintenance.task_events_published
        self.task_event_publish_failures += maintenance.task_event_publish_failures
        self.webhook_delivery_jobs_enqueued += maintenance.webhook_delivery_jobs_enqueued
        self.webhook_delivery_jobs_skipped += maintenance.webhook_delivery_jobs_skipped
        self.scheduled_job_actions_enqueued += maintenance.scheduled_job_actions_enqueued
        self.scheduled_job_actions_recorded += maintenance.scheduled_job_actions_recorded
        self.scheduled_job_actions_skipped += maintenance.scheduled_job_actions_skipped
        merge_counts(
            self.scheduled_job_actions_enqueued_by_job_type,
            maintenance.scheduled_job_actions_enqueued_by_job_type,
        )
        merge_counts(
            self.scheduled_job_actions_recorded_by_job_type,
            maintenance.scheduled_job_actions_recorded_by_job_type,
        )
        merge_counts(
            self.scheduled_job_actions_skipped_by_job_type,
            maintenance.scheduled_job_actions_skipped_by_job_type,
        )
        self.audit_integrity_workspaces_checked += (
            maintenance.audit_integrity_workspaces_checked
        )
        self.audit_integrity_workspaces_invalid += (
            maintenance.audit_integrity_workspaces_invalid
        )

    def heartbeat_details(self, config: WorkerRunnerConfig) -> dict[str, object]:
        return worker_heartbeat_details(
            config=config,
            processed=self.processed,
            failed=self.failed,
            idle_polls=self.idle_polls,
            recovered_runs=self.recovered_runs,
            expired_leases=self.expired_leases,
            stale_runtimes=self.stale_runtimes,
            deleted_runtime_records=self.deleted_runtime_records,
            lifecycle_backup_jobs_enqueued=self.lifecycle_backup_jobs_enqueued,
            lifecycle_backup_jobs_skipped=self.lifecycle_backup_jobs_skipped,
            lifecycle_retention_runs_applied=self.lifecycle_retention_runs_applied,
            lifecycle_retention_runs_skipped=self.lifecycle_retention_runs_skipped,
            lifecycle_restore_drills_completed=self.lifecycle_restore_drills_completed,
            lifecycle_restore_drills_skipped=self.lifecycle_restore_drills_skipped,
            team_execution_loop_jobs_enqueued=self.team_execution_loop_jobs_enqueued,
            team_execution_loop_jobs_skipped=self.team_execution_loop_jobs_skipped,
            team_execution_loop_skip_reasons=self.team_execution_loop_skip_reasons,
            task_events_published=self.task_events_published,
            task_event_publish_failures=self.task_event_publish_failures,
            webhook_delivery_jobs_enqueued=self.webhook_delivery_jobs_enqueued,
            webhook_delivery_jobs_skipped=self.webhook_delivery_jobs_skipped,
            scheduled_job_actions_enqueued=self.scheduled_job_actions_enqueued,
            scheduled_job_actions_recorded=self.scheduled_job_actions_recorded,
            scheduled_job_actions_skipped=self.scheduled_job_actions_skipped,
            scheduled_job_actions_enqueued_by_job_type=self.scheduled_job_actions_enqueued_by_job_type,
            scheduled_job_actions_recorded_by_job_type=self.scheduled_job_actions_recorded_by_job_type,
            scheduled_job_actions_skipped_by_job_type=self.scheduled_job_actions_skipped_by_job_type,
            audit_integrity_workspaces_checked=self.audit_integrity_workspaces_checked,
            audit_integrity_workspaces_invalid=self.audit_integrity_workspaces_invalid,
            last_error=self.last_error,
        )

    def summary(self, *, stopped: bool) -> WorkerRunSummary:
        return WorkerRunSummary(
            processed=self.processed,
            failed=self.failed,
            idle_polls=self.idle_polls,
            recovered_runs=self.recovered_runs,
            expired_leases=self.expired_leases,
            stale_runtimes=self.stale_runtimes,
            deleted_runtime_records=self.deleted_runtime_records,
            lifecycle_backup_jobs_enqueued=self.lifecycle_backup_jobs_enqueued,
            lifecycle_backup_jobs_skipped=self.lifecycle_backup_jobs_skipped,
            lifecycle_retention_runs_applied=self.lifecycle_retention_runs_applied,
            lifecycle_retention_runs_skipped=self.lifecycle_retention_runs_skipped,
            lifecycle_restore_drills_completed=self.lifecycle_restore_drills_completed,
            lifecycle_restore_drills_skipped=self.lifecycle_restore_drills_skipped,
            team_execution_loop_jobs_enqueued=self.team_execution_loop_jobs_enqueued,
            team_execution_loop_jobs_skipped=self.team_execution_loop_jobs_skipped,
            team_execution_loop_skip_reasons=self.team_execution_loop_skip_reasons,
            task_events_published=self.task_events_published,
            task_event_publish_failures=self.task_event_publish_failures,
            webhook_delivery_jobs_enqueued=self.webhook_delivery_jobs_enqueued,
            webhook_delivery_jobs_skipped=self.webhook_delivery_jobs_skipped,
            scheduled_job_actions_enqueued=self.scheduled_job_actions_enqueued,
            scheduled_job_actions_recorded=self.scheduled_job_actions_recorded,
            scheduled_job_actions_skipped=self.scheduled_job_actions_skipped,
            scheduled_job_actions_enqueued_by_job_type=self.scheduled_job_actions_enqueued_by_job_type,
            scheduled_job_actions_recorded_by_job_type=self.scheduled_job_actions_recorded_by_job_type,
            scheduled_job_actions_skipped_by_job_type=self.scheduled_job_actions_skipped_by_job_type,
            audit_integrity_workspaces_checked=self.audit_integrity_workspaces_checked,
            audit_integrity_workspaces_invalid=self.audit_integrity_workspaces_invalid,
            stopped=stopped,
        )
