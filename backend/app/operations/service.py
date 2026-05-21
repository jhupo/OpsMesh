from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar, cast
from uuid import UUID

from redis import Redis
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.api.schemas.operations import (
    ApprovalBacklogResponse,
    DeadLetterJobsResponse,
    McpJobStatusBucketResponse,
    McpJobToolBucketResponse,
    OperationsCapacityResponse,
    OperationsControlPlaneIssueResponse,
    OperationsControlPlaneResponse,
    OperationsMcpJobsResponse,
    OperationsOutcomesResponse,
    OperationsRuntimeCapacityResponse,
    OperationsSchedulerResponse,
    OperationsSelfHostedMachineResponse,
    OperationsSelfHostedMachinesResponse,
    QueueLatencyResponse,
    QueueMetricsResponse,
    RunFailureReasonResponse,
    RunOutcomeWindowResponse,
    RuntimeProviderCapacityResponse,
    RuntimeSpaceQuotaUsageResponse,
    RuntimeSpaceSaturationResponse,
    SchedulerBacklogResponse,
    SchedulerBlockedReasonResponse,
    SchedulerPolicyResponse,
    SchedulerPriorityBucketResponse,
    WorkerCapacityAggregateResponse,
    WorkerTypeCapacityResponse,
)
from backend.app.approvals.models import Approval
from backend.app.audit.models import AuditEvent
from backend.app.operations.models import WorkerHeartbeat, WorkerLease, WorkerNode
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceEvent, RuntimeSpaceQuota
from backend.app.runtimes.models import RuntimeEvent, RuntimeLease, WorkspaceRuntime
from backend.app.security.models import SecurityEvent
from backend.app.self_hosted.models import (
    RuntimeCredential,
    SelfHostedJobClaim,
    SelfHostedMcpJob,
    SelfHostedWorker,
)
from backend.app.tasks.models import Task, TaskStep
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.models import Workspace

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
    capacity: dict[str, object] | None = None


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
            capacity=_worker_capacity(capacity, worker_type),
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
                capacity=_worker_capacity(capacity, worker_type),
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
            node.capacity = _worker_capacity(capacity, worker_type)
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
                capacity=dict(node.capacity),
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

    def expire_stale_worker_leases(
        self,
        *,
        workspace_id: UUID | None = None,
        stale_after_seconds: int = 900,
    ) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        statement = select(WorkerLease).where(
            WorkerLease.status.in_(RUNNING_LEASE_STATUSES),
            WorkerLease.started_at < cutoff,
        )
        if workspace_id is not None:
            statement = statement.where(WorkerLease.workspace_id == workspace_id)
        stale_leases = self._session.scalars(statement).all()
        expired_at = datetime.now(UTC)
        for lease in stale_leases:
            lease.status = "expired"
            lease.finished_at = expired_at
            lease.lease_metadata = lease.lease_metadata | {
                "expired_by": "worker_maintenance",
                "expired_at": expired_at.isoformat(),
            }
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
        now = datetime.now(UTC)
        for runtime in stale_runtimes:
            runtime.connection_status = "offline"
            self._append_runtime_space_event(
                runtime,
                "runtime.marked_offline",
                "Runtime heartbeat is stale",
                {"source": "operations.cleanup"},
                created_at=now,
            )
        deleted_records = self._mark_deleted_terminal_runtimes(workspace_id)
        self._session.commit()
        return len(stale_runtimes), deleted_records

    def cleanup_stale_runtimes_across_workspaces(
        self,
        *,
        stale_after_seconds: int = 600,
    ) -> tuple[int, int]:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        stale_runtimes = self._session.scalars(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.connection_status == "online",
                WorkspaceRuntime.last_heartbeat_at.is_not(None),
                WorkspaceRuntime.last_heartbeat_at < cutoff,
            )
        ).all()
        now = datetime.now(UTC)
        for runtime in stale_runtimes:
            runtime.connection_status = "offline"
            self._append_runtime_space_event(
                runtime,
                "runtime.marked_offline",
                "Runtime heartbeat is stale",
                {"source": "worker.maintenance"},
                created_at=now,
            )
        deleted_records = self._mark_deleted_terminal_runtimes(source="worker.maintenance")
        self._session.commit()
        return len(stale_runtimes), deleted_records

    def overview(self, workspace_id: UUID, queue_name: str) -> dict[str, Any]:
        return self.overview_payload(workspace_id, queue_name)

    def overview_payload(self, workspace_id: UUID, queue_name: str) -> dict[str, Any]:
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
            "queue": self.queue_metrics(queue_name, workspace_id).model_dump(),
            "failed_runs": int(failed_runs or 0),
            "offline_runtimes": int(offline_runtimes or 0),
            "workers_online": int(workers_online or 0),
            "security_warnings": int(recent_security_events or 0),
        }

    def capacity_payload(self, workspace_id: UUID, queue_name: str) -> OperationsCapacityResponse:
        return OperationsCapacityResponse(
            generated_at=datetime.now(UTC),
            queue=self._queue_latency(queue_name, workspace_id),
            worker_capacity=self._worker_capacity_aggregate(),
            runtime_spaces=self._runtime_space_saturation(workspace_id),
        )

    def runtime_capacity_payload(self, workspace_id: UUID) -> OperationsRuntimeCapacityResponse:
        return OperationsRuntimeCapacityResponse(
            generated_at=datetime.now(UTC),
            providers=self._runtime_provider_capacity(workspace_id),
            worker_types=self._worker_type_capacity(),
            runtime_spaces=self._runtime_space_saturation(workspace_id),
        )

    def scheduler_payload(self, workspace_id: UUID) -> OperationsSchedulerResponse:
        task_steps = self._session.execute(
            select(TaskStep, Task)
            .join(Task, Task.id == TaskStep.task_id)
            .where(
                TaskStep.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                Task.status.in_(["queued", "running", "waiting_approval", "blocked"]),
            )
        ).all()
        active_runs = int(
            self._session.scalar(
                select(func.count()).select_from(AgentRun).where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.status.in_(["queued", "running", "waiting_approval"]),
                )
            )
            or 0
        )
        waiting_approval_tasks = int(
            self._session.scalar(
                select(func.count()).select_from(Task).where(
                    Task.workspace_id == workspace_id,
                    Task.status == "waiting_approval",
                )
            )
            or 0
        )
        priority_buckets: dict[int, dict[str, int]] = {}
        blocked_reasons: dict[str, int] = {}
        queued_ages: list[int] = []
        now = datetime.now(UTC)
        queued_steps = 0
        running_steps = 0
        blocked_steps = 0
        highest_priority: int | None = None
        for step, task in task_steps:
            priority = int(task.priority or 0)
            highest_priority = (
                priority if highest_priority is None else max(highest_priority, priority)
            )
            bucket = priority_buckets.setdefault(
                priority,
                {"queued_steps": 0, "running_steps": 0, "blocked_steps": 0},
            )
            if step.status == "queued":
                queued_steps += 1
                bucket["queued_steps"] += 1
                queued_ages.append(
                    max(0, int((now - _aware_datetime(step.created_at)).total_seconds()))
                )
            elif step.status == "running":
                running_steps += 1
                bucket["running_steps"] += 1
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            if dependencies.get("scheduling_status") == "blocked":
                blocked_steps += 1
                bucket["blocked_steps"] += 1
                reason = dependencies.get("blocked_reason")
                blocked_reasons[str(reason or "unknown")] = (
                    blocked_reasons.get(str(reason or "unknown"), 0) + 1
                )
        return OperationsSchedulerResponse(
            generated_at=now,
            backlog=SchedulerBacklogResponse(
                queued_steps=queued_steps,
                running_steps=running_steps,
                waiting_approval_tasks=waiting_approval_tasks,
                blocked_steps=blocked_steps,
                active_runs=active_runs,
                oldest_queued_age_seconds=max(queued_ages) if queued_ages else None,
                highest_priority=highest_priority,
            ),
            priority_buckets=[
                SchedulerPriorityBucketResponse(priority=priority, **counts)
                for priority, counts in sorted(priority_buckets.items(), reverse=True)
            ],
            blocked_reasons=[
                SchedulerBlockedReasonResponse(reason=reason, count=count)
                for reason, count in sorted(blocked_reasons.items())
            ],
            policy=self._scheduler_policy(workspace_id),
        )

    def outcomes_payload(
        self,
        workspace_id: UUID,
        *,
        window_seconds: int,
    ) -> OperationsOutcomesResponse:
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=window_seconds)
        runs = self._session.scalars(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.updated_at >= cutoff,
            )
        ).all()
        completed_runs = sum(1 for run in runs if run.status == "completed")
        failed_runs = [run for run in runs if run.status == "failed"]
        cancelled_runs = sum(1 for run in runs if run.status == "cancelled")
        failure_reasons: dict[str, int] = {}
        for run in failed_runs:
            code = "unknown"
            if isinstance(run.error, dict):
                raw_code = run.error.get("code")
                if isinstance(raw_code, str) and raw_code:
                    code = raw_code
            failure_reasons[code] = failure_reasons.get(code, 0) + 1
        pending_approvals = self._session.scalars(
            select(Approval).where(
                Approval.workspace_id == workspace_id,
                Approval.status == "pending",
            )
        ).all()
        pending_by_type: dict[str, int] = {}
        pending_ages: list[int] = []
        high_risk_pending = 0
        for approval in pending_approvals:
            pending_by_type[approval.approval_type] = (
                pending_by_type.get(approval.approval_type, 0) + 1
            )
            pending_ages.append(
                max(0, int((now - _aware_datetime(approval.created_at)).total_seconds()))
            )
            if approval.risk_level == "high":
                high_risk_pending += 1
        total_runs = len(runs)
        return OperationsOutcomesResponse(
            generated_at=now,
            runs=RunOutcomeWindowResponse(
                window_seconds=window_seconds,
                total_runs=total_runs,
                completed_runs=completed_runs,
                failed_runs=len(failed_runs),
                cancelled_runs=cancelled_runs,
                failure_rate=round(len(failed_runs) / total_runs, 4) if total_runs else 0.0,
                failure_reasons=[
                    RunFailureReasonResponse(code=code, count=count)
                    for code, count in sorted(failure_reasons.items())
                ],
            ),
            approvals=ApprovalBacklogResponse(
                pending=len(pending_approvals),
                high_risk_pending=high_risk_pending,
                oldest_pending_age_seconds=max(pending_ages) if pending_ages else None,
                pending_by_type=dict(sorted(pending_by_type.items())),
            ),
        )

    def mcp_jobs_payload(self, workspace_id: UUID) -> OperationsMcpJobsResponse:
        now = datetime.now(UTC)
        jobs = self._session.scalars(
            select(SelfHostedMcpJob).where(SelfHostedMcpJob.workspace_id == workspace_id)
        ).all()
        status_counts: dict[str, int] = {}
        tool_counts: dict[str, dict[str, int]] = {}
        queued_ages: list[int] = []
        for job in jobs:
            status_counts[job.status] = status_counts.get(job.status, 0) + 1
            tool_bucket = tool_counts.setdefault(
                job.tool_name,
                {"queued": 0, "claimed": 0, "completed": 0, "failed": 0, "total": 0},
            )
            tool_bucket["total"] += 1
            if job.status in tool_bucket:
                tool_bucket[job.status] += 1
            if job.status == "queued":
                queued_ages.append(
                    max(0, int((now - _aware_datetime(job.created_at)).total_seconds()))
                )
        return OperationsMcpJobsResponse(
            generated_at=now,
            total=len(jobs),
            queued=status_counts.get("queued", 0),
            claimed=status_counts.get("claimed", 0),
            completed=status_counts.get("completed", 0),
            failed=status_counts.get("failed", 0),
            oldest_queued_age_seconds=max(queued_ages) if queued_ages else None,
            statuses=[
                McpJobStatusBucketResponse(status=status, count=count)
                for status, count in sorted(status_counts.items())
            ],
            tools=[
                McpJobToolBucketResponse(tool_name=tool_name, **counts)
                for tool_name, counts in sorted(tool_counts.items())
            ],
        )

    def self_hosted_machines_payload(
        self,
        workspace_id: UUID,
        *,
        stale_after_seconds: int = 600,
    ) -> OperationsSelfHostedMachinesResponse:
        now = datetime.now(UTC)
        workers = self._session.scalars(
            select(SelfHostedWorker)
            .where(SelfHostedWorker.workspace_id == workspace_id)
            .order_by(SelfHostedWorker.updated_at.desc(), SelfHostedWorker.name.asc())
        ).all()
        if not workers:
            return OperationsSelfHostedMachinesResponse(
                generated_at=now,
                total=0,
                active=0,
                degraded=0,
                quarantined=0,
                revoked=0,
                offline=0,
                stale=0,
                active_job_claims=0,
                active_mcp_jobs=0,
                queued_mcp_jobs=0,
                items=[],
            )
        runtime_ids = [worker.workspace_runtime_id for worker in workers]
        worker_ids = [worker.id for worker in workers]
        runtimes = {
            runtime.id: runtime
            for runtime in self._session.scalars(
                select(WorkspaceRuntime).where(
                    WorkspaceRuntime.workspace_id == workspace_id,
                    WorkspaceRuntime.id.in_(runtime_ids),
                )
            ).all()
        }
        credentials = {
            row.workspace_runtime_id: row
            for row in self._session.scalars(
                select(RuntimeCredential)
                .where(
                    RuntimeCredential.workspace_id == workspace_id,
                    RuntimeCredential.workspace_runtime_id.in_(runtime_ids),
                )
                .order_by(RuntimeCredential.created_at.asc())
            ).all()
        }
        active_claim_counts = _counts_by_uuid(
            self._session.execute(
                select(SelfHostedJobClaim.worker_id, func.count())
                .where(
                    SelfHostedJobClaim.workspace_id == workspace_id,
                    SelfHostedJobClaim.worker_id.in_(worker_ids),
                    SelfHostedJobClaim.status == "claimed",
                )
                .group_by(SelfHostedJobClaim.worker_id)
            ).all()
        )
        mcp_claim_counts = _counts_by_uuid(
            self._session.execute(
                select(SelfHostedMcpJob.worker_id, func.count())
                .where(
                    SelfHostedMcpJob.workspace_id == workspace_id,
                    SelfHostedMcpJob.worker_id.in_(worker_ids),
                    SelfHostedMcpJob.status == "claimed",
                )
                .group_by(SelfHostedMcpJob.worker_id)
            ).all()
        )
        queued_mcp_counts = _counts_by_uuid(
            self._session.execute(
                select(SelfHostedMcpJob.workspace_runtime_id, func.count())
                .where(
                    SelfHostedMcpJob.workspace_id == workspace_id,
                    SelfHostedMcpJob.workspace_runtime_id.in_(runtime_ids),
                    SelfHostedMcpJob.status == "queued",
                )
                .group_by(SelfHostedMcpJob.workspace_runtime_id)
            ).all()
        )
        state_counts = {"active": 0, "degraded": 0, "quarantined": 0, "revoked": 0, "offline": 0}
        items: list[OperationsSelfHostedMachineResponse] = []
        stale_count = 0
        for worker in workers:
            runtime = runtimes.get(worker.workspace_runtime_id)
            if runtime is None:
                continue
            credential = credentials.get(runtime.id)
            trust_state = _self_hosted_trust_state(worker, runtime, credential)
            state_counts[trust_state] = state_counts.get(trust_state, 0) + 1
            heartbeat_age_seconds = _age_seconds(now, worker.last_heartbeat_at)
            stale = (
                heartbeat_age_seconds is not None
                and heartbeat_age_seconds >= stale_after_seconds
                and trust_state in {"active", "degraded", "offline"}
            )
            stale_count += 1 if stale else 0
            warning_code, warning_message = _self_hosted_machine_warning(
                trust_state,
                stale=stale,
            )
            items.append(
                OperationsSelfHostedMachineResponse(
                    worker_id=worker.id,
                    workspace_runtime_id=runtime.id,
                    runtime_space_id=runtime.runtime_space_id,
                    name=worker.name,
                    machine_id=worker.machine_id,
                    version=worker.version,
                    trust_state=trust_state,
                    worker_status=worker.status,
                    runtime_status=runtime.status,
                    connection_status=runtime.connection_status,
                    credential_status=credential.status if credential else None,
                    last_heartbeat_at=worker.last_heartbeat_at,
                    heartbeat_age_seconds=heartbeat_age_seconds,
                    stale=stale,
                    active_job_claims=active_claim_counts.get(worker.id, 0),
                    active_mcp_jobs=mcp_claim_counts.get(worker.id, 0),
                    queued_mcp_jobs=queued_mcp_counts.get(runtime.id, 0),
                    policy_summary=_self_hosted_policy_summary(worker.capabilities),
                    capabilities=worker.capabilities,
                    warning_code=warning_code,
                    warning_message=warning_message,
                )
            )
        return OperationsSelfHostedMachinesResponse(
            generated_at=now,
            total=len(items),
            stale=stale_count,
            active_job_claims=sum(item.active_job_claims for item in items),
            active_mcp_jobs=sum(item.active_mcp_jobs for item in items),
            queued_mcp_jobs=sum(item.queued_mcp_jobs for item in items),
            items=items,
            **state_counts,
        )

    def control_plane_payload(
        self,
        workspace_id: UUID,
        queue_name: str,
        *,
        window_seconds: int,
    ) -> OperationsControlPlaneResponse:
        now = datetime.now(UTC)
        queue = self._queue_latency(queue_name, workspace_id)
        worker_capacity = self._worker_capacity_aggregate()
        runtime_capacity = self.runtime_capacity_payload(workspace_id)
        scheduler = self.scheduler_payload(workspace_id)
        outcomes = self.outcomes_payload(workspace_id, window_seconds=window_seconds)
        mcp_jobs = self.mcp_jobs_payload(workspace_id)
        self_hosted_machines = self.self_hosted_machines_payload(workspace_id)
        issues = self._control_plane_issues(
            queue=queue,
            worker_capacity=worker_capacity,
            runtime_capacity=runtime_capacity,
            scheduler=scheduler,
            outcomes=outcomes,
            mcp_jobs=mcp_jobs,
            self_hosted_machines=self_hosted_machines,
        )
        return OperationsControlPlaneResponse(
            generated_at=now,
            health=_control_plane_health(issues),
            queue=queue,
            worker_capacity=worker_capacity,
            runtime_capacity=runtime_capacity,
            scheduler=scheduler,
            outcomes=outcomes,
            mcp_jobs=mcp_jobs,
            self_hosted_machines=self_hosted_machines,
            issues=issues,
        )

    def _queue_latency(self, queue_name: str, workspace_id: UUID) -> QueueLatencyResponse:
        if self._redis is None:
            return QueueLatencyResponse(
                queue_name=queue_name,
                queued=0,
                oldest_age_seconds=None,
                newest_age_seconds=None,
                average_age_seconds=None,
                highest_priority=None,
            )
        queue = RedisQueue(self._redis, self._keys, queue_name)
        jobs = [job for job in queue.peek(limit=500) if job.workspace_id == workspace_id]
        if not jobs:
            return QueueLatencyResponse(
                queue_name=queue_name,
                queued=0,
                oldest_age_seconds=None,
                newest_age_seconds=None,
                average_age_seconds=None,
                highest_priority=None,
            )
        now = datetime.now(UTC)
        ages = [max(0, int((now - job.created_at).total_seconds())) for job in jobs]
        return QueueLatencyResponse(
            queue_name=queue_name,
            queued=len(jobs),
            oldest_age_seconds=max(ages),
            newest_age_seconds=min(ages),
            average_age_seconds=int(sum(ages) / len(ages)),
            highest_priority=max(job.priority for job in jobs),
        )

    def _worker_capacity_aggregate(self) -> WorkerCapacityAggregateResponse:
        nodes = list(self._session.scalars(select(WorkerNode)).all())
        running_jobs = int(
            self._session.scalar(
                select(func.count()).select_from(WorkerLease).where(
                    WorkerLease.status.in_(RUNNING_LEASE_STATUSES),
                )
            )
            or 0
        )
        max_jobs = sum(_positive_int(node.capacity.get("max_jobs"), 1) for node in nodes)
        available_slots = max(0, max_jobs - running_jobs)
        online = sum(1 for node in nodes if node.status == "online")
        draining = sum(1 for node in nodes if node.status == "draining")
        offline = sum(1 for node in nodes if node.status == "offline")
        utilization = round(running_jobs / max_jobs, 4) if max_jobs > 0 else 0.0
        return WorkerCapacityAggregateResponse(
            workers_total=len(nodes),
            workers_online=online,
            workers_draining=draining,
            workers_offline=offline,
            max_jobs=max_jobs,
            running_jobs=running_jobs,
            available_slots=available_slots,
            utilization=utilization,
        )

    def _runtime_space_saturation(
        self,
        workspace_id: UUID,
    ) -> list[RuntimeSpaceSaturationResponse]:
        spaces = self._session.scalars(
            select(RuntimeSpace)
            .where(RuntimeSpace.workspace_id == workspace_id)
            .order_by(RuntimeSpace.created_at.desc())
        ).all()
        if not spaces:
            return []
        space_ids = [space.id for space in spaces]
        active_runtime_counts = dict(
            self._session.execute(
                select(WorkspaceRuntime.runtime_space_id, func.count())
                .where(
                    WorkspaceRuntime.workspace_id == workspace_id,
                    WorkspaceRuntime.runtime_space_id.in_(space_ids),
                    WorkspaceRuntime.status.in_(["created", "running"]),
                )
                .group_by(WorkspaceRuntime.runtime_space_id)
            ).all()
        )
        quotas = self._session.scalars(
            select(RuntimeSpaceQuota).where(
                RuntimeSpaceQuota.workspace_id == workspace_id,
                RuntimeSpaceQuota.runtime_space_id.in_(space_ids),
                RuntimeSpaceQuota.status == "active",
            )
        ).all()
        quotas_by_space: dict[UUID, list[RuntimeSpaceQuota]] = {}
        for quota in quotas:
            quotas_by_space.setdefault(quota.runtime_space_id, []).append(quota)
        responses: list[RuntimeSpaceSaturationResponse] = []
        for space in spaces:
            quota_usages = [
                _runtime_space_quota_usage(quota) for quota in quotas_by_space.get(space.id, [])
            ]
            responses.append(
                RuntimeSpaceSaturationResponse(
                    runtime_space_id=space.id,
                    name=space.name,
                    status=space.status,
                    active_runtimes=int(active_runtime_counts.get(space.id, 0)),
                    quotas=quota_usages,
                    saturated=any(quota.saturated for quota in quota_usages),
                )
            )
        return responses

    def _runtime_provider_capacity(
        self,
        workspace_id: UUID,
    ) -> list[RuntimeProviderCapacityResponse]:
        runtimes = self._session.scalars(
            select(WorkspaceRuntime)
            .where(WorkspaceRuntime.workspace_id == workspace_id)
            .order_by(WorkspaceRuntime.runtime_provider.asc(), WorkspaceRuntime.runtime_type.asc())
        ).all()
        active_runs_by_runtime = dict(
            self._session.execute(
                select(AgentRun.runtime_id, func.count())
                .where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.runtime_id.is_not(None),
                    AgentRun.status.in_(["queued", "running", "waiting_approval"]),
                )
                .group_by(AgentRun.runtime_id)
            ).all()
        )
        grouped: dict[tuple[str, str], dict[str, int]] = {}
        for runtime in runtimes:
            key = (runtime.runtime_provider, runtime.runtime_type)
            bucket = grouped.setdefault(
                key,
                {
                    "total": 0,
                    "online": 0,
                    "offline": 0,
                    "degraded": 0,
                    "running": 0,
                    "capacity_slots": 0,
                    "active_runs": 0,
                },
            )
            bucket["total"] += 1
            if runtime.connection_status == "online":
                bucket["online"] += 1
            elif runtime.connection_status == "degraded":
                bucket["degraded"] += 1
            elif runtime.connection_status == "offline":
                bucket["offline"] += 1
            if runtime.status in {"created", "running", "active"}:
                bucket["running"] += 1
            bucket["capacity_slots"] += _runtime_capacity_slots(runtime)
            bucket["active_runs"] += int(active_runs_by_runtime.get(runtime.id, 0))
        return [
            RuntimeProviderCapacityResponse(
                provider=provider,
                runtime_type=runtime_type,
                utilization=round(values["active_runs"] / values["capacity_slots"], 4)
                if values["capacity_slots"] > 0
                else 0.0,
                **values,
            )
            for (provider, runtime_type), values in sorted(grouped.items())
        ]

    def _worker_type_capacity(self) -> list[WorkerTypeCapacityResponse]:
        nodes = self._session.scalars(select(WorkerNode)).all()
        running_by_worker = dict(
            self._session.execute(
                select(WorkerLease.worker_id, func.count())
                .where(WorkerLease.status.in_(RUNNING_LEASE_STATUSES))
                .group_by(WorkerLease.worker_id)
            ).all()
        )
        grouped: dict[str, dict[str, int]] = {}
        for node in nodes:
            bucket = grouped.setdefault(
                node.worker_type,
                {
                    "workers_total": 0,
                    "workers_online": 0,
                    "workers_draining": 0,
                    "max_jobs": 0,
                    "running_jobs": 0,
                    "available_slots": 0,
                },
            )
            max_jobs = _positive_int(node.capacity.get("max_jobs"), 1)
            running_jobs = int(running_by_worker.get(node.worker_id, 0))
            bucket["workers_total"] += 1
            bucket["workers_online"] += 1 if node.status == "online" else 0
            bucket["workers_draining"] += 1 if node.status == "draining" else 0
            bucket["max_jobs"] += max_jobs
            bucket["running_jobs"] += running_jobs
            bucket["available_slots"] += max(0, max_jobs - running_jobs)
        return [
            WorkerTypeCapacityResponse(
                worker_type=worker_type,
                utilization=round(values["running_jobs"] / values["max_jobs"], 4)
                if values["max_jobs"] > 0
                else 0.0,
                **values,
            )
            for worker_type, values in sorted(grouped.items())
        ]

    def _control_plane_issues(
        self,
        *,
        queue: QueueLatencyResponse,
        worker_capacity: WorkerCapacityAggregateResponse,
        runtime_capacity: OperationsRuntimeCapacityResponse,
        scheduler: OperationsSchedulerResponse,
        outcomes: OperationsOutcomesResponse,
        mcp_jobs: OperationsMcpJobsResponse,
        self_hosted_machines: OperationsSelfHostedMachinesResponse,
    ) -> list[OperationsControlPlaneIssueResponse]:
        issues: list[OperationsControlPlaneIssueResponse] = []
        if queue.oldest_age_seconds is not None and queue.oldest_age_seconds >= 300:
            issues.append(
                OperationsControlPlaneIssueResponse(
                    severity="warning",
                    code="queue_latency_high",
                    message="Queued jobs have waited longer than 5 minutes.",
                    count=queue.queued,
                    metadata={"oldest_age_seconds": queue.oldest_age_seconds},
                )
            )
        if worker_capacity.workers_total == 0:
            issues.append(
                OperationsControlPlaneIssueResponse(
                    severity="critical",
                    code="worker_fleet_empty",
                    message="No workers are registered for job execution.",
                )
            )
        elif worker_capacity.available_slots == 0 and queue.queued > 0:
            issues.append(
                OperationsControlPlaneIssueResponse(
                    severity="critical",
                    code="worker_capacity_exhausted",
                    message="Queued jobs exist but the worker fleet has no free slots.",
                    count=queue.queued,
                    metadata={
                        "running_jobs": worker_capacity.running_jobs,
                        "max_jobs": worker_capacity.max_jobs,
                    },
                )
            )
        if worker_capacity.workers_draining > 0:
            issues.append(
                OperationsControlPlaneIssueResponse(
                    severity="info",
                    code="workers_draining",
                    message="Some workers are draining and will not accept new jobs.",
                    count=worker_capacity.workers_draining,
                )
            )
        saturated_spaces = [
            space for space in runtime_capacity.runtime_spaces if space.saturated
        ]
        if saturated_spaces:
            issues.append(
                OperationsControlPlaneIssueResponse(
                    severity="warning",
                    code="runtime_space_saturated",
                    message="Runtime space quotas are saturated.",
                    count=len(saturated_spaces),
                    metadata={
                        "runtime_space_ids": [
                            str(space.runtime_space_id) for space in saturated_spaces
                        ]
                    },
                )
            )
        degraded_providers = [
            provider for provider in runtime_capacity.providers if provider.degraded > 0
        ]
        if degraded_providers:
            issues.append(
                OperationsControlPlaneIssueResponse(
                    severity="warning",
                    code="runtime_provider_degraded",
                    message="Runtime providers have degraded capacity.",
                    count=sum(provider.degraded for provider in degraded_providers),
                    metadata={
                        "providers": [
                            f"{provider.provider}:{provider.runtime_type}"
                            for provider in degraded_providers
                        ]
                    },
                )
            )
        if scheduler.backlog.blocked_steps > 0:
            issues.append(
                OperationsControlPlaneIssueResponse(
                    severity="warning",
                    code="scheduler_blocked_steps",
                    message="Some queued steps are blocked by scheduler policy.",
                    count=scheduler.backlog.blocked_steps,
                    metadata={
                        "blocked_reasons": [
                            reason.model_dump() for reason in scheduler.blocked_reasons
                        ]
                    },
                )
            )
        if outcomes.runs.failure_rate >= 0.2 and outcomes.runs.total_runs >= 5:
            issues.append(
                OperationsControlPlaneIssueResponse(
                    severity="warning",
                    code="run_failure_rate_high",
                    message="Recent run failure rate is elevated.",
                    count=outcomes.runs.failed_runs,
                    metadata={"failure_rate": outcomes.runs.failure_rate},
                )
            )
        if outcomes.approvals.high_risk_pending > 0:
            issues.append(
                OperationsControlPlaneIssueResponse(
                    severity="warning",
                    code="high_risk_approval_backlog",
                    message="High-risk approvals are waiting for operator review.",
                    count=outcomes.approvals.high_risk_pending,
                    metadata={
                        "oldest_pending_age_seconds": outcomes.approvals.oldest_pending_age_seconds
                    },
                )
            )
        if mcp_jobs.failed > 0:
            issues.append(
                OperationsControlPlaneIssueResponse(
                    severity="warning",
                    code="mcp_jobs_failed",
                    message="MCP tool jobs have failed.",
                    count=mcp_jobs.failed,
                )
            )
        if (
            mcp_jobs.oldest_queued_age_seconds is not None
            and mcp_jobs.oldest_queued_age_seconds >= 300
        ):
            issues.append(
                OperationsControlPlaneIssueResponse(
                    severity="warning",
                    code="mcp_queue_latency_high",
                    message="MCP tool jobs have waited longer than 5 minutes.",
                    count=mcp_jobs.queued,
                    metadata={"oldest_queued_age_seconds": mcp_jobs.oldest_queued_age_seconds},
                )
            )
        unavailable_self_hosted = (
            self_hosted_machines.degraded
            + self_hosted_machines.quarantined
            + self_hosted_machines.revoked
            + self_hosted_machines.offline
        )
        if unavailable_self_hosted > 0:
            issues.append(
                OperationsControlPlaneIssueResponse(
                    severity="warning",
                    code="self_hosted_machines_unhealthy",
                    message="Self-hosted machines need operator attention.",
                    count=unavailable_self_hosted,
                    metadata={
                        "degraded": self_hosted_machines.degraded,
                        "quarantined": self_hosted_machines.quarantined,
                        "revoked": self_hosted_machines.revoked,
                        "offline": self_hosted_machines.offline,
                    },
                )
            )
        if self_hosted_machines.stale > 0:
            issues.append(
                OperationsControlPlaneIssueResponse(
                    severity="warning",
                    code="self_hosted_heartbeat_stale",
                    message="Some self-hosted machines have stale heartbeats.",
                    count=self_hosted_machines.stale,
                )
            )
        return issues

    def _scheduler_policy(self, workspace_id: UUID) -> SchedulerPolicyResponse:
        workspace = self._session.get(Workspace, workspace_id)
        settings = workspace.settings if workspace is not None else {}
        raw_scheduler = settings.get("scheduler") if isinstance(settings, dict) else None
        scheduler = raw_scheduler if isinstance(raw_scheduler, dict) else {}
        return SchedulerPolicyResponse(
            max_active_runs=_positive_int_or_none(scheduler.get("max_active_runs")),
            max_running_tasks=_positive_int_or_none(scheduler.get("max_running_tasks")),
            max_runs_to_start_per_tick=_positive_int_or_none(
                scheduler.get("max_runs_to_start_per_tick")
            ),
            max_steps_per_task_per_tick=_positive_int_or_none(
                scheduler.get("max_steps_per_task_per_tick")
            ),
            starvation_boost_after_seconds=_positive_int_or_none(
                scheduler.get("starvation_boost_after_seconds")
            ),
            resource_limits=_positive_number_dict(scheduler.get("resource_limits")),
        )

    def _mark_deleted_terminal_runtimes(
        self,
        workspace_id: UUID | None = None,
        *,
        source: str = "operations.cleanup",
    ) -> int:
        statement = select(WorkspaceRuntime).where(
            WorkspaceRuntime.status.in_(["stopped", "failed"])
        )
        if workspace_id is not None:
            statement = statement.where(WorkspaceRuntime.workspace_id == workspace_id)
        terminal = self._session.scalars(statement).all()
        now = datetime.now(UTC)
        for runtime in terminal:
            runtime.status = "deleted"
            runtime.connection_status = "offline"
            self._append_runtime_space_event(
                runtime,
                "runtime.record_deleted",
                "Terminal runtime record marked deleted",
                {"source": source},
                created_at=now,
            )
        return len(terminal)

    def _append_runtime_space_event(
        self,
        runtime: WorkspaceRuntime,
        event_type: str,
        message: str,
        metadata: dict[str, object],
        *,
        created_at: datetime,
    ) -> None:
        if runtime.runtime_space_id is None:
            return
        self._session.add(
            RuntimeSpaceEvent(
                workspace_id=runtime.workspace_id,
                runtime_space_id=runtime.runtime_space_id,
                event_type=event_type,
                message=message,
                event_metadata={"runtime_id": str(runtime.id), **metadata},
                created_at=created_at,
            )
        )

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


