from __future__ import annotations

from dataclasses import dataclass, field


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
