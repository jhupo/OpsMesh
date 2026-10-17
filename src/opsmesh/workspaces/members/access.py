"""The one space authorization path: membership, role and lifecycle state.

Every space route, service and injected scope boundary resolves permission
through this module. A platform role never satisfies it: the platform
administrator holds no tenant permission here, which is the point. The
workspace row is locked for every decision above read so a concurrent
archive or transfer cannot change the state a write is admitted against.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError
from opsmesh.workspaces.members.models import (
    SPACE_ROLES,
    WorkspaceMember,
)
from opsmesh.workspaces.models import WORKSPACE_ARCHIVED, Workspace

SpacePermission = Literal["read", "write", "manage", "owner"]


def space_role(session: Session, *, workspace_id: UUID, user_id: UUID) -> str | None:
    """The space role one user holds in one workspace, if any."""
    member = session.get(
        WorkspaceMember,
        (workspace_id, user_id),
        populate_existing=True,
    )
    return None if member is None else member.role


def require_workspace_member(
    session: Session,
    *,
    workspace_id: UUID,
    user_id: UUID,
    permission: SpacePermission = "read",
    allow_archived: bool = False,
    lock: bool = False,
) -> Workspace:
    """Authorize one user in one workspace and return the workspace.

    A missing membership and a missing workspace are the same answer: not
    found, so existence is not leaked to a non-member. An insufficient role
    is forbidden, and an archived workspace refuses every write unless the
    caller explicitly allows archived work.
    """
    statement = select(Workspace).where(Workspace.id == workspace_id)
    if lock or permission != "read":
        statement = statement.with_for_update()
    workspace = session.scalar(statement.execution_options(populate_existing=True))
    member = session.get(
        WorkspaceMember,
        (workspace_id, user_id),
        populate_existing=True,
    )
    if workspace is None or workspace.status == "deleted" or member is None:
        raise NotFoundError("The workspace does not exist")
    _require_permission(member.role, permission=permission)
    if permission != "read" and workspace.status == WORKSPACE_ARCHIVED and not allow_archived:
        raise ConflictError(
            "The workspace is archived",
            details={"workspace_id": str(workspace_id)},
        )
    return workspace


def lock_workspace(session: Session, *, workspace_id: UUID) -> Workspace:
    """Lock one workspace row for a transition that is not a member decision.

    Invitation acceptance locks the tenant before it decides, but the
    accepting user is not a member yet: the invitation flow owns that
    check. The lock still serializes the transition against a concurrent
    archive, transfer or removal.
    """
    workspace = session.scalar(
        select(Workspace)
        .where(Workspace.id == workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if workspace is None or workspace.status == "deleted":
        raise NotFoundError("The workspace does not exist")
    return workspace


def _require_permission(role: str, *, permission: SpacePermission) -> None:
    if role not in SPACE_ROLES:
        raise ForbiddenError(
            "The space role is unknown", details={"role": role, "known_roles": list(SPACE_ROLES)}
        )
    if permission == "read":
        return
    if permission == "write" and role == "viewer":
        raise ForbiddenError("A workspace viewer is read-only")
    if permission == "manage" and role not in {"owner", "admin"}:
        raise ForbiddenError("The workspace administrator role is required")
    if permission == "owner" and role != "owner":
        raise ForbiddenError("The workspace owner role is required")
