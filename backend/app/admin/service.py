from __future__ import annotations

from uuid import UUID

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.admin.base import AdminRedisService
from backend.app.admin.models import PlatformPolicy, PlatformPolicyEvent
from backend.app.admin.operations_summary import AdminOperationsSummaryService
from backend.app.admin.policy_control import AdminPolicyService
from backend.app.admin.runtime_control import AdminRuntimeService
from backend.app.admin.security_events import AdminSecurityEventService
from backend.app.admin.workers import AdminWorkerService
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.operations import QueueMetricsResponse
from backend.app.operations.models import WorkerLease, WorkerNode
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runtime_spaces.models import RuntimeSpace
from backend.app.runtimes.models import RuntimeLease, WorkspaceRuntime
from backend.app.security.models import SecurityEvent
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.models import Workspace


class AdminControlPlaneService(AdminRedisService):
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None = None,
        key_builder: RedisKeyBuilder | None = None,
    ) -> None:
        super().__init__(session, redis, key_builder)
        self._policies = AdminPolicyService(session)
        self._workers = AdminWorkerService(session, self._policies)
        self._runtimes = AdminRuntimeService(session)
        self._operations = AdminOperationsSummaryService(session, redis, self._keys)
        self._security = AdminSecurityEventService(session)

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
        return self._workers.list_workers(page, status=status, worker_type=worker_type)

    def drain_worker(self, worker_id: str) -> WorkerNode | None:
        return self._workers.drain_worker(worker_id)

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
        return self._workers.update_worker(
            worker_id,
            status=status,
            worker_type=worker_type,
            queue_name=queue_name,
            worker_version=worker_version,
            hostname=hostname,
            capacity=capacity,
            details=details,
            reason=reason,
            updated_by=updated_by,
        )

    def list_runtime_spaces(
        self,
        page: PageParams,
        *,
        status: str | None = None,
        workspace_id: UUID | None = None,
    ) -> tuple[list[RuntimeSpace], int]:
        return self._runtimes.list_runtime_spaces(
            page,
            status=status,
            workspace_id=workspace_id,
        )

    def quarantine_runtime_space(
        self,
        runtime_space_id: UUID,
        *,
        reason: str,
    ) -> RuntimeSpace | None:
        return self._runtimes.quarantine_runtime_space(runtime_space_id, reason=reason)

    def list_worker_leases(
        self,
        page: PageParams,
        *,
        status: str | None = None,
        workspace_id: UUID | None = None,
        worker_id: str | None = None,
    ) -> tuple[list[WorkerLease], int]:
        return self._workers.list_worker_leases(
            page,
            status=status,
            workspace_id=workspace_id,
            worker_id=worker_id,
        )

    def list_runtime_leases(
        self,
        page: PageParams,
        *,
        status: str | None = None,
        workspace_id: UUID | None = None,
        runtime_space_id: UUID | None = None,
        workspace_runtime_id: UUID | None = None,
    ) -> tuple[list[RuntimeLease], int]:
        return self._runtimes.list_runtime_leases(
            page,
            status=status,
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            workspace_runtime_id=workspace_runtime_id,
        )

    def queue_metrics(self, queue_name: str) -> QueueMetricsResponse:
        return self._operations.queue_metrics(queue_name)

    def operations_summary(self, queue_name: str = "agent_runs") -> dict[str, object]:
        return self._operations.operations_summary(queue_name)

    def list_dead_letters(self, queue_name: str, limit: int) -> tuple[list[JobPayload], int]:
        return self._operations.list_dead_letters(queue_name, limit)

    def requeue_dead_letter(self, queue_name: str, job_id: UUID) -> JobPayload | None:
        return self._operations.requeue_dead_letter(queue_name, job_id)

    def list_runtimes(
        self,
        page: PageParams,
        *,
        workspace_id: UUID | None = None,
        runtime_space_id: UUID | None = None,
        status: str | None = None,
        connection_status: str | None = None,
    ) -> tuple[list[WorkspaceRuntime], int]:
        return self._runtimes.list_runtimes(
            page,
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            status=status,
            connection_status=connection_status,
        )

    def force_stop_runtime(
        self,
        runtime_id: UUID,
        *,
        reason: str,
        queue: RedisQueue | None = None,
    ) -> WorkspaceRuntime | None:
        return self._runtimes.force_stop_runtime(runtime_id, reason=reason, queue=queue)

    def list_platform_policies(
        self,
        page: PageParams,
        *,
        status: str | None = None,
    ) -> tuple[list[PlatformPolicy], int]:
        return self._policies.list_platform_policies(page, status=status)

    def list_platform_policy_events(
        self,
        policy_key: str,
        page: PageParams,
        *,
        event_type: str | None = None,
    ) -> tuple[list[PlatformPolicyEvent], int] | None:
        return self._policies.list_platform_policy_events(
            policy_key,
            page,
            event_type=event_type,
        )

    def get_or_create_risky_execution_policy(self) -> PlatformPolicy:
        return self._policies.get_or_create_risky_execution_policy()

    def get_or_create_worker_control_policy(self) -> PlatformPolicy:
        return self._policies.get_or_create_worker_control_policy()

    def update_risky_execution_policy(
        self,
        *,
        value: dict[str, object],
        updated_by: str | None,
        description: str | None = None,
    ) -> PlatformPolicy:
        return self._policies.update_risky_execution_policy(
            value=value,
            updated_by=updated_by,
            description=description,
        )

    def update_worker_control_policy(
        self,
        *,
        value: dict[str, object],
        updated_by: str | None,
        description: str | None = None,
    ) -> PlatformPolicy:
        return self._policies.update_worker_control_policy(
            value=value,
            updated_by=updated_by,
            description=description,
        )

    def list_security_events(
        self,
        page: PageParams,
        *,
        severity: str | None = None,
        workspace_id: UUID | None = None,
    ) -> tuple[list[SecurityEvent], int]:
        return self._security.list_security_events(
            page,
            severity=severity,
            workspace_id=workspace_id,
        )
