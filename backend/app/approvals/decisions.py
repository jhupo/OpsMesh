from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.approvals.models import Approval
from backend.app.approvals.run_gate import ApprovalRunGateService
from backend.app.audit.service import AuditService
from backend.app.reviews.resource_review_targets import ResourceReviewDecisionService
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.redis_queue import RedisQueue


class ApprovalDecisionService:
    def __init__(self, session: Session, queue: RedisQueue | None = None) -> None:
        self._session = session
        self._queue = queue

    def approve(self, approval: Approval, user_id: UUID, reason: str | None = None) -> Approval:
        return self._decide(approval, user_id, "approved", reason)

    def reject(self, approval: Approval, user_id: UUID, reason: str | None = None) -> Approval:
        return self._decide(approval, user_id, "rejected", reason)

    def _decide(
        self,
        approval: Approval,
        user_id: UUID,
        status: str,
        reason: str | None,
    ) -> Approval:
        if approval.status != "pending":
            raise ValueError("Approval is not pending")
        approval.status = status
        approval.decided_by_user_id = user_id
        approval.decision_reason = reason
        approval.decided_at = datetime.now(UTC)
        self._append_audit_event(approval, user_id, f"approval.{status}")
        ResourceReviewDecisionService(self._session).apply_decision(
            approval,
            user_id=user_id,
            status=status,
        )
        if status == "rejected":
            ApprovalRunGateService(self._session).fail_rejected_run(approval)
        if status == "approved" and approval.agent_run_id is not None and self._queue is not None:
            self._queue.enqueue(
                JobPayload(
                    workspace_id=approval.workspace_id,
                    job_type=JobType.AGENT_RUN,
                    resource_id=approval.agent_run_id,
                    requested_by_user_id=user_id,
                    idempotency_key=f"approval.resume:{approval.workspace_id}:{approval.id}",
                )
            )
        self._session.commit()
        self._session.refresh(approval)
        return approval

    def _append_audit_event(self, approval: Approval, user_id: UUID, action: str) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=approval.workspace_id,
            user_id=user_id,
            action=action,
            target_type="approval",
            target_id=approval.id,
        )
