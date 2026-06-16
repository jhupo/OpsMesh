from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.trace_context import current_trace_metadata
from backend.app.operations.models import WorkerLease
from backend.app.operations.worker_lifecycle import (
    append_worker_lifecycle_events,
    worker_finish_lifecycle_event,
    worker_lifecycle_event,
)
from backend.app.workers.jobs import JobPayload


class WorkerLeaseWriter:
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
            lease = _new_worker_lease(
                worker_id=worker_id,
                queue_name=queue_name,
                job=job,
                metadata=metadata or {},
                now=now,
            )
            self._session.add(lease)
        else:
            _restart_worker_lease(
                lease,
                worker_id=worker_id,
                queue_name=queue_name,
                job=job,
                metadata=metadata or {},
                now=now,
            )
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


def _new_worker_lease(
    *,
    worker_id: str,
    queue_name: str,
    job: JobPayload,
    metadata: dict[str, object],
    now: datetime,
) -> WorkerLease:
    return WorkerLease(
        workspace_id=job.workspace_id,
        worker_id=worker_id,
        queue_name=queue_name,
        job_id=job.job_id,
        job_type=job.job_type.value,
        resource_id=job.resource_id,
        status="running",
        attempt=job.attempt,
        lease_metadata=append_worker_lifecycle_events(
            metadata,
            [
                _lease_event("claimed", now, job),
                _lease_event("started", now, job),
            ],
        ),
        started_at=now,
        last_heartbeat_at=now,
    )


def _restart_worker_lease(
    lease: WorkerLease,
    *,
    worker_id: str,
    queue_name: str,
    job: JobPayload,
    metadata: dict[str, object],
    now: datetime,
) -> None:
    lease.worker_id = worker_id
    lease.queue_name = queue_name
    lease.status = "running"
    lease.attempt = job.attempt
    lease.lease_metadata = append_worker_lifecycle_events(
        dict(lease.lease_metadata or {}) | metadata,
        [
            _lease_event("claimed", now, job),
            _lease_event("started", now, job),
        ],
    )
    lease.started_at = now
    lease.last_heartbeat_at = now
    lease.finished_at = None


def _lease_event(event_type: str, at: datetime, job: JobPayload) -> dict[str, object]:
    return worker_lifecycle_event(
        event_type,
        at,
        attempt=job.attempt,
        metadata=current_trace_metadata(),
    )
