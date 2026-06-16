from __future__ import annotations

from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.admin.policies import PlatformPolicyService
from backend.app.api.pagination import PageParams
from backend.app.audit.service import AuditService
from backend.app.db.pagination import page_scalars
from backend.app.operations.models import WorkerNode
from backend.app.operations.utils import positive_int
from backend.app.operations.worker_capacity import (
    bounded_worker_capacity,
    merge_worker_capacity,
    next_worker_node_status,
    non_empty_string_or_none,
    worker_status_blocks_claims,
)
from backend.app.operations.worker_lease_queries import WorkerLeaseQueryService
from backend.app.operations.worker_models import WorkerCapacitySnapshot

T = TypeVar("T")


class WorkerNodeOperationsService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_worker_node(
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
        last_seen_at: datetime | None = None,
    ) -> WorkerNode:
        node = self._session.scalar(select(WorkerNode).where(WorkerNode.worker_id == worker_id))
        now = last_seen_at or datetime.now(UTC)
        capacity_caps = PlatformPolicyService(self._session).worker_control_policy().capacity_caps()
        if node is None:
            node = WorkerNode(
                worker_id=worker_id,
                worker_type=worker_type,
                status=status,
                queue_name=queue_name,
                worker_version=worker_version,
                hostname=hostname,
                capacity=bounded_worker_capacity(capacity, worker_type, capacity_caps),
                details=details,
                last_seen_at=now,
            )
            self._session.add(node)
        else:
            node.worker_type = worker_type
            node.status = next_worker_node_status(node, status)
            node.queue_name = queue_name
            node.worker_version = worker_version
            node.hostname = hostname
            node.capacity = bounded_worker_capacity(
                merge_worker_capacity(node.capacity, capacity),
                worker_type,
                capacity_caps,
            )
            node.details = details
            node.last_seen_at = now
        return node

    def list_worker_nodes(
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

    def request_worker_drain(self, worker_id: str) -> WorkerNode | None:
        node = self._session.scalar(select(WorkerNode).where(WorkerNode.worker_id == worker_id))
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
        node = self._session.scalar(select(WorkerNode).where(WorkerNode.worker_id == worker_id))
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

    def is_worker_draining(self, worker_id: str) -> bool:
        node = self._session.scalar(select(WorkerNode).where(WorkerNode.worker_id == worker_id))
        return bool(node is not None and node.drain_requested_at is not None)

    def worker_capacity_snapshot(
        self,
        worker_id: str,
        *,
        default_max_jobs: int = 1,
    ) -> WorkerCapacitySnapshot:
        node = self._session.scalar(select(WorkerNode).where(WorkerNode.worker_id == worker_id))
        lease_service = WorkerLeaseQueryService(self._session)
        if node is not None and worker_status_blocks_claims(node):
            return WorkerCapacitySnapshot(
                worker_id=worker_id,
                max_jobs=positive_int(node.capacity.get("max_jobs"), default_max_jobs),
                running_jobs=lease_service.running_leases_for_worker(worker_id),
                available_slots=0,
                accepting=False,
                reason=f"worker_{node.status}",
                capacity=dict(node.capacity),
            )
        max_jobs = (
            positive_int(node.capacity.get("max_jobs"), default_max_jobs)
            if node is not None
            else max(1, default_max_jobs)
        )
        running_jobs = lease_service.running_leases_for_worker(worker_id)
        available_slots = max(0, max_jobs - running_jobs)
        return WorkerCapacitySnapshot(
            worker_id=worker_id,
            max_jobs=max_jobs,
            running_jobs=running_jobs,
            available_slots=available_slots,
            accepting=available_slots > 0,
            reason=None if available_slots > 0 else "worker_capacity_full",
            capacity=dict(node.capacity) if node is not None else {"max_jobs": max_jobs},
        )

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)
