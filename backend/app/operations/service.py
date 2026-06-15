from __future__ import annotations

from datetime import datetime
from typing import Any, TypeVar
from uuid import UUID

from redis import Redis
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.api.schemas.operations import (
    BlockedStepExplanationResponse,
    BlockedStepUnblockResponse,
    DeadLetterJobsResponse,
    OperationsCapacityResponse,
    OperationsControlPlaneResponse,
    OperationsMcpJobsResponse,
    OperationsOutcomesResponse,
    OperationsRunActivityResponse,
    OperationsRuntimeCapacityResponse,
    OperationsSchedulerResponse,
    OperationsSelfHostedMachinesResponse,
    OperationsWorkerLifecycleResponse,
    QueueGovernanceReconcileAction,
    QueueGovernanceReconcileResponse,
    QueueMetricsResponse,
    SchedulerControlResponse,
    StaleRunRecoveryResponse,
    StaleRunsDiagnosticsResponse,
)
from backend.app.audit.models import AuditEvent
from backend.app.audit.service import AuditService
from backend.app.core.metrics import GaugeMetric
from backend.app.db.pagination import page_scalars
from backend.app.operations.activity import OperationsActivityService
from backend.app.operations.capacity import OperationsCapacityService
from backend.app.operations.control_plane import OperationsControlPlaneService
from backend.app.operations.models import WorkerHeartbeat, WorkerLease, WorkerNode
from backend.app.operations.observability import (
    PROMETHEUS_QUEUE_SCAN_LIMIT,
    PROMETHEUS_WORKER_STALE_AFTER_SECONDS,
    OperationsObservabilityService,
)
from backend.app.operations.outcomes import OperationsOutcomeService
from backend.app.operations.overview import OperationsOverviewService
from backend.app.operations.queue_governance import QueueGovernanceService
from backend.app.operations.runtime_cleanup import RuntimeCleanupService
from backend.app.operations.scheduler import OperationsSchedulerService
from backend.app.operations.self_hosted_machines import OperationsSelfHostedMachineService
from backend.app.operations.stale_runs import StaleRunOperationsService
from backend.app.operations.workers import (
    WorkerCapacitySnapshot,
    WorkerOperationsService,
)
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtimes.models import RuntimeEvent, RuntimeLease
from backend.app.security.models import SecurityEvent
from backend.app.workers.jobs import JobPayload

T = TypeVar("T")


