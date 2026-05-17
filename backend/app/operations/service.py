from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar, cast
from uuid import UUID

from redis import Redis
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.api.schemas.operations import QueueMetricsResponse
from backend.app.audit.models import AuditEvent
from backend.app.operations.models import WorkerHeartbeat
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtimes.models import RuntimeEvent, WorkspaceRuntime

T = TypeVar("T")


class OperationsService:
    def __init__(
        self,
        session: Session,
        redis: Redis | None = None,
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
        self._session.commit()
        self._session.refresh(heartbeat)
        return heartbeat

    def queue_metrics(self, queue_name: str) -> QueueMetricsResponse:
        if self._redis is None:
            return QueueMetricsResponse(
                queue_name=queue_name,
                queued=0,
                dead_letter=0,
                idempotency_keys=0,
            )
        queued = self._redis_count(self._redis.llen(self._keys.queue(queue_name)))
        dead = self._redis_count(self._redis.llen(self._keys.dead_letter_queue(queue_name)))
        idempotency_keys = int(self._count_keys(self._keys.idempotency_key("*", "*")))
        return QueueMetricsResponse(
            queue_name=queue_name,
            queued=queued,
            dead_letter=dead,
            idempotency_keys=idempotency_keys,
        )

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
        return {
            "queue": self.queue_metrics(queue_name),
            "failed_runs": int(failed_runs or 0),
            "offline_runtimes": int(offline_runtimes or 0),
            "workers_online": int(workers_online or 0),
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
