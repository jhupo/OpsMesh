from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.security.secrets import SecretEncryptionService
from backend.app.domains.access.context import AuthenticatedUser
from backend.app.domains.access.permissions import WorkspaceAction, WorkspaceRole
from backend.app.domains.access.resources import (
    ResourceAccessDenied,
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from backend.app.domains.access.service import AuthorizationService
from backend.app.domains.agents.memory.episodic import AgentEpisodicMemoryService
from backend.app.domains.orchestration.approvals.models import Approval
from backend.app.domains.orchestration.approvals.pending_tools import PendingToolInvocationService
from backend.app.domains.orchestration.approvals.run_gate import ApprovalRunGateService
from backend.app.domains.orchestration.runs.models import AgentRunStateSnapshot
from backend.app.domains.workspace.reviews.resource_review_targets import (
    ResourceReviewDecisionService,
)
from backend.app.observability.audit.service import AuditService
from backend.app.runtime.workers.contracts import JobPayload, JobType
from backend.app.runtime.workers.queue import RedisQueue


class ApprovalDecisionService:
    def __init__(
        self,
        session: Session,
        queue: RedisQueue | None = None,
        secrets: SecretEncryptionService | None = None,
    ) -> None:
        self._session = session
        self._queue = queue
        settings = get_settings()
        self._secrets = secrets or SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
            previous_secrets=settings.credential_encryption_previous_secrets,
        )

    def approve(self, approval: Approval, user_id: UUID, reason: str | None = None) -> Approval:
        return self._decide(approval, user_id, "approved", reason)

    def require_actor(self, approval: Approval, actor: AuthenticatedUser) -> None:
        authorization = AuthorizationService(self._session)
        actor = authorization.refresh_authenticated_user(actor)
        context = authorization.require_workspace(
            user_id=actor.user_id,
            workspace_id=approval.workspace_id,
            action=WorkspaceAction.APPROVE,
            authenticated_user=actor,
        )
        if approval.payload.get("kind") == "resource_review" and context.role not in {
            WorkspaceRole.OWNER,
            WorkspaceRole.ADMIN,
        }:
            raise ResourceAccessDenied()
        if approval.task_id is not None:
            ResourceAuthorizationService(self._session, actor).require(
                approval.workspace_id, ResourceKind.TASK, approval.task_id, ResourceAction.APPROVE
            )

    def reject(self, approval: Approval, user_id: UUID, reason: str | None = None) -> Approval:
        return self._decide(approval, user_id, "rejected", reason)

    def _decide(
        self,
        approval: Approval,
        user_id: UUID,
        status: str,
        reason: str | None,
    ) -> Approval:
        locked = self._session.scalar(
            select(Approval)
            .where(Approval.workspace_id == approval.workspace_id, Approval.id == approval.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked is None:
            raise ValueError("Approval no longer exists")
        approval = locked
        if approval.status == status:
            return approval
        if approval.status != "pending":
            raise ValueError("Approval is not pending")
        approval.status = status
        approval.decided_by_user_id = user_id
        approval.decision_reason = reason
        approval.decided_at = datetime.now(UTC)
        invocation = PendingToolInvocationService(
            self._session,
            self._secrets,
        ).record_decision(
            workspace_id=approval.workspace_id,
            approval_id=approval.id,
            status=status,
        )
        if invocation is not None:
            snapshot = self._session.scalar(
                select(AgentRunStateSnapshot).where(
                    AgentRunStateSnapshot.workspace_id == approval.workspace_id,
                    AgentRunStateSnapshot.agent_run_id == invocation.agent_run_id,
                )
            )
            if snapshot is not None:
                snapshot.status = status
        self._append_audit_event(approval, user_id, f"approval.{status}")
        AgentEpisodicMemoryService(self._session).capture_approval_decision(
            approval,
            actor_user_id=user_id,
        )
        ResourceReviewDecisionService(self._session).apply_decision(
            approval,
            user_id=user_id,
            status=status,
        )
        if status == "rejected" and invocation is None:
            ApprovalRunGateService(self._session).fail_rejected_run(approval)
        if (
            status == "approved" or invocation is not None
        ) and approval.agent_run_id is not None and self._queue is not None:
            self._enqueue_resume(approval, user_id, status)
        self._session.commit()
        self._session.refresh(approval)
        return approval

    def _enqueue_resume(self, approval: Approval, user_id: UUID, status: str) -> None:
        if self._queue is None or approval.agent_run_id is None:
            return
        self._queue.enqueue(
            JobPayload(
                workspace_id=approval.workspace_id,
                job_type=JobType.AGENT_RUN,
                resource_id=approval.agent_run_id,
                requested_by_user_id=user_id,
                idempotency_key=(
                    f"approval.resume:{status}:{approval.workspace_id}:{approval.id}"
                ),
            )
        )

    def _append_audit_event(self, approval: Approval, user_id: UUID, action: str) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=approval.workspace_id,
            user_id=user_id,
            action=action,
            target_type="approval",
            target_id=approval.id,
        )
