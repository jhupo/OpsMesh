from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.app.admin.policies import PlatformPolicyService
from backend.app.operations.models import WorkerNode
from backend.app.operations.worker_capacity import (
    bounded_worker_capacity,
    merge_worker_capacity,
    next_worker_node_status,
)
from backend.app.operations.worker_node_repository import WorkerNodeRepository


class WorkerNodeRegistry:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._nodes = WorkerNodeRepository(session)

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
        node = self._nodes.by_worker_id(worker_id)
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
            self._nodes.add(node)
            return node

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
