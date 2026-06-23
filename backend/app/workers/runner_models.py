from __future__ import annotations

from dataclasses import dataclass


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
