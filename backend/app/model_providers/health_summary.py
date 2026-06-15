from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.model_providers.models import ModelProviderCredential

_HEALTH_CHECK_SCHEDULE_JOB_LIMIT = 10

if TYPE_CHECKING:
    from backend.app.scheduled_jobs.models import WorkspaceScheduledJob


def model_provider_last_health_check_at(
    credential: ModelProviderCredential,
) -> datetime | None:
    timestamps = [
        value
        for value in (credential.last_success_at, credential.last_failure_at)
        if value is not None
    ]
    return max(timestamps) if timestamps else None


def model_provider_health_check_schedule_summary(
    session: Session,
    *,
    workspace_id: UUID,
    credential_id: UUID,
) -> dict[str, object]:
    from backend.app.scheduled_jobs.models import WorkspaceScheduledJob
    from backend.app.workers.jobs import JobType

    jobs = list(
        session.scalars(
            select(WorkspaceScheduledJob)
            .where(
                WorkspaceScheduledJob.workspace_id == workspace_id,
                WorkspaceScheduledJob.resource_id == credential_id,
                WorkspaceScheduledJob.job_type == JobType.MODEL_PROVIDER_HEALTH_CHECK.value,
            )
            .order_by(
                WorkspaceScheduledJob.status.asc(),
                WorkspaceScheduledJob.next_run_at.asc(),
                WorkspaceScheduledJob.created_at.desc(),
            )
            .limit(_HEALTH_CHECK_SCHEDULE_JOB_LIMIT)
        )
    )
    active_jobs = [job for job in jobs if job.status == "active"]
    active_next_runs = [job.next_run_at for job in active_jobs if job.next_run_at is not None]
    return {
        "configured": bool(jobs),
        "active_count": len(active_jobs),
        "paused_count": sum(1 for job in jobs if job.status == "paused"),
        "next_run_at": min(active_next_runs) if active_next_runs else None,
        "jobs": [health_check_schedule_job_summary(job) for job in jobs],
    }


def health_check_schedule_job_summary(job: WorkspaceScheduledJob) -> dict[str, object]:
    return {
        "id": job.id,
        "name": job.name,
        "status": job.status,
        "schedule_type": job.schedule_type,
        "next_run_at": job.next_run_at,
        "last_run_at": job.last_run_at,
        "routing": health_check_schedule_routing_summary(job.routing),
    }


def health_check_schedule_routing_summary(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    summary: dict[str, object] = {}
    probes = value.get("probes")
    if isinstance(probes, list) and all(isinstance(item, str) for item in probes):
        summary["probes"] = probes
    timeout_seconds = value.get("timeout_seconds")
    if isinstance(timeout_seconds, int | float) and not isinstance(timeout_seconds, bool):
        summary["timeout_seconds"] = timeout_seconds
    return summary
