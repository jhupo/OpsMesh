from __future__ import annotations

from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from redis import Redis
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.admin.models import PlatformPolicy, PlatformPolicyEvent
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.operations import QueueMetricsResponse
from backend.app.operations.models import WorkerLease, WorkerNode
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceEvent
from backend.app.runtimes.models import RuntimeEvent, WorkspaceRuntime
from backend.app.security.models import SecurityEvent
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.models import Workspace

T = TypeVar("T")
RISKY_EXECUTION_POLICY_KEY = "global_risky_execution"


class AdminControlPlaneService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None = None,
        key_builder: RedisKeyBuilder | None = None,
    ) -> None:
        self._session = session
        self._redis = redis
        self._keys = key_builder or RedisKeyBuilder("chaincloud")

    def overview(self) -> dict[str, int]:
        return {
            "workspaces_total": self._count(select(Workspace)),
            "workspaces_active": self._count(select(Workspace).where(Workspace.status == "active")),
            "workers_total": self._count(select(WorkerNode)),
            "workers_online": self._count(select(WorkerNode).where(WorkerNode.status == "online")),
            "workers_draining": self._count(
                select(WorkerNode).where(WorkerNode.status == "draining")
            ),
            "active_worker_leases": self._count(
                select(WorkerLease).where(WorkerLease.status == "running")
            ),
            "runtime_spaces_total": self._count(select(RuntimeSpace)),
            "runtime_spaces_quarantined": self._count(
                select(RuntimeSpace).where(RuntimeSpace.status == "quarantined")
            ),
            "runtimes_running": self._count(
                select(WorkspaceRuntime).where(WorkspaceRuntime.status == "running")
            ),
            "runtimes_offline": self._count(
                select(WorkspaceRuntime).where(
                    WorkspaceRuntime.connection_status == "offline",
                    WorkspaceRuntime.status != "deleted",
                )
            ),
            "critical_security_events": self._count(
                select(SecurityEvent).where(SecurityEvent.severity == "critical")
            ),
        }

    def list_workspaces(
        self,
        page: PageParams,
        *,
        status: str | None = None,
    ) -> tuple[list[Workspace], int]:
        statement = select(Workspace)
        if status is not None:
            statement = statement.where(Workspace.status == status)
        return self._page(statement.order_by(Workspace.created_at.desc()), page)

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
        self._session.commit()
        self._session.refresh(node)
        return node

    def list_runtime_spaces(
        self,
        page: PageParams,
        *,
        status: str | None = None,
        workspace_id: UUID | None = None,
    ) -> tuple[list[RuntimeSpace], int]:
        statement = select(RuntimeSpace)
        if status is not None:
            statement = statement.where(RuntimeSpace.status == status)
        if workspace_id is not None:
            statement = statement.where(RuntimeSpace.workspace_id == workspace_id)
        return self._page(statement.order_by(RuntimeSpace.created_at.desc()), page)

    def quarantine_runtime_space(
        self,
        runtime_space_id: UUID,
        *,
        reason: str,
    ) -> RuntimeSpace | None:
        runtime_space = self._session.get(RuntimeSpace, runtime_space_id)
        if runtime_space is None:
            return None
        runtime_space.status = "quarantined"
        self._session.add(
            RuntimeSpaceEvent(
                workspace_id=runtime_space.workspace_id,
                runtime_space_id=runtime_space.id,
                event_type="runtime_space.quarantined",
                message=reason,
                event_metadata={"source": "platform_admin"},
                created_at=datetime.now(UTC),
            )
        )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space

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

    def queue_metrics(self, queue_name: str) -> QueueMetricsResponse:
        if self._redis is None:
            return QueueMetricsResponse(
                queue_name=queue_name,
                queued=0,
                dead_letter=0,
                idempotency_keys=0,
            )
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return QueueMetricsResponse(
            queue_name=queue_name,
            queued=queue.count_queued(),
            dead_letter=queue.count_dead_letters(),
            idempotency_keys=self._count_keys(self._keys.idempotency_key("*", "*")),
        )

    def list_dead_letters(self, queue_name: str, limit: int) -> tuple[list[JobPayload], int]:
        if self._redis is None:
            return [], 0
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return queue.list_dead_letters(limit), queue.count_dead_letters()

    def requeue_dead_letter(self, queue_name: str, job_id: UUID) -> JobPayload | None:
        if self._redis is None:
            return None
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return queue.requeue_dead_letter(job_id, workspace_id=None)

    def list_runtimes(
        self,
        page: PageParams,
        *,
        workspace_id: UUID | None = None,
        runtime_space_id: UUID | None = None,
        status: str | None = None,
        connection_status: str | None = None,
    ) -> tuple[list[WorkspaceRuntime], int]:
        statement = select(WorkspaceRuntime).where(WorkspaceRuntime.status != "deleted")
        if workspace_id is not None:
            statement = statement.where(WorkspaceRuntime.workspace_id == workspace_id)
        if runtime_space_id is not None:
            statement = statement.where(WorkspaceRuntime.runtime_space_id == runtime_space_id)
        if status is not None:
            statement = statement.where(WorkspaceRuntime.status == status)
        if connection_status is not None:
            statement = statement.where(WorkspaceRuntime.connection_status == connection_status)
        return self._page(statement.order_by(WorkspaceRuntime.created_at.desc()), page)

    def force_stop_runtime(
        self,
        runtime_id: UUID,
        *,
        reason: str,
    ) -> WorkspaceRuntime | None:
        runtime = self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.id == runtime_id,
                WorkspaceRuntime.status != "deleted",
            )
        )
        if runtime is None:
            return None
        runtime.status = "stopped"
        runtime.connection_status = "offline"
        self._session.add(
            RuntimeEvent(
                workspace_id=runtime.workspace_id,
                workspace_runtime_id=runtime.id,
                runtime_space_id=runtime.runtime_space_id,
                event_type="runtime.force_stopped",
                message=reason,
                event_metadata={"source": "platform_admin"},
                created_at=datetime.now(UTC),
            )
        )
        self._session.commit()
        self._session.refresh(runtime)
        return runtime

    def list_platform_policies(
        self,
        page: PageParams,
        *,
        status: str | None = None,
    ) -> tuple[list[PlatformPolicy], int]:
        statement = select(PlatformPolicy)
        if status is not None:
            statement = statement.where(PlatformPolicy.status == status)
        return self._page(statement.order_by(PlatformPolicy.updated_at.desc()), page)

    def get_or_create_risky_execution_policy(self) -> PlatformPolicy:
        policy = self._session.scalar(
            select(PlatformPolicy).where(
                PlatformPolicy.policy_key == RISKY_EXECUTION_POLICY_KEY,
            )
        )
        if policy is not None:
            return policy
        policy = PlatformPolicy(
            policy_key=RISKY_EXECUTION_POLICY_KEY,
            status="active",
            value={
                "allow_runtime_commands": False,
                "allow_network_egress": False,
                "allow_self_hosted_runtimes": True,
                "require_approval_for_high_risk_tools": True,
            },
            description="Global personal-safety controls for risky execution capabilities.",
        )
        self._session.add(policy)
        self._session.flush([policy])
        self._append_policy_event(policy, "platform_policy.created", "Policy created", {})
        self._session.commit()
        self._session.refresh(policy)
        return policy

    def update_risky_execution_policy(
        self,
        *,
        value: dict[str, object],
        updated_by: str | None,
        description: str | None = None,
    ) -> PlatformPolicy:
        policy = self.get_or_create_risky_execution_policy()
        policy.value = self._normalize_risky_execution_policy(value)
        if description is not None:
            policy.description = description
        policy.updated_by = updated_by
        self._append_policy_event(
            policy,
            "platform_policy.updated",
            "Risky execution policy updated",
            {"value": policy.value, "updated_by": updated_by},
        )
        self._session.commit()
        self._session.refresh(policy)
        return policy

    def list_security_events(
        self,
        page: PageParams,
        *,
        severity: str | None = None,
        workspace_id: UUID | None = None,
    ) -> tuple[list[SecurityEvent], int]:
        statement = select(SecurityEvent)
        if severity is not None:
            statement = statement.where(SecurityEvent.severity == severity)
        if workspace_id is not None:
            statement = statement.where(SecurityEvent.workspace_id == workspace_id)
        return self._page(statement.order_by(SecurityEvent.created_at.desc()), page)

    def _normalize_risky_execution_policy(
        self,
        value: dict[str, object],
    ) -> dict[str, object]:
        defaults = self.get_or_create_risky_execution_policy().value
        merged = dict(defaults)
        allowed_keys = {
            "allow_runtime_commands",
            "allow_network_egress",
            "allow_self_hosted_runtimes",
            "require_approval_for_high_risk_tools",
        }
        for key in allowed_keys:
            raw_value = value.get(key)
            if isinstance(raw_value, bool):
                merged[key] = raw_value
        return merged

    def _append_policy_event(
        self,
        policy: PlatformPolicy,
        event_type: str,
        message: str,
        metadata: dict[str, object],
    ) -> None:
        self._session.add(
            PlatformPolicyEvent(
                platform_policy_id=policy.id,
                event_type=event_type,
                message=message,
                event_metadata=metadata,
                created_at=datetime.now(UTC),
            )
        )

    def _count(self, statement: Select[tuple[T]]) -> int:
        count_statement = select(func.count()).select_from(statement.subquery())
        return int(self._session.scalar(count_statement) or 0)

    def _count_keys(self, pattern: str) -> int:
        if self._redis is None:
            return 0
        return sum(1 for _ in self._redis.scan_iter(pattern))

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)
