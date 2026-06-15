from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from backend.app.runs.models import AgentRun
from backend.app.workers.jobs import JobPayload


@dataclass(frozen=True)
class QueueGovernanceSnapshot:
    generated_at: datetime
    queue_name: str
    scan_limit: int
    stale_after_seconds: int
    queued_total: int
    queued_scanned: int
    agent_run_jobs: list[JobPayload]
    dead_letter_total: int
    orphaned_jobs: list[JobPayload]
    non_runnable_jobs: list[JobPayload]
    duplicate_jobs: list[JobPayload]
    missing_runs: list[AgentRun]
    old_queued_jobs: list[JobPayload]
    truncated: bool


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
