from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import (
    QueueGovernanceReconcileAction,
    QueueGovernanceReconcileResponse,
)
from backend.app.audit.service import AuditService
from backend.app.operations.queue_governance_diagnostics import queue_governance_issues
from backend.app.operations.queue_governance_snapshot import QueueGovernanceSnapshotBuilder
from backend.app.operations.queue_governance_time import non_empty_string_or_none
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue import RedisQueue


@dataclass
class QueueGovernanceReconcileCounts:
    requeued_missing_runs: int = 0
    removed_orphaned_jobs: int = 0
    removed_non_runnable_jobs: int = 0
    skipped_items: int = 0
    remaining: int = 0


class QueueGovernanceReconciliationService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None = None,
        key_builder: RedisKeyBuilder | None = None,
    ) -> None:
        self._session = session
        self._redis = redis
        self._keys = key_builder or RedisKeyBuilder("opsmesh")
        self._snapshot_builder = QueueGovernanceSnapshotBuilder(session, redis, self._keys)

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
        queue = RedisQueue(self._redis, self._keys, queue_name) if self._redis is not None else None
        counts = QueueGovernanceReconcileCounts(remaining=max_items)

        if queue is not None:
            run_service = RunOrchestrationService(self._session, queue=queue)
            if "requeue_missing_runs" in actions:
                _requeue_missing_runs(run_service, snapshot.missing_runs, actor_user_id, counts)
            if counts.remaining > 0 and "remove_orphaned_jobs" in actions:
                counts.removed_orphaned_jobs = _remove_queued_jobs(
                    queue,
                    workspace_id,
                    snapshot.orphaned_jobs,
                    counts,
                )
            if counts.remaining > 0 and "remove_non_runnable_jobs" in actions:
                counts.removed_non_runnable_jobs = _remove_queued_jobs(
                    queue,
                    workspace_id,
                    snapshot.non_runnable_jobs,
                    counts,
                )

        self._record_audit(
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

    def _record_audit(
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


def _requeue_missing_runs(
    run_service: RunOrchestrationService,
    missing_runs: list,
    actor_user_id: UUID,
    counts: QueueGovernanceReconcileCounts,
) -> None:
    for run in missing_runs[: counts.remaining]:
        if run_service.enqueue_run(run, actor_user_id, force=True):
            counts.requeued_missing_runs += 1
            counts.remaining -= 1
        else:
            counts.skipped_items += 1
        if counts.remaining <= 0:
            break


def _remove_queued_jobs(
    queue: RedisQueue,
    workspace_id: UUID,
    jobs: list[JobPayload],
    counts: QueueGovernanceReconcileCounts,
) -> int:
    removed_count = 0
    for job in jobs[: counts.remaining]:
        removed = queue.remove_queued_job(job.job_id, workspace_id=workspace_id)
        if removed is not None:
            removed_count += 1
            counts.remaining -= 1
        else:
            counts.skipped_items += 1
        if counts.remaining <= 0:
            break
    return removed_count
