from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from secrets import token_urlsafe
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.api.schemas.workspaces import (
    WorkspaceCreateRequest,
    WorkspaceInviteAcceptRequest,
    WorkspaceInviteCreateRequest,
    WorkspaceMemberCreateRequest,
    WorkspaceMemberUpdateRequest,
    WorkspaceQuotaUpsertRequest,
    WorkspaceUpdateRequest,
)
from backend.app.audit.service import AuditService
from backend.app.auth.permissions import WorkspaceRole
from backend.app.core.config import Settings, get_settings
from backend.app.db.errors import commit_or_raise_conflict
from backend.app.identity.models import User
from backend.app.workspaces.models import (
    Workspace,
    WorkspaceInvite,
    WorkspaceMember,
    WorkspaceQuota,
    WorkspaceReservation,
)

EXECUTION_SLOT_QUOTA_KEYS = ("active_runs", "docker_runtimes", "self_hosted_jobs")

T = TypeVar("T")


class WorkspaceMemberNotFoundError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class WorkspaceMemberConflictError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class WorkspaceMemberPermissionError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class WorkspaceInviteNotFoundError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class WorkspaceInviteConflictError(Exception):
    def __init__(
        self,
        message: str,
        *,
        workspace_id: UUID | None = None,
        fingerprint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.workspace_id = workspace_id
        self.fingerprint = fingerprint


class WorkspaceInvitePermissionError(Exception):
    def __init__(
        self,
        message: str,
        *,
        workspace_id: UUID | None = None,
        fingerprint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.workspace_id = workspace_id
        self.fingerprint = fingerprint


@dataclass(frozen=True)
class CreatedWorkspaceInvite:
    invite: WorkspaceInvite
    token: str


@dataclass(frozen=True)
class AcceptedWorkspaceInvite:
    invite: WorkspaceInvite
    member: WorkspaceMember


class WorkspaceService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def list_for_user(self, user_id: UUID, page: PageParams) -> tuple[list[Workspace], int]:
        statement = (
            select(Workspace)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user_id, WorkspaceMember.status == "active")
            .order_by(Workspace.created_at.desc())
        )
        return self._page(statement, page)

    def create_for_owner(self, owner_user_id: UUID, data: WorkspaceCreateRequest) -> Workspace:
        workspace = Workspace(
            owner_user_id=owner_user_id,
            name=data.name,
            slug=data.slug,
            settings=data.settings,
        )
        membership = WorkspaceMember(
            workspace=workspace,
            user_id=owner_user_id,
            role=WorkspaceRole.OWNER.value,
        )
        self._session.add_all([workspace, membership])
        commit_or_raise_conflict(self._session, "Workspace slug already exists")
        self._session.refresh(workspace)
        return workspace

    def get_scoped(self, workspace_id: UUID) -> Workspace | None:
        return self._session.get(Workspace, workspace_id)

    def get_owned(self, owner_user_id: UUID, workspace_id: UUID) -> Workspace | None:
        return self._session.scalar(
            select(Workspace).where(
                Workspace.owner_user_id == owner_user_id,
                Workspace.id == workspace_id,
            )
        )

    def update(
        self,
        workspace: Workspace,
        data: WorkspaceUpdateRequest,
        *,
        actor_user_id: UUID | None = None,
    ) -> Workspace:
        old_status = workspace.status
        old_scheduler = _scheduler_settings(workspace.settings)
        updates = data.model_dump(exclude_unset=True)
        for field, value in updates.items():
            setattr(workspace, field, value)
        new_scheduler = _scheduler_settings(workspace.settings)
        if actor_user_id is not None:
            if workspace.status != old_status:
                AuditService(self._session).record_user_action(
                    workspace_id=workspace.id,
                    user_id=actor_user_id,
                    action="workspace.status_updated",
                    target_type="workspace",
                    target_id=workspace.id,
                    metadata={"before": old_status, "after": workspace.status},
                )
            if new_scheduler != old_scheduler:
                AuditService(self._session).record_user_action(
                    workspace_id=workspace.id,
                    user_id=actor_user_id,
                    action="workspace.scheduler_policy_updated",
                    target_type="workspace",
                    target_id=workspace.id,
                    metadata={"before": old_scheduler, "after": new_scheduler},
                )
        self._session.commit()
        self._session.refresh(workspace)
        return workspace

    def list_members(
        self,
        workspace_id: UUID,
        page: PageParams,
    ) -> tuple[list[WorkspaceMember], int]:
        statement = (
            select(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == workspace_id)
            .order_by(WorkspaceMember.created_at.desc())
        )
        return self._page(statement, page)

    def list_invites(
        self,
        workspace_id: UUID,
        page: PageParams,
    ) -> tuple[list[WorkspaceInvite], int]:
        expired_count = self._expire_workspace_invites(workspace_id)
        statement = (
            select(WorkspaceInvite)
            .where(WorkspaceInvite.workspace_id == workspace_id)
            .order_by(WorkspaceInvite.created_at.desc())
        )
        items, total = self._page(statement, page)
        if expired_count:
            self._session.commit()
        return items, total

    def create_invite(
        self,
        workspace_id: UUID,
        data: WorkspaceInviteCreateRequest,
        *,
        actor_user_id: UUID,
        actor_role: str,
    ) -> CreatedWorkspaceInvite:
        self._ensure_actor_can_assign_role(actor_role, data.role)
        self._expire_workspace_invites(workspace_id)
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

        token = self.generate_invite_token()
        invite = WorkspaceInvite(
            workspace_id=workspace_id,
            email=data.email,
            role=data.role,
            token_hash=self.hash_invite_token(token, self._settings),
            fingerprint=self.fingerprint_invite_token(token),
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
                "expires_at": _canonical_datetime(invite.expires_at),
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
            before = _invite_snapshot(invite)
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
                    "after": _invite_snapshot(invite),
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
        token_hash = self.hash_invite_token(data.token, self._settings)
        fingerprint = self.fingerprint_invite_token(data.token)
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
        if _as_utc(invite.expires_at) <= now:
            before = _invite_snapshot(invite)
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
                    "after": _invite_snapshot(invite),
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
            before_member = _member_snapshot(member)
            member.role = invite.role
            member.status = "active"

        before_invite = _invite_snapshot(invite)
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
                "after": _invite_snapshot(invite),
                "member_before": before_member,
                "member_after": _member_snapshot(member),
            },
        )
        commit_or_raise_conflict(self._session, "Workspace member already exists")
        self._session.refresh(invite)
        self._session.refresh(member)
        return AcceptedWorkspaceInvite(invite=invite, member=member)

    def create_member(
        self,
        workspace_id: UUID,
        data: WorkspaceMemberCreateRequest,
        *,
        actor_user_id: UUID,
        actor_role: str,
    ) -> WorkspaceMember:
        self._ensure_actor_can_assign_role(actor_role, data.role)
        user = self._session.get(User, data.user_id)
        if user is None or user.status != "active":
            raise WorkspaceMemberNotFoundError("User not found")

        existing = self._session.scalar(
            select(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == data.user_id,
            )
            .with_for_update()
        )
        if existing is not None and existing.status == "active":
            raise WorkspaceMemberConflictError("Workspace member already exists")

        before: dict[str, object] | None
        if existing is None:
            member = WorkspaceMember(
                workspace_id=workspace_id,
                user_id=data.user_id,
                role=data.role,
                status="active",
            )
            self._session.add(member)
            self._session.flush([member])
            before = None
        else:
            member = existing
            before = _member_snapshot(member)
            member.role = data.role
            member.status = "active"

        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="workspace.member_created",
            target_type="workspace_member",
            target_id=member.id,
            metadata={
                "target_user_id": str(member.user_id),
                "before": before,
                "after": _member_snapshot(member),
            },
        )
        commit_or_raise_conflict(self._session, "Workspace member already exists")
        self._session.refresh(member)
        return member

    def update_member(
        self,
        workspace_id: UUID,
        member_id: UUID,
        data: WorkspaceMemberUpdateRequest,
        *,
        actor_user_id: UUID,
        actor_role: str,
    ) -> WorkspaceMember:
        member = self._get_member_for_update(workspace_id, member_id)
        before = _member_snapshot(member)
        new_role = data.role if data.role is not None else member.role
        new_status = data.status if data.status is not None else member.status
        self._ensure_actor_can_manage_member_role(actor_role, member, new_role)
        self._ensure_owner_can_change(member, new_role=new_role, new_status=new_status)

        member.role = new_role
        member.status = new_status
        after = _member_snapshot(member)
        if after != before:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="workspace.member_updated",
                target_type="workspace_member",
                target_id=member.id,
                metadata={
                    "target_user_id": str(member.user_id),
                    "before": before,
                    "after": after,
                },
            )
        self._session.commit()
        self._session.refresh(member)
        return member

    def disable_member(
        self,
        workspace_id: UUID,
        member_id: UUID,
        *,
        actor_user_id: UUID,
        actor_role: str,
    ) -> WorkspaceMember:
        member = self._get_member_for_update(workspace_id, member_id)
        before = _member_snapshot(member)
        self._ensure_actor_can_manage_member_role(actor_role, member, member.role)
        self._ensure_owner_can_change(
            member,
            new_role=member.role,
            new_status="disabled",
        )

        member.status = "disabled"
        after = _member_snapshot(member)
        if after != before:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="workspace.member_disabled",
                target_type="workspace_member",
                target_id=member.id,
                metadata={
                    "target_user_id": str(member.user_id),
                    "before": before,
                    "after": after,
                },
            )
        self._session.commit()
        self._session.refresh(member)
        return member

    def list_quotas(self, workspace_id: UUID, page: PageParams) -> tuple[list[WorkspaceQuota], int]:
        statement = (
            select(WorkspaceQuota)
            .where(WorkspaceQuota.workspace_id == workspace_id)
            .order_by(WorkspaceQuota.status, WorkspaceQuota.quota_key)
        )
        return self._page(statement, page)

    def upsert_quotas(
        self,
        workspace_id: UUID,
        data: WorkspaceQuotaUpsertRequest,
        *,
        actor_user_id: UUID | None = None,
    ) -> list[WorkspaceQuota]:
        existing = {
            quota.quota_key: quota
            for quota in self._session.scalars(
                select(WorkspaceQuota)
                .where(WorkspaceQuota.workspace_id == workspace_id)
                .with_for_update()
            ).all()
        }
        updated: list[WorkspaceQuota] = []
        audit_items: list[dict[str, object]] = []
        for item in data.quotas:
            quota = existing.get(item.quota_key)
            if quota is None:
                quota = WorkspaceQuota(
                    workspace_id=workspace_id,
                    quota_key=item.quota_key,
                    limit_value=item.limit_value,
                    unit=item.unit,
                )
                self._session.add(quota)
                before: dict[str, object] | None = None
            else:
                before = _quota_snapshot(quota)
                quota.limit_value = item.limit_value
                quota.unit = item.unit
                quota.status = "active"
            updated.append(quota)
            audit_items.append(
                {
                    "quota_key": item.quota_key,
                    "before": before,
                    "after": {
                        "quota_key": item.quota_key,
                        "limit_value": item.limit_value,
                        "unit": item.unit,
                        "status": "active",
                    },
                }
            )
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="workspace.quotas_upserted",
                target_type="workspace",
                target_id=workspace_id,
                metadata={"quotas": audit_items},
            )
        self._session.commit()
        for quota in updated:
            self._session.refresh(quota)
        return sorted(updated, key=lambda quota: quota.quota_key)

    def disable_quota(
        self,
        workspace_id: UUID,
        quota_key: str,
        *,
        actor_user_id: UUID | None = None,
    ) -> WorkspaceQuota | None:
        quota = self._session.scalar(
            select(WorkspaceQuota)
            .where(
                WorkspaceQuota.workspace_id == workspace_id,
                WorkspaceQuota.quota_key == quota_key,
            )
            .with_for_update()
        )
        if quota is None:
            return None
        before = _quota_snapshot(quota)
        quota.status = "disabled"
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="workspace.quota_disabled",
                target_type="workspace_quota",
                target_id=quota.id,
                metadata={
                    "quota_key": quota.quota_key,
                    "before": before,
                    "after": _quota_snapshot(quota),
                    "over_reserved": quota.reserved_value > quota.limit_value,
                },
            )
        self._session.commit()
        self._session.refresh(quota)
        return quota

    def execution_slot_summary(self, workspace_id: UUID) -> dict[str, object]:
        quotas = list(
            self._session.scalars(
                select(WorkspaceQuota)
                .where(
                    WorkspaceQuota.workspace_id == workspace_id,
                    WorkspaceQuota.quota_key.in_(EXECUTION_SLOT_QUOTA_KEYS),
                )
                .order_by(WorkspaceQuota.quota_key.asc())
            )
        )
        reservations = list(
            self._session.scalars(
                select(WorkspaceReservation)
                .where(
                    WorkspaceReservation.workspace_id == workspace_id,
                    WorkspaceReservation.status == "active",
                )
                .order_by(WorkspaceReservation.created_at.asc())
            )
        )
        active_reservation_usage: Counter[str] = Counter()
        over_reserved_quota_keys: list[str] = []
        for quota in quotas:
            if quota.reserved_value > quota.limit_value:
                over_reserved_quota_keys.append(quota.quota_key)
        active_reservation_count = 0
        for reservation in reservations:
            active_reservation_count += 1
            for quota_key, amount in _reservation_usage(reservation).items():
                if quota_key in EXECUTION_SLOT_QUOTA_KEYS:
                    active_reservation_usage[quota_key] += amount
        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "quotas": quotas,
            "active_reservations": reservations,
            "active_reservation_count": active_reservation_count,
            "reservation_usage": dict(sorted(active_reservation_usage.items())),
            "over_reserved_quota_keys": sorted(over_reserved_quota_keys),
        }

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)

    def _get_member_for_update(self, workspace_id: UUID, member_id: UUID) -> WorkspaceMember:
        member = self._session.scalar(
            select(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.id == member_id,
            )
            .with_for_update()
        )
        if member is None:
            raise WorkspaceMemberNotFoundError("Workspace member not found")
        return member

    def _ensure_owner_can_change(
        self,
        member: WorkspaceMember,
        *,
        new_role: str,
        new_status: str,
    ) -> None:
        remains_active_owner = (
            new_role == WorkspaceRole.OWNER.value and new_status == "active"
        )
        if (
            member.role != WorkspaceRole.OWNER.value
            or member.status != "active"
            or remains_active_owner
        ):
            return

        active_owner_count = self._session.scalar(
            select(func.count())
            .select_from(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == member.workspace_id,
                WorkspaceMember.role == WorkspaceRole.OWNER.value,
                WorkspaceMember.status == "active",
            )
        )
        if int(active_owner_count or 0) <= 1:
            raise WorkspaceMemberConflictError(
                "Cannot remove or downgrade the last workspace owner"
            )

    @staticmethod
    def _ensure_actor_can_assign_role(actor_role: str, target_role: str) -> None:
        if (
            target_role == WorkspaceRole.OWNER.value
            and actor_role != WorkspaceRole.OWNER.value
        ):
            raise WorkspaceMemberPermissionError("Only workspace owners can grant owner role")

    @classmethod
    def _ensure_actor_can_manage_member_role(
        cls,
        actor_role: str,
        member: WorkspaceMember,
        new_role: str,
    ) -> None:
        if actor_role == WorkspaceRole.OWNER.value:
            return
        if member.role == WorkspaceRole.OWNER.value:
            raise WorkspaceMemberPermissionError("Only workspace owners can manage owners")
        cls._ensure_actor_can_assign_role(actor_role, new_role)

    def _resolve_invitee(self, email: str, invitee_user_id: UUID | None) -> User | None:
        if invitee_user_id is not None:
            user = self._session.get(User, invitee_user_id)
            if user is None or user.status != "active":
                raise WorkspaceInviteNotFoundError("Invitee user not found")
            if user.email.lower() != email.lower():
                raise WorkspaceInviteNotFoundError("Invitee user not found")
            return user
        return None

    def _expire_workspace_invites(self, workspace_id: UUID) -> int:
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

    @staticmethod
    def generate_invite_token() -> str:
        return f"ccwi_{token_urlsafe(32)}"

    @staticmethod
    def hash_invite_token(token: str, settings: Settings) -> str:
        material = f"{settings.token_hash_pepper}:workspace_invite:{token}"
        return sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def fingerprint_invite_token(token: str) -> str:
        return f"sha256:{sha256(token.encode('utf-8')).hexdigest()[:32]}"


