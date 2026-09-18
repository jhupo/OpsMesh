"""Live authorization shared by inbox dispatch and deferred outbound delivery."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.access.permissions import WorkspaceAction, role_allows
from backend.app.domains.capabilities.plugins.policy import plugin_resource_available
from backend.app.domains.integrations.automation_contracts import AutomationConfiguration
from backend.app.domains.integrations.automation_models import Automation, AutomationEvent
from backend.app.domains.integrations.webhooks.models import WebhookDeliveryAttempt
from backend.app.domains.workspace.tenants.models import Workspace, WorkspaceMember


def require_automation_principal(session: Session, item: Automation) -> None:
    member = session.scalar(
        select(WorkspaceMember)
        .join(Workspace)
        .where(
            WorkspaceMember.workspace_id == item.workspace_id,
            WorkspaceMember.user_id == item.created_by_user_id,
            WorkspaceMember.status == "active",
            Workspace.status == "active",
        )
        .execution_options(populate_existing=True)
    )
    if member is None or not role_allows(member.role, WorkspaceAction.WRITE):
        raise ValueError("Automation principal no longer has workspace write access")


def automation_delivery_available(session: Session, attempt: WebhookDeliveryAttempt) -> bool:
    try:
        event_id = UUID(str(attempt.payload.get("event_id")))
        pair = session.execute(
            select(AutomationEvent, Automation)
            .join(
                Automation,
                Automation.id == AutomationEvent.automation_id,
            )
            .where(
                AutomationEvent.id == event_id,
                AutomationEvent.workspace_id == attempt.workspace_id,
                Automation.workspace_id == attempt.workspace_id,
                Automation.status == "active",
            )
            .execution_options(populate_existing=True)
        ).one_or_none()
        if pair is None:
            return False
        event, item = pair
        require_automation_principal(session, item)
        config = AutomationConfiguration.model_validate(event.configuration)
        live_config = AutomationConfiguration.model_validate(item.configuration)
        if (
            config.reply_subscription_id != attempt.subscription_id
            or str(item.id) != attempt.payload.get("automation_id")
            or str(event.task_id) != attempt.payload.get("task_id")
        ):
            return False
        if (
            config.trigger_type == "message"
            and event.input_payload.get("sender_id") not in live_config.allowed_senders
        ):
            return False
        return plugin_resource_available(session, attempt.workspace_id, "message_trigger", item.id)
    except ValueError:
        return False
