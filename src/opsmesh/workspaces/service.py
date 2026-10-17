"""Workspace lifecycle: creation, metadata, archive, transfer and deletion.

Creating a workspace is one transaction: the tenant row, its owner
membership and its capacity accounts commit together, so no workspace can
exist without the owner that authorizes everything else. The owner role
moves only through the transfer here; the membership service refuses to
rewrite it.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditActor, append_platform_event
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.users.models import USER_STATUS_ACTIVE, UserProfile
from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.errors import ConflictError, InvalidArgumentError
from opsmesh.shared.pagination import PageParams
from opsmesh.workspaces.capacity import service as capacity_service
from opsmesh.workspaces.members import access
from opsmesh.workspaces.members.models import (
    SPACE_ROLE_ADMIN,
    SPACE_ROLE_OWNER,
    WorkspaceMember,
)
from opsmesh.workspaces.models import (
    WORKSPACE_ARCHIVED,
    WORKSPACE_DELETED,
    Workspace,
)


def create_workspace(
    session: Session,
    *,
    actor: CurrentUser,
    name: str,
    description: str,
    capacity_limits: Mapping[str, int | None],
) -> Workspace:
    """Create one workspace with its owner membership and capacity accounts."""
    workspace = Workspace(
        name=name, description=description, status="active", created_by_user_id=actor.user_id
    )
    session.add(workspace)
    session.flush()
    session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=actor.user_id, role=SPACE_ROLE_OWNER)
    )
    capacity_service.ensure_accounts(session, workspace_id=workspace.id, limits=capacity_limits)
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace.id),
        action="workspace.created",
        target_type="workspace",
        target_id=str(workspace.id),
        summary="Workspace created",
        details={"name": name},
    )
    return workspace


def list_workspaces(
    session: Session, *, actor: CurrentUser, page: PageParams
) -> tuple[list[Workspace], int]:
    """The workspaces the current user is a member of, newest first."""
    statement = (
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == actor.user_id,
            Workspace.status != WORKSPACE_DELETED,
        )
        .order_by(Workspace.created_at.desc(), Workspace.id)
    )
    return page_scalars(session, statement, page)


def get_workspace(session: Session, *, actor: CurrentUser, workspace_id: UUID) -> Workspace:
    return access.require_workspace_member(
        session, workspace_id=workspace_id, user_id=actor.user_id
    )


def update_workspace(
    session: Session,
    *,
    actor: CurrentUser,
    workspace_id: UUID,
    name: str | None,
    description: str | None,
) -> Workspace:
    """Change the workspace metadata; an archived workspace refuses writes."""
    workspace = access.require_workspace_member(
        session, workspace_id=workspace_id, user_id=actor.user_id, permission="manage", lock=True
    )
    if name is not None:
        workspace.name = name
    if description is not None:
        workspace.description = description
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace.id),
        action="workspace.updated",
        target_type="workspace",
        target_id=str(workspace.id),
        summary="Workspace updated",
        details={},
    )
    return workspace


def archive_workspace(session: Session, *, actor: CurrentUser, workspace_id: UUID) -> Workspace:
    """Archive one workspace; its facts stay readable, its writes are refused.

    Archiving an already archived workspace is a no-op, exactly like
    deleting an already deleted one: the owner lifecycle actions stay
    available on an archived workspace.
    """
    workspace = access.require_workspace_member(
        session,
        workspace_id=workspace_id,
        user_id=actor.user_id,
        permission="owner",
        allow_archived=True,
        lock=True,
    )
    if workspace.status == WORKSPACE_ARCHIVED:
        return workspace
    workspace.status = WORKSPACE_ARCHIVED
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace.id),
        action="workspace.archived",
        target_type="workspace",
        target_id=str(workspace.id),
        summary="Workspace archived",
        details={},
    )
    return workspace


def transfer_ownership(
    session: Session, *, actor: CurrentUser, workspace_id: UUID, new_owner_user_id: UUID
) -> Workspace:
    """Move the owner role to another member; the old owner becomes admin.

    The demotion runs before the promotion so the single-owner index never
    sees two owners at once: the flush between the two writes emits the
    demotion first, because the unit of work gives no ordering guarantee
    for two updates of one table inside one transaction. Both rows move
    inside the workspace lock this transition holds.
    """
    workspace = access.require_workspace_member(
        session,
        workspace_id=workspace_id,
        user_id=actor.user_id,
        permission="owner",
        allow_archived=True,
        lock=True,
    )
    if new_owner_user_id == actor.user_id:
        raise ConflictError("The workspace owner already holds the role")
    target = session.get(
        WorkspaceMember, (workspace_id, new_owner_user_id), populate_existing=True
    )
    if target is None:
        raise InvalidArgumentError("The new owner is not a workspace member")
    profile = session.get(UserProfile, new_owner_user_id)
    if profile is None or profile.status != USER_STATUS_ACTIVE:
        raise InvalidArgumentError("The new owner is not an active account")
    current = session.get(WorkspaceMember, (workspace_id, actor.user_id), populate_existing=True)
    if current is None:  # pragma: no cover - the owner check above guarantees it
        raise ConflictError("The workspace owner membership is missing")
    current.role = SPACE_ROLE_ADMIN
    session.flush()
    target.role = SPACE_ROLE_OWNER
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace.id),
        action="workspace.ownership_transferred",
        target_type="workspace",
        target_id=str(workspace.id),
        summary="Workspace ownership transferred",
        details={"new_owner_user_id": str(new_owner_user_id)},
    )
    return workspace


def delete_workspace(session: Session, *, actor: CurrentUser, workspace_id: UUID) -> None:
    """Delete one workspace; the row stays as history and refuses every access."""
    workspace = access.require_workspace_member(
        session,
        workspace_id=workspace_id,
        user_id=actor.user_id,
        permission="owner",
        allow_archived=True,
        lock=True,
    )
    if workspace.status == WORKSPACE_DELETED:
        return
    workspace.status = WORKSPACE_DELETED
    append_platform_event(
        session,
        actor=_actor(actor, workspace_id=workspace.id),
        action="workspace.deleted",
        target_type="workspace",
        target_id=str(workspace.id),
        summary="Workspace deleted",
        details={},
    )


def _actor(actor: CurrentUser, *, workspace_id: UUID) -> AuditActor:
    return AuditActor(actor_type="user", actor_id=str(actor.user_id), workspace_id=workspace_id)
