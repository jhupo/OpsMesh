from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.trace_context import with_current_trace_metadata
from backend.app.operations.models import WorkerHeartbeat
from backend.app.operations.worker_capacity import worker_capacity, worker_heartbeat_details
from backend.app.operations.worker_lease_heartbeats import WorkerLeaseHeartbeatRecorder
from backend.app.operations.worker_nodes import WorkerNodeOperationsService


class WorkerHeartbeatOperationsService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_worker_heartbeat(
        self,
        *,
        worker_id: str,
        worker_type: str,
        status: str,
        queue_name: str,
        details: dict[str, object],
        worker_version: str | None = None,
        hostname: str | None = None,
        capacity: dict[str, object] | None = None,
        workspace_id: UUID | None = None,
    ) -> WorkerHeartbeat:
        details = worker_heartbeat_details(with_current_trace_metadata(details))
        heartbeat = self._session.scalar(
            select(WorkerHeartbeat).where(
                WorkerHeartbeat.worker_id == worker_id,
                WorkerHeartbeat.queue_name == queue_name,
            )
        )
        now = datetime.now(UTC)
        if heartbeat is None:
            heartbeat = WorkerHeartbeat(
                workspace_id=workspace_id,
                worker_id=worker_id,
                worker_type=worker_type,
                status=status,
                queue_name=queue_name,
                details=details,
                last_seen_at=now,
            )
            self._session.add(heartbeat)
        else:
            heartbeat.workspace_id = workspace_id
            heartbeat.worker_type = worker_type
            heartbeat.status = status
            heartbeat.details = details
            heartbeat.last_seen_at = now
        WorkerNodeOperationsService(self._session).upsert_worker_node(
            worker_id=worker_id,
            worker_type=worker_type,
            status=status,
            queue_name=queue_name,
            details=details,
            worker_version=worker_version,
            hostname=hostname,
            capacity=worker_capacity(capacity, worker_type),
            last_seen_at=now,
        )
        WorkerLeaseHeartbeatRecorder(self._session).record_running_worker_lease_heartbeat(
            worker_id=worker_id,
            queue_name=queue_name,
            status=status,
            at=now,
        )
        self._session.commit()
        self._session.refresh(heartbeat)
        return heartbeat
