from __future__ import annotations

from uuid import UUID

from backend.app.workers.jobs import JobPayload, JobType


def matches_job_filters(
    job: JobPayload,
    *,
    workspace_id: UUID | None,
    job_type: JobType | str | None,
    resource_id: UUID | None,
) -> bool:
    if workspace_id is not None and job.workspace_id != workspace_id:
        return False
    if job_type is not None and job.job_type != JobType(job_type):
        return False
    return resource_id is None or job.resource_id == resource_id