def _positive_int_or_none(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    return None


def _positive_number_dict(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): float(raw_value)
        for key, raw_value in value.items()
        if isinstance(raw_value, int | float) and not isinstance(raw_value, bool) and raw_value >= 0
    }


def _worker_capacity(capacity: dict[str, object] | None, worker_type: str) -> dict[str, object]:
    normalized = dict(capacity or {})
    normalized.setdefault("worker_type", worker_type)
    return normalized


def _counts_by_uuid(rows: list[tuple[UUID | None, int]]) -> dict[UUID, int]:
    return {key: int(count) for key, count in rows if key is not None}


def _age_seconds(now: datetime, value: datetime | None) -> int | None:
    if value is None:
        return None
    return max(0, int((now - _aware_datetime(value)).total_seconds()))


def _self_hosted_trust_state(
    worker: SelfHostedWorker,
    runtime: WorkspaceRuntime,
    credential: RuntimeCredential | None,
) -> str:
    if credential is not None and credential.status == "revoked":
        return "revoked"
    if worker.status == "revoked" or runtime.status == "revoked":
        return "revoked"
    if worker.status == "quarantined" or runtime.status == "quarantined":
        return "quarantined"
    if worker.status == "degraded" or runtime.connection_status == "degraded":
        return "degraded"
    if worker.status in {"offline", "disabled"} or runtime.connection_status == "offline":
        return "offline"
    return "active"


