from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import (
    ApprovalBacklogResponse,
    McpJobStatusBucketResponse,
    McpJobToolBucketResponse,
    OperationsMcpJobsResponse,
    OperationsOutcomesResponse,
    RunFailureReasonResponse,
    RunOutcomeWindowResponse,
)
from backend.app.approvals.models import Approval
from backend.app.runs.models import AgentRun
from backend.app.self_hosted.models import SelfHostedMcpJob


class OperationsOutcomeService:
    def __init__(self, session: Session) -> None:
        self._session = session

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


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
