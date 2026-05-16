from enum import StrEnum


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class WorkspaceAction(StrEnum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"
    OWNER = "owner"


ROLE_PERMISSIONS: dict[WorkspaceRole, set[WorkspaceAction]] = {
    WorkspaceRole.OWNER: {
        WorkspaceAction.READ,
        WorkspaceAction.WRITE,
        WorkspaceAction.ADMIN,
        WorkspaceAction.OWNER,
    },
    WorkspaceRole.ADMIN: {
        WorkspaceAction.READ,
        WorkspaceAction.WRITE,
        WorkspaceAction.ADMIN,
    },
    WorkspaceRole.OPERATOR: {
        WorkspaceAction.READ,
        WorkspaceAction.WRITE,
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

