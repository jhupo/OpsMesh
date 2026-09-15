from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.core.db.pagination import page_scalars
from backend.app.core.pagination import PageParams
from backend.app.observability.telemetry.trace_context import current_trace_metadata
from backend.app.runtime.workers.contracts import JobPayload, JobType
from backend.app.runtime.workers.models import WorkerLease

RUNNING_LEASE_STATUSES = frozenset({"running"})
LIFECYCLE_EVENTS_LIMIT = 50


def mark_agent_run_worker_cancel_requested(
    session: Session,
    *,
    workspace_id: UUID,
    run_id: UUID,
    requested_at: datetime,
) -> int:
    """Mark running worker leases for an agent run as cancel-requested.

    The lease stays in the running state so worker capacity is not released
    until the worker process actually stops or finishes the job.
    """

    leases = session.scalars(
        select(WorkerLease).where(
            WorkerLease.workspace_id == workspace_id,
            WorkerLease.job_type == JobType.AGENT_RUN.value,
            WorkerLease.resource_id == run_id,
            WorkerLease.status == "running",
        )
    ).all()
    for lease in leases:
        lease.lease_metadata = _append_cancel_requested_event(
            dict(lease.lease_metadata or {}),
            requested_at=requested_at,
            attempt=lease.attempt,
        )
    return len(leases)


def _append_cancel_requested_event(
    metadata: dict[str, object],
    *,
    requested_at: datetime,
    attempt: int,
) -> dict[str, object]:
    events = metadata.get("lifecycle_events")
    lifecycle_events = list(events) if isinstance(events, list) else []
    event = {
        "type": "cancel_requested",
        "at": requested_at.isoformat(),
        "attempt": attempt,
        "status": "running",
    }
    lifecycle_events.append(event)
    metadata["cancel_requested"] = True
    metadata["cancel_requested_at"] = requested_at.isoformat()
    metadata["lifecycle_events"] = lifecycle_events[-LIFECYCLE_EVENTS_LIMIT:]
    metadata["last_lifecycle_event"] = event
    return metadata


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
            _expire_run_worker_lease(lease, expired_at)
        return len(leases)


def _expire_run_worker_lease(lease: WorkerLease, expired_at: datetime) -> None:
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
            _expire_stale_worker_lease(lease, expired_at)
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


def _expire_stale_worker_lease(lease: WorkerLease, expired_at: datetime) -> None:
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


T = TypeVar("T")


class WorkerLeaseQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

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

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)


class WorkerLeaseWriter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def start_worker_lease(
        self,
        *,
        worker_id: str,
        queue_name: str,
        job: JobPayload,
        claim_token: str,
        metadata: dict[str, object] | None = None,
    ) -> WorkerLease:
        now = datetime.now(UTC)
        lease = self._session.scalar(
            select(WorkerLease)
            .where(WorkerLease.job_id == job.job_id)
            .with_for_update()
        )
        if lease is None:
            lease = _new_worker_lease(
                worker_id=worker_id,
                queue_name=queue_name,
                job=job,
                claim_token=claim_token,
                metadata=metadata or {},
                now=now,
            )
            self._session.add(lease)
        else:
            if lease.status == "running" and lease.claim_token not in {None, claim_token}:
                raise WorkerLeaseOwnershipError("Worker lease is owned by another claim")
            _restart_worker_lease(
                lease,
                worker_id=worker_id,
                queue_name=queue_name,
                job=job,
                claim_token=claim_token,
                metadata=metadata or {},
                now=now,
            )
        return lease

    def heartbeat_worker_lease(
        self,
        *,
        job_id: UUID,
        claim_token: str,
        status: str = "online",
        at: datetime | None = None,
    ) -> bool:
        lease = self._session.scalar(
            select(WorkerLease)
            .where(
                WorkerLease.job_id == job_id,
                WorkerLease.claim_token == claim_token,
                WorkerLease.status == "running",
            )
            .with_for_update()
        )
        if lease is None:
            return False
        heartbeat_at = at or datetime.now(UTC)
        lease.last_heartbeat_at = heartbeat_at
        lease.lease_metadata = append_worker_lifecycle_events(
            dict(lease.lease_metadata or {}),
            [
                worker_lifecycle_event(
                    "heartbeat",
                    heartbeat_at,
                    attempt=lease.attempt,
                    status=status,
                    metadata=current_trace_metadata(),
                )
            ],
        )
        return True

    def finish_worker_lease(
        self,
        *,
        job_id: UUID,
        claim_token: str,
        status: str,
        metadata: dict[str, object] | None = None,
    ) -> WorkerLease | None:
        lease = self._session.scalar(
            select(WorkerLease)
            .where(
                WorkerLease.job_id == job_id,
                WorkerLease.claim_token == claim_token,
                WorkerLease.status == "running",
            )
            .with_for_update()
        )
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
        return lease


class WorkerLeaseOwnershipError(RuntimeError):
    """Raised when a stale worker tries to claim an active job lease."""


def _new_worker_lease(
    *,
    worker_id: str,
    queue_name: str,
    job: JobPayload,
    claim_token: str,
    metadata: dict[str, object],
    now: datetime,
) -> WorkerLease:
    return WorkerLease(
        workspace_id=job.workspace_id,
        worker_id=worker_id,
        queue_name=queue_name,
        job_id=job.job_id,
        claim_token=claim_token,
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
    claim_token: str,
    metadata: dict[str, object],
    now: datetime,
) -> None:
    lease.worker_id = worker_id
    lease.queue_name = queue_name
    lease.status = "running"
    lease.claim_token = claim_token
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


def append_worker_lifecycle_events(
    metadata: dict[str, object],
    events: list[dict[str, object]],
) -> dict[str, object]:
    existing = metadata.get("lifecycle_events")
    lifecycle_events = list(existing) if isinstance(existing, list) else []
    lifecycle_events.extend(events)
    metadata["lifecycle_events"] = lifecycle_events[-LIFECYCLE_EVENTS_LIMIT:]
    if events:
        metadata["last_lifecycle_event"] = events[-1]
    return metadata

def worker_lifecycle_event(
    event_type: str,
    at: datetime,
    *,
    attempt: int,
    status: str | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    event: dict[str, object] = {
        "type": event_type,
        "at": at.isoformat(),
        "attempt": attempt,
    }
    if status is not None:
        event["status"] = status
    if metadata:
        event.update(metadata)
    return event

def worker_finish_lifecycle_event(status: str) -> str:
    if status == "retrying":
        return "requeued"
    if status == "failed":
        return "failed"
    if status == "expired":
        return "expired"
    return "completed"
