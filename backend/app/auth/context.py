from dataclasses import dataclass
from uuid import UUID

from backend.app.auth.permissions import AccountAction, WorkspaceAction, WorkspaceRole
from backend.app.identity.models import User
from backend.app.workspaces.models import Workspace, WorkspaceMember


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: UUID
    email: str
    display_name: str
    token_id: UUID | None = None
    token_scopes: dict[str, object] | None = None

    @classmethod
    def from_model(
        cls,
        user: User,
        *,
        token_id: UUID | None = None,
        token_scopes: dict[str, object] | None = None,
    ) -> "AuthenticatedUser":
        return cls(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            token_id=token_id,
            token_scopes=token_scopes,
        )

    @property
    def uses_restricted_token(self) -> bool:
        return self.token_id is not None and self.token_scopes is not None

    def allows_account_action(self, action: AccountAction) -> bool:
        if not self.uses_restricted_token:
            return True
        raw_actions = self.token_scopes.get("account_actions") if self.token_scopes else None
        return isinstance(raw_actions, list) and action.value in raw_actions

    def allows_workspace_action(self, workspace_id: UUID, action: WorkspaceAction) -> bool:
        if not self.uses_restricted_token:
            return True
        raw_workspace_ids = self.token_scopes.get("workspace_ids") if self.token_scopes else None
        raw_actions = self.token_scopes.get("workspace_actions") if self.token_scopes else None
        return (
            isinstance(raw_workspace_ids, list)
            and str(workspace_id) in raw_workspace_ids
            and isinstance(raw_actions, list)
            and action.value in raw_actions
        )

    @property
    def allowed_workspace_ids(self) -> frozenset[UUID] | None:
        if not self.uses_restricted_token:
            return None
        raw_workspace_ids = self.token_scopes.get("workspace_ids") if self.token_scopes else None
        if not isinstance(raw_workspace_ids, list):
            return frozenset()
        try:
            return frozenset(UUID(str(item)) for item in raw_workspace_ids)
        except (TypeError, ValueError):
            return frozenset()


@dataclass(frozen=True)
class WorkspaceContext:
    user: AuthenticatedUser
    workspace: Workspace
    membership: WorkspaceMember

    @property
    def role(self) -> WorkspaceRole:
        return WorkspaceRole(self.membership.role)
