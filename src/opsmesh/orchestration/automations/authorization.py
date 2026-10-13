"""Live authorization shared by inbox dispatch and deferred outbound delivery."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.permissions import WorkspaceAction, role_allows
from opsmesh.identity.authorization.resources import (
    ResourceAccessDeniedError,
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from opsmesh.orchestration.automations.contracts import AutomationConfiguration
from opsmesh.orchestration.automations.identities import AutomationSenderIdentityService
from opsmesh.orchestration.automations.models import Automation, AutomationEvent
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember


def require_automation_principal(session: Session, item: Automation) -> None:
    ExecutionIdentityService(session).restore(item.workspace_id, item.execution_identity)
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


def require_event_principal(
    session: Session,
    item: Automation,
    event: AutomationEvent,
) -> AuthenticatedUser:
    identity = event.execution_identity
    config = AutomationConfiguration.model_validate(event.configuration)
    if config.trigger_type == "message":
        live = AutomationSenderIdentityService(session).resolve(
            item,
            str(event.input_payload.get("sender_id") or ""),
        )
        if live != identity:
            raise ResourceAccessDeniedError()
    user = ExecutionIdentityService(session).restore(event.workspace_id, identity)
    if event.task_id is not None:
        ResourceAuthorizationService(session, user).require(
            event.workspace_id,
            ResourceKind.TASK,
            event.task_id,
            ResourceAction.READ,
        )
    return user
