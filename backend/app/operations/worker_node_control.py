from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.observability.audit_service import AuditService
from backend.app.operations.models import WorkerNode
from backend.app.operations.utils import non_empty_string_or_none
from backend.app.operations.worker_node_repository import WorkerNodeRepository


class WorkerNodeControlService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._nodes = WorkerNodeRepository(session)

    def request_worker_drain(self, worker_id: str) -> WorkerNode | None:
        node = self._nodes.by_worker_id(worker_id)
        if node is None:
            return None
        node.status = "draining"
        node.drain_requested_at = datetime.now(UTC)
        self._session.commit()
        self._session.refresh(node)
        return node

    def set_worker_status(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        worker_id: str,
        status: str,
        reason: str | None,
    ) -> WorkerNode | None:
        node = self._nodes.by_worker_id(worker_id)
        if node is None:
            return None
        now = datetime.now(UTC)
        previous_status = node.status
        previous_drain_requested_at = node.drain_requested_at
        node.status = status
        node.drain_requested_at = now if status == "draining" else None
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="worker.status_updated",
            target_type="worker",
            target_id=node.worker_id,
            metadata={
                "worker_id": node.worker_id,
                "previous_status": previous_status,
                "status": status,
                "reason": non_empty_string_or_none(reason),
                "previous_drain_requested_at": previous_drain_requested_at.isoformat()
                if previous_drain_requested_at is not None
                else None,
            },
        )
        self._session.commit()
        self._session.refresh(node)
        return node
