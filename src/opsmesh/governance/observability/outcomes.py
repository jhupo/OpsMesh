from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.governance.observability.outcome_schemas import (
    ApprovalBacklogResponse,
    OperationsOutcomesResponse,
    RunFailureReasonResponse,
    RunOutcomeWindowResponse,
)
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.shared.utils import ensure_aware_utc


class ExecutionOutcomeService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def summarize(self, workspace_id: UUID, *, window_seconds: int) -> OperationsOutcomesResponse:
        now = datetime.now(UTC)
        scope = (
            AgentRun.workspace_id == workspace_id,
            AgentRun.updated_at >= now - timedelta(seconds=window_seconds),
        )
        states = dict(
            self.session.execute(
                select(AgentRun.status, func.count()).where(*scope).group_by(AgentRun.status)
            )
            .tuples()
            .all()
        )
        failure_code = func.coalesce(AgentRun.error["code"].astext, "unknown")
        reasons = (
            self.session.execute(
                select(failure_code, func.count())
                .where(*scope, AgentRun.status == "failed")
                .group_by(failure_code)
            )
            .tuples()
            .all()
        )
        pending_scope = (Approval.workspace_id == workspace_id, Approval.status == "pending")
        by_type = dict(
            self.session.execute(
                select(Approval.approval_type, func.count())
                .where(*pending_scope)
                .group_by(Approval.approval_type)
            )
            .tuples()
            .all()
        )
        oldest = self.session.scalar(select(func.min(Approval.created_at)).where(*pending_scope))
        high_risk = int(
            self.session.scalar(
                select(func.count())
                .select_from(Approval)
                .where(*pending_scope, Approval.risk_level == "high")
            )
            or 0
        )
        total = sum(states.values())
        failed = states.get("failed", 0)
        return OperationsOutcomesResponse(
            generated_at=now,
            runs=RunOutcomeWindowResponse(
                window_seconds=window_seconds,
                total_runs=total,
                completed_runs=states.get("completed", 0),
                failed_runs=failed,
                cancelled_runs=states.get("cancelled", 0),
                failure_rate=round(failed / total, 4) if total else 0,
                failure_reasons=[
                    RunFailureReasonResponse(code=code, count=count)
                    for code, count in sorted(reasons)
                ],
            ),
            approvals=ApprovalBacklogResponse(
                pending=sum(by_type.values()),
                high_risk_pending=high_risk,
                oldest_pending_age_seconds=max(
                    0, int((now - ensure_aware_utc(oldest)).total_seconds())
                )
                if oldest
                else None,
                pending_by_type=dict(sorted(by_type.items())),
            ),
        )
