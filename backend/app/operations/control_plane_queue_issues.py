from __future__ import annotations

from backend.app.api.schemas.operation_capacity import WorkerCapacityAggregateResponse
from backend.app.api.schemas.operation_control_plane import OperationsControlPlaneIssueResponse
from backend.app.api.schemas.operation_queue import QueueLatencyResponse


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
