from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import (
    DeadLetterJobsResponse,
    OperationsQueueInsightsResponse,
    QueueGovernanceDiagnosticsResponse,
    QueueGovernanceIssueResponse,
    QueueGovernanceReconcileAction,
    QueueGovernanceReconcileResponse,
    QueueJobTypeBucketResponse,
    QueuePriorityBucketResponse,
)
from backend.app.audit.service import AuditService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue


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


class QueueGovernanceService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None = None,
        key_builder: RedisKeyBuilder | None = None,
    ) -> None:
        self._session = session
        self._redis = redis
        self._keys = key_builder or RedisKeyBuilder("opsmesh")

    def queue_insights(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        scan_limit: int = 500,
    ) -> OperationsQueueInsightsResponse:
        now = datetime.now(UTC)
        scan_limit = max(1, min(scan_limit, 5_000))
        if self._redis is None:
            return OperationsQueueInsightsResponse(
                generated_at=now,
                queue_name=queue_name,
                scan_limit=scan_limit,
                queued_total=0,
                dead_letter_total=0,
                queued_scanned=0,
                dead_letter_scanned=0,
                truncated=False,
                oldest_queued_age_seconds=None,
                highest_priority=None,
                priority_buckets=[],
                job_type_buckets=[],
            )
        queue = RedisQueue(self._redis, self._keys, queue_name)
        queued_total = queue.count_queued(workspace_id=workspace_id)
        dead_letter_total = queue.count_dead_letters(workspace_id=workspace_id)
        queued_jobs = [
            job for job in queue.peek(limit=scan_limit) if job.workspace_id == workspace_id
        ]
        dead_letter_jobs = queue.list_dead_letters(scan_limit, workspace_id=workspace_id)
        oldest_queued_age_seconds: int | None = None
        highest_priority: int | None = None
        priority_buckets: dict[int, dict[str, int | None]] = {}
        job_type_buckets: dict[str, dict[str, int | None]] = {}

        for job in queued_jobs:
            age_seconds = max(0, int((now - aware_datetime(job.created_at)).total_seconds()))
            oldest_queued_age_seconds = max_optional_int(oldest_queued_age_seconds, age_seconds)
            highest_priority = max_optional_int(highest_priority, job.priority)

            priority_bucket = priority_buckets.setdefault(
                job.priority,
                {"queued": 0, "dead_letter": 0, "oldest_queued_age_seconds": None},
            )
            priority_bucket["queued"] = int(priority_bucket["queued"] or 0) + 1
            priority_bucket["oldest_queued_age_seconds"] = max_optional_int(
                priority_bucket["oldest_queued_age_seconds"],
                age_seconds,
            )

            job_type = str(job.job_type)
            type_bucket = job_type_buckets.setdefault(
                job_type,
                {
                    "queued": 0,
                    "dead_letter": 0,
                    "highest_priority": None,
                    "oldest_queued_age_seconds": None,
                },
            )
            type_bucket["queued"] = int(type_bucket["queued"] or 0) + 1
            type_bucket["highest_priority"] = max_optional_int(
                type_bucket["highest_priority"],
                job.priority,
            )
            type_bucket["oldest_queued_age_seconds"] = max_optional_int(
                type_bucket["oldest_queued_age_seconds"],
                age_seconds,
            )

        for job in dead_letter_jobs:
            priority_bucket = priority_buckets.setdefault(
                job.priority,
                {"queued": 0, "dead_letter": 0, "oldest_queued_age_seconds": None},
            )
            priority_bucket["dead_letter"] = int(priority_bucket["dead_letter"] or 0) + 1

            job_type = str(job.job_type)
            type_bucket = job_type_buckets.setdefault(
                job_type,
                {
                    "queued": 0,
                    "dead_letter": 0,
                    "highest_priority": None,
                    "oldest_queued_age_seconds": None,
                },
            )
            type_bucket["dead_letter"] = int(type_bucket["dead_letter"] or 0) + 1

        return OperationsQueueInsightsResponse(
            generated_at=now,
            queue_name=queue_name,
            scan_limit=scan_limit,
            queued_total=queued_total,
            dead_letter_total=dead_letter_total,
            queued_scanned=len(queued_jobs),
            dead_letter_scanned=len(dead_letter_jobs),
            truncated=queued_total > len(queued_jobs) or dead_letter_total > len(dead_letter_jobs),
            oldest_queued_age_seconds=oldest_queued_age_seconds,
            highest_priority=highest_priority,
            priority_buckets=[
                QueuePriorityBucketResponse(
                    priority=priority,
                    queued=int(counts["queued"] or 0),
                    dead_letter=int(counts["dead_letter"] or 0),
                    oldest_queued_age_seconds=cast(
                        int | None,
                        counts["oldest_queued_age_seconds"],
                    ),
                )
                for priority, counts in sorted(priority_buckets.items(), reverse=True)
            ],
            job_type_buckets=[
                QueueJobTypeBucketResponse(
                    job_type=job_type,
                    queued=int(counts["queued"] or 0),
                    dead_letter=int(counts["dead_letter"] or 0),
                    highest_priority=cast(int | None, counts["highest_priority"]),
                    oldest_queued_age_seconds=cast(
                        int | None,
                        counts["oldest_queued_age_seconds"],
                    ),
                )
                for job_type, counts in sorted(job_type_buckets.items())
            ],
        )

    def queue_governance(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        scan_limit: int = 500,
        stale_after_seconds: int = 900,
    ) -> QueueGovernanceDiagnosticsResponse:
        snapshot = self._queue_governance_snapshot(
            workspace_id=workspace_id,
            queue_name=queue_name,
            scan_limit=scan_limit,
            stale_after_seconds=stale_after_seconds,
        )
        return self._queue_governance_response(snapshot)

    def reconcile_queue_governance(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        queue_name: str,
        scan_limit: int,
        stale_after_seconds: int,
        actions: list[QueueGovernanceReconcileAction],
        max_items: int,
        reason: str | None = None,
    ) -> QueueGovernanceReconcileResponse:
        snapshot = self._queue_governance_snapshot(
            workspace_id=workspace_id,
            queue_name=queue_name,
            scan_limit=scan_limit,
            stale_after_seconds=stale_after_seconds,
        )
        queue = RedisQueue(self._redis, self._keys, queue_name) if self._redis is not None else None
        run_service = RunOrchestrationService(self._session, queue=queue)

        remaining = max_items
        requeued_missing_runs = 0
        removed_orphaned_jobs = 0
        removed_non_runnable_jobs = 0
        skipped_items = 0

        if queue is not None and "requeue_missing_runs" in actions:
            for run in snapshot.missing_runs[:remaining]:
                if run_service.enqueue_run(run, actor_user_id, force=True):
                    requeued_missing_runs += 1
                    remaining -= 1
                else:
                    skipped_items += 1
                if remaining <= 0:
                    break

        if queue is not None and remaining > 0 and "remove_orphaned_jobs" in actions:
            for job in snapshot.orphaned_jobs[:remaining]:
                removed = queue.remove_queued_job(job.job_id, workspace_id=workspace_id)
                if removed is not None:
                    removed_orphaned_jobs += 1
                    remaining -= 1
                else:
                    skipped_items += 1
                if remaining <= 0:
                    break

        if queue is not None and remaining > 0 and "remove_non_runnable_jobs" in actions:
            for job in snapshot.non_runnable_jobs[:remaining]:
                removed = queue.remove_queued_job(job.job_id, workspace_id=workspace_id)
                if removed is not None:
                    removed_non_runnable_jobs += 1
                    remaining -= 1
                else:
                    skipped_items += 1
                if remaining <= 0:
                    break

        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="operations.queue_governance_reconciled",
            target_type="workspace",
            target_id=workspace_id,
            metadata={
                "queue_name": queue_name,
                "scan_limit": scan_limit,
                "stale_after_seconds": stale_after_seconds,
                "actions": list(actions),
                "max_items": max_items,
                "reason": non_empty_string_or_none(reason),
                "scanned_jobs": snapshot.queued_scanned,
                "requeued_missing_runs": requeued_missing_runs,
                "removed_orphaned_jobs": removed_orphaned_jobs,
                "removed_non_runnable_jobs": removed_non_runnable_jobs,
                "skipped_items": skipped_items,
            },
        )
        self._session.commit()

        remaining_snapshot = self._queue_governance_snapshot(
            workspace_id=workspace_id,
            queue_name=queue_name,
            scan_limit=scan_limit,
            stale_after_seconds=stale_after_seconds,
        )
        return QueueGovernanceReconcileResponse(
            workspace_id=workspace_id,
            queue_name=queue_name,
            scanned_jobs=snapshot.queued_scanned,
            actions=list(actions),
            requeued_missing_runs=requeued_missing_runs,
            removed_orphaned_jobs=removed_orphaned_jobs,
            removed_non_runnable_jobs=removed_non_runnable_jobs,
            skipped_items=skipped_items,
            remaining_issues=self._queue_governance_issues(remaining_snapshot),
        )

    def list_dead_letters(
        self,
        workspace_id: UUID,
        queue_name: str,
        limit: int,
    ) -> DeadLetterJobsResponse:
        if self._redis is None:
            return DeadLetterJobsResponse(items=[], total=0)
        queue = RedisQueue(self._redis, self._keys, queue_name)
        items = queue.list_dead_letters(limit, workspace_id=workspace_id)
        total = queue.count_dead_letters(workspace_id=workspace_id)
        return DeadLetterJobsResponse(items=items, total=total)

    def requeue_dead_letter(
        self,
        workspace_id: UUID,
        queue_name: str,
        job_id: UUID,
    ) -> JobPayload | None:
        if self._redis is None:
            return None
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return queue.requeue_dead_letter(job_id, workspace_id=workspace_id)

    def _queue_governance_snapshot(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        scan_limit: int,
        stale_after_seconds: int,
    ) -> QueueGovernanceSnapshot:
        scan_limit = max(1, min(scan_limit, 5_000))
        stale_after_seconds = max(60, min(stale_after_seconds, 86_400))
        now = datetime.now(UTC)
        if self._redis is None:
            return QueueGovernanceSnapshot(
                generated_at=now,
                queue_name=queue_name,
                scan_limit=scan_limit,
                stale_after_seconds=stale_after_seconds,
                queued_total=0,
                queued_scanned=0,
                agent_run_jobs=[],
                dead_letter_total=0,
                orphaned_jobs=[],
                non_runnable_jobs=[],
                duplicate_jobs=[],
                missing_runs=[],
                old_queued_jobs=[],
                truncated=False,
            )

        queue = RedisQueue(self._redis, self._keys, queue_name)
        queued_total = queue.count_queued(workspace_id=workspace_id)
        dead_letter_total = queue.count_dead_letters(workspace_id=workspace_id)
        scanned_jobs = queue.peek(limit=scan_limit)
        workspace_jobs = [job for job in scanned_jobs if job.workspace_id == workspace_id]
        agent_run_jobs = [job for job in workspace_jobs if job.job_type == JobType.AGENT_RUN]
        truncated = queue.count_queued() > scan_limit or queued_total > len(workspace_jobs)

        run_ids = {job.resource_id for job in agent_run_jobs}
        runs_by_id: dict[UUID, AgentRun] = {}
        if run_ids:
            runs = self._session.scalars(
                select(AgentRun).where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.id.in_(run_ids),
                )
            ).all()
            runs_by_id = {run.id: run for run in runs}

        orphaned_jobs = [job for job in agent_run_jobs if job.resource_id not in runs_by_id]
        non_runnable_jobs = [
            job
            for job in agent_run_jobs
            if job.resource_id in runs_by_id
            and runs_by_id[job.resource_id].status != RunStatus.QUEUED.value
        ]

        jobs_by_run: dict[UUID, list[JobPayload]] = {}
        for job in agent_run_jobs:
            jobs_by_run.setdefault(job.resource_id, []).append(job)
        duplicate_jobs = [
            duplicate
            for jobs in jobs_by_run.values()
            if len(jobs) > 1
            for duplicate in jobs[1:]
        ]

        queued_run_ids_in_queue = {
            job.resource_id
            for job in agent_run_jobs
            if job.resource_id in runs_by_id
            and runs_by_id[job.resource_id].status == RunStatus.QUEUED.value
        }
        missing_runs: list[AgentRun] = []
        if not truncated:
            missing_runs = self._session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.status == RunStatus.QUEUED.value,
                )
                .order_by(AgentRun.updated_at.asc(), AgentRun.created_at.asc())
            ).all()
            missing_runs = [run for run in missing_runs if run.id not in queued_run_ids_in_queue]

        cutoff = now - timedelta(seconds=stale_after_seconds)
        old_queued_jobs = [
            job for job in workspace_jobs if aware_datetime(job.created_at) < cutoff
        ]

        return QueueGovernanceSnapshot(
            generated_at=now,
            queue_name=queue_name,
            scan_limit=scan_limit,
            stale_after_seconds=stale_after_seconds,
            queued_total=queued_total,
            queued_scanned=len(workspace_jobs),
            agent_run_jobs=agent_run_jobs,
            dead_letter_total=dead_letter_total,
            orphaned_jobs=orphaned_jobs,
            non_runnable_jobs=non_runnable_jobs,
            duplicate_jobs=duplicate_jobs,
            missing_runs=missing_runs,
            old_queued_jobs=old_queued_jobs,
            truncated=truncated,
        )

    def _queue_governance_response(
        self,
        snapshot: QueueGovernanceSnapshot,
    ) -> QueueGovernanceDiagnosticsResponse:
        return QueueGovernanceDiagnosticsResponse(
            generated_at=snapshot.generated_at,
            queue_name=snapshot.queue_name,
            scan_limit=snapshot.scan_limit,
            stale_after_seconds=snapshot.stale_after_seconds,
            queued_total=snapshot.queued_total,
            queued_scanned=snapshot.queued_scanned,
            agent_run_jobs_scanned=len(snapshot.agent_run_jobs),
            dead_letter_total=snapshot.dead_letter_total,
            orphaned_queue_jobs=len(snapshot.orphaned_jobs),
            non_runnable_queue_jobs=len(snapshot.non_runnable_jobs),
            duplicate_queue_jobs=len(snapshot.duplicate_jobs),
            queued_runs_missing_queue_job=len(snapshot.missing_runs),
            old_queued_jobs=len(snapshot.old_queued_jobs),
            truncated=snapshot.truncated,
            issues=self._queue_governance_issues(snapshot),
            recommended_actions=self._queue_governance_recommended_actions(snapshot),
        )

    def _queue_governance_issues(
        self,
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
        if snapshot.orphaned_jobs:
            issues.append(
                QueueGovernanceIssueResponse(
                    code="orphaned_queue_jobs",
                    severity="warning",
                    message="Queued agent run jobs reference runs that no longer exist.",
                    count=len(snapshot.orphaned_jobs),
                    resource_ids=job_resource_ids(snapshot.orphaned_jobs),
                    job_ids=job_ids(snapshot.orphaned_jobs),
                    oldest_age_seconds=oldest_job_age(
                        snapshot.generated_at,
                        snapshot.orphaned_jobs,
                    ),
                )
            )
        if snapshot.non_runnable_jobs:
            issues.append(
                QueueGovernanceIssueResponse(
                    code="non_runnable_queue_jobs",
                    severity="warning",
                    message="Queued agent run jobs reference runs that are not in queued status.",
                    count=len(snapshot.non_runnable_jobs),
                    resource_ids=job_resource_ids(snapshot.non_runnable_jobs),
                    job_ids=job_ids(snapshot.non_runnable_jobs),
                    oldest_age_seconds=oldest_job_age(
                        snapshot.generated_at,
                        snapshot.non_runnable_jobs,
                    ),
                )
            )
        if snapshot.duplicate_jobs:
            issues.append(
                QueueGovernanceIssueResponse(
                    code="duplicate_queue_jobs",
                    severity="warning",
                    message="Multiple queued jobs reference the same agent run.",
                    count=len(snapshot.duplicate_jobs),
                    resource_ids=job_resource_ids(snapshot.duplicate_jobs),
                    job_ids=job_ids(snapshot.duplicate_jobs),
                    oldest_age_seconds=oldest_job_age(
                        snapshot.generated_at,
                        snapshot.duplicate_jobs,
                    ),
                )
            )
        if snapshot.missing_runs:
            issues.append(
                QueueGovernanceIssueResponse(
                    code="queued_runs_missing_queue_job",
                    severity="critical",
                    message="Queued runs are missing from the worker queue.",
                    count=len(snapshot.missing_runs),
                    resource_ids=run_ids(snapshot.missing_runs),
                    oldest_age_seconds=oldest_run_age(
                        snapshot.generated_at,
                        snapshot.missing_runs,
                    ),
                )
            )
        if snapshot.old_queued_jobs:
            issues.append(
                QueueGovernanceIssueResponse(
                    code="old_queued_jobs",
                    severity="warning",
                    message="Queued jobs have waited longer than the governance threshold.",
                    count=len(snapshot.old_queued_jobs),
                    resource_ids=job_resource_ids(snapshot.old_queued_jobs),
                    job_ids=job_ids(snapshot.old_queued_jobs),
                    oldest_age_seconds=oldest_job_age(
                        snapshot.generated_at,
                        snapshot.old_queued_jobs,
                    ),
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

    def _queue_governance_recommended_actions(
        self,
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


def oldest_job_age(now: datetime, jobs: list[JobPayload]) -> int | None:
    ages = [max(0, int((now - aware_datetime(job.created_at)).total_seconds())) for job in jobs]
    return max(ages) if ages else None


def oldest_run_age(now: datetime, runs: list[AgentRun]) -> int | None:
    ages = [max(0, int((now - aware_datetime(run.updated_at)).total_seconds())) for run in runs]
    return max(ages) if ages else None


def max_optional_int(current: object, candidate: int) -> int:
    return candidate if not isinstance(current, int) else max(current, candidate)


def non_empty_string_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