def _scheduler_settings(settings: dict[str, object]) -> dict[str, object]:
    raw_scheduler = settings.get("scheduler") if isinstance(settings, dict) else None
    if not isinstance(raw_scheduler, dict):
        return {}
    return dict(raw_scheduler)


def _quota_snapshot(quota: WorkspaceQuota) -> dict[str, object]:
    return {
        "quota_key": quota.quota_key,
        "limit_value": quota.limit_value,
        "reserved_value": quota.reserved_value,
        "unit": quota.unit,
        "status": quota.status,
    }


def _member_snapshot(member: WorkspaceMember) -> dict[str, object]:
    return {
        "id": str(member.id),
        "workspace_id": str(member.workspace_id),
        "user_id": str(member.user_id),
        "role": member.role,
        "status": member.status,
    }


def _invite_snapshot(invite: WorkspaceInvite) -> dict[str, object]:
    return {
        "id": str(invite.id),
        "workspace_id": str(invite.workspace_id),
        "email": invite.email,
        "role": invite.role,
        "status": invite.status,
        "fingerprint": invite.fingerprint,
        "invitee_user_id": str(invite.invitee_user_id) if invite.invitee_user_id else None,
        "accepted_by_user_id": (
            str(invite.accepted_by_user_id) if invite.accepted_by_user_id else None
        ),
        "revoked_by_user_id": (
            str(invite.revoked_by_user_id) if invite.revoked_by_user_id else None
        ),
        "expires_at": _canonical_datetime(invite.expires_at),
        "accepted_at": _canonical_datetime(invite.accepted_at),
        "revoked_at": _canonical_datetime(invite.revoked_at),
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _canonical_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).replace(tzinfo=None).isoformat()


def _reservation_usage(reservation: WorkspaceReservation) -> dict[str, int]:
    usage: dict[str, int] = {}
    for quota_key, value in reservation.resource_usage.items():
        if isinstance(quota_key, str) and isinstance(value, int) and value > 0:
            usage[quota_key] = value
    return usage
