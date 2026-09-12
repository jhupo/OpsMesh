from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations.queue import (
    QueueGovernanceDiagnosticsResponse,
    QueueGovernanceIssueResponse,
    QueueGovernanceReconcileAction,
    QueueGovernanceReconcileResponse,
)
from backend.app.core.common.values import non_empty_string_or_none
from backend.app.core.redis.keys import RedisKeyBuilder
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.runs.service import RunOrchestrationService
from backend.app.domains.orchestration.runs.status import RunStatus
from backend.app.observability.audit_service import AuditService
from backend.app.runtime.workers.jobs import JobPayload, JobType
from backend.app.runtime.workers.redis_queue import RedisQueue


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



def aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def oldest_age_seconds(now: datetime, values: list[datetime]) -> int | None:
    ages = [max(0, int((now - aware_datetime(value)).total_seconds())) for value in values]
    return max(ages) if ages else None


def max_optional_int(current: object, candidate: int) -> int:
    return candidate if not isinstance(current, int) else max(current, candidate)




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





class QueueGovernanceRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def runs_by_id(self, workspace_id: UUID, run_ids: set[UUID]) -> dict[UUID, AgentRun]:
        if not run_ids:
            return {}
        runs = self._session.scalars(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.id.in_(run_ids),
            )
        ).all()
        return {run.id: run for run in runs}

    def queued_runs_missing_jobs(
        self,
        *,
        workspace_id: UUID,
        queued_run_ids: set[UUID],
        truncated: bool,
    ) -> list[AgentRun]:
        if truncated:
            return []
        runs = self._session.scalars(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.status == RunStatus.QUEUED.value,
            )
            .order_by(AgentRun.updated_at.asc(), AgentRun.created_at.asc())
        ).all()
        return [run for run in runs if run.id not in queued_run_ids]




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




@dataclass(frozen=True, slots=True)
class QueueGovernanceQueueScan:
    queued_total: int
    dead_letter_total: int
    workspace_jobs: list[JobPayload]
    agent_run_jobs: list[JobPayload]
    old_queued_jobs: list[JobPayload]
    truncated: bool


class QueueGovernanceQueueScanner:
    def __init__(self, queue: RedisQueue) -> None:
        self._queue = queue

    def scan(
        self,
        *,
        workspace_id: UUID,
        scan_limit: int,
        generated_at: datetime,
        stale_after_seconds: int,
    ) -> QueueGovernanceQueueScan:
        workspace_jobs = [
            job for job in self._queue.peek(limit=scan_limit) if job.workspace_id == workspace_id
        ]
        queued_total = self._queue.count_queued(workspace_id=workspace_id)
        return QueueGovernanceQueueScan(
            queued_total=queued_total,
            dead_letter_total=self._queue.count_dead_letters(workspace_id=workspace_id),
            workspace_jobs=workspace_jobs,
            agent_run_jobs=[job for job in workspace_jobs if job.job_type == JobType.AGENT_RUN],
            old_queued_jobs=_old_queued_jobs(
                generated_at,
                workspace_jobs,
                stale_after_seconds,
            ),
            truncated=self._queue.count_queued() > scan_limit
            or queued_total > len(workspace_jobs),
        )


def _old_queued_jobs(
    generated_at: datetime,
    workspace_jobs: list[JobPayload],
    stale_after_seconds: int,
) -> list[JobPayload]:
    cutoff = generated_at - timedelta(seconds=stale_after_seconds)
    return [job for job in workspace_jobs if aware_datetime(job.created_at) < cutoff]





