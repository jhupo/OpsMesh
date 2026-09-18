"""Administrator-managed channel identities; message metadata never grants authority."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.access.context import AuthenticatedUser
from backend.app.domains.access.execution import ExecutionIdentityService
from backend.app.domains.access.permissions import WorkspaceAction
from backend.app.domains.access.resources import (
    ResourceAccessDenied,
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from backend.app.domains.access.service import AuthorizationService
from backend.app.domains.integrations.automation_models import Automation, ExternalIdentityBinding
from backend.app.observability.audit.service import AuditService


class ExternalIdentityService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def set_binding(
        self,
        *,
        workspace_id: UUID,
        automation_id: UUID,
        sender_id: str,
        user_id: UUID,
        active: bool,
        actor: AuthenticatedUser,
    ) -> ExternalIdentityBinding:
        auth = AuthorizationService(self._session)
        auth.require_workspace(
            user_id=actor.user_id,
            workspace_id=workspace_id,
            action=WorkspaceAction.ADMIN,
            authenticated_user=actor,
        )
        auth.require_workspace(
            user_id=user_id, workspace_id=workspace_id, action=WorkspaceAction.READ
        )
        automation = self._session.scalar(
            select(Automation)
            .where(
                Automation.workspace_id == workspace_id,
                Automation.id == automation_id,
            )
            .with_for_update()
        )
        if automation is None:
            raise ResourceAccessDenied()
        binding = self._session.scalar(
            select(ExternalIdentityBinding).where(
                ExternalIdentityBinding.workspace_id == workspace_id,
                ExternalIdentityBinding.automation_id == automation_id,
                ExternalIdentityBinding.sender_id == sender_id,
            )
        )
        if binding is None:
            binding = ExternalIdentityBinding(
                workspace_id=workspace_id,
                automation_id=automation_id,
                sender_id=sender_id,
                user_id=user_id,
            )
            self._session.add(binding)
        binding.user_id = user_id
        binding.status = "active" if active else "revoked"
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor.user_id,
            action="external_identity.updated",
            target_type="automation",
            target_id=automation_id,
            metadata={"subject_user_id": str(user_id), "status": binding.status},
        )
        self._session.flush()
        return binding

    def resolve(self, automation: Automation, sender_id: str) -> dict[str, object]:
        binding = self._session.scalar(
            select(ExternalIdentityBinding)
            .where(
                ExternalIdentityBinding.workspace_id == automation.workspace_id,
                ExternalIdentityBinding.automation_id == automation.id,
                ExternalIdentityBinding.sender_id == sender_id,
                ExternalIdentityBinding.status == "active",
            )
            .execution_options(populate_existing=True)
        )
        if binding is None:
            raise ResourceAccessDenied()
        identity: dict[str, object] = {
            "user_id": str(binding.user_id),
            "token_id": None,
            "token_scopes": None,
        }
        user = ExecutionIdentityService(self._session).restore(automation.workspace_id, identity)
        ResourceAuthorizationService(self._session, user).require(
            automation.workspace_id,
            ResourceKind.AUTOMATION,
            automation.id,
            ResourceAction.INVOKE,
        )
        return identity
