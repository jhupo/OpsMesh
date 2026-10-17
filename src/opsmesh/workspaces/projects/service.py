"""Project service: creation, metadata, archive and deletion.

Creating a project also seats its creator as project administrator, so a
project never exists without someone who can administer it. Listing is
visibility-aware: space leadership sees every project in the workspace,
everyone else sees only the projects they hold a seat in.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditActor, append_platform_event
from opsmesh.identity.context import CurrentUser
from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.pagination import PageParams
from opsmesh.workspaces.members import access as space_access
from opsmesh.workspaces.projects.members import access
from opsmesh.workspaces.projects.members.models import (
    PROJECT_ROLE_ADMIN,
    ProjectMember,
)
from opsmesh.workspaces.projects.models import (
    PROJECT_ARCHIVED,
    PROJECT_DELETED,
    Project,
)


def create_project(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, name: str, description: str
) -> Project:
    """Create one project and seat its creator as project administrator."""
    space_access.require_workspace_member(
        session, workspace_id=workspace_id, user_id=actor.user_id, permission="write", lock=True
    )
    project = Project(
        workspace_id=workspace_id,
        created_by_user_id=actor.user_id,
        name=name,
        description=description,
        status="active",
    )
    session.add(project)
    session.flush()
    session.add(
        ProjectMember(
            workspace_id=workspace_id,
            project_id=project.id,
            user_id=actor.user_id,
            role=PROJECT_ROLE_ADMIN,
        )
    )
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id, project_id=project.id),
        action="project.created",
        target_type="project",
        target_id=str(project.id),
        summary="Project created",
        details={"name": name},
    )
    return project


def list_projects(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, page: PageParams
) -> tuple[list[Project], int]:
    """List the projects of one workspace the current user may see."""
    space_access.require_workspace_member(
        session, workspace_id=workspace_id, user_id=actor.user_id
    )
    statement = select(Project).where(
        Project.workspace_id == workspace_id, Project.status != PROJECT_DELETED
    )
    space_role = space_access.space_role(session, workspace_id=workspace_id, user_id=actor.user_id)
    if space_role not in {"owner", "admin"}:
        statement = statement.where(
            Project.id.in_(
                select(ProjectMember.project_id).where(
                    ProjectMember.workspace_id == workspace_id,
                    ProjectMember.user_id == actor.user_id,
                )
            )
        )
    statement = statement.order_by(Project.created_at.desc(), Project.id)
    return page_scalars(session, statement, page)


def get_project(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, project_id: UUID
) -> Project:
    return access.require_project_member(
        session, workspace_id=workspace_id, project_id=project_id, user_id=actor.user_id
    )


def update_project(
    session: Session,
    *,
    actor: CurrentUser,
    workspace_id: UUID,
    project_id: UUID,
    name: str | None,
    description: str | None,
) -> Project:
    """Change the project metadata; an archived project refuses writes."""
    project = access.require_project_member(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        permission="manage",
        lock=True,
    )
    if name is not None:
        project.name = name
    if description is not None:
        project.description = description
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id, project_id=project.id),
        action="project.updated",
        target_type="project",
        target_id=str(project.id),
        summary="Project updated",
        details={},
    )
    return project


def archive_project(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, project_id: UUID
) -> Project:
    """Archive one project; reads stay open, writes are refused."""
    project = access.require_project_member(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        permission="manage",
        lock=True,
    )
    if project.status == PROJECT_ARCHIVED:
        raise ConflictError("The project is already archived")
    project.status = PROJECT_ARCHIVED
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id, project_id=project.id),
        action="project.archived",
        target_type="project",
        target_id=str(project.id),
        summary="Project archived",
        details={},
    )
    return project


def delete_project(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, project_id: UUID
) -> None:
    """Delete one project; its history stays and every access is refused."""
    project = access.require_project_member(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        permission="manage",
        lock=True,
    )
    if project.status == PROJECT_DELETED:
        return
    project.status = PROJECT_DELETED
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace_id, project_id=project.id),
        action="project.deleted",
        target_type="project",
        target_id=str(project.id),
        summary="Project deleted",
        details={},
    )


def _actor(actor: CurrentUser, *, workspace_id: UUID, project_id: UUID) -> AuditActor:
    return AuditActor(
        actor_type="user",
        actor_id=str(actor.user_id),
        workspace_id=workspace_id,
        project_id=project_id,
    )
