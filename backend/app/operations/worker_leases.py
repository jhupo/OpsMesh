from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.core.trace_context import current_trace_metadata
from backend.app.db.pagination import page_scalars
from backend.app.operations.models import WorkerLease
from backend.app.operations.worker_lifecycle import (
    RUNNING_LEASE_STATUSES,
    append_worker_lifecycle_events,
    worker_finish_lifecycle_event,
    worker_lifecycle_event,
)
from backend.app.workers.jobs import JobPayload

T = TypeVar("T")


class WorkerLeaseOperationsService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def start_worker_lease(
        self,
        *,
        worker_id: str,
        queue_name: str,
        job: JobPayload,
        metadata: dict[str, object] | None = None,
    ) -> WorkerLease:
        now = datetime.now(UTC)
        lease = self._session.scalar(select(WorkerLease).where(WorkerLease.job_id == job.job_id))
        if lease is None:
            lease = WorkerLease(
                workspace_id=job.workspace_id,
                worker_id=worker_id,
                queue_name=queue_name,
                job_id=job.job_id,
                job_type=job.job_type.value,
                resource_id=job.resource_id,
                status="running",
                attempt=job.attempt,
                lease_metadata=append_worker_lifecycle_events(
                    metadata or {},
                    [
                        worker_lifecycle_event(
                            "claimed",
                            now,
                            attempt=job.attempt,
                            metadata=current_trace_metadata(),
                        ),
                        worker_lifecycle_event(
                            "started",
                            now,
                            attempt=job.attempt,
                            metadata=current_trace_metadata(),
                        ),
                    ],
                ),
                started_at=now,
                last_heartbeat_at=now,
            )
            self._session.add(lease)
        else:
            existing_metadata = dict(lease.lease_metadata or {})
            lease.worker_id = worker_id
            lease.queue_name = queue_name
            lease.status = "running"
            lease.attempt = job.attempt
            lease.lease_metadata = append_worker_lifecycle_events(
                existing_metadata | (metadata or {}),
                [
                    worker_lifecycle_event(
                        "claimed",
                        now,
                        attempt=job.attempt,
                        metadata=current_trace_metadata(),
                    ),
                    worker_lifecycle_event(
                        "started",
                        now,
                        attempt=job.attempt,
                        metadata=current_trace_metadata(),
                    ),
                ],
            )
            lease.started_at = now
            lease.last_heartbeat_at = now
            lease.finished_at = None
        self._session.commit()
        self._session.refresh(lease)
        return lease

    def finish_worker_lease(
        self,
        *,
        job_id: UUID,
        status: str,
        metadata: dict[str, object] | None = None,
    ) -> WorkerLease | None:
        lease = self._session.scalar(select(WorkerLease).where(WorkerLease.job_id == job_id))
        if lease is None:
            return None
        finished_at = datetime.now(UTC)
        lease.status = status
        lease.finished_at = finished_at
        lease.lease_metadata = append_worker_lifecycle_events(
            dict(lease.lease_metadata or {}) | (metadata or {}),
            [
                worker_lifecycle_event(
                    worker_finish_lifecycle_event(status),
                    finished_at,
                    attempt=lease.attempt,
                    status=status,
                    metadata=current_trace_metadata(),
                )
            ],
        )
        self._session.commit()
        self._session.refresh(lease)
        return lease

    def expire_stale_worker_leases(
        self,
        *,
        workspace_id: UUID | None = None,
        stale_after_seconds: int = 900,
    ) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        freshness = func.coalesce(WorkerLease.last_heartbeat_at, WorkerLease.started_at)
        statement = select(WorkerLease).where(
            WorkerLease.status.in_(RUNNING_LEASE_STATUSES),
            freshness < cutoff,
        )
        if workspace_id is not None:
            statement = statement.where(WorkerLease.workspace_id == workspace_id)
        stale_leases = self._session.scalars(statement).all()
        expired_at = datetime.now(UTC)
        for lease in stale_leases:
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
        self._session.commit()
        return len(stale_leases)

    def list_worker_leases(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        status: str | None = None,
        worker_id: str | None = None,
    ) -> tuple[list[WorkerLease], int]:
        statement = select(WorkerLease).where(WorkerLease.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(WorkerLease.status == status)
        if worker_id is not None:
            statement = statement.where(WorkerLease.worker_id == worker_id)
        return self._page(statement.order_by(WorkerLease.created_at.desc()), page)

    def running_leases_for_worker(self, worker_id: str) -> int:
        running = self._session.scalar(
            select(func.count())
            .select_from(WorkerLease)
            .where(
                WorkerLease.worker_id == worker_id,
                WorkerLease.status.in_(RUNNING_LEASE_STATUSES),
            )
        )
        return int(running or 0)

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

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)
