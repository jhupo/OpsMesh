from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar, cast
from uuid import UUID

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import Select, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.admin.policies import PlatformPolicyService
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.operations import (
    ApprovalBacklogResponse,
    BlockedStepExplanationResponse,
    BlockedStepUnblockResponse,
    DeadLetterJobsResponse,
    McpJobStatusBucketResponse,
    McpJobToolBucketResponse,
    OperationsCapacityResponse,
    OperationsControlPlaneIssueResponse,
    OperationsControlPlaneResponse,
    OperationsMcpJobsResponse,
    OperationsOutcomesResponse,
    OperationsQueueInsightsResponse,
    OperationsRunActivityResponse,
    OperationsRuntimeCapacityResponse,
    OperationsSchedulerResponse,
    OperationsSelfHostedMachineResponse,
    OperationsSelfHostedMachinesResponse,
    OperationsWorkerLifecycleResponse,
    QueueGovernanceDiagnosticsResponse,
    QueueGovernanceIssueResponse,
    QueueGovernanceReconcileAction,
    QueueGovernanceReconcileResponse,
    QueueJobTypeBucketResponse,
    QueueLatencyResponse,
    QueueMetricsResponse,
    QueuePriorityBucketResponse,
    RunActivityOldestRunResponse,
    RunActivityPhaseBucketResponse,
    RunFailureReasonResponse,
    RunOutcomeWindowResponse,
    RuntimeProviderCapacityResponse,
    RuntimeSpaceQuotaUsageResponse,
    RuntimeSpaceSaturationResponse,
    SchedulerBacklogResponse,
    SchedulerBlockedReasonResponse,
    SchedulerControlResponse,
    SchedulerPolicyResponse,
    SchedulerPriorityBucketResponse,
    StaleRunDiagnosticResponse,
    StaleRunRecoveryItemResponse,
    StaleRunRecoveryResponse,
    StaleRunsDiagnosticsResponse,
    WorkerCapacityAggregateResponse,
    WorkerLifecycleBucketResponse,
    WorkerTypeCapacityResponse,
)
from backend.app.approvals.models import Approval
from backend.app.audit.models import AuditEvent
from backend.app.audit.service import AuditService
from backend.app.core.metrics import GaugeMetric
from backend.app.core.trace_context import current_trace_metadata, with_current_trace_metadata
from backend.app.operations.models import WorkerHeartbeat, WorkerLease, WorkerNode
from backend.app.orchestration.blocked_reasons import explain_blocked_reason
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.activity import run_activity
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus
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
from backend.app.teams.models import AgentTeam
from backend.app.teams.runtime import (
    TEAM_RUNTIME_HEARTBEAT_STALE_AFTER_SECONDS,
    TEAM_RUNTIME_STATUS_KEY,
    TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY,
)
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.data_lifecycle import WorkspaceDataLifecycleService
from backend.app.workspaces.models import Workspace

T = TypeVar("T")
RUNNING_LEASE_STATUSES = {"running"}
TERMINAL_LEASE_STATUSES = {"completed", "failed", "expired"}
LIFECYCLE_EVENTS_LIMIT = 50
PROMETHEUS_WORKER_STALE_AFTER_SECONDS = 300
PROMETHEUS_QUEUE_SCAN_LIMIT = 1_000
ACTIVE_RUNTIME_RUN_STATUSES = {"queued", "running", "waiting_approval"}


@dataclass(frozen=True)
class WorkerCapacitySnapshot:
    worker_id: str
    max_jobs: int
    running_jobs: int
    available_slots: int
    accepting: bool
    reason: str | None = None
    capacity: dict[str, object] | None = None


