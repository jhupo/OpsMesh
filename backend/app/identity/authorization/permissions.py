from enum import StrEnum


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class WorkspaceAction(StrEnum):
    READ = "read"
    WRITE = "write"
    APPROVE = "approve"
    OPERATE = "operate"
    MANAGE_RUNTIME = "manage_runtime"
    MANAGE_CAPABILITY = "manage_capability"
    MANAGE_MEMBERS = "manage_members"
    ADMIN = "admin"
    OWNER = "owner"


class AccountAction(StrEnum):
    PROFILE_READ = "profile:read"
    PROFILE_WRITE = "profile:write"
    PASSWORD_CHANGE = "password:change"
    TOKENS_READ = "tokens:read"
    TOKENS_MANAGE = "tokens:manage"
    WORKSPACES_CREATE = "workspaces:create"


ROLE_PERMISSIONS: dict[WorkspaceRole, set[WorkspaceAction]] = {
    WorkspaceRole.OWNER: {
        WorkspaceAction.READ,
        WorkspaceAction.WRITE,
        WorkspaceAction.APPROVE,
        WorkspaceAction.OPERATE,
        WorkspaceAction.MANAGE_RUNTIME,
        WorkspaceAction.MANAGE_CAPABILITY,
        WorkspaceAction.MANAGE_MEMBERS,
        WorkspaceAction.ADMIN,
        WorkspaceAction.OWNER,
    },
    WorkspaceRole.ADMIN: {
        WorkspaceAction.READ,
        WorkspaceAction.WRITE,
        WorkspaceAction.APPROVE,
        WorkspaceAction.OPERATE,
        WorkspaceAction.MANAGE_RUNTIME,
        WorkspaceAction.MANAGE_CAPABILITY,
        WorkspaceAction.MANAGE_MEMBERS,
        WorkspaceAction.ADMIN,
    },
    WorkspaceRole.OPERATOR: {
        WorkspaceAction.READ,
        WorkspaceAction.WRITE,
        WorkspaceAction.APPROVE,
        WorkspaceAction.OPERATE,
        WorkspaceAction.MANAGE_RUNTIME,
    },
    WorkspaceRole.VIEWER: {
        WorkspaceAction.READ,
    },
}


def role_allows(role: str, action: WorkspaceAction) -> bool:
    try:
        workspace_role = WorkspaceRole(role)
    except ValueError:
        return False
    return action in ROLE_PERMISSIONS[workspace_role]
