"""Authorized plugin interaction with accepted messages; no channel-specific identities."""

from uuid import UUID

from opsmesh_plugin_sdk.messaging.contracts import ApprovalDecision, ApprovalReceipt
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.plugins.authentication import PluginAuthenticationService, PluginPrincipal
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.resources import (
    ResourceAccessDeniedError,
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from opsmesh.orchestration.approvals.decisions import ApprovalDecisionService
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.automations.contracts import AutomationConfiguration
from opsmesh.orchestration.automations.identities import AutomationSenderIdentityService
from opsmesh.orchestration.automations.models import Automation
from opsmesh.orchestration.automations.stream import AutomationStreamService
from opsmesh.shared.errors import DomainError


class PluginApprovalDecisionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def decide_approval(
        self,
        principal: PluginPrincipal,
        automation_id: UUID,
        event_id: UUID,
        approval_id: UUID,
        request: ApprovalDecision,
    ) -> ApprovalReceipt:
        PluginAuthenticationService(self.session).require_automation(
            principal, automation_id, "approvals.decide"
        )
        event, _ = AutomationStreamService(self.session).authorize(
            principal.workspace_id, automation_id, event_id, principal
        )
        item = self.session.scalar(
            select(Automation).where(
                Automation.workspace_id == principal.workspace_id,
                Automation.id == automation_id,
                Automation.status == "active",
            )
        )
        if (
            item is None
            or request.sender_id
            not in AutomationConfiguration.model_validate(item.configuration).allowed_senders
        ):
            raise ResourceAccessDeniedError()
        identity = AutomationSenderIdentityService(self.session).resolve(item, request.sender_id)
        actor = ExecutionIdentityService(self.session).restore(principal.workspace_id, identity)
        if event.task_id is None:
            raise ResourceAccessDeniedError()
        ResourceAuthorizationService(self.session, actor).require(
            principal.workspace_id, ResourceKind.TASK, event.task_id, ResourceAction.READ
        )
        approval = self.session.scalar(
            select(Approval).where(
                Approval.workspace_id == principal.workspace_id,
                Approval.id == approval_id,
                Approval.task_id == event.task_id,
            )
        )
        if approval is None:
            raise ResourceAccessDeniedError()
        decisions = ApprovalDecisionService(self.session)
        decisions.require_actor(approval, actor)
        try:
            if request.decision == "approve":
                result = decisions.approve(approval, actor.user_id, request.reason)
            else:
                result = decisions.reject(approval, actor.user_id, request.reason)
        except ValueError as exc:
            raise DomainError(
                "Approval decision conflicts with current state",
                code="approval_conflict",
                status_code=409,
            ) from exc
        return ApprovalReceipt.model_validate({"id": result.id, "status": result.status})
