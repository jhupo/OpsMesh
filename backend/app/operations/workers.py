from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.admin.policies import PlatformPolicyService
from backend.app.api.pagination import PageParams
from backend.app.audit.service import AuditService
from backend.app.core.trace_context import current_trace_metadata, with_current_trace_metadata
from backend.app.core.typing import int_or_zero
from backend.app.db.pagination import page_scalars
from backend.app.operations.models import WorkerHeartbeat, WorkerLease, WorkerNode
from backend.app.operations.utils import positive_int
from backend.app.operations.worker_lifecycle import (
    RUNNING_LEASE_STATUSES,
    append_worker_lifecycle_events,
    worker_finish_lifecycle_event,
    worker_lifecycle_event,
)
from backend.app.workers.jobs import JobPayload, JobType

T = TypeVar("T")


@dataclass(frozen=True)
class WorkerCapacitySnapshot:
    worker_id: str
    max_jobs: int
    running_jobs: int
    available_slots: int
    accepting: bool
    reason: str | None = None
    capacity: dict[str, object] | None = None


class WorkerOperationsService:
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
        details = _worker_heartbeat_details(with_current_trace_metadata(details))
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
        self.upsert_worker_node(
            worker_id=worker_id,
            worker_type=worker_type,
            status=status,
            queue_name=queue_name,
            details=details,
            worker_version=worker_version,
            hostname=hostname,
            capacity=_worker_capacity(capacity, worker_type),
            last_seen_at=now,
        )
        self._record_running_worker_lease_heartbeat(
            worker_id=worker_id,
            queue_name=queue_name,
            status=status,
            at=now,
        )
        self._session.commit()
        self._session.refresh(heartbeat)
        return heartbeat

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
        if node is None:
            normalized_capacity = _bounded_worker_capacity(
                capacity,
                worker_type,
                PlatformPolicyService(self._session).worker_control_policy().capacity_caps(),
            )
            node = WorkerNode(
                worker_id=worker_id,
                worker_type=worker_type,
                status=status,
                queue_name=queue_name,
                worker_version=worker_version,
                hostname=hostname,
                capacity=normalized_capacity,
                details=details,
                last_seen_at=now,
            )
            self._session.add(node)
        else:
            normalized_capacity = _bounded_worker_capacity(
                _merge_worker_capacity(node.capacity, capacity),
                worker_type,
                PlatformPolicyService(self._session).worker_control_policy().capacity_caps(),
            )
            node.worker_type = worker_type
            node.status = _next_worker_node_status(node, status)
            node.queue_name = queue_name
            node.worker_version = worker_version
            node.hostname = hostname
            node.capacity = normalized_capacity
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
                "reason": _non_empty_string_or_none(reason),
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
        if node is not None and _worker_status_blocks_claims(node):
            return WorkerCapacitySnapshot(
                worker_id=worker_id,
                max_jobs=positive_int(node.capacity.get("max_jobs"), default_max_jobs),
                running_jobs=self._running_leases_for_worker(worker_id),
                available_slots=0,
                accepting=False,
                reason=f"worker_{node.status}",
                capacity=dict(node.capacity),
            )
        if node is not None and node.drain_requested_at is not None:
            return WorkerCapacitySnapshot(
                worker_id=worker_id,
                max_jobs=positive_int(node.capacity.get("max_jobs"), default_max_jobs),
                running_jobs=self._running_leases_for_worker(worker_id),
                available_slots=0,
                accepting=False,
                reason="worker_draining",
                capacity=dict(node.capacity),
            )
        max_jobs = (
            positive_int(node.capacity.get("max_jobs"), default_max_jobs)
            if node is not None
            else max(1, default_max_jobs)
        )
        running_jobs = self._running_leases_for_worker(worker_id)
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

    def _running_leases_for_worker(self, worker_id: str) -> int:
        running = self._session.scalar(
            select(func.count())
            .select_from(WorkerLease)
            .where(
                WorkerLease.worker_id == worker_id,
                WorkerLease.status.in_(RUNNING_LEASE_STATUSES),
            )
        )
        return int(running or 0)

    def _record_running_worker_lease_heartbeat(
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


def _worker_heartbeat_details(details: dict[str, object]) -> dict[str, object]:
    sanitized = dict(details)
    capacity = sanitized.get("capacity")
    if isinstance(capacity, dict):
        sanitized["capacity"] = dict(capacity)
    enqueued = int_or_zero(sanitized.get("scheduled_job_actions_enqueued"))
    recorded = int_or_zero(sanitized.get("scheduled_job_actions_recorded"))
    skipped = int_or_zero(sanitized.get("scheduled_job_actions_skipped"))
    enqueued_by_type = _string_int_dict(sanitized.get("scheduled_job_actions_enqueued_by_job_type"))
    recorded_by_type = _string_int_dict(sanitized.get("scheduled_job_actions_recorded_by_job_type"))
    skipped_by_type = _string_int_dict(sanitized.get("scheduled_job_actions_skipped_by_job_type"))
    if not any((enqueued, recorded, skipped, enqueued_by_type, recorded_by_type, skipped_by_type)):
        return sanitized
    provider_health_job_type = JobType.MODEL_PROVIDER_HEALTH_CHECK.value
    sanitized["scheduled_job_actions"] = {
        "enqueued": enqueued,
        "recorded": recorded,
        "skipped": skipped,
        "enqueued_by_job_type": enqueued_by_type,
        "recorded_by_job_type": recorded_by_type,
        "skipped_by_job_type": skipped_by_type,
        "model_provider_health_check": {
            "enqueued": enqueued_by_type.get(provider_health_job_type, 0),
            "recorded": recorded_by_type.get(provider_health_job_type, 0),
            "skipped": skipped_by_type.get(provider_health_job_type, 0),
        },
    }
    return sanitized


def _string_int_dict(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(item, int) and not isinstance(item, bool)
    }


def _non_empty_string_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _worker_capacity(capacity: dict[str, object] | None, worker_type: str) -> dict[str, object]:
    normalized = dict(capacity or {})
    normalized.setdefault("worker_type", worker_type)
    return normalized


def _merge_worker_capacity(
    current: dict[str, object] | None,
    incoming: dict[str, object] | None,
) -> dict[str, object]:
    merged = dict(current or {})
    merged.update(dict(incoming or {}))
    return merged


def _next_worker_node_status(node: WorkerNode, heartbeat_status: str) -> str:
    if node.drain_requested_at is not None:
        return "draining"
    if node.status in {"offline", "maintenance", "disabled", "quarantined"}:
        return node.status
    return heartbeat_status


def _worker_status_blocks_claims(node: WorkerNode) -> bool:
    return node.drain_requested_at is not None or node.status in {
        "offline",
        "maintenance",
        "disabled",
        "quarantined",
    }


def _bounded_worker_capacity(
    capacity: dict[str, object] | None,
    worker_type: str,
    caps: dict[str, int],
) -> dict[str, object]:
    normalized = _worker_capacity(capacity, worker_type)
    for key, cap in caps.items():
        value = normalized.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > cap:
            normalized[key] = cap
    return normalized
