from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar, cast
from uuid import UUID

from redis import Redis
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.api.schemas.operations import DeadLetterJobsResponse, QueueMetricsResponse
from backend.app.audit.models import AuditEvent
from backend.app.operations.models import WorkerHeartbeat, WorkerLease, WorkerNode
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtimes.models import RuntimeEvent, WorkspaceRuntime
from backend.app.security.models import SecurityEvent
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue import RedisQueue

T = TypeVar("T")
RUNNING_LEASE_STATUSES = {"running"}


@dataclass(frozen=True)
class WorkerCapacitySnapshot:
    worker_id: str
    max_jobs: int
    running_jobs: int
    available_slots: int
    accepting: bool
    reason: str | None = None


class OperationsService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None = None,
        key_builder: RedisKeyBuilder | None = None,
    ) -> None:
        self._session = session
        self._redis = redis
        self._keys = key_builder or RedisKeyBuilder("chaincloud")

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
            capacity=capacity or {},
            last_seen_at=now,
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
            node = WorkerNode(
                worker_id=worker_id,
                worker_type=worker_type,
                status=status,
                queue_name=queue_name,
                worker_version=worker_version,
                hostname=hostname,
                capacity=capacity or {},
                details=details,
                last_seen_at=now,
            )
            self._session.add(node)
        else:
            node.worker_type = worker_type
            node.status = "draining" if node.drain_requested_at is not None else status
            node.queue_name = queue_name
            node.worker_version = worker_version
            node.hostname = hostname
            node.capacity = capacity or {}
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
        if node is not None and node.drain_requested_at is not None:
            return WorkerCapacitySnapshot(
                worker_id=worker_id,
                max_jobs=_positive_int(node.capacity.get("max_jobs"), default_max_jobs),
                running_jobs=self._running_leases_for_worker(worker_id),
                available_slots=0,
                accepting=False,
                reason="worker_draining",
            )
        max_jobs = (
            _positive_int(node.capacity.get("max_jobs"), default_max_jobs)
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
                lease_metadata=metadata or {},
                started_at=now,
            )
            self._session.add(lease)
        else:
            lease.worker_id = worker_id
            lease.queue_name = queue_name
            lease.status = "running"
            lease.attempt = job.attempt
            lease.lease_metadata = metadata or {}
            lease.started_at = now
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
        lease.status = status
        lease.finished_at = datetime.now(UTC)
        if metadata:
            lease.lease_metadata = lease.lease_metadata | metadata
        self._session.commit()
        self._session.refresh(lease)
        return lease

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

    def queue_metrics(
        self,
        queue_name: str,
        workspace_id: UUID | None = None,
    ) -> QueueMetricsResponse:
        if self._redis is None:
            return QueueMetricsResponse(
                queue_name=queue_name,
                queued=0,
                dead_letter=0,
                idempotency_keys=0,
            )
        queue = RedisQueue(self._redis, self._keys, queue_name)
        queued = queue.count_queued(workspace_id=workspace_id)
        dead = queue.count_dead_letters(workspace_id=workspace_id)
        idempotency_pattern = (
            self._keys.idempotency_key(str(workspace_id), "*")
            if workspace_id is not None
            else self._keys.idempotency_key("*", "*")
        )
        idempotency_keys = int(self._count_keys(idempotency_pattern))
        return QueueMetricsResponse(
            queue_name=queue_name,
            queued=queued,
            dead_letter=dead,
            idempotency_keys=idempotency_keys,
        )

    def list_dead_letters(
        self,
        workspace_id: UUID,
        queue_name: str,
        limit: int,
    ) -> DeadLetterJobsResponse:
        if self._redis is None:
            return DeadLetterJobsResponse(items=[], total=0)
        queue = RedisQueue(self._redis, self._keys, queue_name)
        items = queue.list_dead_letters(limit, workspace_id=workspace_id)
        total = queue.count_dead_letters(workspace_id=workspace_id)
        return DeadLetterJobsResponse(items=items, total=total)

    def requeue_dead_letter(
        self,
        workspace_id: UUID,
        queue_name: str,
        job_id: UUID,
    ) -> JobPayload | None:
        if self._redis is None:
            return None
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return queue.requeue_dead_letter(job_id, workspace_id=workspace_id)

    def list_run_events(
        self,
        workspace_id: UUID,
        page: PageParams,
        event_type: str | None = None,
    ) -> tuple[list[RunEvent], int]:
        statement = select(RunEvent).where(RunEvent.workspace_id == workspace_id)
        if event_type is not None:
            statement = statement.where(RunEvent.event_type == event_type)
        return self._page(statement.order_by(RunEvent.created_at.desc()), page)

    def list_runtime_events(
        self,
        workspace_id: UUID,
        page: PageParams,
        runtime_id: UUID | None = None,
        event_type: str | None = None,
    ) -> tuple[list[RuntimeEvent], int]:
        statement = select(RuntimeEvent).where(RuntimeEvent.workspace_id == workspace_id)
        if runtime_id is not None:
            statement = statement.where(RuntimeEvent.workspace_runtime_id == runtime_id)
        if event_type is not None:
            statement = statement.where(RuntimeEvent.event_type == event_type)
        return self._page(statement.order_by(RuntimeEvent.created_at.desc()), page)

    def inspect_failed_runs(
        self,
        workspace_id: UUID,
        page: PageParams,
    ) -> tuple[list[AgentRun], int]:
        statement = (
            select(AgentRun)
            .where(AgentRun.workspace_id == workspace_id, AgentRun.status == "failed")
            .order_by(AgentRun.updated_at.desc())
        )
        return self._page(statement, page)

    def filter_audit_events(
        self,
        workspace_id: UUID,
        page: PageParams,
        action: str | None = None,
        target_type: str | None = None,
    ) -> tuple[list[AuditEvent], int]:
        statement = select(AuditEvent).where(AuditEvent.workspace_id == workspace_id)
        if action is not None:
            statement = statement.where(AuditEvent.action == action)
        if target_type is not None:
            statement = statement.where(AuditEvent.target_type == target_type)
        return self._page(statement.order_by(AuditEvent.created_at.desc()), page)

    def filter_security_events(
        self,
        workspace_id: UUID,
        page: PageParams,
        action: str | None = None,
        severity: str | None = None,
        user_id: UUID | None = None,
    ) -> tuple[list[SecurityEvent], int]:
        statement = select(SecurityEvent).where(SecurityEvent.workspace_id == workspace_id)
        if action is not None:
            statement = statement.where(SecurityEvent.action == action)
        if severity is not None:
            statement = statement.where(SecurityEvent.severity == severity)
        if user_id is not None:
            statement = statement.where(SecurityEvent.user_id == user_id)
        return self._page(statement.order_by(SecurityEvent.created_at.desc()), page)

    def cleanup_stale_runtimes(
        self,
        workspace_id: UUID,
        *,
        stale_after_seconds: int = 600,
    ) -> tuple[int, int]:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        stale_runtimes = self._session.scalars(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.connection_status == "online",
                WorkspaceRuntime.last_heartbeat_at.is_not(None),
                WorkspaceRuntime.last_heartbeat_at < cutoff,
            )
        ).all()
        for runtime in stale_runtimes:
            runtime.connection_status = "offline"
        deleted_records = self._mark_deleted_terminal_runtimes(workspace_id)
        self._session.commit()
        return len(stale_runtimes), deleted_records

    def overview(self, workspace_id: UUID, queue_name: str) -> dict[str, Any]:
        failed_runs = self._session.scalar(
            select(func.count()).select_from(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.status == "failed",
            )
        )
        offline_runtimes = self._session.scalar(
            select(func.count()).select_from(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.connection_status == "offline",
            )
        )
        workers_online = self._session.scalar(
            select(func.count()).select_from(WorkerHeartbeat).where(
                WorkerHeartbeat.workspace_id == workspace_id,
                WorkerHeartbeat.status == "online",
            )
        )
        recent_security_events = self._session.scalar(
            select(func.count()).select_from(SecurityEvent).where(
                SecurityEvent.workspace_id == workspace_id,
                SecurityEvent.severity.in_(["warning", "critical"]),
            )
        )
        return {
            "queue": self.queue_metrics(queue_name, workspace_id),
            "failed_runs": int(failed_runs or 0),
            "offline_runtimes": int(offline_runtimes or 0),
            "workers_online": int(workers_online or 0),
            "security_warnings": int(recent_security_events or 0),
        }

    def _mark_deleted_terminal_runtimes(self, workspace_id: UUID) -> int:
        terminal = self._session.scalars(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.status.in_(["stopped", "failed"]),
            )
        ).all()
        for runtime in terminal:
            runtime.status = "deleted"
            runtime.connection_status = "offline"
        return len(terminal)

    def _count_keys(self, pattern: str) -> int:
        if self._redis is None:
            return 0
        return sum(1 for _ in self._redis.scan_iter(pattern))

    def _redis_count(self, value: object) -> int:
        return int(cast(int, value))

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)


def _positive_int(value: object, fallback: int) -> int:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return max(1, fallback)
        return parsed if parsed > 0 else max(1, fallback)
    return max(1, fallback)
