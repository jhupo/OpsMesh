from __future__ import annotations

from backend.app.api.schemas.operation_control_plane import OperationsControlPlaneIssueResponse
from backend.app.api.schemas.operation_outcomes import (
    OperationsMcpJobsResponse,
    OperationsOutcomesResponse,
)


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