@dataclass(frozen=True)
class QueueGovernanceSnapshot:
    generated_at: datetime
    queue_name: str
    scan_limit: int
    stale_after_seconds: int
    queued_total: int
    queued_scanned: int
    agent_run_jobs: list[JobPayload]
    dead_letter_total: int
    orphaned_jobs: list[JobPayload]
    non_runnable_jobs: list[JobPayload]
    duplicate_jobs: list[JobPayload]
    missing_runs: list[AgentRun]
    old_queued_jobs: list[JobPayload]
    truncated: bool


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

    def pause_scheduler(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        reason: str | None,
    ) -> SchedulerControlResponse | None:
        workspace = self._session.get(Workspace, workspace_id)
        if workspace is None:
            return None
        settings = dict(workspace.settings or {})
        scheduler = _scheduler_settings(settings)
        scheduler["paused"] = True
        scheduler["pause_reason"] = _non_empty_string_or_none(reason) or "operator_paused"
        settings["scheduler"] = scheduler
        workspace.settings = settings
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=actor_user_id,
            action="workspace.scheduler_paused",
            target_type="workspace",
            target_id=workspace.id,
            metadata={"pause_reason": scheduler["pause_reason"]},
        )
        self._session.commit()
        return SchedulerControlResponse(
            workspace_id=workspace.id,
            paused=True,
            pause_reason=str(scheduler["pause_reason"]),
            cleared_blocked_steps=0,
            policy=self._scheduler_policy(workspace.id),
        )

    def resume_scheduler(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
    ) -> SchedulerControlResponse | None:
        workspace = self._session.get(Workspace, workspace_id)
        if workspace is None:
            return None
        settings = dict(workspace.settings or {})
        scheduler = _scheduler_settings(settings)
        previous_reason = _non_empty_string_or_none(scheduler.get("pause_reason"))
        scheduler["paused"] = False
        scheduler.pop("pause_reason", None)
        settings["scheduler"] = scheduler
        workspace.settings = settings
        cleared = self._clear_workspace_pause_blocks(
            workspace.id,
            reason=previous_reason or "workspace_scheduler_paused",
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace.id,
            user_id=actor_user_id,
            action="workspace.scheduler_resumed",
            target_type="workspace",
            target_id=workspace.id,
            metadata={
                "previous_pause_reason": previous_reason,
                "cleared_blocked_steps": cleared,
            },
        )
        self._session.commit()
        return SchedulerControlResponse(
            workspace_id=workspace.id,
            paused=False,
            pause_reason=None,
            cleared_blocked_steps=cleared,
            policy=self._scheduler_policy(workspace.id),
        )

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
                max_jobs=_positive_int(node.capacity.get("max_jobs"), default_max_jobs),
                running_jobs=self._running_leases_for_worker(worker_id),
                available_slots=0,
                accepting=False,
                reason=f"worker_{node.status}",
                capacity=dict(node.capacity),
            )
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
                lease_metadata=_append_worker_lifecycle_events(
                    metadata or {},
                    [
                        _worker_lifecycle_event(
                            "claimed",
                            now,
                            attempt=job.attempt,
                            metadata=current_trace_metadata(),
                        ),
                        _worker_lifecycle_event(
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
            lease.lease_metadata = _append_worker_lifecycle_events(
                existing_metadata | (metadata or {}),
                [
                    _worker_lifecycle_event(
                        "claimed",
                        now,
                        attempt=job.attempt,
                        metadata=current_trace_metadata(),
                    ),
                    _worker_lifecycle_event(
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
        lease.lease_metadata = _append_worker_lifecycle_events(
            dict(lease.lease_metadata or {}) | (metadata or {}),
            [
                _worker_lifecycle_event(
                    _worker_finish_lifecycle_event(status),
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
            lease.lease_metadata = _append_worker_lifecycle_events(
                dict(lease.lease_metadata or {}),
                [
                    _worker_lifecycle_event(
                        "heartbeat",
                        at,
                        attempt=lease.attempt,
                        status=status,
                        metadata=current_trace_metadata(),
                    )
                ],
            )

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
            lease.lease_metadata = _append_worker_lifecycle_events(
                dict(lease.lease_metadata or {})
                | {
                    "expired_by": "worker_maintenance",
                    "expired_at": expired_at.isoformat(),
                },
                [
                    _worker_lifecycle_event(
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

    def stale_runs_diagnostics(
        self,
        workspace_id: UUID,
        *,
        stale_after_seconds: int,
        statuses: list[str] | None = None,
        limit: int = 100,
    ) -> StaleRunsDiagnosticsResponse:
        now = datetime.now(UTC)
        normalized_statuses = _normalized_stale_run_statuses(statuses)
        runs = self._stale_runs(
            workspace_id,
            stale_after_seconds=stale_after_seconds,
            statuses=normalized_statuses,
            limit=limit,
            now=now,
        )
        leases_by_run_id = self._latest_worker_leases_by_run_id(
            workspace_id,
            [run.id for run in runs],
        )
        items = [
            _stale_run_diagnostic_item(
                run,
                now=now,
                lease=leases_by_run_id.get(run.id),
            )
            for run in runs
        ]
        return StaleRunsDiagnosticsResponse(
            generated_at=now,
            stale_after_seconds=stale_after_seconds,
            total=len(items),
            items=items,
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
        now = datetime.now(UTC)
        normalized_statuses = _normalized_stale_run_statuses(statuses)
        runs = self._stale_runs(
            workspace_id,
            stale_after_seconds=stale_after_seconds,
            statuses=normalized_statuses,
            limit=limit,
            now=now,
        )
        queue = RedisQueue(self._redis, self._keys, queue_name) if self._redis is not None else None
        run_service = RunOrchestrationService(self._session, queue=queue)
        items: list[StaleRunRecoveryItemResponse] = []
        requeued = 0
        failed_closed = 0
        for run in runs:
            previous_status = run.status
            if previous_status == RunStatus.QUEUED.value:
                enqueued = run_service.requeue_stale_run(
                    run,
                    requested_by_user_id=actor_user_id,
                    reason=reason,
                )
                requeued += 1
                items.append(
                    StaleRunRecoveryItemResponse(
                        run_id=run.id,
                        previous_status=RunStatus.QUEUED.value,
                        action="requeued",
                        enqueued=enqueued,
                    )
                )
                continue

            run_service.fail_recovered_run(
                run,
                code="stale_worker_run",
                message=_stale_run_failure_message(previous_status),
                retryable=True,
                event_message="Marked failed by stale run recovery control",
            )
            failed_closed += 1
            items.append(
                StaleRunRecoveryItemResponse(
                    run_id=run.id,
                    previous_status=cast(RunStatus, RunStatus(previous_status)).value,
                    action="failed_closed",
                )
            )

        expired_worker_leases = self._expire_worker_leases_for_runs(
            workspace_id=workspace_id,
            run_ids=[run.id for run in runs],
            expired_at=now,
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="worker.stale_runs_recovered",
            target_type="workspace",
            target_id=workspace_id,
            metadata={
                "stale_after_seconds": stale_after_seconds,
                "statuses": sorted(status.value for status in normalized_statuses),
                "limit": limit,
                "queue_name": queue_name,
                "reason": _non_empty_string_or_none(reason),
                "scanned_runs": len(runs),
                "requeued_runs": requeued,
                "failed_closed_runs": failed_closed,
                "expired_worker_leases": expired_worker_leases,
            },
        )
        self._session.commit()
        return StaleRunRecoveryResponse(
            workspace_id=workspace_id,
            stale_after_seconds=stale_after_seconds,
            scanned_runs=len(runs),
            requeued_runs=requeued,
            failed_closed_runs=failed_closed,
            expired_worker_leases=expired_worker_leases,
            items=items,
        )

    def _stale_runs(
        self,
        workspace_id: UUID,
        *,
        stale_after_seconds: int,
        statuses: set[RunStatus],
        limit: int,
        now: datetime,
    ) -> list[AgentRun]:
        cutoff = now - timedelta(seconds=stale_after_seconds)
        candidates = self._session.scalars(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.status.in_([status.value for status in statuses]),
            )
            .order_by(AgentRun.updated_at.asc(), AgentRun.created_at.asc())
            .limit(limit * 3)
        ).all()
        stale_runs = []
        for run in candidates:
            age_anchor = _stale_run_age_anchor(run)
            if age_anchor is not None and age_anchor < cutoff:
                stale_runs.append(run)
        stale_runs.sort(key=lambda run: _stale_run_age_anchor(run) or run.created_at)
        return stale_runs[:limit]

    def _latest_worker_leases_by_run_id(
        self,
        workspace_id: UUID,
        run_ids: list[UUID],
    ) -> dict[UUID, WorkerLease]:
        if not run_ids:
            return {}
        leases = self._session.scalars(
            select(WorkerLease)
            .where(
                WorkerLease.workspace_id == workspace_id,
                WorkerLease.job_type == JobType.AGENT_RUN.value,
                WorkerLease.resource_id.in_(run_ids),
            )
            .order_by(WorkerLease.started_at.desc(), WorkerLease.created_at.desc())
        ).all()
        latest: dict[UUID, WorkerLease] = {}
        for lease in leases:
            latest.setdefault(lease.resource_id, lease)
        return latest

    def _expire_worker_leases_for_runs(
        self,
        *,
        workspace_id: UUID,
        run_ids: list[UUID],
        expired_at: datetime,
    ) -> int:
        if not run_ids:
            return 0
        leases = self._session.scalars(
            select(WorkerLease).where(
                WorkerLease.workspace_id == workspace_id,
                WorkerLease.job_type == JobType.AGENT_RUN.value,
                WorkerLease.resource_id.in_(run_ids),
                WorkerLease.status.in_(RUNNING_LEASE_STATUSES),
            )
        ).all()
        for lease in leases:
            lease.status = "expired"
            lease.finished_at = expired_at
            lease.lease_metadata = _append_worker_lifecycle_events(
                dict(lease.lease_metadata or {})
                | {
                    "expired_by": "stale_run_recovery",
                    "expired_at": expired_at.isoformat(),
                },
                [
                    _worker_lifecycle_event(
                        "expired",
                        expired_at,
                        attempt=lease.attempt,
                        status="expired",
                        metadata=current_trace_metadata(),
                    )
                ],
            )
        return len(leases)

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

    def prometheus_gauges(
        self,
        queue_name: str,
        *,
        worker_stale_after_seconds: int = PROMETHEUS_WORKER_STALE_AFTER_SECONDS,
        queue_scan_limit: int = PROMETHEUS_QUEUE_SCAN_LIMIT,
    ) -> list[GaugeMetric]:
        now = datetime.now(UTC)
        gauges: list[GaugeMetric] = []

        try:
            queue = self.queue_metrics(queue_name)
            gauges.extend(
                [
                    GaugeMetric(
                        "chaincloud_queue_jobs",
                        queue.queued,
                        labels={"queue_name": queue_name, "state": "queued"},
                        help_text="Jobs currently waiting in Redis queues.",
                    ),
                    GaugeMetric(
                        "chaincloud_queue_jobs",
                        queue.dead_letter,
                        labels={"queue_name": queue_name, "state": "dead_letter"},
                        help_text="Jobs currently waiting in Redis queues.",
                    ),
                    GaugeMetric(
                        "chaincloud_queue_idempotency_keys",
                        queue.idempotency_keys,
                        labels={"queue_name": queue_name},
                        help_text="Active Redis idempotency keys for queued work.",
                    ),
                    GaugeMetric(
                        "chaincloud_queue_oldest_queued_age_seconds",
                        self._oldest_queued_age_seconds(queue_name, now, queue_scan_limit) or 0,
                        labels={"queue_name": queue_name},
                        help_text="Age of the oldest queued job seen in the queue scan.",
                    ),
                ]
            )
        except (OSError, RedisError, TimeoutError):
            pass

        try:
            gauges.extend(self._prometheus_worker_gauges(now, worker_stale_after_seconds))
            gauges.extend(self._prometheus_runtime_gauges())
            gauges.extend(self._prometheus_team_runtime_gauges(now))
        except SQLAlchemyError:
            pass
        return gauges

    def queue_insights(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        scan_limit: int = 500,
    ) -> OperationsQueueInsightsResponse:
        scan_limit = max(1, min(scan_limit, 5_000))
        now = datetime.now(UTC)
        if self._redis is None:
            return OperationsQueueInsightsResponse(
                generated_at=now,
                queue_name=queue_name,
                scan_limit=scan_limit,
                queued_total=0,
                dead_letter_total=0,
                queued_scanned=0,
                dead_letter_scanned=0,
                truncated=False,
                oldest_queued_age_seconds=None,
                highest_priority=None,
                priority_buckets=[],
                job_type_buckets=[],
            )

        queue = RedisQueue(self._redis, self._keys, queue_name)
        queued_total = queue.count_queued(workspace_id=workspace_id)
        dead_letter_total = queue.count_dead_letters(workspace_id=workspace_id)
        queued_jobs = [
            job for job in queue.peek(limit=scan_limit) if job.workspace_id == workspace_id
        ]
        dead_letter_jobs = queue.list_dead_letters(scan_limit, workspace_id=workspace_id)

        priority_buckets: dict[int, dict[str, int | None]] = {}
        job_type_buckets: dict[str, dict[str, int | None]] = {}
        oldest_queued_age_seconds: int | None = None
        highest_priority: int | None = None

        for job in queued_jobs:
            age_seconds = max(0, int((now - _aware_datetime(job.created_at)).total_seconds()))
            oldest_queued_age_seconds = (
                age_seconds
                if oldest_queued_age_seconds is None
                else max(oldest_queued_age_seconds, age_seconds)
            )
            highest_priority = (
                job.priority
                if highest_priority is None
                else max(highest_priority, job.priority)
            )
            priority_bucket = priority_buckets.setdefault(
                job.priority,
                {"queued": 0, "dead_letter": 0, "oldest_queued_age_seconds": None},
            )
            priority_bucket["queued"] = int(priority_bucket["queued"] or 0) + 1
            priority_bucket["oldest_queued_age_seconds"] = _max_optional_int(
                priority_bucket["oldest_queued_age_seconds"],
                age_seconds,
            )

            job_type = str(job.job_type)
            type_bucket = job_type_buckets.setdefault(
                job_type,
                {
                    "queued": 0,
                    "dead_letter": 0,
                    "highest_priority": None,
                    "oldest_queued_age_seconds": None,
                },
            )
            type_bucket["queued"] = int(type_bucket["queued"] or 0) + 1
            type_bucket["highest_priority"] = _max_optional_int(
                type_bucket["highest_priority"],
                job.priority,
            )
            type_bucket["oldest_queued_age_seconds"] = _max_optional_int(
                type_bucket["oldest_queued_age_seconds"],
                age_seconds,
            )

        for job in dead_letter_jobs:
            priority_bucket = priority_buckets.setdefault(
                job.priority,
                {"queued": 0, "dead_letter": 0, "oldest_queued_age_seconds": None},
            )
            priority_bucket["dead_letter"] = int(priority_bucket["dead_letter"] or 0) + 1

            job_type = str(job.job_type)
            type_bucket = job_type_buckets.setdefault(
                job_type,
                {
                    "queued": 0,
                    "dead_letter": 0,
                    "highest_priority": None,
                    "oldest_queued_age_seconds": None,
                },
            )
            type_bucket["dead_letter"] = int(type_bucket["dead_letter"] or 0) + 1

        return OperationsQueueInsightsResponse(
            generated_at=now,
            queue_name=queue_name,
            scan_limit=scan_limit,
            queued_total=queued_total,
            dead_letter_total=dead_letter_total,
            queued_scanned=len(queued_jobs),
            dead_letter_scanned=len(dead_letter_jobs),
            truncated=queued_total > len(queued_jobs) or dead_letter_total > len(dead_letter_jobs),
            oldest_queued_age_seconds=oldest_queued_age_seconds,
            highest_priority=highest_priority,
            priority_buckets=[
                QueuePriorityBucketResponse(
                    priority=priority,
                    queued=int(counts["queued"] or 0),
                    dead_letter=int(counts["dead_letter"] or 0),
                    oldest_queued_age_seconds=cast(
                        int | None,
                        counts["oldest_queued_age_seconds"],
                    ),
                )
                for priority, counts in sorted(priority_buckets.items(), reverse=True)
            ],
            job_type_buckets=[
                QueueJobTypeBucketResponse(
                    job_type=job_type,
                    queued=int(counts["queued"] or 0),
                    dead_letter=int(counts["dead_letter"] or 0),
                    highest_priority=cast(int | None, counts["highest_priority"]),
                    oldest_queued_age_seconds=cast(
                        int | None,
                        counts["oldest_queued_age_seconds"],
                    ),
                )
                for job_type, counts in sorted(job_type_buckets.items())
            ],
        )

    def queue_governance(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        scan_limit: int = 500,
        stale_after_seconds: int = 900,
    ) -> QueueGovernanceDiagnosticsResponse:
        snapshot = self._queue_governance_snapshot(
            workspace_id=workspace_id,
            queue_name=queue_name,
            scan_limit=scan_limit,
            stale_after_seconds=stale_after_seconds,
        )
        return self._queue_governance_response(snapshot)

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
        snapshot = self._queue_governance_snapshot(
            workspace_id=workspace_id,
            queue_name=queue_name,
            scan_limit=scan_limit,
            stale_after_seconds=stale_after_seconds,
        )
        queue = RedisQueue(self._redis, self._keys, queue_name) if self._redis is not None else None
        run_service = RunOrchestrationService(self._session, queue=queue)

        remaining = max_items
        requeued_missing_runs = 0
        removed_orphaned_jobs = 0
        removed_non_runnable_jobs = 0
        skipped_items = 0

        if queue is not None and "requeue_missing_runs" in actions:
            for run in snapshot.missing_runs[:remaining]:
                if run_service.enqueue_run(run, actor_user_id, force=True):
                    requeued_missing_runs += 1
                    remaining -= 1
                else:
                    skipped_items += 1
                if remaining <= 0:
                    break

        if queue is not None and remaining > 0 and "remove_orphaned_jobs" in actions:
            for job in snapshot.orphaned_jobs[:remaining]:
                removed = queue.remove_queued_job(job.job_id, workspace_id=workspace_id)
                if removed is not None:
                    removed_orphaned_jobs += 1
                    remaining -= 1
                else:
                    skipped_items += 1
                if remaining <= 0:
                    break

        if queue is not None and remaining > 0 and "remove_non_runnable_jobs" in actions:
            for job in snapshot.non_runnable_jobs[:remaining]:
                removed = queue.remove_queued_job(job.job_id, workspace_id=workspace_id)
                if removed is not None:
                    removed_non_runnable_jobs += 1
                    remaining -= 1
                else:
                    skipped_items += 1
                if remaining <= 0:
                    break

        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="operations.queue_governance_reconciled",
            target_type="workspace",
            target_id=workspace_id,
            metadata={
                "queue_name": queue_name,
                "scan_limit": scan_limit,
                "stale_after_seconds": stale_after_seconds,
                "actions": list(actions),
                "max_items": max_items,
                "reason": _non_empty_string_or_none(reason),
                "scanned_jobs": snapshot.queued_scanned,
                "requeued_missing_runs": requeued_missing_runs,
                "removed_orphaned_jobs": removed_orphaned_jobs,
                "removed_non_runnable_jobs": removed_non_runnable_jobs,
                "skipped_items": skipped_items,
            },
        )
        self._session.commit()

        remaining_snapshot = self._queue_governance_snapshot(
            workspace_id=workspace_id,
            queue_name=queue_name,
            scan_limit=scan_limit,
            stale_after_seconds=stale_after_seconds,
        )
        return QueueGovernanceReconcileResponse(
            workspace_id=workspace_id,
            queue_name=queue_name,
            scanned_jobs=snapshot.queued_scanned,
            actions=list(actions),
            requeued_missing_runs=requeued_missing_runs,
            removed_orphaned_jobs=removed_orphaned_jobs,
            removed_non_runnable_jobs=removed_non_runnable_jobs,
            skipped_items=skipped_items,
            remaining_issues=self._queue_governance_issues(remaining_snapshot),
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

    def _queue_governance_snapshot(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        scan_limit: int,
        stale_after_seconds: int,
    ) -> QueueGovernanceSnapshot:
        scan_limit = max(1, min(scan_limit, 5_000))
        stale_after_seconds = max(60, min(stale_after_seconds, 86_400))
        now = datetime.now(UTC)
        if self._redis is None:
            return QueueGovernanceSnapshot(
                generated_at=now,
                queue_name=queue_name,
                scan_limit=scan_limit,
                stale_after_seconds=stale_after_seconds,
                queued_total=0,
                queued_scanned=0,
                agent_run_jobs=[],
                dead_letter_total=0,
                orphaned_jobs=[],
                non_runnable_jobs=[],
                duplicate_jobs=[],
                missing_runs=[],
                old_queued_jobs=[],
                truncated=False,
            )

        queue = RedisQueue(self._redis, self._keys, queue_name)
        queued_total = queue.count_queued(workspace_id=workspace_id)
        dead_letter_total = queue.count_dead_letters(workspace_id=workspace_id)
        scanned_jobs = queue.peek(limit=scan_limit)
        workspace_jobs = [job for job in scanned_jobs if job.workspace_id == workspace_id]
        agent_run_jobs = [job for job in workspace_jobs if job.job_type == JobType.AGENT_RUN]
        truncated = queue.count_queued() > scan_limit or queued_total > len(workspace_jobs)

        run_ids = {job.resource_id for job in agent_run_jobs}
        runs_by_id: dict[UUID, AgentRun] = {}
        if run_ids:
            runs = self._session.scalars(
                select(AgentRun).where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.id.in_(run_ids),
                )
            ).all()
            runs_by_id = {run.id: run for run in runs}

        orphaned_jobs = [job for job in agent_run_jobs if job.resource_id not in runs_by_id]
        non_runnable_jobs = [
            job
            for job in agent_run_jobs
            if job.resource_id in runs_by_id
            and runs_by_id[job.resource_id].status != RunStatus.QUEUED.value
        ]

        jobs_by_run: dict[UUID, list[JobPayload]] = {}
        for job in agent_run_jobs:
            jobs_by_run.setdefault(job.resource_id, []).append(job)
        duplicate_jobs = [
            duplicate
            for jobs in jobs_by_run.values()
            if len(jobs) > 1
            for duplicate in jobs[1:]
        ]

        queued_run_ids_in_queue = {
            job.resource_id
            for job in agent_run_jobs
            if job.resource_id in runs_by_id
            and runs_by_id[job.resource_id].status == RunStatus.QUEUED.value
        }
        missing_runs: list[AgentRun] = []
        if not truncated:
            missing_runs = self._session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.status == RunStatus.QUEUED.value,
                )
                .order_by(AgentRun.updated_at.asc(), AgentRun.created_at.asc())
            ).all()
            missing_runs = [run for run in missing_runs if run.id not in queued_run_ids_in_queue]

        cutoff = now - timedelta(seconds=stale_after_seconds)
        old_queued_jobs = [
            job for job in workspace_jobs if _aware_datetime(job.created_at) < cutoff
        ]

        return QueueGovernanceSnapshot(
            generated_at=now,
            queue_name=queue_name,
            scan_limit=scan_limit,
            stale_after_seconds=stale_after_seconds,
            queued_total=queued_total,
            queued_scanned=len(workspace_jobs),
            agent_run_jobs=agent_run_jobs,
            dead_letter_total=dead_letter_total,
            orphaned_jobs=orphaned_jobs,
            non_runnable_jobs=non_runnable_jobs,
            duplicate_jobs=duplicate_jobs,
            missing_runs=missing_runs,
            old_queued_jobs=old_queued_jobs,
            truncated=truncated,
        )

    def _queue_governance_response(
        self,
        snapshot: QueueGovernanceSnapshot,
    ) -> QueueGovernanceDiagnosticsResponse:
        return QueueGovernanceDiagnosticsResponse(
            generated_at=snapshot.generated_at,
            queue_name=snapshot.queue_name,
            scan_limit=snapshot.scan_limit,
            stale_after_seconds=snapshot.stale_after_seconds,
            queued_total=snapshot.queued_total,
            queued_scanned=snapshot.queued_scanned,
            agent_run_jobs_scanned=len(snapshot.agent_run_jobs),
            dead_letter_total=snapshot.dead_letter_total,
            orphaned_queue_jobs=len(snapshot.orphaned_jobs),
            non_runnable_queue_jobs=len(snapshot.non_runnable_jobs),
            duplicate_queue_jobs=len(snapshot.duplicate_jobs),
            queued_runs_missing_queue_job=len(snapshot.missing_runs),
            old_queued_jobs=len(snapshot.old_queued_jobs),
            truncated=snapshot.truncated,
            issues=self._queue_governance_issues(snapshot),
            recommended_actions=self._queue_governance_recommended_actions(snapshot),
        )

    def _queue_governance_issues(
        self,
        snapshot: QueueGovernanceSnapshot,
    ) -> list[QueueGovernanceIssueResponse]:
        issues: list[QueueGovernanceIssueResponse] = []
        if snapshot.truncated:
            issues.append(
                QueueGovernanceIssueResponse(
                    code="queue_scan_truncated",
                    severity="warning",
                    message="Queue scan did not cover every workspace job; increase scan_limit.",
                    count=max(0, snapshot.queued_total - snapshot.queued_scanned),
                    metadata={"scan_limit": snapshot.scan_limit},
                )
            )
        if snapshot.orphaned_jobs:
            issues.append(
                QueueGovernanceIssueResponse(
                    code="orphaned_queue_jobs",
                    severity="warning",
                    message="Queued agent run jobs reference runs that no longer exist.",
                    count=len(snapshot.orphaned_jobs),
                    resource_ids=_job_resource_ids(snapshot.orphaned_jobs),
                    job_ids=_job_ids(snapshot.orphaned_jobs),
                    oldest_age_seconds=_oldest_job_age(
                        snapshot.generated_at,
                        snapshot.orphaned_jobs,
                    ),
                )
            )
        if snapshot.non_runnable_jobs:
            issues.append(
                QueueGovernanceIssueResponse(
                    code="non_runnable_queue_jobs",
                    severity="warning",
                    message="Queued agent run jobs reference runs that are not in queued status.",
                    count=len(snapshot.non_runnable_jobs),
                    resource_ids=_job_resource_ids(snapshot.non_runnable_jobs),
                    job_ids=_job_ids(snapshot.non_runnable_jobs),
                    oldest_age_seconds=_oldest_job_age(
                        snapshot.generated_at,
                        snapshot.non_runnable_jobs,
                    ),
                )
            )
        if snapshot.duplicate_jobs:
            issues.append(
                QueueGovernanceIssueResponse(
                    code="duplicate_queue_jobs",
                    severity="warning",
                    message="Multiple queued jobs reference the same agent run.",
                    count=len(snapshot.duplicate_jobs),
                    resource_ids=_job_resource_ids(snapshot.duplicate_jobs),
                    job_ids=_job_ids(snapshot.duplicate_jobs),
                    oldest_age_seconds=_oldest_job_age(
                        snapshot.generated_at,
                        snapshot.duplicate_jobs,
                    ),
                )
            )
        if snapshot.missing_runs:
            issues.append(
                QueueGovernanceIssueResponse(
                    code="queued_runs_missing_queue_job",
                    severity="critical",
                    message="Queued runs are missing from the worker queue.",
                    count=len(snapshot.missing_runs),
                    resource_ids=_run_ids(snapshot.missing_runs),
                    oldest_age_seconds=_oldest_run_age(
                        snapshot.generated_at,
                        snapshot.missing_runs,
                    ),
                )
            )
        if snapshot.old_queued_jobs:
            issues.append(
                QueueGovernanceIssueResponse(
                    code="old_queued_jobs",
                    severity="warning",
                    message="Queued jobs have waited longer than the governance threshold.",
                    count=len(snapshot.old_queued_jobs),
                    resource_ids=_job_resource_ids(snapshot.old_queued_jobs),
                    job_ids=_job_ids(snapshot.old_queued_jobs),
                    oldest_age_seconds=_oldest_job_age(
                        snapshot.generated_at,
                        snapshot.old_queued_jobs,
                    ),
                )
            )
        if snapshot.dead_letter_total > 0:
            issues.append(
                QueueGovernanceIssueResponse(
                    code="dead_letter_pressure",
                    severity="warning",
                    message="Dead-letter jobs exist and should be inspected before bulk recovery.",
                    count=snapshot.dead_letter_total,
                )
            )
        return issues

    def _queue_governance_recommended_actions(
        self,
        snapshot: QueueGovernanceSnapshot,
    ) -> list[QueueGovernanceReconcileAction]:
        actions: list[QueueGovernanceReconcileAction] = []
        if snapshot.missing_runs:
            actions.append("requeue_missing_runs")
        if snapshot.orphaned_jobs:
            actions.append("remove_orphaned_jobs")
        if snapshot.non_runnable_jobs:
            actions.append("remove_non_runnable_jobs")
        return actions

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
            "data_lifecycle": self._data_lifecycle_rollup(workspace_id),
        }

    def _data_lifecycle_rollup(self, workspace_id: UUID) -> dict[str, object]:
        readiness = WorkspaceDataLifecycleService(self._session).get_recovery_readiness(
            workspace_id=workspace_id
        )
        if readiness is None:
            return {
                "status": "unknown",
                "ready": False,
                "blocked_reasons": ["workspace_not_found"],
                "warnings": [],
                "recommended_actions": [],
            }
        restore_readiness = _dict_value(readiness.get("restore_readiness"))
        retention_safety = _dict_value(readiness.get("retention_safety"))
        latest_backup = _dict_value(readiness.get("latest_successful_archive_export"))
        latest_restore_drill = _dict_value(readiness.get("latest_restore_drill"))
        import_conflict_history = _dict_value(
            restore_readiness.get("import_conflict_history")
        )
        blocked_reasons = _string_list(restore_readiness.get("blocked_reasons"))
        warnings = _string_list(restore_readiness.get("warnings"))
        recommended_actions = _string_list(restore_readiness.get("recommended_actions"))
        return {
            "status": _data_lifecycle_status(
                ready=restore_readiness.get("ready") is True,
                blocked_reasons=blocked_reasons,
                warnings=warnings,
            ),
            "ready": restore_readiness.get("ready") is True,
            "blocked_reasons": blocked_reasons,
            "warnings": warnings,
            "recommended_actions": recommended_actions,
            "next_safe_action": recommended_actions[0] if recommended_actions else None,
            "latest_backup": {
                "job_id": _string_or_none(latest_backup.get("id")),
                "status": latest_backup.get("status"),
                "completed_at": _iso_datetime_or_none(latest_backup.get("completed_at")),
                "storage_object_configured": latest_backup.get("has_storage_object") is True,
                "checksum_configured": latest_backup.get("checksum_sha256") is not None,
                "size_bytes": latest_backup.get("size_bytes"),
            },
            "latest_restore_drill": {
                "event_id": _string_or_none(latest_restore_drill.get("id")),
                "created_at": _iso_datetime_or_none(latest_restore_drill.get("created_at")),
                "action": latest_restore_drill.get("action"),
            },
            "retention_safety": {
                "retention_enabled": retention_safety.get("retention_enabled") is True,
                "backup_policy_enabled": retention_safety.get("backup_policy_enabled") is True,
                "protected_by_successful_archive": (
                    retention_safety.get("protected_by_successful_archive") is True
                ),
                "warnings": _string_list(retention_safety.get("warnings")),
            },
            "import_conflict_preview": {
                "preview_count": _int_value(import_conflict_history.get("total_previews")),
                "required_resolution_count": _int_value(
                    import_conflict_history.get("required_resolution_count")
                ),
                "suggested_resolution_count": _int_value(
                    import_conflict_history.get("suggested_resolution_count")
                ),
                "latest_preview_at": _iso_datetime_or_none(
                    import_conflict_history.get("latest_previewed_at")
                ),
            },
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

    def worker_lifecycle_payload(
        self,
        workspace_id: UUID,
        queue_name: str,
    ) -> OperationsWorkerLifecycleResponse:
        now = datetime.now(UTC)
        buckets: dict[str, dict[str, int | list[int] | None]] = {}
        if self._redis is not None:
            queue = RedisQueue(self._redis, self._keys, queue_name)
            for job in queue.peek(limit=1_000):
                if job.workspace_id != workspace_id:
                    continue
                age_seconds = max(
                    0,
                    int((now - _aware_datetime(job.created_at)).total_seconds()),
                )
                for worker_type in _job_worker_types(job):
                    bucket = _worker_lifecycle_bucket(buckets, worker_type)
                    bucket["queued_jobs"] = int(bucket["queued_jobs"]) + 1
                    bucket["oldest_queued_age_seconds"] = _max_optional_int(
                        bucket.get("oldest_queued_age_seconds"),
                        age_seconds,
                    )

        nodes_by_worker_id = {
            node.worker_id: node for node in self._session.scalars(select(WorkerNode)).all()
        }
        leases = self._session.scalars(
            select(WorkerLease).where(WorkerLease.workspace_id == workspace_id)
        ).all()
        for lease in leases:
            worker_type = _lease_worker_type(lease, nodes_by_worker_id)
            bucket = _worker_lifecycle_bucket(buckets, worker_type)
            if lease.status == "running":
                bucket["running_jobs"] = int(bucket["running_jobs"]) + 1
                running_age_seconds = max(
                    0,
                    int((now - _aware_datetime(lease.started_at)).total_seconds()),
                )
                bucket["oldest_running_age_seconds"] = _max_optional_int(
                    bucket.get("oldest_running_age_seconds"),
                    running_age_seconds,
                )
                continue
            if lease.status == "completed":
                bucket["completed_jobs"] = int(bucket["completed_jobs"]) + 1
            elif lease.status == "failed":
                bucket["failed_jobs"] = int(bucket["failed_jobs"]) + 1
            elif lease.status == "retrying":
                bucket["retried_jobs"] = int(bucket["retried_jobs"]) + 1
            elif lease.status == "expired":
                bucket["expired_jobs"] = int(bucket["expired_jobs"]) + 1
            if lease.status in TERMINAL_LEASE_STATUSES and lease.finished_at is not None:
                durations = bucket["durations"]
                if isinstance(durations, list):
                    durations.append(
                        max(
                            0,
                            int(
                                (
                                    _aware_datetime(lease.finished_at)
                                    - _aware_datetime(lease.started_at)
                                ).total_seconds()
                            ),
                        )
                    )

        return OperationsWorkerLifecycleResponse(
            generated_at=now,
            queue_name=queue_name,
            worker_types=[
                _worker_lifecycle_response(worker_type, values)
                for worker_type, values in sorted(buckets.items())
            ],
        )

    def run_activity_payload(
        self,
        workspace_id: UUID,
        *,
        team_id: UUID | None = None,
        scan_limit: int = 500,
    ) -> OperationsRunActivityResponse:
        now = datetime.now(UTC)
        scan_limit = max(1, min(scan_limit, 1_000))
        active_statuses = (
            RunStatus.QUEUED.value,
            RunStatus.RUNNING.value,
            RunStatus.WAITING_RUNTIME.value,
            RunStatus.WAITING_APPROVAL.value,
        )
        statement = select(AgentRun).where(
            AgentRun.workspace_id == workspace_id,
            AgentRun.status.in_(active_statuses),
        )
        if team_id is not None:
            statement = statement.join(Task, Task.id == AgentRun.task_id).where(
                Task.workspace_id == workspace_id,
                Task.agent_team_id == team_id,
            )
        active_runs_subquery = statement.order_by(None).subquery()
        total_active_runs = int(
            self._session.scalar(select(func.count()).select_from(active_runs_subquery))
            or 0
        )
        status_counts = {
            str(status): int(count)
            for status, count in self._session.execute(
                select(active_runs_subquery.c.status, func.count()).group_by(
                    active_runs_subquery.c.status
                )
            )
        }
        runs = self._session.scalars(
            statement.order_by(AgentRun.updated_at.asc(), AgentRun.created_at.asc()).limit(
                scan_limit
            )
        ).all()
        latest_events = self._latest_run_events_by_run_id(workspace_id, [run.id for run in runs])

        phase_buckets: dict[str, dict[str, object]] = {}
        oldest_active_run: RunActivityOldestRunResponse | None = None
        for run in runs:
            latest_event = latest_events.get(run.id)
            activity = run_activity(run, latest_event)
            phase = str(activity["phase"])
            oldest_run = _run_activity_oldest_response(
                run,
                latest_event=latest_event,
                activity=activity,
                now=now,
            )
            bucket = phase_buckets.setdefault(
                phase,
                {
                    "label": activity["label"],
                    "count": 0,
                    "oldest_run": None,
                    "recommended_action": activity["recommended_action"],
                },
            )
            bucket["count"] = int(bucket["count"]) + 1
            current_oldest = bucket.get("oldest_run")
            if not isinstance(current_oldest, RunActivityOldestRunResponse) or (
                oldest_run.age_seconds > current_oldest.age_seconds
            ):
                bucket["oldest_run"] = oldest_run
            if oldest_active_run is None or oldest_run.age_seconds > oldest_active_run.age_seconds:
                oldest_active_run = oldest_run

        phases = [
            RunActivityPhaseBucketResponse(
                phase=phase,
                label=str(values["label"]),
                count=int(values["count"]),
                oldest_age_seconds=(
                    values["oldest_run"].age_seconds
                    if isinstance(values["oldest_run"], RunActivityOldestRunResponse)
                    else None
                ),
                oldest_run=(
                    values["oldest_run"]
                    if isinstance(values["oldest_run"], RunActivityOldestRunResponse)
                    else None
                ),
                recommended_action=(
                    str(values["recommended_action"])
                    if values.get("recommended_action") is not None
                    else None
                ),
            )
            for phase, values in sorted(phase_buckets.items())
        ]
        return OperationsRunActivityResponse(
            generated_at=now,
            team_id=team_id,
            total_active_runs=total_active_runs,
            scanned_active_runs=len(runs),
            truncated=total_active_runs > len(runs),
            status_counts=dict(sorted(status_counts.items())),
            phases=phases,
            oldest_active_run=oldest_active_run,
        )

    def _latest_run_events_by_run_id(
        self,
        workspace_id: UUID,
        run_ids: list[UUID],
    ) -> dict[UUID, RunEvent]:
        if not run_ids:
            return {}
        latest_sequences = (
            select(
                RunEvent.agent_run_id.label("agent_run_id"),
                func.max(RunEvent.sequence).label("latest_sequence"),
            )
            .where(
                RunEvent.workspace_id == workspace_id,
                RunEvent.agent_run_id.in_(run_ids),
            )
            .group_by(RunEvent.agent_run_id)
            .subquery()
        )
        events = self._session.scalars(
            select(RunEvent).join(
                latest_sequences,
                (RunEvent.agent_run_id == latest_sequences.c.agent_run_id)
                & (RunEvent.sequence == latest_sequences.c.latest_sequence),
            )
        ).all()
        return {event.agent_run_id: event for event in events}

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
                SchedulerBlockedReasonResponse(
                    reason=explanation.reason,
                    code=explanation.code,
                    message=explanation.message,
                    resource_key=explanation.resource_key,
                    count=count,
                )
                for reason, count in sorted(blocked_reasons.items())
                for explanation in [explain_blocked_reason(reason)]
            ],
            policy=self._scheduler_policy(workspace_id),
        )

    def list_blocked_steps(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        code: str | None = None,
    ) -> tuple[list[BlockedStepExplanationResponse], int]:
        rows = self._session.execute(
            select(TaskStep, Task)
            .join(Task, Task.id == TaskStep.task_id)
            .where(
                TaskStep.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                TaskStep.status == "queued",
            )
            .order_by(TaskStep.created_at.asc(), TaskStep.id.asc())
        ).all()
        blocked: list[BlockedStepExplanationResponse] = []
        for step, task in rows:
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            if dependencies.get("scheduling_status") != "blocked":
                continue
            explanation = explain_blocked_reason(dependencies.get("blocked_reason"))
            if code is not None and explanation.code != code:
                continue
            blocked.append(
                BlockedStepExplanationResponse(
                    task_step_id=step.id,
                    task_id=task.id,
                    task_title=task.title,
                    step_title=step.title,
                    status=step.status,
                    reason=explanation.reason,
                    code=explanation.code,
                    message=explanation.message,
                    resource_key=explanation.resource_key,
                    runtime_space_id=step.runtime_space_id or task.runtime_space_id,
                    blocked_resource_keys=_string_list(
                        dependencies.get("blocked_resource_keys")
                    ),
                    priority_score=_positive_int_or_none(dependencies.get("priority_score")),
                    created_at=step.created_at,
                    updated_at=step.updated_at,
                )
            )
        return blocked[page.offset : page.offset + page.limit], len(blocked)

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
        normalized_code = _non_empty_string_or_none(code)
        normalized_reason = _non_empty_string_or_none(reason)
        if normalized_code is None and normalized_reason is None and runtime_space_id is None:
            raise ValueError("At least one unblock filter is required")
        rows = self._session.execute(
            select(TaskStep, Task)
            .join(Task, Task.id == TaskStep.task_id)
            .where(
                TaskStep.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                TaskStep.status == "queued",
            )
            .order_by(TaskStep.created_at.asc(), TaskStep.id.asc())
        ).all()
        unblocked = 0
        for step, task in rows:
            if unblocked >= limit:
                break
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            if dependencies.get("scheduling_status") != "blocked":
                continue
            explanation = explain_blocked_reason(dependencies.get("blocked_reason"))
            effective_runtime_space_id = step.runtime_space_id or task.runtime_space_id
            if normalized_code is not None and explanation.code != normalized_code:
                continue
            if normalized_reason is not None and explanation.reason != normalized_reason:
                continue
            if runtime_space_id is not None and effective_runtime_space_id != runtime_space_id:
                continue
            updated = dict(dependencies)
            updated.pop("scheduling_status", None)
            updated.pop("blocked_reason", None)
            updated.pop("blocked_resource_keys", None)
            updated.pop("priority_score", None)
            step.dependencies = updated
            unblocked += 1
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="scheduler.blocked_steps_unblocked",
            target_type="workspace",
            target_id=workspace_id,
            metadata={
                "code": normalized_code,
                "reason": normalized_reason,
                "runtime_space_id": str(runtime_space_id) if runtime_space_id else None,
                "limit": limit,
                "unblocked_steps": unblocked,
            },
        )
        self._session.commit()
        return BlockedStepUnblockResponse(
            workspace_id=workspace_id,
            unblocked_steps=unblocked,
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
                    remediation_actions=_self_hosted_remediation_actions(
                        trust_state,
                        stale=stale,
                    ),
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
        if scheduler.policy.paused:
            issues.append(
                OperationsControlPlaneIssueResponse(
                    severity="warning",
                    code="scheduler_paused",
                    message="Workspace scheduling is paused.",
                    metadata={"pause_reason": scheduler.policy.pause_reason},
                )
            )
        paused_spaces = [
            space for space in runtime_capacity.runtime_spaces if space.status == "paused"
        ]
        if paused_spaces:
            issues.append(
                OperationsControlPlaneIssueResponse(
                    severity="warning",
                    code="runtime_spaces_paused",
                    message="Runtime spaces are paused and will not accept new runs.",
                    count=len(paused_spaces),
                    metadata={
                        "runtime_space_ids": [
                            str(space.runtime_space_id) for space in paused_spaces
                        ]
                    },
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
            paused=scheduler.get("paused") is True,
            pause_reason=_non_empty_string_or_none(scheduler.get("pause_reason")),
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

    def _clear_workspace_pause_blocks(self, workspace_id: UUID, *, reason: str) -> int:
        steps = self._session.scalars(
            select(TaskStep).where(
                TaskStep.workspace_id == workspace_id,
                TaskStep.status == "queued",
            )
        ).all()
        cleared = 0
        for step in steps:
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            if dependencies.get("scheduling_status") != "blocked":
                continue
            if dependencies.get("blocked_reason") != reason:
                continue
            updated = dict(dependencies)
            updated.pop("scheduling_status", None)
            updated.pop("blocked_reason", None)
            updated.pop("priority_score", None)
            step.dependencies = updated
            cleared += 1
        return cleared

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

    def _oldest_queued_age_seconds(
        self,
        queue_name: str,
        now: datetime,
        scan_limit: int,
    ) -> int | None:
        if self._redis is None:
            return None
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return _oldest_job_age(now, queue.peek(limit=scan_limit))

    def _prometheus_worker_gauges(
        self,
        now: datetime,
        stale_after_seconds: int,
    ) -> list[GaugeMetric]:
        stale_cutoff = now - timedelta(seconds=stale_after_seconds)
        worker_states = {"online": 0, "offline": 0, "stale": 0}
        nodes = self._session.scalars(select(WorkerNode)).all()
        for node in nodes:
            if _aware_datetime(node.last_seen_at) < stale_cutoff:
                worker_states["stale"] += 1
            elif node.status == "online":
                worker_states["online"] += 1
            elif node.status == "offline":
                worker_states["offline"] += 1

        gauges = [
            GaugeMetric(
                "chaincloud_workers",
                count,
                labels={"state": state},
                help_text="Worker nodes by operational state.",
            )
            for state, count in sorted(worker_states.items())
        ]

        lease_counts = {
            status: int(count)
            for status, count in self._session.execute(
                select(WorkerLease.status, func.count())
                .where(WorkerLease.status.in_(["running", "failed"]))
                .group_by(WorkerLease.status)
            ).all()
        }
        gauges.extend(
            GaugeMetric(
                "chaincloud_worker_leases",
                lease_counts.get(status, 0),
                labels={"status": status},
                help_text="Worker leases by lifecycle status.",
            )
            for status in ("running", "failed")
        )
        return gauges

    def _prometheus_runtime_gauges(self) -> list[GaugeMetric]:
        gauges: list[GaugeMetric] = []
        active_runs_by_runtime = dict(
            self._session.execute(
                select(AgentRun.runtime_id, func.count())
                .where(
                    AgentRun.runtime_id.is_not(None),
                    AgentRun.status.in_(ACTIVE_RUNTIME_RUN_STATUSES),
                )
                .group_by(AgentRun.runtime_id)
            ).all()
        )
        grouped: dict[tuple[str, str], dict[str, int]] = {}
        for runtime in self._session.scalars(select(WorkspaceRuntime)).all():
            key = (runtime.runtime_provider, runtime.runtime_type)
            bucket = grouped.setdefault(key, {"capacity_slots": 0, "active_runs": 0})
            bucket["capacity_slots"] += _runtime_capacity_slots(runtime)
            bucket["active_runs"] += int(active_runs_by_runtime.get(runtime.id, 0))

        for (provider, runtime_type), values in sorted(grouped.items()):
            labels = {"provider": provider, "runtime_type": runtime_type}
            capacity_slots = values["capacity_slots"]
            active_runs = values["active_runs"]
            saturation = active_runs / capacity_slots if capacity_slots > 0 else 0.0
            gauges.extend(
                [
                    GaugeMetric(
                        "chaincloud_runtime_capacity_slots",
                        capacity_slots,
                        labels=labels,
                        help_text="Runtime capacity slots by provider and runtime type.",
                    ),
                    GaugeMetric(
                        "chaincloud_runtime_active_runs",
                        active_runs,
                        labels=labels,
                        help_text="Active runs assigned to runtimes by provider and type.",
                    ),
                    GaugeMetric(
                        "chaincloud_runtime_saturation_ratio",
                        round(saturation, 4),
                        labels=labels,
                        help_text="Runtime active-run saturation by provider and type.",
                    ),
                ]
            )

        quota_rows = self._session.execute(
            select(
                RuntimeSpaceQuota.quota_key,
                RuntimeSpaceQuota.unit,
                func.sum(RuntimeSpaceQuota.reserved_value),
                func.sum(RuntimeSpaceQuota.limit_value),
            )
            .where(RuntimeSpaceQuota.status == "active")
            .group_by(RuntimeSpaceQuota.quota_key, RuntimeSpaceQuota.unit)
        ).all()
        for quota_key, unit, reserved, limit in sorted(quota_rows):
            labels = {"quota_key": quota_key, "unit": unit}
            reserved_value = int(reserved or 0)
            limit_value = int(limit or 0)
            usage = reserved_value / limit_value if limit_value > 0 else 0.0
            gauges.extend(
                [
                    GaugeMetric(
                        "chaincloud_runtime_space_quota_reserved",
                        reserved_value,
                        labels=labels,
                        help_text="Reserved runtime space quota by quota key.",
                    ),
                    GaugeMetric(
                        "chaincloud_runtime_space_quota_limit",
                        limit_value,
                        labels=labels,
                        help_text="Configured runtime space quota limit by quota key.",
                    ),
                    GaugeMetric(
                        "chaincloud_runtime_space_quota_usage_ratio",
                        round(usage, 4),
                        labels=labels,
                        help_text="Runtime space quota usage ratio by quota key.",
                    ),
                ]
            )
        return gauges

    def _prometheus_team_runtime_gauges(self, now: datetime) -> list[GaugeMetric]:
        teams = self._session.scalars(select(AgentTeam).where(AgentTeam.status == "active")).all()
        runtime_ids = {
            runtime_id
            for team in teams
            for runtime_id in [_team_runtime_workspace_runtime_id(team)]
            if runtime_id is not None
        }
        runtimes = {
            runtime.id: runtime
            for runtime in self._session.scalars(
                select(WorkspaceRuntime).where(WorkspaceRuntime.id.in_(runtime_ids))
            ).all()
        } if runtime_ids else {}
        health_counts = {
            "starting": 0,
            "healthy": 0,
            "stale": 0,
            "degraded": 0,
            "paused": 0,
            "stopped": 0,
        }
        iteration_count = 0
        scheduled_loop_enabled = 0
        for team in teams:
            runtime_metadata = _team_runtime_metadata(team)
            if not runtime_metadata:
                continue
            runtime_id = _team_runtime_workspace_runtime_id(team)
            health = _team_runtime_health_for_metrics(
                runtime_metadata=runtime_metadata,
                runtime=runtimes.get(runtime_id) if runtime_id is not None else None,
                generated_at=now,
            )
            health_counts[health] = health_counts.get(health, 0) + 1
            iteration_count += _non_negative_int(runtime_metadata.get("iteration_count"))
            scheduling_policy = runtime_metadata.get("scheduling_policy")
            if not isinstance(scheduling_policy, dict) or (
                scheduling_policy.get("scheduled_loop_enabled") is not False
            ):
                scheduled_loop_enabled += 1
        gauges = [
            GaugeMetric(
                "chaincloud_team_runtimes",
                count,
                labels={"health": health},
                help_text="Team runtimes by low-cardinality runtime health.",
            )
            for health, count in sorted(health_counts.items())
        ]
        gauges.append(
            GaugeMetric(
                "chaincloud_team_runtime_iterations_total",
                iteration_count,
                labels={},
                help_text="Total persisted team runtime iterations across active teams.",
            )
        )
        gauges.append(
            GaugeMetric(
                "chaincloud_team_runtime_scheduled_loops",
                scheduled_loop_enabled,
                labels={"state": "enabled"},
                help_text="Active team runtimes with scheduled loop cadence enabled.",
            )
        )
        return gauges

    def _redis_count(self, value: object) -> int:
        return int(cast(int, value))

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)


def _job_ids(jobs: list[JobPayload], *, limit: int = 25) -> list[UUID]:
    return [job.job_id for job in jobs[:limit]]


def _job_resource_ids(jobs: list[JobPayload], *, limit: int = 25) -> list[UUID]:
    seen: set[UUID] = set()
    resource_ids: list[UUID] = []
    for job in jobs:
        if job.resource_id in seen:
            continue
        seen.add(job.resource_id)
        resource_ids.append(job.resource_id)
        if len(resource_ids) >= limit:
            break
    return resource_ids


def _run_ids(runs: list[AgentRun], *, limit: int = 25) -> list[UUID]:
    return [run.id for run in runs[:limit]]


def _oldest_job_age(now: datetime, jobs: list[JobPayload]) -> int | None:
    ages = [max(0, int((now - _aware_datetime(job.created_at)).total_seconds())) for job in jobs]
    return max(ages) if ages else None


def _oldest_run_age(now: datetime, runs: list[AgentRun]) -> int | None:
    ages = [max(0, int((now - _aware_datetime(run.updated_at)).total_seconds())) for run in runs]
    return max(ages) if ages else None


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


def _non_negative_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    if isinstance(value, str):
        try:
            return max(0, int(value))
        except ValueError:
            return 0
    return 0


def _team_runtime_metadata(team: AgentTeam) -> dict[str, object]:
    policy = team.default_task_policy if isinstance(team.default_task_policy, dict) else {}
    runtime_metadata = policy.get(TEAM_RUNTIME_STATUS_KEY)
    return dict(runtime_metadata) if isinstance(runtime_metadata, dict) else {}


def _team_runtime_workspace_runtime_id(team: AgentTeam) -> UUID | None:
    runtime_id = _team_runtime_metadata(team).get(TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY)
    if isinstance(runtime_id, UUID):
        return runtime_id
    if isinstance(runtime_id, str) and runtime_id:
        try:
            return UUID(runtime_id)
        except ValueError:
            return None
    return None


def _team_runtime_health_for_metrics(
    *,
    runtime_metadata: dict[str, object],
    runtime: WorkspaceRuntime | None,
    generated_at: datetime,
) -> str:
    status = runtime_metadata.get("status")
    if status == "paused":
        return "paused"
    if status == "stopped" or status is None:
        return "stopped"
    if runtime is None and runtime_metadata.get(TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY):
        return "degraded"
    if runtime is not None and (
        runtime.status != "running" or runtime.connection_status in {"offline", "error"}
    ):
        return "degraded"
    if runtime_metadata.get("heartbeat_status") == "skipped":
        return "degraded"
    last_heartbeat_at = _datetime_from_metadata(runtime_metadata.get("last_heartbeat_at"))
    if last_heartbeat_at is None:
        return "starting"
    if generated_at - last_heartbeat_at > timedelta(
        seconds=TEAM_RUNTIME_HEARTBEAT_STALE_AFTER_SECONDS
    ):
        return "stale"
    return "healthy"


def _datetime_from_metadata(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _append_worker_lifecycle_events(
    metadata: dict[str, object],
    events: list[dict[str, object]],
) -> dict[str, object]:
    existing = metadata.get("lifecycle_events")
    lifecycle_events = list(existing) if isinstance(existing, list) else []
    lifecycle_events.extend(events)
    metadata["lifecycle_events"] = lifecycle_events[-LIFECYCLE_EVENTS_LIMIT:]
    if events:
        metadata["last_lifecycle_event"] = events[-1]
    return metadata


def _worker_lifecycle_event(
    event_type: str,
    at: datetime,
    *,
    attempt: int,
    status: str | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    event: dict[str, object] = {
        "type": event_type,
        "at": at.isoformat(),
        "attempt": attempt,
    }
    if status is not None:
        event["status"] = status
    if metadata:
        event.update(metadata)
    return event


def _worker_finish_lifecycle_event(status: str) -> str:
    if status == "retrying":
        return "requeued"
    if status == "failed":
        return "failed"
    if status == "expired":
        return "expired"
    return "completed"


def _job_worker_types(job: JobPayload) -> list[str]:
    worker_types = _string_list(job.routing.get("worker_types"))
    return worker_types or ["unrouted"]


def _lease_worker_type(lease: WorkerLease, nodes_by_worker_id: dict[str, WorkerNode]) -> str:
    node = nodes_by_worker_id.get(lease.worker_id)
    if node is not None:
        return node.worker_type
    metadata_worker_type = lease.lease_metadata.get("worker_type")
    return metadata_worker_type if isinstance(metadata_worker_type, str) else "unknown"


def _worker_lifecycle_bucket(
    buckets: dict[str, dict[str, int | list[int] | None]],
    worker_type: str,
) -> dict[str, int | list[int] | None]:
    return buckets.setdefault(
        worker_type,
        {
            "queued_jobs": 0,
            "running_jobs": 0,
            "completed_jobs": 0,
            "failed_jobs": 0,
            "retried_jobs": 0,
            "expired_jobs": 0,
            "oldest_queued_age_seconds": None,
            "oldest_running_age_seconds": None,
            "durations": [],
        },
    )


def _worker_lifecycle_response(
    worker_type: str,
    values: dict[str, int | list[int] | None],
) -> WorkerLifecycleBucketResponse:
    terminal_jobs = (
        int(values["completed_jobs"])
        + int(values["failed_jobs"])
        + int(values["expired_jobs"])
    )
    unsuccessful_jobs = int(values["failed_jobs"]) + int(values["expired_jobs"])
    durations = values["durations"]
    duration_values = durations if isinstance(durations, list) else []
    return WorkerLifecycleBucketResponse(
        worker_type=worker_type,
        queued_jobs=int(values["queued_jobs"]),
        running_jobs=int(values["running_jobs"]),
        completed_jobs=int(values["completed_jobs"]),
        failed_jobs=int(values["failed_jobs"]),
        retried_jobs=int(values["retried_jobs"]),
        expired_jobs=int(values["expired_jobs"]),
        failure_rate=round(unsuccessful_jobs / terminal_jobs, 4) if terminal_jobs > 0 else 0.0,
        average_duration_seconds=int(sum(duration_values) / len(duration_values))
        if duration_values
        else None,
        oldest_queued_age_seconds=cast(int | None, values["oldest_queued_age_seconds"]),
        oldest_running_age_seconds=cast(int | None, values["oldest_running_age_seconds"]),
    )


def _run_activity_oldest_response(
    run: AgentRun,
    *,
    latest_event: RunEvent | None,
    activity: dict[str, object],
    now: datetime,
) -> RunActivityOldestRunResponse:
    last_activity_at = _aware_datetime(cast(datetime, activity["since"]))
    return RunActivityOldestRunResponse(
        run_id=run.id,
        task_id=run.task_id,
        task_step_id=run.task_step_id,
        agent_profile_id=run.agent_profile_id,
        runtime_id=run.runtime_id,
        runtime_space_id=run.runtime_space_id,
        status=run.status,
        latest_event_type=latest_event.event_type if latest_event is not None else None,
        age_seconds=max(0, int((now - last_activity_at).total_seconds())),
        started_at=_aware_datetime(run.started_at) if run.started_at is not None else None,
        last_activity_at=last_activity_at,
    )


def _positive_int_or_none(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    return None


def _max_optional_int(current: object, candidate: int) -> int:
    return candidate if not isinstance(current, int) else max(current, candidate)


def _scheduler_settings(settings: dict[str, object]) -> dict[str, object]:
    raw_scheduler = settings.get("scheduler")
    if not isinstance(raw_scheduler, dict):
        return {}
    return dict(raw_scheduler)


def _non_empty_string_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


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


def _dict_value(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _iso_datetime_or_none(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def _data_lifecycle_status(
    *,
    ready: bool,
    blocked_reasons: list[str],
    warnings: list[str],
) -> str:
    if ready:
        return "ready_with_warnings" if warnings else "ready"
    if blocked_reasons:
        return "blocked"
    return "unknown"


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


def _self_hosted_remediation_actions(
    trust_state: str,
    *,
    stale: bool,
) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    if stale:
        actions.append(
            {
                "code": "check_runner_heartbeat",
                "label": "Check runner heartbeat",
                "severity": "warning",
                "description": (
                    "Verify the self-hosted runner process is online and can reach the API."
                ),
            }
        )
    if trust_state == "degraded":
        actions.append(
            {
                "code": "restart_runner",
                "label": "Restart runner",
                "severity": "warning",
                "description": (
                    "Restart the local worker and confirm its policy capabilities match the "
                    "workspace."
                ),
            }
        )
        actions.append(
            {
                "code": "review_machine_policy",
                "label": "Review machine policy",
                "severity": "warning",
                "description": (
                    "Check allowed tools, runtime spaces, network modes, and capacity limits."
                ),
            }
        )
    elif trust_state == "quarantined":
        actions.append(
            {
                "code": "review_quarantine_reason",
                "label": "Review quarantine",
                "severity": "critical",
                "description": (
                    "Inspect security events and only restore the runner after the issue is "
                    "resolved."
                ),
            }
        )
    elif trust_state == "revoked":
        actions.append(
            {
                "code": "rotate_runtime_credential",
                "label": "Rotate credential",
                "severity": "critical",
                "description": "Issue a new runtime credential and re-enroll the local machine.",
            }
        )
    elif trust_state == "offline":
        actions.append(
            {
                "code": "start_runner",
                "label": "Start runner",
                "severity": "warning",
                "description": (
                    "Start the local worker service or reconnect the machine to the network."
                ),
            }
        )
    return actions


def _normalized_stale_run_statuses(statuses: list[str] | None) -> set[RunStatus]:
    allowed = {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING_RUNTIME}
    if not statuses:
        return allowed
    normalized: set[RunStatus] = set()
    for status in statuses:
        try:
            run_status = RunStatus(status)
        except ValueError as exc:
            raise ValueError(f"Unsupported stale run status: {status}") from exc
        if run_status not in allowed:
            raise ValueError(f"Unsupported stale run status: {status}")
        normalized.add(run_status)
    return normalized


def _worker_heartbeat_details(details: dict[str, object]) -> dict[str, object]:
    normalized = dict(details)
    enqueued = _int_value(normalized.get("scheduled_job_actions_enqueued"))
    recorded = _int_value(normalized.get("scheduled_job_actions_recorded"))
    skipped = _int_value(normalized.get("scheduled_job_actions_skipped"))
    enqueued_by_type = _string_int_dict(
        normalized.get("scheduled_job_actions_enqueued_by_job_type")
    )
    recorded_by_type = _string_int_dict(
        normalized.get("scheduled_job_actions_recorded_by_job_type")
    )
    skipped_by_type = _string_int_dict(
        normalized.get("scheduled_job_actions_skipped_by_job_type")
    )
    if not any((enqueued, recorded, skipped, enqueued_by_type, recorded_by_type, skipped_by_type)):
        return normalized
    provider_health_job_type = JobType.MODEL_PROVIDER_HEALTH_CHECK.value
    normalized["scheduled_job_actions"] = {
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
    return normalized


def _string_int_dict(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(item, int) and not isinstance(item, bool)
    }


def _int_value(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _stale_run_age_anchor(run: AgentRun) -> datetime | None:
    if run.status == RunStatus.RUNNING.value:
        anchor = run.started_at or run.updated_at or run.created_at
    elif run.status == RunStatus.WAITING_RUNTIME.value:
        anchor = run.updated_at or run.started_at or run.created_at
    elif run.status == RunStatus.QUEUED.value:
        anchor = run.updated_at or run.created_at
    else:
        return None
    return _aware_datetime(anchor)


def _stale_run_diagnostic_item(
    run: AgentRun,
    *,
    now: datetime,
    lease: WorkerLease | None,
) -> StaleRunDiagnosticResponse:
    anchor = _stale_run_age_anchor(run) or _aware_datetime(run.created_at)
    reason_code, reason_message = _stale_run_reason(run.status)
    lease_started_at = _aware_datetime(lease.started_at) if lease is not None else None
    return StaleRunDiagnosticResponse(
        run_id=run.id,
        status=RunStatus(run.status).value,
        stale_reason_code=reason_code,
        stale_reason_message=reason_message,
        age_seconds=max(0, int((now - anchor).total_seconds())),
        created_at=_aware_datetime(run.created_at),
        updated_at=_aware_datetime(run.updated_at),
        started_at=_aware_datetime(run.started_at) if run.started_at is not None else None,
        task_id=run.task_id,
        task_step_id=run.task_step_id,
        agent_profile_id=run.agent_profile_id,
        runtime_id=run.runtime_id,
        runtime_space_id=run.runtime_space_id,
        worker_id=lease.worker_id if lease is not None else None,
        worker_lease_status=lease.status if lease is not None else None,
        worker_lease_started_at=lease_started_at,
        worker_lease_age_seconds=max(0, int((now - lease_started_at).total_seconds()))
        if lease_started_at is not None
        else None,
    )


def _stale_run_reason(status: str) -> tuple[str, str]:
    if status == RunStatus.QUEUED.value:
        return "stale_queued_run", "Queued run has not been claimed by a worker."
    if status == RunStatus.WAITING_RUNTIME.value:
        return "stale_waiting_runtime_run", "Run is waiting for a runtime result too long."
    return "stale_running_run", "Running run has exceeded the worker lease window."


def _stale_run_failure_message(status: str) -> str:
    if status == RunStatus.WAITING_RUNTIME.value:
        return "Runtime tool result did not arrive before the recovery window expired"
    return "Worker stopped reporting before the run completed"


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
