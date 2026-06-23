from __future__ import annotations

from datetime import datetime

from backend.app.api.schemas.operation_queue import (
    QueueGovernanceIssueResponse,
    QueueGovernanceReconcileAction,
)
from backend.app.operations.queue_governance_issue_refs import (
    job_ids,
    job_resource_ids,
    run_ids,
)
from backend.app.operations.queue_governance_snapshot_models import QueueGovernanceSnapshot
from backend.app.operations.queue_governance_time import aware_datetime, oldest_age_seconds
from backend.app.runs.models import AgentRun
from backend.app.workers.jobs import JobPayload


def queue_governance_issues(
    snapshot: QueueGovernanceSnapshot,
) -> list[QueueGovernanceIssueResponse]:
    issues: list[QueueGovernanceIssueResponse] = []
    if snapshot.truncated:
        issues.append(
            QueueGovernanceIssueResponse(
                code="queue_scan_truncated",
                severity="warning",
                message="Queue scan did not cover every workspace job; increase scan_limit.",
                count=max(0, snapshot.queued_total - snapshot.queued_scanned),
                metadata={"scan_limit": snapshot.scan_limit},
            )
        )
    issues.extend(_job_issue_responses(snapshot))
    if snapshot.missing_runs:
        issues.append(
            QueueGovernanceIssueResponse(
                code="queued_runs_missing_queue_job",
                severity="critical",
                message="Queued runs are missing from the worker queue.",
                count=len(snapshot.missing_runs),
                resource_ids=run_ids(snapshot.missing_runs),
                oldest_age_seconds=_oldest_run_age(snapshot.generated_at, snapshot.missing_runs),
            )
        )
    if snapshot.dead_letter_total > 0:
        issues.append(
            QueueGovernanceIssueResponse(
                code="dead_letter_pressure",
                severity="warning",
                message="Dead-letter jobs exist and should be inspected before bulk recovery.",
                count=snapshot.dead_letter_total,
            )
        )
    return issues


def recommended_actions(
    snapshot: QueueGovernanceSnapshot,
) -> list[QueueGovernanceReconcileAction]:
    actions: list[QueueGovernanceReconcileAction] = []
    if snapshot.missing_runs:
        actions.append("requeue_missing_runs")
    if snapshot.orphaned_jobs:
        actions.append("remove_orphaned_jobs")
    if snapshot.non_runnable_jobs:
        actions.append("remove_non_runnable_jobs")
    return actions


def _job_issue_responses(
    snapshot: QueueGovernanceSnapshot,
) -> list[QueueGovernanceIssueResponse]:
    definitions = [
        (
            snapshot.orphaned_jobs,
            "orphaned_queue_jobs",
            "Queued agent run jobs reference runs that no longer exist.",
        ),
        (
            snapshot.non_runnable_jobs,
            "non_runnable_queue_jobs",
            "Queued agent run jobs reference runs that are not in queued status.",
        ),
        (
            snapshot.duplicate_jobs,
            "duplicate_queue_jobs",
            "Multiple queued jobs reference the same agent run.",
        ),
        (
            snapshot.old_queued_jobs,
            "old_queued_jobs",
            "Queued jobs have waited longer than the governance threshold.",
        ),
    ]
    return [
        QueueGovernanceIssueResponse(
            code=code,
            severity="warning",
            message=message,
            count=len(jobs),
            resource_ids=job_resource_ids(jobs),
            job_ids=job_ids(jobs),
            oldest_age_seconds=_oldest_job_age(snapshot.generated_at, jobs),
        )
        for jobs, code, message in definitions
        if jobs
    ]


def _oldest_job_age(now: datetime, jobs: list[JobPayload]) -> int | None:
    return oldest_age_seconds(now, [aware_datetime(job.created_at) for job in jobs])


def _oldest_run_age(now: datetime, runs: list[AgentRun]) -> int | None:
    return oldest_age_seconds(now, [aware_datetime(run.updated_at) for run in runs])
