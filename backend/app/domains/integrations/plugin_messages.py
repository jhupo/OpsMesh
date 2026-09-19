"""Authorized plugin interaction with accepted messages; no channel-specific identities."""

from uuid import UUID

from opsmesh_plugin_sdk.services import ApprovalDecision, ApprovalReceipt
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainError
from backend.app.domains.access.execution import ExecutionIdentityService
from backend.app.domains.access.resources import (
    ResourceAccessDenied,
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from backend.app.domains.capabilities.plugins.services import PluginPrincipal, PluginServices
from backend.app.domains.integrations.automation_contracts import AutomationConfiguration
from backend.app.domains.integrations.automation_models import Automation
from backend.app.domains.integrations.automation_stream import AutomationStreamService
from backend.app.domains.integrations.identities import ExternalIdentityService
from backend.app.domains.orchestration.approvals.decisions import ApprovalDecisionService
from backend.app.domains.orchestration.approvals.models import Approval
from backend.app.runtime.workers.queue import RedisQueue


class PluginMessageService:
    def __init__(self, session: Session, queue: RedisQueue) -> None:
        self.session = session
        self.queue = queue

    def decide_approval(
        self,
        principal: PluginPrincipal,
        automation_id: UUID,
        event_id: UUID,
        approval_id: UUID,
        request: ApprovalDecision,
    ) -> ApprovalReceipt:
        PluginServices(self.session).require_automation(
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
        if item is None or request.sender_id not in AutomationConfiguration.model_validate(
            item.configuration
        ).allowed_senders:
            raise ResourceAccessDenied()
        identity = ExternalIdentityService(self.session).resolve(item, request.sender_id)
        actor = ExecutionIdentityService(self.session).restore(principal.workspace_id, identity)
        if event.task_id is None:
            raise ResourceAccessDenied()
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
            raise ResourceAccessDenied()
        decisions = ApprovalDecisionService(self.session, self.queue)
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