class OperationsService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None = None,
        key_builder: RedisKeyBuilder | None = None,
    ) -> None:
        self._session = session
        self._redis = redis
        self._keys = key_builder or RedisKeyBuilder("opsmesh")

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
        return WorkerOperationsService(self._session).record_worker_heartbeat(
            worker_id=worker_id,
            worker_type=worker_type,
            status=status,
            queue_name=queue_name,
            details=details,
            worker_version=worker_version,
            hostname=hostname,
            capacity=capacity,
            workspace_id=workspace_id,
        )

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
        return WorkerOperationsService(self._session).upsert_worker_node(
            worker_id=worker_id,
            worker_type=worker_type,
            status=status,
            queue_name=queue_name,
            details=details,
            worker_version=worker_version,
            hostname=hostname,
            capacity=capacity,
            last_seen_at=last_seen_at,
        )

    def list_worker_nodes(
        self,
        page: PageParams,
        *,
        status: str | None = None,
        worker_type: str | None = None,
    ) -> tuple[list[WorkerNode], int]:
        return WorkerOperationsService(self._session).list_worker_nodes(
            page,
            status=status,
            worker_type=worker_type,
        )

    def request_worker_drain(self, worker_id: str) -> WorkerNode | None:
        return WorkerOperationsService(self._session).request_worker_drain(worker_id)

    def set_worker_status(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        worker_id: str,
        status: str,
        reason: str | None,
    ) -> WorkerNode | None:
        return WorkerOperationsService(self._session).set_worker_status(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            worker_id=worker_id,
            status=status,
            reason=reason,
        )

    def is_worker_draining(self, worker_id: str) -> bool:
        return WorkerOperationsService(self._session).is_worker_draining(worker_id)

    def worker_capacity_snapshot(
        self,
        worker_id: str,
        *,
        default_max_jobs: int = 1,
    ) -> WorkerCapacitySnapshot:
        return WorkerOperationsService(self._session).worker_capacity_snapshot(
            worker_id,
            default_max_jobs=default_max_jobs,
        )

    def start_worker_lease(
        self,
        *,
        worker_id: str,
        queue_name: str,
        job: JobPayload,
        metadata: dict[str, object] | None = None,
    ) -> WorkerLease:
        return WorkerOperationsService(self._session).start_worker_lease(
            worker_id=worker_id,
            queue_name=queue_name,
            job=job,
            metadata=metadata,
        )

    def finish_worker_lease(
        self,
        *,
        job_id: UUID,
        status: str,
        metadata: dict[str, object] | None = None,
    ) -> WorkerLease | None:
        return WorkerOperationsService(self._session).finish_worker_lease(
            job_id=job_id,
            status=status,
            metadata=metadata,
        )

    def expire_stale_worker_leases(
        self,
        *,
        workspace_id: UUID | None = None,
        stale_after_seconds: int = 900,
    ) -> int:
        return WorkerOperationsService(self._session).expire_stale_worker_leases(
            workspace_id=workspace_id,
            stale_after_seconds=stale_after_seconds,
        )

    def list_worker_leases(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        status: str | None = None,
        worker_id: str | None = None,
    ) -> tuple[list[WorkerLease], int]:
        return WorkerOperationsService(self._session).list_worker_leases(
            workspace_id,
            page,
            status=status,
            worker_id=worker_id,
        )

    def pause_scheduler(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        reason: str | None,
    ) -> SchedulerControlResponse | None:
        return OperationsSchedulerService(self._session).pause_scheduler(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            reason=reason,
        )

    def resume_scheduler(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
    ) -> SchedulerControlResponse | None:
        return OperationsSchedulerService(self._session).resume_scheduler(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
        )

    def stale_runs_diagnostics(
        self,
        workspace_id: UUID,
        *,
        stale_after_seconds: int,
        statuses: list[str] | None = None,
        limit: int = 100,
    ) -> StaleRunsDiagnosticsResponse:
        return StaleRunOperationsService(self._session, self._redis, self._keys).diagnostics(
            workspace_id,
            stale_after_seconds=stale_after_seconds,
            statuses=statuses,
            limit=limit,
        )

    def recover_stale_runs(
        self,
        workspace_id: UUID,
        *,
        actor_user_id: UUID,
        stale_after_seconds: int,
        statuses: list[str],
        limit: int,
        queue_name: str = "agent_runs",
        reason: str | None = None,
    ) -> StaleRunRecoveryResponse:
        return StaleRunOperationsService(self._session, self._redis, self._keys).recover(
            workspace_id,
            actor_user_id=actor_user_id,
            stale_after_seconds=stale_after_seconds,
            statuses=statuses,
            limit=limit,
            queue_name=queue_name,
            reason=reason,
        )

    def list_runtime_leases(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        status: str | None = None,
        runtime_space_id: UUID | None = None,
    ) -> tuple[list[RuntimeLease], int]:
        statement = select(RuntimeLease).where(RuntimeLease.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(RuntimeLease.status == status)
        if runtime_space_id is not None:
            statement = statement.where(RuntimeLease.runtime_space_id == runtime_space_id)
        return self._page(statement.order_by(RuntimeLease.created_at.desc()), page)

    def queue_metrics(
        self,
        queue_name: str,
        workspace_id: UUID | None = None,
    ) -> QueueMetricsResponse:
        return OperationsObservabilityService(
            self._session,
            self._redis,
            self._keys,
        ).queue_metrics(queue_name, workspace_id)

    def prometheus_gauges(
        self,
        queue_name: str,
        *,
        worker_stale_after_seconds: int = PROMETHEUS_WORKER_STALE_AFTER_SECONDS,
        queue_scan_limit: int = PROMETHEUS_QUEUE_SCAN_LIMIT,
    ) -> list[GaugeMetric]:
        return OperationsObservabilityService(
            self._session,
            self._redis,
            self._keys,
        ).prometheus_gauges(
            queue_name,
            worker_stale_after_seconds=worker_stale_after_seconds,
            queue_scan_limit=queue_scan_limit,
        )

    def queue_insights(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        scan_limit: int = 500,
    ):
        return QueueGovernanceService(self._session, self._redis, self._keys).queue_insights(
            workspace_id=workspace_id,
            queue_name=queue_name,
            scan_limit=scan_limit,
        )

    def queue_governance(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        scan_limit: int = 500,
        stale_after_seconds: int = 900,
    ):
        return QueueGovernanceService(self._session, self._redis, self._keys).queue_governance(
            workspace_id=workspace_id,
            queue_name=queue_name,
            scan_limit=scan_limit,
            stale_after_seconds=stale_after_seconds,
        )

    def reconcile_queue_governance(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        queue_name: str,
        scan_limit: int,
        stale_after_seconds: int,
        actions: list[QueueGovernanceReconcileAction],
        max_items: int,
        reason: str | None = None,
    ) -> QueueGovernanceReconcileResponse:
        return QueueGovernanceService(
            self._session,
            self._redis,
            self._keys,
        ).reconcile_queue_governance(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            queue_name=queue_name,
            scan_limit=scan_limit,
            stale_after_seconds=stale_after_seconds,
            actions=actions,
            max_items=max_items,
            reason=reason,
        )

    def list_dead_letters(
        self,
        workspace_id: UUID,
        queue_name: str,
        limit: int,
    ) -> DeadLetterJobsResponse:
        return QueueGovernanceService(self._session, self._redis, self._keys).list_dead_letters(
            workspace_id,
            queue_name,
            limit,
        )

    def requeue_dead_letter(
        self,
        workspace_id: UUID,
        queue_name: str,
        job_id: UUID,
    ) -> JobPayload | None:
        return QueueGovernanceService(self._session, self._redis, self._keys).requeue_dead_letter(
            workspace_id,
            queue_name,
            job_id,
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
        statement = AuditService(self._session).apply_retention_to_statement(statement)
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
        return RuntimeCleanupService(self._session).cleanup_stale_runtimes(
            workspace_id,
            stale_after_seconds=stale_after_seconds,
        )

    def cleanup_stale_runtimes_across_workspaces(
        self,
        *,
        stale_after_seconds: int = 600,
    ) -> tuple[int, int]:
        return RuntimeCleanupService(self._session).cleanup_stale_runtimes_across_workspaces(
            stale_after_seconds=stale_after_seconds,
        )

    def overview(self, workspace_id: UUID, queue_name: str) -> dict[str, Any]:
        return self.overview_payload(workspace_id, queue_name)

    def overview_payload(self, workspace_id: UUID, queue_name: str) -> dict[str, Any]:
        return OperationsOverviewService(
            self._session,
            self._redis,
            self._keys,
        ).overview_payload(workspace_id, queue_name)

    def capacity_payload(self, workspace_id: UUID, queue_name: str) -> OperationsCapacityResponse:
        return OperationsCapacityService(self._session, self._redis, self._keys).capacity_payload(
            workspace_id,
            queue_name,
        )

    def runtime_capacity_payload(self, workspace_id: UUID) -> OperationsRuntimeCapacityResponse:
        return OperationsCapacityService(
            self._session,
            self._redis,
            self._keys,
        ).runtime_capacity_payload(workspace_id)

    def worker_lifecycle_payload(
        self,
        workspace_id: UUID,
        queue_name: str,
    ) -> OperationsWorkerLifecycleResponse:
        return OperationsActivityService(
            self._session,
            self._redis,
            self._keys,
        ).worker_lifecycle_payload(workspace_id, queue_name)

    def run_activity_payload(
        self,
        workspace_id: UUID,
        *,
        team_id: UUID | None = None,
        scan_limit: int = 500,
    ) -> OperationsRunActivityResponse:
        return OperationsActivityService(
            self._session,
            self._redis,
            self._keys,
        ).run_activity_payload(
            workspace_id,
            team_id=team_id,
            scan_limit=scan_limit,
        )

    def scheduler_payload(self, workspace_id: UUID) -> OperationsSchedulerResponse:
        return OperationsSchedulerService(self._session).scheduler_payload(workspace_id)

    def list_blocked_steps(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        code: str | None = None,
    ) -> tuple[list[BlockedStepExplanationResponse], int]:
        return OperationsSchedulerService(self._session).list_blocked_steps(
            workspace_id,
            page,
            code=code,
        )

    def unblock_steps(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        code: str | None,
        reason: str | None,
        runtime_space_id: UUID | None,
        limit: int,
    ) -> BlockedStepUnblockResponse:
        return OperationsSchedulerService(self._session).unblock_steps(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            code=code,
            reason=reason,
            runtime_space_id=runtime_space_id,
            limit=limit,
        )

    def outcomes_payload(
        self,
        workspace_id: UUID,
        *,
        window_seconds: int,
    ) -> OperationsOutcomesResponse:
        return OperationsOutcomeService(self._session).outcomes_payload(
            workspace_id,
            window_seconds=window_seconds,
        )

    def mcp_jobs_payload(self, workspace_id: UUID) -> OperationsMcpJobsResponse:
        return OperationsOutcomeService(self._session).mcp_jobs_payload(workspace_id)

    def self_hosted_machines_payload(
        self,
        workspace_id: UUID,
        *,
        stale_after_seconds: int = 600,
    ) -> OperationsSelfHostedMachinesResponse:
        return OperationsSelfHostedMachineService(self._session).self_hosted_machines_payload(
            workspace_id,
            stale_after_seconds=stale_after_seconds,
        )

    def control_plane_payload(
        self,
        workspace_id: UUID,
        queue_name: str,
        *,
        window_seconds: int,
    ) -> OperationsControlPlaneResponse:
        return OperationsControlPlaneService(
            self._session,
            self._redis,
            self._keys,
        ).control_plane_payload(
            workspace_id,
            queue_name,
            window_seconds=window_seconds,
        )

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)
