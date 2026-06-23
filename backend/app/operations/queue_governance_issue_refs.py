from __future__ import annotations

from uuid import UUID

from backend.app.runs.models import AgentRun
from backend.app.workers.jobs import JobPayload


def job_ids(jobs: list[JobPayload], *, limit: int = 25) -> list[UUID]:
    return [job.job_id for job in jobs[:limit]]


def job_resource_ids(jobs: list[JobPayload], *, limit: int = 25) -> list[UUID]:
    seen: set[UUID] = set()
    resource_ids: list[UUID] = []
    for job in jobs:
        if job.resource_id in seen:
            continue
        seen.add(job.resource_id)
        resource_ids.append(job.resource_id)
        if len(resource_ids) >= limit:
            break
    return resource_ids


def run_ids(runs: list[AgentRun], *, limit: int = 25) -> list[UUID]:
    return [run.id for run in runs[:limit]]
