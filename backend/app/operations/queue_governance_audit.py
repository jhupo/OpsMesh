from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.api.schemas.operation_queue import QueueGovernanceReconcileAction
from backend.app.audit.service import AuditService
from backend.app.operations.queue_governance_reconcile_models import (
    QueueGovernanceReconcileCounts,
)
from backend.app.operations.utils import non_empty_string_or_none


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
