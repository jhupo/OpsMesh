"""Mutating queue reconciliation owned by the worker recovery boundary."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from backend.app.core.redis.keys import RedisKeyBuilder
from backend.app.core.utils import non_empty_string_or_none
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.runs.service import RunOrchestrationService
from backend.app.observability.audit.service import AuditService
from backend.app.runtime.operations.contracts.queue import (
    QueueGovernanceReconcileAction,
    QueueGovernanceReconcileResponse,
)
from backend.app.runtime.operations.queues.governance import (
    QueueGovernanceSnapshotBuilder,
    queue_governance_issues,
)
from backend.app.runtime.workers.contracts import JobPayload
from backend.app.runtime.workers.queue import RedisQueue


@dataclass(slots=True)
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
