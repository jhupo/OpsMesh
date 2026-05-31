from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.operations.models import WorkerLease
from backend.app.workers.jobs import JobType

WORKER_LIFECYCLE_EVENTS_LIMIT = 50


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
    metadata["lifecycle_events"] = lifecycle_events[-WORKER_LIFECYCLE_EVENTS_LIMIT:]
    metadata["last_lifecycle_event"] = event
    return metadata
