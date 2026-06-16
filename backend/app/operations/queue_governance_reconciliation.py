from __future__ import annotations

from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.schemas.operation_queue import (
    QueueGovernanceReconcileAction,
    QueueGovernanceReconcileResponse,
)
from backend.app.operations.queue_governance_audit import QueueGovernanceAuditRecorder
from backend.app.operations.queue_governance_policy import queue_governance_issues
from backend.app.operations.queue_governance_reconcile_actions import (
    QueueGovernanceActionExecutor,
)
from backend.app.operations.queue_governance_reconcile_models import (
    QueueGovernanceReconcileCounts,
)
from backend.app.operations.queue_governance_snapshot_builder import (
    QueueGovernanceSnapshotBuilder,
)
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.queue import RedisQueue


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
