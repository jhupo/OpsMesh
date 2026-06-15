from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import (
    OperationsControlPlaneIssueResponse,
    OperationsControlPlaneResponse,
    OperationsMcpJobsResponse,
    OperationsOutcomesResponse,
    OperationsRuntimeCapacityResponse,
    OperationsSchedulerResponse,
    OperationsSelfHostedMachinesResponse,
    QueueLatencyResponse,
    WorkerCapacityAggregateResponse,
)
from backend.app.operations.capacity import OperationsCapacityService
from backend.app.operations.outcomes import OperationsOutcomeService
from backend.app.operations.scheduler import OperationsSchedulerService
from backend.app.operations.self_hosted_machines import OperationsSelfHostedMachineService
from backend.app.redis.keys import RedisKeyBuilder


class OperationsControlPlaneService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None,
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._session = session
        self._redis = redis
        self._keys = key_builder

    def control_plane_payload(
        self,
        workspace_id: UUID,
        queue_name: str,
        *,
        window_seconds: int,
    ) -> OperationsControlPlaneResponse:
        now = datetime.now(UTC)
        capacity = OperationsCapacityService(self._session, self._redis, self._keys)
        queue = capacity.queue_latency(queue_name, workspace_id)
        worker_capacity = capacity.worker_capacity_aggregate()
        runtime_capacity = capacity.runtime_capacity_payload(workspace_id)
        scheduler = OperationsSchedulerService(self._session).scheduler_payload(workspace_id)
        outcome_service = OperationsOutcomeService(self._session)
        outcomes = outcome_service.outcomes_payload(workspace_id, window_seconds=window_seconds)
        mcp_jobs = outcome_service.mcp_jobs_payload(workspace_id)
        self_hosted_machines = OperationsSelfHostedMachineService(
            self._session
        ).self_hosted_machines_payload(workspace_id)
        issues = _control_plane_issues(
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


def _control_plane_issues(
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
    paused_spaces = [space for space in runtime_capacity.runtime_spaces if space.status == "paused"]
    if paused_spaces:
        issues.append(
            OperationsControlPlaneIssueResponse(
                severity="warning",
                code="runtime_spaces_paused",
                message="Runtime spaces are paused and will not accept new runs.",
                count=len(paused_spaces),
                metadata={
                    "runtime_space_ids": [str(space.runtime_space_id) for space in paused_spaces]
                },
            )
        )
    saturated_spaces = [space for space in runtime_capacity.runtime_spaces if space.saturated]
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
    if mcp_jobs.oldest_queued_age_seconds is not None and mcp_jobs.oldest_queued_age_seconds >= 300:
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


def _control_plane_health(issues: list[OperationsControlPlaneIssueResponse]) -> str:
    severities = {issue.severity for issue in issues}
    if "critical" in severities:
        return "critical"
    if "warning" in severities:
        return "warning"
    return "healthy"
