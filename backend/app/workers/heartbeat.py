from __future__ import annotations

from backend.app.workers.runner_models import WorkerRunnerConfig


def worker_status_for_failures(failed: int) -> str:
    return "degraded" if failed > 0 else "online"


def worker_heartbeat_details(
    *,
    config: WorkerRunnerConfig,
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
    audit_integrity_workspaces_checked: int,
    audit_integrity_workspaces_invalid: int,
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
        "audit_integrity_workspaces_checked": audit_integrity_workspaces_checked,
        "audit_integrity_workspaces_invalid": audit_integrity_workspaces_invalid,
        "capacity": {
            "max_jobs": config.max_jobs,
        },
    }
    if last_error is not None:
        details["last_error"] = last_error
    return details
