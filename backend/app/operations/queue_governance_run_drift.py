from __future__ import annotations

from uuid import UUID

from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.workers.jobs import JobPayload


def orphaned_jobs(
    agent_run_jobs: list[JobPayload],
    runs_by_id: dict[UUID, AgentRun],
) -> list[JobPayload]:
    return [job for job in agent_run_jobs if job.resource_id not in runs_by_id]


def non_runnable_jobs(
    agent_run_jobs: list[JobPayload],
    runs_by_id: dict[UUID, AgentRun],
) -> list[JobPayload]:
    return [
        job
        for job in agent_run_jobs
        if job.resource_id in runs_by_id
        and runs_by_id[job.resource_id].status != RunStatus.QUEUED.value
    ]


def duplicate_jobs(agent_run_jobs: list[JobPayload]) -> list[JobPayload]:
    jobs_by_run: dict[UUID, list[JobPayload]] = {}
    for job in agent_run_jobs:
        jobs_by_run.setdefault(job.resource_id, []).append(job)
    return [duplicate for jobs in jobs_by_run.values() if len(jobs) > 1 for duplicate in jobs[1:]]


def queued_run_ids(
    agent_run_jobs: list[JobPayload],
    runs_by_id: dict[UUID, AgentRun],
) -> set[UUID]:
    return {
        job.resource_id
        for job in agent_run_jobs
        if job.resource_id in runs_by_id
        and runs_by_id[job.resource_id].status == RunStatus.QUEUED.value
    }
