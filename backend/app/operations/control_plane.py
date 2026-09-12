from __future__ import annotations

from backend.app.api.schemas.operation_capacity import (
    OperationsRuntimeCapacityResponse,
    WorkerCapacityAggregateResponse,
)
from backend.app.api.schemas.operation_control_plane import (
    OperationsControlPlaneIssueResponse,
    OperationsSelfHostedMachinesResponse,
)
from backend.app.api.schemas.operation_outcomes import (
    OperationsMcpJobsResponse,
    OperationsOutcomesResponse,
)
from backend.app.api.schemas.operation_queue import QueueLatencyResponse
from backend.app.api.schemas.operation_scheduler import OperationsSchedulerResponse


def control_plane_health(issues: list[OperationsControlPlaneIssueResponse]) -> str:
    severities = {issue.severity for issue in issues}
    if "critical" in severities:
        return "critical"
    if "warning" in severities:
        return "warning"
    return "healthy"


def append_outcome_issues(
    issues: list[OperationsControlPlaneIssueResponse],
    outcomes: OperationsOutcomesResponse,
) -> None:
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


def append_mcp_job_issues(
    issues: list[OperationsControlPlaneIssueResponse],
    mcp_jobs: OperationsMcpJobsResponse,
) -> None:
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


def append_queue_issues(
    issues: list[OperationsControlPlaneIssueResponse],
    queue: QueueLatencyResponse,
) -> None:
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


def append_worker_capacity_issues(
    issues: list[OperationsControlPlaneIssueResponse],
    worker_capacity: WorkerCapacityAggregateResponse,
    queue: QueueLatencyResponse,
) -> None:
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


def append_runtime_capacity_issues(
    issues: list[OperationsControlPlaneIssueResponse],
    runtime_capacity: OperationsRuntimeCapacityResponse,
) -> None:
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
                    "runtime_space_ids": [str(space.runtime_space_id) for space in saturated_spaces]
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


def append_scheduler_policy_issues(
    issues: list[OperationsControlPlaneIssueResponse],
    scheduler: OperationsSchedulerResponse,
) -> None:
    if scheduler.policy.paused:
        issues.append(
            OperationsControlPlaneIssueResponse(
                severity="warning",
                code="scheduler_paused",
                message="Workspace scheduling is paused.",
                metadata={"pause_reason": scheduler.policy.pause_reason},
            )
        )


def append_scheduler_backlog_issues(
    issues: list[OperationsControlPlaneIssueResponse],
    scheduler: OperationsSchedulerResponse,
) -> None:
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


def append_self_hosted_machine_issues(
    issues: list[OperationsControlPlaneIssueResponse],
    self_hosted_machines: OperationsSelfHostedMachinesResponse,
) -> None:
    unavailable = (
        self_hosted_machines.degraded
        + self_hosted_machines.quarantined
        + self_hosted_machines.revoked
        + self_hosted_machines.offline
    )
    if unavailable > 0:
        issues.append(
            OperationsControlPlaneIssueResponse(
                severity="warning",
                code="self_hosted_machines_unhealthy",
                message="Self-hosted machines need operator attention.",
                count=unavailable,
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


def control_plane_issues(
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
    append_queue_issues(issues, queue)
    append_worker_capacity_issues(issues, worker_capacity, queue)
    append_scheduler_policy_issues(issues, scheduler)
    append_runtime_capacity_issues(issues, runtime_capacity)
    append_scheduler_backlog_issues(issues, scheduler)
    append_outcome_issues(issues, outcomes)
    append_mcp_job_issues(issues, mcp_jobs)
    append_self_hosted_machine_issues(issues, self_hosted_machines)
    return issues
