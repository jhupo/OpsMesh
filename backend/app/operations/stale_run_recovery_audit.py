from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.observability.audit_service import AuditService
from backend.app.operations.stale_run_recovery_models import StaleRunRecoveryCounts
from backend.app.operations.utils import non_empty_string_or_none
from backend.app.runs.status import RunStatus


class StaleRunRecoveryAuditRecorder:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_recovery(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        stale_after_seconds: int,
        statuses: set[RunStatus],
        limit: int,
        queue_name: str,
        reason: str | None,
        scanned_runs: int,
        counts: StaleRunRecoveryCounts,
        expired_worker_leases: int,
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="worker.stale_runs_recovered",
            target_type="workspace",
            target_id=workspace_id,
            metadata={
                "stale_after_seconds": stale_after_seconds,
                "statuses": sorted(status.value for status in statuses),
                "limit": limit,
                "queue_name": queue_name,
                "reason": non_empty_string_or_none(reason),
                "scanned_runs": scanned_runs,
                "requeued_runs": counts.requeued,
                "failed_closed_runs": counts.failed_closed,
                "expired_worker_leases": expired_worker_leases,
            },
        )
