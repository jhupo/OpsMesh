from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.workspaces import (
    WorkspaceInviteAcceptRequest,
    WorkspaceInviteCreateRequest,
)
from backend.app.api.services.workspace_errors import (
    WorkspaceInviteConflictError,
    WorkspaceInviteNotFoundError,
    WorkspaceInvitePermissionError,
)
from backend.app.api.services.workspace_members import ensure_actor_can_assign_role
from backend.app.api.services.workspace_snapshots import (
    as_utc,
    canonical_datetime,
    invite_snapshot,
    member_snapshot,
)
from backend.app.audit.service import AuditService
from backend.app.core.config import Settings, get_settings
from backend.app.db.errors import commit_or_raise_conflict
from backend.app.identity.models import User
from backend.app.workspaces.models import Workspace, WorkspaceInvite, WorkspaceMember


@dataclass(frozen=True)
class CreatedWorkspaceInvite:
    invite: WorkspaceInvite
    token: str


@dataclass(frozen=True)
class AcceptedWorkspaceInvite:
    invite: WorkspaceInvite
    member: WorkspaceMember


class WorkspaceInviteService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def create_invite(
        self,
        workspace_id: UUID,
        data: WorkspaceInviteCreateRequest,
        *,
        actor_user_id: UUID,
        actor_role: str,
    ) -> CreatedWorkspaceInvite:
        ensure_actor_can_assign_role(actor_role, data.role)
        self.expire_workspace_invites(workspace_id)
        invitee = self._resolve_invitee(data.email, data.invitee_user_id)
        invitee_user_id = invitee.id if invitee is not None else None
        if invitee is not None:
            active_member = self._session.scalar(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == invitee.id,
                    WorkspaceMember.status == "active",
                )
            )
            if active_member is not None:
                raise WorkspaceInviteConflictError("User is already an active workspace member")

        existing_invite = self._session.scalar(
            select(WorkspaceInvite)
            .where(
                WorkspaceInvite.workspace_id == workspace_id,
                WorkspaceInvite.email == data.email,
                WorkspaceInvite.status == "active",
            )
            .with_for_update()
        )
        if existing_invite is not None:
            raise WorkspaceInviteConflictError("Active workspace invite already exists")

        token = generate_invite_token()
        invite = WorkspaceInvite(
            workspace_id=workspace_id,
            email=data.email,
            role=data.role,
            token_hash=hash_invite_token(token, self._settings),
            fingerprint=fingerprint_invite_token(token),
            inviter_user_id=actor_user_id,
            invitee_user_id=invitee_user_id,
            expires_at=data.expires_at,
        )
        self._session.add(invite)
        self._session.flush([invite])
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="workspace.invite_created",
            target_type="workspace_invite",
            target_id=invite.id,
            metadata={
                "email": invite.email,
                "role": invite.role,
                "fingerprint": invite.fingerprint,
                "invitee_user_id": str(invitee_user_id) if invitee_user_id else None,
                "expires_at": canonical_datetime(invite.expires_at),
            },
        )
        commit_or_raise_conflict(self._session, "Workspace invite already exists")
        self._session.refresh(invite)
        return CreatedWorkspaceInvite(invite=invite, token=token)

    def revoke_invite(
        self,
        workspace_id: UUID,
        invite_id: UUID,
        *,
        actor_user_id: UUID,
    ) -> WorkspaceInvite:
        invite = self._session.scalar(
            select(WorkspaceInvite)
            .where(
                WorkspaceInvite.workspace_id == workspace_id,
                WorkspaceInvite.id == invite_id,
            )
            .with_for_update()
        )
        if invite is None:
            raise WorkspaceInviteNotFoundError("Workspace invite not found")
        if invite.status == "accepted":
            raise WorkspaceInviteConflictError("Accepted workspace invite cannot be revoked")
        if invite.status != "revoked":
            before = invite_snapshot(invite)
            invite.status = "revoked"
            invite.revoked_at = datetime.now(UTC)
            invite.revoked_by_user_id = actor_user_id
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="workspace.invite_revoked",
                target_type="workspace_invite",
                target_id=invite.id,
                metadata={
                    "fingerprint": invite.fingerprint,
                    "before": before,
                    "after": invite_snapshot(invite),
                },
            )
        self._session.commit()
        self._session.refresh(invite)
        return invite

    def accept_invite(
        self,
        data: WorkspaceInviteAcceptRequest,
        *,
        actor_user_id: UUID,
    ) -> AcceptedWorkspaceInvite:
        actor = self._session.get(User, actor_user_id)
        if actor is None or actor.status != "active":
            raise WorkspaceInvitePermissionError("Authenticated user was not found or is inactive")
        token_hash = hash_invite_token(data.token, self._settings)
        fingerprint = fingerprint_invite_token(data.token)
        invite = self._session.scalar(
            select(WorkspaceInvite)
            .where(WorkspaceInvite.token_hash == token_hash)
            .with_for_update()
        )
        if invite is None:
            raise WorkspaceInviteNotFoundError("Workspace invite not found")
        workspace = self._session.get(Workspace, invite.workspace_id)
        if workspace is None or workspace.status != "active":
            raise WorkspaceInviteConflictError(
                "Workspace invite is not active",
                workspace_id=invite.workspace_id,
                fingerprint=invite.fingerprint,
            )
        if invite.fingerprint != fingerprint:
            raise WorkspaceInviteNotFoundError("Workspace invite not found")
        if invite.status != "active":
            raise WorkspaceInviteConflictError(
                "Workspace invite is not active",
                workspace_id=invite.workspace_id,
                fingerprint=invite.fingerprint,
            )
        now = datetime.now(UTC)
        if as_utc(invite.expires_at) <= now:
            before = invite_snapshot(invite)
            invite.status = "expired"
            AuditService(self._session).record_user_action(
                workspace_id=invite.workspace_id,
                user_id=actor_user_id,
                action="workspace.invite_expired",
                target_type="workspace_invite",
                target_id=invite.id,
                metadata={
                    "fingerprint": invite.fingerprint,
                    "before": before,
                    "after": invite_snapshot(invite),
                },
            )
            self._session.commit()
            raise WorkspaceInviteConflictError(
                "Workspace invite has expired",
                workspace_id=invite.workspace_id,
                fingerprint=invite.fingerprint,
            )
        if invite.invitee_user_id is not None and invite.invitee_user_id != actor_user_id:
            raise WorkspaceInvitePermissionError(
                "Workspace invite is not assigned to the authenticated user",
                workspace_id=invite.workspace_id,
                fingerprint=invite.fingerprint,
            )
        if invite.email.lower() != actor.email.lower():
            raise WorkspaceInvitePermissionError(
                "Workspace invite email does not match the authenticated user",
                workspace_id=invite.workspace_id,
                fingerprint=invite.fingerprint,
            )

        member = self._session.scalar(
            select(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == invite.workspace_id,
                WorkspaceMember.user_id == actor_user_id,
            )
            .with_for_update()
        )
        if member is not None and member.status == "active":
            raise WorkspaceInviteConflictError(
                "User is already an active workspace member",
                workspace_id=invite.workspace_id,
                fingerprint=invite.fingerprint,
            )

        before_member: dict[str, object] | None
        if member is None:
            member = WorkspaceMember(
                workspace_id=invite.workspace_id,
                user_id=actor_user_id,
                role=invite.role,
                status="active",
            )
            self._session.add(member)
            self._session.flush([member])
            before_member = None
        else:
            before_member = member_snapshot(member)
            member.role = invite.role
            member.status = "active"

        before_invite = invite_snapshot(invite)
        invite.status = "accepted"
        invite.accepted_at = now
        invite.accepted_by_user_id = actor_user_id
        AuditService(self._session).record_user_action(
            workspace_id=invite.workspace_id,
            user_id=actor_user_id,
            action="workspace.invite_accepted",
            target_type="workspace_invite",
            target_id=invite.id,
            metadata={
                "fingerprint": invite.fingerprint,
                "before": before_invite,
                "after": invite_snapshot(invite),
                "member_before": before_member,
                "member_after": member_snapshot(member),
            },
        )
        commit_or_raise_conflict(self._session, "Workspace member already exists")
        self._session.refresh(invite)
        self._session.refresh(member)
        return AcceptedWorkspaceInvite(invite=invite, member=member)

    def expire_workspace_invites(self, workspace_id: UUID) -> int:
        now = datetime.now(UTC)
        expired = list(
            self._session.scalars(
                select(WorkspaceInvite)
                .where(
                    WorkspaceInvite.workspace_id == workspace_id,
                    WorkspaceInvite.status == "active",
                    WorkspaceInvite.expires_at <= now,
                )
                .with_for_update()
            )
        )
        for invite in expired:
            invite.status = "expired"
        if expired:
            self._session.flush(expired)
        return len(expired)

    def _resolve_invitee(self, email: str, invitee_user_id: UUID | None) -> User | None:
        if invitee_user_id is not None:
            user = self._session.get(User, invitee_user_id)
            if user is None or user.status != "active":
                raise WorkspaceInviteNotFoundError("Invitee user not found")
            if user.email.lower() != email.lower():
                raise WorkspaceInviteNotFoundError("Invitee user not found")
            return user
        return None


def generate_invite_token() -> str:
    return f"ccwi_{token_urlsafe(32)}"


def hash_invite_token(token: str, settings: Settings) -> str:
    material = f"{settings.token_hash_pepper}:workspace_invite:{token}"
    return sha256(material.encode("utf-8")).hexdigest()


def fingerprint_invite_token(token: str) -> str:
    return f"sha256:{sha256(token.encode('utf-8')).hexdigest()[:32]}"