def _self_hosted_policy_summary(capabilities: dict[str, object]) -> dict[str, object]:
    return {
        "allowed_tools": _string_list(capabilities.get("allowed_tools")),
        "supported_models": _string_list(capabilities.get("supported_models")),
        "supported_runtimes": _string_list(capabilities.get("supported_runtimes"))
        or _string_list(capabilities.get("runtime_types")),
        "supported_network_modes": _string_list(capabilities.get("supported_network_modes"))
        or _string_list(capabilities.get("network_modes")),
        "allowed_runtime_space_ids": _string_list(capabilities.get("allowed_runtime_space_ids")),
        "max_concurrent_jobs": _positive_int_or_none(capabilities.get("max_concurrent_jobs")),
        "max_concurrent_mcp_jobs": _positive_int_or_none(
            capabilities.get("max_concurrent_mcp_jobs")
        ),
        "max_artifact_bytes": _positive_int_or_none(capabilities.get("max_artifact_bytes")),
    }


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _self_hosted_machine_warning(
    trust_state: str,
    *,
    stale: bool,
) -> tuple[str | None, str | None]:
    if trust_state == "revoked":
        return "credential_revoked", "Machine credential is revoked."
    if trust_state == "quarantined":
        return "machine_quarantined", "Machine is quarantined and cannot accept jobs."
    if trust_state == "degraded":
        return "machine_degraded", "Machine is degraded and cannot accept jobs."
    if trust_state == "offline":
        return "machine_offline", "Machine is offline."
    if stale:
        return "heartbeat_stale", "Machine heartbeat is stale."
    return None, None


def _runtime_capacity_slots(runtime: WorkspaceRuntime) -> int:
    for key in ("max_concurrent_jobs", "max_jobs", "slots", "capacity_slots"):
        value = _positive_int_or_none(runtime.capabilities.get(key))
        if value is not None:
            return value
    if runtime.runtime_provider == "self_hosted":
        return 1
    return 1


def _runtime_space_quota_usage(quota: RuntimeSpaceQuota) -> RuntimeSpaceQuotaUsageResponse:
    utilization = (
        round(quota.reserved_value / quota.limit_value, 4) if quota.limit_value > 0 else 0.0
    )
    return RuntimeSpaceQuotaUsageResponse(
        quota_key=quota.quota_key,
        limit_value=quota.limit_value,
        reserved_value=quota.reserved_value,
        unit=quota.unit,
        utilization=utilization,
        saturated=quota.limit_value > 0 and quota.reserved_value >= quota.limit_value,
    )


def _control_plane_health(issues: list[OperationsControlPlaneIssueResponse]) -> str:
    severities = {issue.severity for issue in issues}
    if "critical" in severities:
        return "critical"
    if "warning" in severities:
        return "warning"
    return "healthy"


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
