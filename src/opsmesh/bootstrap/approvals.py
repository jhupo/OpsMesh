"""Wire durable approval decisions to the owners of their targets."""

from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.platform.approvals.port import PlatformApprovals
from opsmesh.shared.approvals import ApprovalSnapshot, ApprovalSubject, ApprovalTarget


def build_approvals() -> PlatformApprovals:

    return PlatformApprovals(ApplicationApprovalNotifier(), prepare_approval_decision)


def prepare_approval_decision(
    session: Session, request: ApprovalSnapshot, subject: ApprovalSubject, decision: str
) -> None:
    if request.kind == "capability_publication":
        from opsmesh.capabilities.lifecycle import prepare_publication_decision

        prepare_publication_decision(session, review_id=request.id, decision=decision)


class ApplicationApprovalNotifier:
    def notify_decided(
        self,
        session: Session,
        *,
        approval_id: UUID,
        decision: str,
        target: ApprovalTarget,
        decided_by_user_id: UUID,
        reason: str | None,
    ) -> None:
        if target.target_type == "capability_version":
            from opsmesh.capabilities.lifecycle import apply_publication_decision

            apply_publication_decision(session, approval_id=approval_id, decision=decision)
        else:
            from opsmesh.bootstrap.orchestration import ExecutionApprovalNotifier

            ExecutionApprovalNotifier().notify_decided(
                session,
                approval_id=approval_id,
                decision=decision,
                target=target,
                decided_by_user_id=decided_by_user_id,
                reason=reason,
            )
