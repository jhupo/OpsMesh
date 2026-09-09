from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.admin.base import AdminSessionService
from backend.app.admin.common import normalized_worker_capacity, worker_node_snapshot
from backend.app.admin.worker_policy_control import AdminWorkerPolicyControlService
from backend.app.api.pagination import PageParams
from backend.app.operations.models import WorkerLease, WorkerNode


class AdminWorkerService(AdminSessionService):
    def __init__(self, session: Session, policy_service: AdminWorkerPolicyControlService) -> None:
        super().__init__(session)
        self._policy_service = policy_service

    def list_workers(
        self,
        page: PageParams,
        *,
        status: str | None = None,
        worker_type: str | None = None,
    ) -> tuple[list[WorkerNode], int]:
        statement = select(WorkerNode)
        if status is not None:
            statement = statement.where(WorkerNode.status == status)
        if worker_type is not None:
            statement = statement.where(WorkerNode.worker_type == worker_type)
        return self._page(statement.order_by(WorkerNode.last_seen_at.desc()), page)

    def drain_worker(self, worker_id: str) -> WorkerNode | None:
        node = self._session.scalar(select(WorkerNode).where(WorkerNode.worker_id == worker_id))
        if node is None:
            return None
        node.status = "draining"
        node.drain_requested_at = datetime.now(UTC)
        self._policy_service.append_worker_control_event(
            "worker.drained",
            f"Worker {worker_id} marked draining",
            {"worker_id": worker_id, "reason": "Drain requested by platform admin"},
        )
        self._session.commit()
        self._session.refresh(node)
        return node

    def update_worker(
        self,
        worker_id: str,
        *,
        status: str | None,
        worker_type: str | None,
        queue_name: str | None,
        worker_version: str | None,
        hostname: str | None,
        capacity: dict[str, object] | None,
        details: dict[str, object] | None,
        reason: str,
        updated_by: str | None,
    ) -> WorkerNode | None:
        node = self._session.scalar(select(WorkerNode).where(WorkerNode.worker_id == worker_id))
        if node is None:
            return None
        policy = self._policy_service.worker_control_policy()
        self._policy_service.assert_worker_update_allowed(
            policy,
            status=status,
            worker_type=worker_type,
            queue_name=queue_name,
            capacity=capacity,
        )
        before = worker_node_snapshot(node)
        changed_fields: list[str] = []
        if status is not None:
            node.status = status
            node.drain_requested_at = datetime.now(UTC) if status == "draining" else None
            changed_fields.append("status")
        if worker_type is not None:
            node.worker_type = worker_type
            changed_fields.append("worker_type")
        if queue_name is not None:
            node.queue_name = queue_name
            changed_fields.append("queue_name")
        if worker_version is not None:
            node.worker_version = worker_version
            changed_fields.append("worker_version")
        if hostname is not None:
            node.hostname = hostname
            changed_fields.append("hostname")
        if capacity is not None:
            node.capacity = normalized_worker_capacity(capacity, node.worker_type)
            changed_fields.append("capacity")
        elif worker_type is not None:
            node.capacity = normalized_worker_capacity(node.capacity, node.worker_type)
        if details is not None:
            node.details = dict(details)
            changed_fields.append("details")
        if changed_fields:
            self._policy_service.append_worker_control_event(
                "worker.updated",
                f"Worker {worker_id} updated",
                {
                    "worker_id": worker_id,
                    "changed_fields": changed_fields,
                    "before": before,
                    "after": worker_node_snapshot(node),
                    "reason": reason,
                    "updated_by": updated_by,
                },
            )
        self._session.commit()
        self._session.refresh(node)
        return node

    def list_worker_leases(
        self,
        page: PageParams,
        *,
        status: str | None = None,
        workspace_id: UUID | None = None,
        worker_id: str | None = None,
    ) -> tuple[list[WorkerLease], int]:
        statement = select(WorkerLease)
        if status is not None:
            statement = statement.where(WorkerLease.status == status)
        if workspace_id is not None:
            statement = statement.where(WorkerLease.workspace_id == workspace_id)
        if worker_id is not None:
            statement = statement.where(WorkerLease.worker_id == worker_id)
        return self._page(statement.order_by(WorkerLease.created_at.desc()), page)
