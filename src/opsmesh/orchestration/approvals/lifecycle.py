from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.orchestration.approvals.models import Approval, PendingToolInvocation
from opsmesh.orchestration.runs.models import AgentResumeStateSnapshot


class AgentToolApprovalLifecycleService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def cancel_for_run(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        actor_user_id: UUID | None,
    ) -> int:
        rows = self._rows_for_run(workspace_id=workspace_id, run_id=run_id)
        cancelled = 0
        decided_at = datetime.now(UTC)
        for approval, invocation in rows:
            if approval.status == "pending":
                approval.status = "cancelled"
                approval.decided_by_user_id = actor_user_id
                approval.decision_reason = "run_cancelled"
                approval.decided_at = decided_at
                cancelled += 1
            if invocation.status == "executing":
                invocation.status = "cancel_requested"
            elif invocation.status not in {
                "completed",
                "failed",
                "rejection_consumed",
                "outcome_unknown",
            }:
                invocation.status = "cancelled"
                invocation.completed_at = decided_at
        for approval in self._session.scalars(
            select(Approval).where(
                Approval.workspace_id == workspace_id,
                Approval.agent_run_id == run_id,
                Approval.status == "pending",
            )
        ):
            approval.status = "cancelled"
            approval.decided_by_user_id = actor_user_id
            approval.decision_reason = "run_cancelled"
            approval.decided_at = decided_at
            cancelled += 1
        self._mark_snapshot(workspace_id=workspace_id, run_id=run_id, status="cancelled")
        self._session.flush()
        return cancelled

    def _rows_for_run(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
    ) -> list[tuple[Approval, PendingToolInvocation]]:
        return list(
            self._session.execute(
                select(Approval, PendingToolInvocation)
                .join(
                    PendingToolInvocation,
                    PendingToolInvocation.approval_id == Approval.id,
                )
                .where(
                    Approval.workspace_id == workspace_id,
                    PendingToolInvocation.workspace_id == workspace_id,
                    PendingToolInvocation.agent_run_id == run_id,
                )
            )
            .tuples()
            .all()
        )

    def _mark_snapshot(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        status: str,
    ) -> None:
        snapshot = self._session.scalar(
            select(AgentResumeStateSnapshot).where(
                AgentResumeStateSnapshot.workspace_id == workspace_id,
                AgentResumeStateSnapshot.agent_run_id == run_id,
            )
        )
        if snapshot is not None:
            snapshot.status = status
