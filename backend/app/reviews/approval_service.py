from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.approvals.models import Approval
from backend.app.approvals.service import ApprovalService
from backend.app.audit.service import AuditService
from backend.app.reviews.models import ResourceReview
from backend.app.security.redaction import redact_sensitive_payload


class ResourceReviewApprovalService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def request_resource_review(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        approval_type: str,
        target_type: str,
        target_id: UUID,
        target_name: str,
        review: ResourceReview,
        snapshot: dict[str, object],
    ) -> Approval:
        approval = ApprovalService(self._session).create_approval(
            workspace_id=workspace_id,
            task_id=None,
            agent_run_id=None,
            requested_by_agent_profile_id=None,
            approval_type=approval_type,
            risk_level=review.risk_level,
            payload={
                "kind": "resource_review",
                "action": "activate",
                "target_type": target_type,
                "target_id": str(target_id),
                "target_name": target_name,
                "review": {
                    "required": review.required,
                    "risk_level": review.risk_level,
                    "reasons": list(review.reasons),
                    "signals": dict(review.signals),
                },
                "snapshot": redact_sensitive_payload(snapshot),
            },
        )
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="resource_review.requested",
                target_type=target_type,
                target_id=target_id,
                metadata={
                    "approval_id": str(approval.id),
                    "approval_type": approval_type,
                    "risk_level": review.risk_level,
                    "reasons": list(review.reasons),
                },
            )
        return approval