class QueueGovernanceSnapshotBuilder:
    def __init__(
        self,
        session: Session,
        redis: Redis[str],
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._runs = QueueGovernanceRunRepository(session)
        self._redis = redis
        self._keys = key_builder

    def build(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        scan_limit: int,
        stale_after_seconds: int,
    ) -> QueueGovernanceSnapshot:
        now = datetime.now(UTC)
        queue = RedisQueue(self._redis, self._keys, queue_name)
        scan = QueueGovernanceQueueScanner(queue).scan(
            workspace_id=workspace_id,
            scan_limit=scan_limit,
            generated_at=now,
            stale_after_seconds=stale_after_seconds,
        )
        runs_by_id = self._runs.runs_by_id(
            workspace_id,
            {job.resource_id for job in scan.agent_run_jobs},
        )
        queued_ids = queued_run_ids(scan.agent_run_jobs, runs_by_id)
        return QueueGovernanceSnapshot(
            generated_at=now,
            queue_name=queue_name,
            scan_limit=scan_limit,
            stale_after_seconds=stale_after_seconds,
            queued_total=scan.queued_total,
            queued_scanned=len(scan.workspace_jobs),
            agent_run_jobs=scan.agent_run_jobs,
            dead_letter_total=scan.dead_letter_total,
            orphaned_jobs=orphaned_jobs(scan.agent_run_jobs, runs_by_id),
            non_runnable_jobs=non_runnable_jobs(scan.agent_run_jobs, runs_by_id),
            duplicate_jobs=duplicate_jobs(scan.agent_run_jobs),
            missing_runs=self._runs.queued_runs_missing_jobs(
                workspace_id=workspace_id,
                queued_run_ids=queued_ids,
                truncated=scan.truncated,
            ),
            old_queued_jobs=scan.old_queued_jobs,
            truncated=scan.truncated,
        )



@dataclass
class QueueGovernanceReconcileCounts:
    requeued_missing_runs: int = 0
    removed_orphaned_jobs: int = 0
    removed_non_runnable_jobs: int = 0
    skipped_items: int = 0
    remaining: int = 0




class QueueGovernanceActionExecutor:
    def __init__(
        self,
        *,
        queue: RedisQueue,
        run_service: RunOrchestrationService,
    ) -> None:
        self._queue = queue
        self._run_service = run_service

    def requeue_missing_runs(
        self,
        missing_runs: list[AgentRun],
        actor_user_id: UUID,
        counts: QueueGovernanceReconcileCounts,
    ) -> None:
        for run in missing_runs[: counts.remaining]:
            if self._run_service.enqueue_run(run, actor_user_id, force=True):
                counts.requeued_missing_runs += 1
                counts.remaining -= 1
            else:
                counts.skipped_items += 1
            if counts.remaining <= 0:
                break

    def remove_queued_jobs(
        self,
        workspace_id: UUID,
        jobs: list[JobPayload],
        counts: QueueGovernanceReconcileCounts,
    ) -> int:
        removed_count = 0
        for job in jobs[: counts.remaining]:
            removed = self._queue.remove_queued_job(job.job_id, workspace_id=workspace_id)
            if removed is not None:
                removed_count += 1
                counts.remaining -= 1
            else:
                counts.skipped_items += 1
            if counts.remaining <= 0:
                break
        return removed_count




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



def queue_governance_response(
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
        issues=queue_governance_issues(snapshot),
        recommended_actions=recommended_actions(snapshot),
    )





class QueueGovernanceAuditRecorder:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_reconciliation(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        queue_name: str,
        scan_limit: int,
        stale_after_seconds: int,
        actions: list[QueueGovernanceReconcileAction],
        max_items: int,
        reason: str | None,
        scanned_jobs: int,
        counts: QueueGovernanceReconcileCounts,
    ) -> None:
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
                "scanned_jobs": scanned_jobs,
                "requeued_missing_runs": counts.requeued_missing_runs,
                "removed_orphaned_jobs": counts.removed_orphaned_jobs,
                "removed_non_runnable_jobs": counts.removed_non_runnable_jobs,
                "skipped_items": counts.skipped_items,
            },
        )





class QueueGovernanceDiagnosticsService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str],
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._snapshot_builder = QueueGovernanceSnapshotBuilder(session, redis, key_builder)

    def queue_governance(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        scan_limit: int = 500,
        stale_after_seconds: int = 900,
    ) -> QueueGovernanceDiagnosticsResponse:
        snapshot = self._snapshot_builder.build(
            workspace_id=workspace_id,
            queue_name=queue_name,
            scan_limit=scan_limit,
            stale_after_seconds=stale_after_seconds,
        )
        return queue_governance_response(snapshot)





class QueueGovernanceReconciliationService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str],
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._session = session
        self._redis = redis
        self._keys = key_builder
        self._snapshot_builder = QueueGovernanceSnapshotBuilder(session, redis, key_builder)

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
        snapshot = self._snapshot_builder.build(
            workspace_id=workspace_id,
            queue_name=queue_name,
            scan_limit=scan_limit,
            stale_after_seconds=stale_after_seconds,
        )
        queue = RedisQueue(self._redis, self._keys, queue_name)
        executor = QueueGovernanceActionExecutor(
            queue=queue,
            run_service=RunOrchestrationService(self._session, queue=queue),
        )
        counts = QueueGovernanceReconcileCounts(remaining=max_items)
        if "requeue_missing_runs" in actions:
            executor.requeue_missing_runs(snapshot.missing_runs, actor_user_id, counts)
        if counts.remaining > 0 and "remove_orphaned_jobs" in actions:
            counts.removed_orphaned_jobs = executor.remove_queued_jobs(
                workspace_id,
                snapshot.orphaned_jobs,
                counts,
            )
        if counts.remaining > 0 and "remove_non_runnable_jobs" in actions:
            counts.removed_non_runnable_jobs = executor.remove_queued_jobs(
                workspace_id,
                snapshot.non_runnable_jobs,
                counts,
            )

        QueueGovernanceAuditRecorder(self._session).record_reconciliation(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            queue_name=queue_name,
            scan_limit=scan_limit,
            stale_after_seconds=stale_after_seconds,
            actions=actions,
            max_items=max_items,
            reason=reason,
            scanned_jobs=snapshot.queued_scanned,
            counts=counts,
        )
        self._session.commit()

        remaining_snapshot = self._snapshot_builder.build(
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
            requeued_missing_runs=counts.requeued_missing_runs,
            removed_orphaned_jobs=counts.removed_orphaned_jobs,
            removed_non_runnable_jobs=counts.removed_non_runnable_jobs,
            skipped_items=counts.skipped_items,
            remaining_issues=queue_governance_issues(remaining_snapshot),
        )
