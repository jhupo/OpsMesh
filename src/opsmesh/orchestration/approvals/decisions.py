from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditService
from opsmesh.governance.reviews.resource_review_targets import ResourceReviewDecisionService
from opsmesh.identity.auth.service import AuthenticationService
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.identity.authorization.permissions import WorkspaceAction, WorkspaceRole
from opsmesh.identity.authorization.resources import (
    ResourceAccessDeniedError,
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from opsmesh.identity.authorization.service import WorkspaceAuthorizationService
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.approvals.pending_tools import PendingToolInvocationService
from opsmesh.orchestration.approvals.run_gate import ApprovalRunGateService
from opsmesh.orchestration.runs.models import AgentResumeStateSnapshot
from opsmesh.resources.memory.episodic import AgentEpisodicMemoryService
from opsmesh.runtime.commands.approvals import apply_command_decision
from opsmesh.runtime.temporal.outbox import stage_signal
from opsmesh.shared.config import get_settings
from opsmesh.shared.security.secrets import SecretEncryptionService


class ApprovalDecisionService:
    def __init__(
        self,
        session: Session,
        secrets: SecretEncryptionService | None = None,
    ) -> None:
        self._session = session
        settings = get_settings()
        self._secrets = secrets or SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
            previous_secrets=settings.credential_encryption_previous_secrets,
        )

    def approve(self, approval: Approval, user_id: UUID, reason: str | None = None) -> Approval:
        return self._decide(approval, user_id, "approved", reason)

    def require_actor(self, approval: Approval, actor: AuthenticatedUser) -> None:
        authorization = AuthenticationService(self._session)
        actor = authorization.refresh_authenticated_user(actor)
        context = WorkspaceAuthorizationService(self._session).require_workspace(
            user_id=actor.user_id,
            workspace_id=approval.workspace_id,
            action=WorkspaceAction.APPROVE,
            authenticated_user=actor,
        )
        if approval.approval_type == "runtime.command.control":
            WorkspaceAuthorizationService(self._session).require_workspace(
                user_id=actor.user_id,
                workspace_id=approval.workspace_id,
                action=WorkspaceAction.MANAGE_RUNTIME,
                authenticated_user=actor,
            )
        if approval.payload.get("kind") == "resource_review" and context.role not in {
            WorkspaceRole.OWNER,
            WorkspaceRole.ADMIN,
        }:
            raise ResourceAccessDeniedError()
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
        apply_command_decision(self._session, approval, status)
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
                select(AgentResumeStateSnapshot).where(
                    AgentResumeStateSnapshot.workspace_id == approval.workspace_id,
                    AgentResumeStateSnapshot.agent_run_id == invocation.agent_run_id,
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
        # A rejection is also a durable wake-up. The run may already be failed by
        # its gate, but its Workflow must leave approval wait and close as well.
        if approval.agent_run_id is not None:
            self._stage_resume(approval)
        self._session.commit()
        self._session.refresh(approval)
        return approval

    def _stage_resume(self, approval: Approval) -> None:
        if approval.agent_run_id is None:
            return
        from opsmesh.orchestration.runs.models import AgentRun
        from opsmesh.orchestration.runs.temporal import run_execution_reference

        run = self._session.scalar(
            select(AgentRun).where(
                AgentRun.workspace_id == approval.workspace_id,
                AgentRun.id == approval.agent_run_id,
            )
        )
        if run is None:
            raise ValueError("Approval run is unavailable")
        stage_signal(
            self._session,
            dispatch_id=uuid4(),
            workspace_id=approval.workspace_id,
            workflow_id=run_execution_reference(self._session, run).workflow_id,
            signal_name="approval",
            resource_id=approval.id,
        )

    def _append_audit_event(self, approval: Approval, user_id: UUID, action: str) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=approval.workspace_id,
            user_id=user_id,
            action=action,
            target_type="approval",
            target_id=approval.id,
        )
