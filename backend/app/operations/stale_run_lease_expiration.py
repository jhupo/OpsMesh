from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.trace_context import current_trace_metadata
from backend.app.operations.models import WorkerLease
from backend.app.operations.worker_lifecycle import (
    RUNNING_LEASE_STATUSES,
    append_worker_lifecycle_events,
    worker_lifecycle_event,
)
from backend.app.workers.jobs import JobType


class StaleRunLeaseExpirationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def expire_worker_leases_for_runs(
        self,
        *,
        workspace_id: UUID,
        run_ids: list[UUID],
        expired_at: datetime,
    ) -> int:
        if not run_ids:
            return 0
        leases = self._session.scalars(
            select(WorkerLease).where(
                WorkerLease.workspace_id == workspace_id,
                WorkerLease.job_type == JobType.AGENT_RUN.value,
                WorkerLease.resource_id.in_(run_ids),
                WorkerLease.status.in_(RUNNING_LEASE_STATUSES),
            )
        ).all()
        for lease in leases:
            _expire_worker_lease(lease, expired_at)
        return len(leases)


def _expire_worker_lease(lease: WorkerLease, expired_at: datetime) -> None:
    lease.status = "expired"
    lease.finished_at = expired_at
    lease.lease_metadata = append_worker_lifecycle_events(
        dict(lease.lease_metadata or {})
        | {
            "expired_by": "stale_run_recovery",
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
