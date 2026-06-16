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
from backend.app.operations.control_plane_outcome_issues import (
    append_mcp_job_issues,
    append_outcome_issues,
)
from backend.app.operations.control_plane_queue_issues import (
    append_queue_issues,
    append_worker_capacity_issues,
)
from backend.app.operations.control_plane_runtime_issues import append_runtime_capacity_issues
from backend.app.operations.control_plane_scheduler_issues import (
    append_scheduler_backlog_issues,
    append_scheduler_policy_issues,
)
from backend.app.operations.control_plane_self_hosted_issues import (
    append_self_hosted_machine_issues,
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
