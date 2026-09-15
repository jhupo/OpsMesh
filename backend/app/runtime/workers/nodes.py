from __future__ import annotations

from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.core.db.pagination import page_scalars
from backend.app.core.pagination import PageParams
from backend.app.core.utils import non_empty_string_or_none
from backend.app.domains.platform.admin.policy_reader import PlatformPolicyService
from backend.app.observability.audit.service import AuditService
from backend.app.observability.telemetry.trace_context import with_current_trace_metadata
from backend.app.runtime.workers.capacity import (
    bounded_worker_capacity,
    merge_worker_capacity,
    next_worker_node_status,
    worker_capacity,
)
from backend.app.runtime.workers.contracts import JobType
from backend.app.runtime.workers.leases import WorkerLeaseHeartbeatRecorder
from backend.app.runtime.workers.models import WorkerHeartbeat, WorkerNode


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
        details = _normalize_heartbeat_details(with_current_trace_metadata(details))
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
        WorkerNodeRegistry(self._session).upsert_worker_node(
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


T = TypeVar("T")


class WorkerNodeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def by_worker_id(self, worker_id: str) -> WorkerNode | None:
        return self._session.scalar(select(WorkerNode).where(WorkerNode.worker_id == worker_id))

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
        return page_scalars(self._session, statement.order_by(WorkerNode.last_seen_at.desc()), page)

    def add(self, node: WorkerNode) -> None:
        self._session.add(node)


def page_worker_nodes(
    session: Session,
    statement: Select[tuple[T]],
    page: PageParams,
) -> tuple[list[T], int]:
    return page_scalars(session, statement, page)


def _normalize_heartbeat_details(details: dict[str, object]) -> dict[str, object]:
    sanitized = dict(details)
    capacity = sanitized.get("capacity")
    if isinstance(capacity, dict):
        sanitized["capacity"] = dict(capacity)
    keys = (
        "scheduled_job_actions_enqueued",
        "scheduled_job_actions_recorded",
        "scheduled_job_actions_skipped",
    )
    counts = {key: _integer_count(sanitized.get(key)) for key in keys}
    by_type = {key: _string_int_dict(sanitized.get(f"{key}_by_job_type")) for key in keys}
    if not any((*counts.values(), *by_type.values())):
        return sanitized
    provider_health_type = JobType.MODEL_PROVIDER_HEALTH_CHECK.value
    sanitized["scheduled_job_actions"] = {
        "enqueued": counts["scheduled_job_actions_enqueued"],
        "recorded": counts["scheduled_job_actions_recorded"],
        "skipped": counts["scheduled_job_actions_skipped"],
        "enqueued_by_job_type": by_type["scheduled_job_actions_enqueued"],
        "recorded_by_job_type": by_type["scheduled_job_actions_recorded"],
        "skipped_by_job_type": by_type["scheduled_job_actions_skipped"],
        "model_provider_health_check": {
            "enqueued": by_type["scheduled_job_actions_enqueued"].get(provider_health_type, 0),
            "recorded": by_type["scheduled_job_actions_recorded"].get(provider_health_type, 0),
            "skipped": by_type["scheduled_job_actions_skipped"].get(provider_health_type, 0),
        },
    }
    return sanitized


def _integer_count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _string_int_dict(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(item, int) and not isinstance(item, bool)
    }
