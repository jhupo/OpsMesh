"""Restore a channel sender identity through existing workspace authorization."""

from opsmesh_plugin_sdk.context import UserContext, UserIdentity
from opsmesh_plugin_sdk.services.identity import PermissionQuery, PermissionResult
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.plugins.authentication import PluginAuthenticationService, PluginPrincipal
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.resources import (
    ResourceAccessDeniedError,
    ResourceAuthorizationService,
    ResourceKind,
)
from opsmesh.orchestration.automations.contracts import AutomationConfiguration
from opsmesh.orchestration.automations.identities import AutomationSenderIdentityService
from opsmesh.orchestration.automations.models import Automation


class PluginUserIdentityService:
    """Delegate sender mapping and resource permissions to their business services."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def resolve_user(
        self, principal: PluginPrincipal, query: UserContext, permission: str
    ) -> AuthenticatedUser:
        PluginAuthenticationService(self.session).require_automation(
            principal, query.automation_id, permission
        )
        item = self.session.scalar(
            select(Automation).where(
                Automation.workspace_id == principal.workspace_id,
                Automation.id == query.automation_id,
                Automation.status == "active",
            )
        )
        if (
            item is None
            or query.sender_id
            not in AutomationConfiguration.model_validate(item.configuration).allowed_senders
        ):
            raise ResourceAccessDeniedError()
        identity = AutomationSenderIdentityService(self.session).resolve(item, query.sender_id)
        return ExecutionIdentityService(self.session).restore(principal.workspace_id, identity)

    def identity(self, principal: PluginPrincipal, query: UserContext) -> UserIdentity:
        user = self.resolve_user(principal, query, "identity.read")
        return UserIdentity(
            user_id=user.user_id,
            display_name=user.display_name,
            workspace_id=principal.workspace_id,
        )

    def permissions(self, principal: PluginPrincipal, query: PermissionQuery) -> PermissionResult:
        user = self.resolve_user(principal, query, "permissions.read")
        try:
            actions = ResourceAuthorizationService(self.session, user).effective_actions(
                principal.workspace_id,
                ResourceKind(query.resource_kind),
                query.resource_id,
            )
        except ResourceAccessDeniedError:
            actions = []
        return PermissionResult(actions=[action.value for action in actions])
