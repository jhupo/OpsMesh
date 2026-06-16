from __future__ import annotations

from backend.app.api.schemas.operation_control_plane import OperationsControlPlaneIssueResponse
from backend.app.api.schemas.operation_scheduler import OperationsSchedulerResponse


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
                    "blocked_reasons": [reason.model_dump() for reason in scheduler.blocked_reasons]
                },
            )
        )
