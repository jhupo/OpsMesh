from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.operations.models import WorkerLease
from backend.app.operations.timeline_models import TimelineEvent, TimelineFilters
from backend.app.operations.timeline_utils import aware_datetime, within
from backend.app.workers.jobs import JobType


class TeamRuntimeWorkerTimelineCollector:
    def __init__(self, session: Session) -> None:
        self._session = session

    def worker_leases(
        self,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
    ) -> list[TimelineEvent]:
        statement = select(WorkerLease).where(
            WorkerLease.workspace_id == workspace_id,
            WorkerLease.job_type == JobType.TEAM_EXECUTION_LOOP.value,
            WorkerLease.resource_id == team_id,
        )
        leases = self._session.scalars(statement).all()
        events: list[TimelineEvent] = []
        for lease in leases:
            metadata = {
                "worker_lease_id": str(lease.id),
                "worker_id": lease.worker_id,
                "queue_name": lease.queue_name,
                "job_id": str(lease.job_id),
                "job_type": lease.job_type,
                "resource_id": str(lease.resource_id),
                "attempt": lease.attempt,
                "lease_metadata": dict(lease.lease_metadata or {}),
            }
            if within(lease.started_at, filters):
                events.append(
                    TimelineEvent(
                        id=f"worker_lease:{lease.id}:started",
                        source_type="worker_lease",
                        event_type="worker_lease.started",
                        occurred_at=aware_datetime(lease.started_at),
                        resource_id=str(lease.id),
                        message=f"Worker lease started on {lease.worker_id}",
                        metadata=metadata | {"status": lease.status},
                    )
                )
            if lease.finished_at is not None and within(lease.finished_at, filters):
                events.append(
                    TimelineEvent(
                        id=f"worker_lease:{lease.id}:finished",
                        source_type="worker_lease",
                        event_type=f"worker_lease.{lease.status}",
                        occurred_at=aware_datetime(lease.finished_at),
                        resource_id=str(lease.id),
                        message=f"Worker lease {lease.status} on {lease.worker_id}",
                        metadata=metadata | {"status": lease.status},
                    )
                )
        return events
