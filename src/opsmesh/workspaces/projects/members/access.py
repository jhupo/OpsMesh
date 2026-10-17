"""The one project authorization path: membership, role and lifecycle state.

A project role is derived, never stored twice: a workspace owner or admin
administers every project in the workspace, and everyone else holds the
role of their project membership row. Lock order is the workspace row
before the project row, the same order the space access path takes, so a
project decision can never deadlock against a space transition.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError
from opsmesh.workspaces.members import access as space_access
from opsmesh.workspaces.members.models import WorkspaceMember
from opsmesh.workspaces.projects.members.models import (
    PROJECT_ROLES,
    ProjectMember,
)
from opsmesh.workspaces.projects.models import PROJECT_ARCHIVED, Project

ProjectPermission = Literal["read", "write", "manage"]


def project_role(
    session: Session, *, workspace_id: UUID, project_id: UUID, user_id: UUID
) -> str | None:
    """The project role one user holds: space leadership administers, the
    membership row decides for everyone else."""
    member = session.get(WorkspaceMember, (workspace_id, user_id), populate_existing=True)
    if member is None:
        return None
    if member.role in {"owner", "admin"}:
        return "admin"
    project_member = session.get(ProjectMember, (project_id, user_id), populate_existing=True)
    if project_member is None or project_member.workspace_id != workspace_id:
        return None
    return "viewer" if member.role == "viewer" else project_member.role


def require_project_member(
    session: Session,
    *,
    workspace_id: UUID,
    project_id: UUID,
    user_id: UUID,
    permission: ProjectPermission = "read",
    allow_archived: bool = False,
    lock: bool = False,
) -> Project:
    """Authorize one user in one project and return the project.

    The workspace membership is checked first through the space access
    path, so a removed space member is refused here with the same answer
    the space itself would give.
    """
    space_access.require_workspace_member(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        permission="read" if permission == "read" else "write",
        allow_archived=allow_archived,
        lock=lock or permission != "read",
    )
    statement = select(Project).where(
        Project.id == project_id, Project.workspace_id == workspace_id
    )
    if lock or permission != "read":
        statement = statement.with_for_update()
    project = session.scalar(statement.execution_options(populate_existing=True))
    role = project_role(session, workspace_id=workspace_id, project_id=project_id, user_id=user_id)
    if project is None or project.status == "deleted" or role is None:
        raise NotFoundError("The project does not exist")
    _require_permission(role, permission=permission)
    if permission != "read" and project.status == PROJECT_ARCHIVED and not allow_archived:
        raise ConflictError("The project is archived", details={"project_id": str(project_id)})
    return project


def lock_project(session: Session, *, workspace_id: UUID, project_id: UUID) -> Project:
    """Lock one project row for a transition that is not a member decision.

    Invitation acceptance locks the project before it decides, but the
    accepting user may not be a project member yet.
    """
    space_access.lock_workspace(session, workspace_id=workspace_id)
    project = session.scalar(
        select(Project)
        .where(Project.id == project_id, Project.workspace_id == workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if project is None or project.status == "deleted":
        raise NotFoundError("The project does not exist")
    return project


def _require_permission(role: str, *, permission: ProjectPermission) -> None:
    if role not in PROJECT_ROLES:
        raise ForbiddenError(
            "The project role is unknown",
            details={"role": role, "known_roles": list(PROJECT_ROLES)},
        )
    if permission == "read":
        return
    if permission == "write" and role == "viewer":
        raise ForbiddenError("A project viewer is read-only")
    if permission == "manage" and role != "admin":
        raise ForbiddenError("The project administrator role is required")
