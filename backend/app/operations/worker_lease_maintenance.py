from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.trace_context import current_trace_metadata
from backend.app.operations.models import WorkerLease
from backend.app.operations.worker_lifecycle import (
    RUNNING_LEASE_STATUSES,
    append_worker_lifecycle_events,
    worker_lifecycle_event,
)


class WorkerLeaseMaintenanceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def expire_stale_worker_leases(
        self,
        *,
        workspace_id: UUID | None = None,
        stale_after_seconds: int = 900,
    ) -> int:
        stale_leases = self._stale_worker_leases(
            workspace_id=workspace_id,
            stale_after_seconds=stale_after_seconds,
        )
        expired_at = datetime.now(UTC)
        for lease in stale_leases:
            _expire_worker_lease(lease, expired_at)
        self._session.commit()
        return len(stale_leases)

    def _stale_worker_leases(
        self,
        *,
        workspace_id: UUID | None,
        stale_after_seconds: int,
    ) -> list[WorkerLease]:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        freshness = func.coalesce(WorkerLease.last_heartbeat_at, WorkerLease.started_at)
        statement = select(WorkerLease).where(
            WorkerLease.status.in_(RUNNING_LEASE_STATUSES),
            freshness < cutoff,
        )
        if workspace_id is not None:
            statement = statement.where(WorkerLease.workspace_id == workspace_id)
        return list(self._session.scalars(statement).all())


def _expire_worker_lease(lease: WorkerLease, expired_at: datetime) -> None:
    lease.status = "expired"
    lease.finished_at = expired_at
    lease.lease_metadata = append_worker_lifecycle_events(
        dict(lease.lease_metadata or {})
        | {
            "expired_by": "worker_maintenance",
            "expired_at": expired_at.isoformat(),
        },
        [
            worker_lifecycle_event(
                "expired",
                expired_at,
                attempt=lease.attempt,
                status="expired",
                metadata=current_trace_metadata(),
            )
        ],
    )
