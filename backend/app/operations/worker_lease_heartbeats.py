from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.trace_context import current_trace_metadata
from backend.app.operations.models import WorkerLease
from backend.app.operations.worker_lifecycle import (
    RUNNING_LEASE_STATUSES,
    append_worker_lifecycle_events,
    worker_lifecycle_event,
)


class WorkerLeaseHeartbeatRecorder:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_running_worker_lease_heartbeat(
        self,
        *,
        worker_id: str,
        queue_name: str,
        status: str,
        at: datetime,
    ) -> None:
        leases = self._session.scalars(
            select(WorkerLease).where(
                WorkerLease.worker_id == worker_id,
                WorkerLease.queue_name == queue_name,
                WorkerLease.status.in_(RUNNING_LEASE_STATUSES),
            )
        ).all()
        for lease in leases:
            lease.last_heartbeat_at = at
            lease.lease_metadata = append_worker_lifecycle_events(
                dict(lease.lease_metadata or {}),
                [
                    worker_lifecycle_event(
                        "heartbeat",
                        at,
                        attempt=lease.attempt,
                        status=status,
                        metadata=current_trace_metadata(),
                    )
                ],
            )
