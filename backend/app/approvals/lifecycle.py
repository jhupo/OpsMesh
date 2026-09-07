from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.approvals.models import Approval, PendingToolInvocation
from backend.app.approvals.run_gate import ApprovalRunGateService
from backend.app.audit.service import AuditService
from backend.app.runs.models import AgentRunStateSnapshot


@dataclass(frozen=True, slots=True)
class ApprovalExpirationSummary:
    expired: int


class AgentToolApprovalLifecycleService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def cancel_for_run(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        actor_user_id: UUID,
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
        self._mark_snapshot(workspace_id=workspace_id, run_id=run_id, status="cancelled")
        self._session.flush()
        return cancelled

    def expire_pending(
        self,
        *,
        timeout_seconds: int,
        limit: int = 100,
        now: datetime | None = None,
    ) -> ApprovalExpirationSummary:
        if timeout_seconds < 1:
            raise ValueError("Approval timeout must be positive")
        decided_at = now or datetime.now(UTC)
        cutoff = decided_at - timedelta(seconds=timeout_seconds)
        rows = self._session.execute(
            select(Approval, PendingToolInvocation)
            .join(
                PendingToolInvocation,
                PendingToolInvocation.approval_id == Approval.id,
            )
            .where(
                Approval.status == "pending",
                PendingToolInvocation.status == "pending",
                Approval.created_at < cutoff,
            )
            .order_by(Approval.created_at, Approval.id)
            .limit(limit)
        ).all()
        for approval, invocation in rows:
            approval.status = "timed_out"
            approval.decision_reason = "approval_timeout"
            approval.decided_at = decided_at
            invocation.status = "timed_out"
            invocation.completed_at = decided_at
            self._mark_snapshot(
                workspace_id=approval.workspace_id,
                run_id=invocation.agent_run_id,
                status="timed_out",
            )
            ApprovalRunGateService(self._session).fail_run(
                approval,
                code="approval_timeout",
                message="Tool approval timed out",
            )
            AuditService(self._session).record_system_action(
                workspace_id=approval.workspace_id,
                action="approval.timed_out",
                target_type="approval",
                target_id=approval.id,
                metadata={
                    "agent_run_id": str(invocation.agent_run_id),
                    "tool_call_id": invocation.tool_call_id,
                    "tool_name": invocation.tool_name,
                },
            )
        self._session.flush()
        return ApprovalExpirationSummary(expired=len(rows))

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
                    PendingToolInvocation.workspace_id == workspace_id,
                    PendingToolInvocation.agent_run_id == run_id,
                )
            ).all()
        )

    def _mark_snapshot(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        status: str,
    ) -> None:
        snapshot = self._session.scalar(
            select(AgentRunStateSnapshot).where(
                AgentRunStateSnapshot.workspace_id == workspace_id,
                AgentRunStateSnapshot.agent_run_id == run_id,
            )
        )
        if snapshot is not None:
            snapshot.status = status
