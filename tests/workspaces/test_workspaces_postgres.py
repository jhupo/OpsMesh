"""S6 workspace tests against real PostgreSQL.

These tests require OPSMESH_TEST_DATABASE_URL; they are skipped otherwise and
must run against a real PostgreSQL instance (never SQLite). They cover the
acceptance surface of V4 section 16.2 for workspaces: space and project
roles, invitation and archive state isolation, the credential boundary,
the workspace capacity ledger as an account separate from the user quota,
the model provider connection lifecycle, the notification inbox, and the
real member checks the resource and audit scopes resolve through.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy.ext.asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.bootstrap.routers import (
    WorkspaceAuditScopeAdmission,
    WorkspaceScopeAdmission,
)
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.governance.audit.service import AuditActor, AuditReader, authorize_audit_read
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.quotas import service as quotas
from opsmesh.identity.users import service as users
from opsmesh.identity.users.models import UserProfile
from opsmesh.resources.files import service as file_service
from opsmesh.resources.files.access import ResourceReader, ScopeRef
from opsmesh.shared.errors import (
    ConflictError,
    ForbiddenError,
    InvalidArgumentError,
    NotFoundError,
)
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.security.secrets import SecretCipher
from opsmesh.workspaces import service as workspace_service
from opsmesh.workspaces.capacity import service as capacity_service
from opsmesh.workspaces.capacity.models import (
    CAPACITY_CONCURRENCY,
    CAPACITY_ENVIRONMENT,
)
from opsmesh.workspaces.capacity.service import CapacityExceededError
from opsmesh.workspaces.credentials import service as credential_service
from opsmesh.workspaces.credentials.models import WorkspaceCredential
from opsmesh.workspaces.members import service as member_service
from opsmesh.workspaces.members.models import WorkspaceInvitation, WorkspaceMember
from opsmesh.workspaces.model_providers import service as provider_service
from opsmesh.workspaces.model_providers.models import (
    PROBE_OK,
    PROBE_RUNNING,
    ModelProviderConnection,
)
from opsmesh.workspaces.models import Workspace
from opsmesh.workspaces.notifications import service as notification_service
from opsmesh.workspaces.notifications.models import WorkspaceNotification
from opsmesh.workspaces.projects import service as project_service
from opsmesh.workspaces.projects.members import service as project_member_service

TEST_KEY = "JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0="


def cipher() -> SecretCipher:
    return SecretCipher(key=TEST_KEY, key_id="test")


def open_account(sessions, *, email: str, display_name: str, admin: bool = False) -> UserProfile:
    roles = frozenset({"admin", "member"}) if admin else frozenset({"member"})
    with sessions() as session:
        profile = users.create_user(
            session,
            email=email,
            display_name=display_name,
            platform_roles=roles,
            actor=AuditActor(actor_type="platform"),
        )
        quotas.ensure_ledger(session, user_id=profile.id)
        session.commit()
    return profile


def as_user(profile: UserProfile) -> CurrentUser:
    return CurrentUser(
        user_id=profile.id,
        email=profile.email,
        display_name=profile.display_name,
        status="active",
        platform_roles=frozenset({"member"}),
    )


def as_admin(profile: UserProfile) -> CurrentUser:
    return CurrentUser(
        user_id=profile.id,
        email=profile.email,
        display_name=profile.display_name,
        status="active",
        platform_roles=frozenset({"admin", "member"}),
    )


def make_workspace(sessions, owner: UserProfile, *, name: str = "Acme") -> Workspace:
    with sessions() as session:
        workspace = workspace_service.create_workspace(
            session,
            actor=as_user(owner),
            name=name,
            description="",
            capacity_limits={"concurrency": None, "environment": None},
        )
        session.commit()
    return workspace


def invite(
    sessions, *, workspace_id, inviter: UserProfile, invitee: UserProfile, role: str = "member"
) -> WorkspaceInvitation:
    with sessions() as session:
        invitation = member_service.create_invitation(
            session,
            actor=as_user(inviter),
            workspace_id=workspace_id,
            invitee_user_id=invitee.id,
            role=role,
            ttl_seconds=7 * 24 * 60 * 60,
        )
        session.commit()
    return invitation


def latest_invitation(session: Session, workspace_id) -> object:
    invitation = session.scalar(
        select(WorkspaceInvitation)
        .where(WorkspaceInvitation.workspace_id == workspace_id)
        .order_by(WorkspaceInvitation.created_at.desc())
    )
    assert invitation is not None
    return invitation.id


def accept_latest_invitation(sessions, *, workspace_id, invitee: UserProfile) -> None:
    with sessions() as session:
        member_service.accept_invitation(
            session, actor=as_user(invitee), invitation_id=latest_invitation(session, workspace_id)
        )
        session.commit()


# ---------------------------------------------------------------------------
# Workspace lifecycle and membership
# ---------------------------------------------------------------------------


def test_create_workspace_seats_owner_and_capacity_accounts(database_sessions) -> None:
    owner = open_account(database_sessions, email="owner@example.com", display_name="Owner")
    workspace = make_workspace(database_sessions, owner)
    with database_sessions() as session:
        member = session.get(WorkspaceMember, (workspace.id, owner.id))
        assert member is not None and member.role == "owner"
        kinds = capacity_service.capacity_snapshot(session, workspace_id=workspace.id)
    assert [item.kind for item in kinds] == [CAPACITY_CONCURRENCY, CAPACITY_ENVIRONMENT]
    assert all(item.limit_value is None for item in kinds)


def test_platform_administrator_gets_no_tenant_permission(database_sessions) -> None:
    owner = open_account(database_sessions, email="tenant-owner@example.com", display_name="T")
    admin = open_account(
        database_sessions, email="root@example.com", display_name="Root", admin=True
    )
    workspace = make_workspace(database_sessions, owner)
    with database_sessions() as session:
        with pytest.raises(NotFoundError):
            workspace_service.get_workspace(
                session, actor=as_admin(admin), workspace_id=workspace.id
            )
        with pytest.raises(NotFoundError):
            workspace_service.update_workspace(
                session,
                actor=as_admin(admin),
                workspace_id=workspace.id,
                name="hijack",
                description=None,
            )
        with pytest.raises(NotFoundError):
            member_service.list_members(
                session,
                actor=as_admin(admin),
                workspace_id=workspace.id,
                page=PageParams(),
            )


def test_space_role_matrix_isolates_read_write_and_manage(database_sessions) -> None:
    owner = open_account(database_sessions, email="matrix-owner@example.com", display_name="O")
    viewer = open_account(database_sessions, email="viewer@example.com", display_name="V")
    workspace = make_workspace(database_sessions, owner)
    invite(
        sessions=database_sessions, workspace_id=workspace.id, inviter=owner, invitee=viewer,
        role="viewer",
    )
    accept_latest_invitation(
        database_sessions, workspace_id=workspace.id, invitee=viewer
    )
    with database_sessions() as session:
        assert workspace_service.get_workspace(
            session, actor=as_user(viewer), workspace_id=workspace.id
        ).id == workspace.id
        with pytest.raises(ForbiddenError):
            workspace_service.update_workspace(
                session,
                actor=as_user(viewer),
                workspace_id=workspace.id,
                name="x",
                description=None,
            )
        with pytest.raises(ForbiddenError):
            capacity_service.set_limit(
                session,
                actor=as_user(viewer),
                workspace_id=workspace.id,
                kind=CAPACITY_CONCURRENCY,
                limit_value=1,
            )
        with pytest.raises(ForbiddenError):
            workspace_service.archive_workspace(
                session, actor=as_user(viewer), workspace_id=workspace.id
            )


def test_invitation_acceptance_joins_and_replays(database_sessions) -> None:
    owner = open_account(database_sessions, email="inv-owner@example.com", display_name="O")
    invitee = open_account(database_sessions, email="invitee@example.com", display_name="I")
    workspace = make_workspace(database_sessions, owner)
    invitation = invite(
        sessions=database_sessions, workspace_id=workspace.id, inviter=owner, invitee=invitee
    )
    with database_sessions() as session:
        member = member_service.accept_invitation(
            session, actor=as_user(invitee), invitation_id=invitation.id
        )
        session.commit()
        assert member.role == "member"
        with pytest.raises(ConflictError):
            member_service.accept_invitation(
                session, actor=as_user(invitee), invitation_id=invitation.id
            )


def test_acceptance_refuses_an_inviter_who_lost_the_permission(database_sessions) -> None:
    owner = open_account(database_sessions, email="demote-owner@example.com", display_name="O")
    admin = open_account(database_sessions, email="demote-admin@example.com", display_name="A")
    invitee = open_account(database_sessions, email="demote-invitee@example.com", display_name="I")
    workspace = make_workspace(database_sessions, owner)
    # Seat the inviter as admin, let them invite, then demote the inviter.
    invite(
        sessions=database_sessions, workspace_id=workspace.id, inviter=owner, invitee=admin,
        role="admin",
    )
    accept_latest_invitation(database_sessions, workspace_id=workspace.id, invitee=admin)
    pending = invite(
        sessions=database_sessions, workspace_id=workspace.id, inviter=admin, invitee=invitee,
        role="member",
    )
    with database_sessions() as session:
        member_service.update_member_role(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            user_id=admin.id,
            role="member",
        )
        session.commit()
    with database_sessions() as session, pytest.raises(ForbiddenError):
        member_service.accept_invitation(
            session, actor=as_user(invitee), invitation_id=pending.id
        )


def test_an_aged_invitation_closes_for_reissue(database_sessions) -> None:
    owner = open_account(database_sessions, email="aged-owner@example.com", display_name="O")
    invitee = open_account(database_sessions, email="aged-invitee@example.com", display_name="I")
    workspace = make_workspace(database_sessions, owner)
    with database_sessions() as session:
        aged = WorkspaceInvitation(
            workspace_id=workspace.id,
            invitee_id=invitee.id,
            invited_by_user_id=owner.id,
            role="member",
            state="pending",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        session.add(aged)
        session.commit()
        aged_id = aged.id
    fresh = invite(
        sessions=database_sessions, workspace_id=workspace.id, inviter=owner, invitee=invitee
    )
    with database_sessions() as session:
        stored = session.get(WorkspaceInvitation, aged_id)
        assert stored is not None and stored.state == "expired"
        with pytest.raises(ConflictError):
            member_service.accept_invitation(
                session, actor=as_user(invitee), invitation_id=aged_id
            )
        member_service.accept_invitation(
            session, actor=as_user(invitee), invitation_id=fresh.id
        )
        session.commit()


def test_revoked_invitation_cannot_be_accepted(database_sessions) -> None:
    owner = open_account(database_sessions, email="rev-owner@example.com", display_name="O")
    invitee = open_account(database_sessions, email="rev-invitee@example.com", display_name="I")
    workspace = make_workspace(database_sessions, owner)
    invitation = invite(
        sessions=database_sessions, workspace_id=workspace.id, inviter=owner, invitee=invitee
    )
    with database_sessions() as session:
        member_service.revoke_invitation(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            invitation_id=invitation.id,
        )
        session.commit()
    with database_sessions() as session, pytest.raises(ConflictError):
        member_service.accept_invitation(
            session, actor=as_user(invitee), invitation_id=invitation.id
        )


def test_archived_workspace_refuses_writes_but_keeps_reads(database_sessions) -> None:
    owner = open_account(database_sessions, email="arch-owner@example.com", display_name="O")
    workspace = make_workspace(database_sessions, owner)
    with database_sessions() as session:
        workspace_service.archive_workspace(
            session, actor=as_user(owner), workspace_id=workspace.id
        )
        session.commit()
    with database_sessions() as session:
        assert workspace_service.get_workspace(
            session, actor=as_user(owner), workspace_id=workspace.id
        ).status == "archived"
        with pytest.raises(ConflictError):
            workspace_service.update_workspace(
                session,
                actor=as_user(owner),
                workspace_id=workspace.id,
                name="renamed",
                description=None,
            )
        with pytest.raises(ConflictError):
            member_service.create_invitation(
                session,
                actor=as_user(owner),
                workspace_id=workspace.id,
                invitee_user_id=uuid4(),
                role="member",
                ttl_seconds=3600,
            )
        # The owner-only lifecycle actions still work on an archived workspace.
        workspace_service.archive_workspace(
            session, actor=as_user(owner), workspace_id=workspace.id
        )
        session.rollback()


def test_transfer_ownership_moves_the_single_owner(database_sessions) -> None:
    owner = open_account(database_sessions, email="xfer-owner@example.com", display_name="O")
    successor = open_account(database_sessions, email="xfer-next@example.com", display_name="N")
    workspace = make_workspace(database_sessions, owner)
    invite(
        sessions=database_sessions, workspace_id=workspace.id, inviter=owner, invitee=successor,
        role="admin",
    )
    accept_latest_invitation(database_sessions, workspace_id=workspace.id, invitee=successor)
    with database_sessions() as session:
        workspace_service.transfer_ownership(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            new_owner_user_id=successor.id,
        )
        session.commit()
    with database_sessions() as session:
        assert session.get(WorkspaceMember, (workspace.id, successor.id)).role == "owner"
        assert session.get(WorkspaceMember, (workspace.id, owner.id)).role == "admin"
        with pytest.raises(ForbiddenError):
            workspace_service.transfer_ownership(
                session,
                actor=as_user(owner),
                workspace_id=workspace.id,
                new_owner_user_id=successor.id,
            )


def test_owner_role_cannot_be_rewritten_through_membership(database_sessions) -> None:
    owner = open_account(database_sessions, email="own-role@example.com", display_name="O")
    other = open_account(database_sessions, email="own-other@example.com", display_name="X")
    workspace = make_workspace(database_sessions, owner)
    invite(
        sessions=database_sessions, workspace_id=workspace.id, inviter=owner, invitee=other
    )
    accept_latest_invitation(database_sessions, workspace_id=workspace.id, invitee=other)
    with database_sessions() as session:
        with pytest.raises(ConflictError):
            member_service.update_member_role(
                session,
                actor=as_user(owner),
                workspace_id=workspace.id,
                user_id=owner.id,
                role="member",
            )
        with pytest.raises(ConflictError):
            member_service.remove_member(
                session, actor=as_user(owner), workspace_id=workspace.id, user_id=owner.id
            )


def test_removed_member_loses_access_and_may_leave(database_sessions) -> None:
    owner = open_account(database_sessions, email="rm-owner@example.com", display_name="O")
    member = open_account(database_sessions, email="rm-member@example.com", display_name="M")
    workspace = make_workspace(database_sessions, owner)
    invite(
        sessions=database_sessions, workspace_id=workspace.id, inviter=owner, invitee=member
    )
    accept_latest_invitation(database_sessions, workspace_id=workspace.id, invitee=member)
    with database_sessions() as session:
        member_service.remove_member(
            session, actor=as_user(member), workspace_id=workspace.id, user_id=member.id
        )
        session.commit()
        with pytest.raises(NotFoundError):
            workspace_service.get_workspace(
                session, actor=as_user(member), workspace_id=workspace.id
            )


def test_deleted_workspace_refuses_every_access(database_sessions) -> None:
    owner = open_account(database_sessions, email="del-owner@example.com", display_name="O")
    workspace = make_workspace(database_sessions, owner)
    with database_sessions() as session:
        workspace_service.delete_workspace(
            session, actor=as_user(owner), workspace_id=workspace.id
        )
        session.commit()
    with database_sessions() as session, pytest.raises(NotFoundError):
        workspace_service.get_workspace(
            session, actor=as_user(owner), workspace_id=workspace.id
        )


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


def test_project_create_seats_its_creator_as_administrator(database_sessions) -> None:
    owner = open_account(database_sessions, email="prj-owner@example.com", display_name="O")
    workspace = make_workspace(database_sessions, owner)
    with database_sessions() as session:
        project = project_service.create_project(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            name="Launch",
            description="",
        )
        session.commit()
        project_id = project.id
    with database_sessions() as session:
        members, total = project_member_service.list_members(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            project_id=project_id,
            page=PageParams(),
        )
        assert total == 1 and members[0].role == "admin"


def test_project_visibility_hides_projects_without_a_seat(database_sessions) -> None:
    owner = open_account(database_sessions, email="vis-owner@example.com", display_name="O")
    member = open_account(database_sessions, email="vis-member@example.com", display_name="M")
    workspace = make_workspace(database_sessions, owner)
    invite(
        sessions=database_sessions, workspace_id=workspace.id, inviter=owner, invitee=member
    )
    accept_latest_invitation(database_sessions, workspace_id=workspace.id, invitee=member)
    with database_sessions() as session:
        project = project_service.create_project(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            name="Hidden",
            description="",
        )
        session.commit()
        project_id = project.id
        projects, total = project_service.list_projects(
            session, actor=as_user(member), workspace_id=workspace.id, page=PageParams()
        )
        assert total == 0 and projects == []
        with pytest.raises(NotFoundError):
            project_service.get_project(
                session, actor=as_user(member), workspace_id=workspace.id, project_id=project_id
            )
        assert project_service.get_project(
            session, actor=as_user(owner), workspace_id=workspace.id, project_id=project_id
        ).id == project_id


def test_project_invitation_acceptance_and_archive(database_sessions) -> None:
    owner = open_account(database_sessions, email="pinv-owner@example.com", display_name="O")
    member = open_account(database_sessions, email="pinv-member@example.com", display_name="M")
    workspace = make_workspace(database_sessions, owner)
    invite(
        sessions=database_sessions, workspace_id=workspace.id, inviter=owner, invitee=member
    )
    accept_latest_invitation(database_sessions, workspace_id=workspace.id, invitee=member)
    with database_sessions() as session:
        project = project_service.create_project(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            name="Team",
            description="",
        )
        session.commit()
        project_id = project.id
    with database_sessions() as session:
        project_invitation = project_member_service.create_invitation(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            project_id=project_id,
            invitee_user_id=member.id,
            role="viewer",
            ttl_seconds=7 * 24 * 60 * 60,
        )
        session.commit()
        invitation_id = project_invitation.id
    with database_sessions() as session:
        seat = project_member_service.accept_invitation(
            session, actor=as_user(member), invitation_id=invitation_id
        )
        session.commit()
        assert seat.role == "viewer"
    with database_sessions() as session:
        project_service.archive_project(
            session, actor=as_user(owner), workspace_id=workspace.id, project_id=project_id
        )
        session.commit()
    with database_sessions() as session:
        with pytest.raises(ConflictError):
            project_service.update_project(
                session,
                actor=as_user(owner),
                workspace_id=workspace.id,
                project_id=project_id,
                name="renamed",
                description=None,
            )
        assert project_service.get_project(
            session, actor=as_user(owner), workspace_id=workspace.id, project_id=project_id
        ).status == "archived"


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def make_credential(
    sessions, *, workspace_id, actor: UserProfile, name: str = "openai-key", secret: str = "sk-test"
) -> WorkspaceCredential:
    with sessions() as session:
        credential = credential_service.create_credential(
            session,
            actor=as_user(actor),
            workspace_id=workspace_id,
            name=name,
            kind="model_provider",
            secret={"api_key": secret},
            cipher=cipher(),
        )
        session.commit()
    return credential


def test_credential_rotation_advances_the_revision(database_sessions) -> None:
    owner = open_account(database_sessions, email="cred-owner@example.com", display_name="O")
    workspace = make_workspace(database_sessions, owner)
    credential = make_credential(
        sessions=database_sessions, workspace_id=workspace.id, actor=owner
    )
    with database_sessions() as session:
        rotated = credential_service.rotate_credential(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            credential_id=credential.id,
            secret={"api_key": "sk-rotated"},
            cipher=cipher(),
        )
        session.commit()
    assert rotated.revision == 2
    assert rotated.fingerprint != credential.fingerprint
    with database_sessions() as session:
        secret = credential_service.resolve_credential_secret(
            session, workspace_id=workspace.id, credential_id=credential.id, cipher=cipher()
        )
        assert secret == {"api_key": "sk-rotated"}


def test_revoked_credential_refuses_resolution(database_sessions) -> None:
    owner = open_account(database_sessions, email="rev-cred@example.com", display_name="O")
    workspace = make_workspace(database_sessions, owner)
    credential = make_credential(
        sessions=database_sessions, workspace_id=workspace.id, actor=owner
    )
    with database_sessions() as session:
        credential_service.revoke_credential(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            credential_id=credential.id,
        )
        session.commit()
    with database_sessions() as session, pytest.raises(ForbiddenError):
        credential_service.resolve_credential_secret(
            session, workspace_id=workspace.id, credential_id=credential.id, cipher=cipher()
        )


def test_credential_resolution_refuses_a_foreign_workspace(database_sessions) -> None:
    owner = open_account(database_sessions, email="foreign-cred@example.com", display_name="O")
    stranger = open_account(database_sessions, email="stranger@example.com", display_name="S")
    workspace = make_workspace(database_sessions, owner)
    credential = make_credential(
        sessions=database_sessions, workspace_id=workspace.id, actor=owner
    )
    with database_sessions() as session:
        other = workspace_service.create_workspace(
            session,
            actor=as_user(stranger),
            name="Other",
            description="",
            capacity_limits={"concurrency": None, "environment": None},
        )
        session.commit()
        other_id = other.id
    with database_sessions() as session, pytest.raises(NotFoundError):
        credential_service.resolve_credential_secret(
            session, workspace_id=other_id, credential_id=credential.id, cipher=cipher()
        )


def test_credential_views_and_audit_never_carry_the_secret(database_sessions) -> None:
    owner = open_account(database_sessions, email="audit-cred@example.com", display_name="O")
    workspace = make_workspace(database_sessions, owner)
    credential = make_credential(
        sessions=database_sessions,
        workspace_id=workspace.id,
        actor=owner,
        secret="sk-super-secret-value",
    )
    with database_sessions() as session:
        stored = session.get(WorkspaceCredential, credential.id)
        assert stored is not None
        assert "sk-super-secret-value" not in stored.ciphertext
        recorded = list(session.scalars(select(AuditEvent)))
        assert recorded
        for event in recorded:
            assert "sk-super-secret-value" not in str(event.details)


# ---------------------------------------------------------------------------
# Capacity
# ---------------------------------------------------------------------------


def test_capacity_reserve_and_release_are_idempotent(database_sessions) -> None:
    owner = open_account(database_sessions, email="cap-owner@example.com", display_name="O")
    workspace = make_workspace(database_sessions, owner)
    with database_sessions() as session:
        capacity_service.set_limit(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            kind=CAPACITY_CONCURRENCY,
            limit_value=1,
        )
        session.commit()
    with database_sessions() as session:
        first = capacity_service.reserve(
            session,
            workspace_id=workspace.id,
            kind=CAPACITY_CONCURRENCY,
            amount=1,
            idempotency_scope="run",
            idempotency_key="claim-1",
        )
        session.commit()
        reservation_id = first.id
    with database_sessions() as session:
        replayed = capacity_service.reserve(
            session,
            workspace_id=workspace.id,
            kind=CAPACITY_CONCURRENCY,
            amount=1,
            idempotency_scope="run",
            idempotency_key="claim-1",
        )
        assert replayed.id == reservation_id
        with pytest.raises(CapacityExceededError):
            capacity_service.reserve(
                session,
                workspace_id=workspace.id,
                kind=CAPACITY_CONCURRENCY,
                amount=1,
                idempotency_scope="run",
                idempotency_key="claim-2",
            )
        with pytest.raises(ConflictError):
            capacity_service.reserve(
                session,
                workspace_id=workspace.id,
                kind=CAPACITY_CONCURRENCY,
                amount=2,
                idempotency_scope="run",
                idempotency_key="claim-1",
            )
        session.rollback()
    with database_sessions() as session:
        released = capacity_service.release(
            session, workspace_id=workspace.id, reservation_id=reservation_id
        )
        assert released.status == "released"
        again = capacity_service.release(
            session, workspace_id=workspace.id, reservation_id=reservation_id
        )
        assert again.status == "released"
        snapshot = capacity_service.capacity_snapshot(session, workspace_id=workspace.id)
        session.commit()
    concurrency = next(item for item in snapshot if item.kind == CAPACITY_CONCURRENCY)
    assert concurrency.reserved_value == 0


def test_workspace_capacity_is_accounted_separately_from_the_user_quota(
    database_sessions,
) -> None:
    owner = open_account(database_sessions, email="sep-owner@example.com", display_name="O")
    workspace = make_workspace(database_sessions, owner)
    with database_sessions() as session:
        before = quotas.get_quota(session, owner.id)
    with database_sessions() as session:
        capacity_service.reserve(
            session,
            workspace_id=workspace.id,
            kind=CAPACITY_ENVIRONMENT,
            amount=2,
            idempotency_scope="environment",
            idempotency_key="env-1",
        )
        session.commit()
    with database_sessions() as session:
        after = quotas.get_quota(session, owner.id)
    assert (before.used_tokens, before.used_bytes) == (after.used_tokens, after.used_bytes)
    assert (after.reserved_tokens, after.reserved_bytes) == (0, 0)


def test_a_capacity_limit_below_the_held_amount_is_refused(database_sessions) -> None:
    owner = open_account(database_sessions, email="low-owner@example.com", display_name="O")
    workspace = make_workspace(database_sessions, owner)
    with database_sessions() as session:
        capacity_service.reserve(
            session,
            workspace_id=workspace.id,
            kind=CAPACITY_CONCURRENCY,
            amount=2,
            idempotency_scope="run",
            idempotency_key="hold-1",
        )
        session.commit()
    with database_sessions() as session, pytest.raises(ConflictError):
        capacity_service.set_limit(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            kind=CAPACITY_CONCURRENCY,
            limit_value=1,
        )


# ---------------------------------------------------------------------------
# Model providers
# ---------------------------------------------------------------------------


def make_connection(
    sessions,
    *,
    workspace_id,
    actor: UserProfile,
    credential_id,
    name: str = "openai",
    default_model: str = "gpt-5",
    models: list[str] | None = None,
) -> ModelProviderConnection:
    with sessions() as session:
        connection = provider_service.create_connection(
            session,
            actor=as_user(actor),
            workspace_id=workspace_id,
            name=name,
            credential_id=credential_id,
            base_url=None,
            default_model=default_model,
            models=models if models is not None else ["gpt-5", "gpt-4"],
            is_default=True,
        )
        session.commit()
    return connection


def test_connection_requires_an_active_provider_credential(database_sessions) -> None:
    owner = open_account(database_sessions, email="mp-owner@example.com", display_name="O")
    workspace = make_workspace(database_sessions, owner)
    credential = make_credential(
        sessions=database_sessions, workspace_id=workspace.id, actor=owner, name="capability-slot"
    )
    with database_sessions() as session:
        stored = session.get(WorkspaceCredential, credential.id)
        assert stored is not None
        stored.kind = "capability"
        session.commit()
    with database_sessions() as session, pytest.raises(InvalidArgumentError):
        provider_service.create_connection(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            name="bad",
            credential_id=credential.id,
            base_url=None,
            default_model="gpt-5",
            models=["gpt-5"],
            is_default=False,
        )


def test_default_connection_is_unique_and_steppable(database_sessions) -> None:
    owner = open_account(database_sessions, email="def-owner@example.com", display_name="O")
    workspace = make_workspace(database_sessions, owner)
    credential = make_credential(
        sessions=database_sessions, workspace_id=workspace.id, actor=owner
    )
    first = make_connection(
        sessions=database_sessions,
        workspace_id=workspace.id,
        actor=owner,
        credential_id=credential.id,
        name="primary",
    )
    with database_sessions() as session:
        second = provider_service.create_connection(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            name="secondary",
            credential_id=credential.id,
            base_url=None,
            default_model="gpt-4",
            models=["gpt-4"],
            is_default=True,
        )
        session.commit()
        second_id = second.id
    with database_sessions() as session:
        # Re-read both rows: the objects returned above stay detached and
        # would still carry the pre-demotion flag.
        stored_first = session.get(ModelProviderConnection, first.id)
        stored_second = session.get(ModelProviderConnection, second_id)
        assert stored_first is not None and stored_first.is_default is False
        assert stored_second is not None and stored_second.is_default is True
    with database_sessions() as session:
        resolved = provider_service.resolve_default_model(
            session, actor=as_user(owner), workspace_id=workspace.id
        )
        assert resolved is not None and resolved.id == second_id
        provider_service.set_default_connection(
            session, actor=as_user(owner), workspace_id=workspace.id, connection_id=first.id
        )
        session.commit()
    with database_sessions() as session:
        resolved = provider_service.resolve_default_model(
            session, actor=as_user(owner), workspace_id=workspace.id
        )
        assert resolved is not None and resolved.id == first.id


def test_starting_a_probe_stages_one_durable_execution(database_sessions) -> None:
    owner = open_account(database_sessions, email="probe-owner@example.com", display_name="O")
    workspace = make_workspace(database_sessions, owner)
    credential = make_credential(
        sessions=database_sessions, workspace_id=workspace.id, actor=owner
    )
    connection = make_connection(
        sessions=database_sessions,
        workspace_id=workspace.id,
        actor=owner,
        credential_id=credential.id,
    )
    with database_sessions() as session:
        provider_service.start_probe(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            connection_id=connection.id,
        )
        session.commit()
    with database_sessions() as session:
        stored = session.get(ModelProviderConnection, connection.id)
        assert stored is not None and stored.last_probe_status == PROBE_RUNNING
        staged = {
            row[0]
            for row in session.execute(text("SELECT workflow_id FROM temporal_dispatches")).all()
        }
    assert f"model-provider-probe:{connection.id}" in staged
    assert len(staged) == 1


def run_probe(engine: Engine, connection_id) -> None:
    """Run one probe activity directly against the test database."""
    from opsmesh.workspaces.model_providers.activities import ModelProviderActivities

    async_engine = sqlalchemy.ext.asyncio.create_async_engine(engine.url)
    async_sessions = sqlalchemy.ext.asyncio.async_sessionmaker(
        bind=async_engine, autoflush=False, expire_on_commit=False
    )
    probe = ModelProviderActivities(cipher(), async_sessions)

    async def run() -> None:
        try:
            await probe.probe_connection(connection_id)
        finally:
            await async_engine.dispose()

    asyncio.run(run())


def test_a_probe_records_its_outcome(database_sessions, database_engine, monkeypatch) -> None:
    owner = open_account(database_sessions, email="probe2-owner@example.com", display_name="O")
    workspace = make_workspace(database_sessions, owner)
    credential = make_credential(
        sessions=database_sessions,
        workspace_id=workspace.id,
        actor=owner,
        secret="sk-never-returned",
    )
    connection = make_connection(
        sessions=database_sessions,
        workspace_id=workspace.id,
        actor=owner,
        credential_id=credential.id,
    )
    with database_sessions() as session:
        provider_service.start_probe(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            connection_id=connection.id,
        )
        session.commit()

    seen: dict[str, str] = {}
    monkeypatch.setattr(
        "opsmesh.workspaces.model_providers.activities._list_models",
        lambda url, api_key: seen.update(url=url, api_key=api_key),
    )
    run_probe(database_engine, connection.id)
    assert seen["api_key"] == "sk-never-returned"
    with database_sessions() as session:
        stored = session.get(ModelProviderConnection, connection.id)
        assert stored is not None and stored.last_probe_status == PROBE_OK
        assert stored.last_probe_at is not None


def test_a_revoked_credential_stops_the_queued_probe(
    database_sessions, database_engine
) -> None:
    owner = open_account(database_sessions, email="probe3-owner@example.com", display_name="O")
    workspace = make_workspace(database_sessions, owner)
    credential = make_credential(
        sessions=database_sessions, workspace_id=workspace.id, actor=owner
    )
    connection = make_connection(
        sessions=database_sessions,
        workspace_id=workspace.id,
        actor=owner,
        credential_id=credential.id,
    )
    with database_sessions() as session:
        provider_service.start_probe(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            connection_id=connection.id,
        )
        credential_service.revoke_credential(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            credential_id=credential.id,
        )
        session.commit()
    run_probe(database_engine, connection.id)
    with database_sessions() as session:
        stored = session.get(ModelProviderConnection, connection.id)
        assert stored is not None
        assert stored.last_probe_status == "failed"
        assert stored.last_probe_detail == "credential_unavailable"


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def test_notifications_deduplicate_and_track_receipts(database_sessions) -> None:
    owner = open_account(database_sessions, email="note-owner@example.com", display_name="O")
    member = open_account(database_sessions, email="note-member@example.com", display_name="M")
    workspace = make_workspace(database_sessions, owner)
    invite(
        sessions=database_sessions, workspace_id=workspace.id, inviter=owner, invitee=member
    )
    accept_latest_invitation(database_sessions, workspace_id=workspace.id, invitee=member)
    actor = AuditActor(actor_type="user", actor_id=str(owner.id), workspace_id=workspace.id)
    with database_sessions() as session:
        first = notification_service.record_notification(
            session,
            actor=actor,
            workspace_id=workspace.id,
            recipient_user_id=member.id,
            notification_type="workspace.invitation",
            severity="info",
            source_type="workspace_invitation",
            source_id=None,
            title="You were invited",
            body="",
            payload={},
        )
        replayed = notification_service.record_notification(
            session,
            actor=actor,
            workspace_id=workspace.id,
            recipient_user_id=member.id,
            notification_type="workspace.invitation",
            severity="info",
            source_type="workspace_invitation",
            source_id=None,
            title="You were invited",
            body="",
            payload={},
        )
        session.commit()
        assert replayed.id == first.id
        notification_id = first.id
    with database_sessions() as session:
        rows, total, unread = notification_service.list_notifications(
            session, actor=as_user(member), workspace_id=workspace.id, page=PageParams()
        )
        assert total == 1 and unread == 1 and rows[0].id == notification_id
        marked = notification_service.mark_read(
            session,
            actor=as_user(member),
            workspace_id=workspace.id,
            notification_ids=[notification_id],
            mark_all=False,
        )
        session.commit()
        assert marked == 1
    with database_sessions() as session:
        _, _, unread = notification_service.list_notifications(
            session, actor=as_user(member), workspace_id=workspace.id, page=PageParams()
        )
        assert unread == 0


def test_notifications_refuse_a_recipient_outside_the_workspace(database_sessions) -> None:
    owner = open_account(database_sessions, email="note2-owner@example.com", display_name="O")
    stranger = open_account(database_sessions, email="note-stranger@example.com", display_name="S")
    workspace = make_workspace(database_sessions, owner)
    actor = AuditActor(actor_type="user", actor_id=str(owner.id), workspace_id=workspace.id)
    with database_sessions() as session, pytest.raises(InvalidArgumentError):
        notification_service.record_notification(
            session,
            actor=actor,
            workspace_id=workspace.id,
            recipient_user_id=stranger.id,
            notification_type="x",
            severity="info",
            source_type="y",
            source_id=None,
            title="t",
            body="",
            payload={},
        )


# ---------------------------------------------------------------------------
# Scope authorization wiring
# ---------------------------------------------------------------------------


def test_resource_scope_admission_uses_real_membership(database_sessions) -> None:
    owner = open_account(database_sessions, email="scope-owner@example.com", display_name="O")
    member = open_account(database_sessions, email="scope-member@example.com", display_name="M")
    stranger = open_account(database_sessions, email="scope-stranger@example.com", display_name="S")
    workspace = make_workspace(database_sessions, owner)
    invite(
        sessions=database_sessions, workspace_id=workspace.id, inviter=owner, invitee=member
    )
    accept_latest_invitation(database_sessions, workspace_id=workspace.id, invitee=member)
    with database_sessions() as session:
        project = project_service.create_project(
            session,
            actor=as_user(owner),
            workspace_id=workspace.id,
            name="Scoped",
            description="",
        )
        session.commit()
        project_id = project.id
    admission = WorkspaceScopeAdmission()
    with database_sessions() as session:
        # The user scope needs no external owner.
        admission.admit(session, reader=ResourceReader(user_id=stranger.id), scope=ScopeRef())
        # A workspace scope requires membership of that workspace.
        admission.admit(
            session,
            reader=ResourceReader(user_id=member.id),
            scope=ScopeRef(workspace_id=workspace.id),
        )
        with pytest.raises(ForbiddenError):
            admission.admit(
                session,
                reader=ResourceReader(user_id=stranger.id),
                scope=ScopeRef(workspace_id=workspace.id),
            )
        # A project scope requires a project seat, which space leadership holds.
        admission.admit(
            session,
            reader=ResourceReader(user_id=owner.id),
            scope=ScopeRef(workspace_id=workspace.id, project_id=project_id),
        )
        with pytest.raises(ForbiddenError):
            admission.admit(
                session,
                reader=ResourceReader(user_id=member.id),
                scope=ScopeRef(workspace_id=workspace.id, project_id=project_id),
            )
        # A project scope without its workspace is malformed.
        with pytest.raises(ForbiddenError):
            admission.admit(
                session,
                reader=ResourceReader(user_id=owner.id),
                scope=ScopeRef(project_id=project_id),
            )


def test_audit_scope_reads_use_real_membership(database_sessions) -> None:
    owner = open_account(database_sessions, email="audit-owner@example.com", display_name="O")
    member = open_account(database_sessions, email="audit-member@example.com", display_name="M")
    stranger = open_account(
        database_sessions, email="audit-stranger@example.com", display_name="S"
    )
    workspace = make_workspace(database_sessions, owner)
    invite(
        sessions=database_sessions, workspace_id=workspace.id, inviter=owner, invitee=member
    )
    accept_latest_invitation(database_sessions, workspace_id=workspace.id, invitee=member)
    admission = WorkspaceAuditScopeAdmission()

    class Reader(AuditReader):
        def __init__(self, profile: UserProfile, *, admin: bool = False) -> None:
            self._profile = profile
            self._admin = admin

        @property
        def user_id(self):
            return self._profile.id

        def has_platform_role(self, role: str) -> bool:
            return self._admin and role == "admin"

    with database_sessions() as session:
        with pytest.raises(ForbiddenError):
            authorize_audit_read(
                session,
                reader=Reader(member),
                admission=admission,
                workspace_id=None,
                project_id=None,
            )
        authorize_audit_read(
            session,
            reader=Reader(owner, admin=True),
            admission=admission,
            workspace_id=None,
            project_id=None,
        )
        authorize_audit_read(
            session,
            reader=Reader(member),
            admission=admission,
            workspace_id=workspace.id,
            project_id=None,
        )
        with pytest.raises(ForbiddenError):
            authorize_audit_read(
                session,
                reader=Reader(stranger),
                admission=admission,
                workspace_id=workspace.id,
                project_id=None,
            )


def test_the_workspace_file_scope_admits_members_only(database_sessions) -> None:
    owner = open_account(database_sessions, email="file-owner@example.com", display_name="O")
    member = open_account(database_sessions, email="file-member@example.com", display_name="M")
    stranger = open_account(database_sessions, email="file-stranger@example.com", display_name="S")
    workspace = make_workspace(database_sessions, owner)
    invite(
        sessions=database_sessions, workspace_id=workspace.id, inviter=owner, invitee=member
    )
    accept_latest_invitation(database_sessions, workspace_id=workspace.id, invitee=member)
    admission = WorkspaceScopeAdmission()
    with database_sessions() as session:
        directory = file_service.create_directory(
            session,
            owner_id=owner.id,
            path="docs",
            admission=admission,
            reader=ResourceReader(user_id=owner.id),
            scope=ScopeRef(workspace_id=workspace.id),
        )
        session.commit()
        directory_id = directory.id
    with database_sessions() as session:
        with pytest.raises(ForbiddenError):
            file_service.create_directory(
                session,
                owner_id=stranger.id,
                path="docs",
                admission=admission,
                reader=ResourceReader(user_id=stranger.id),
                scope=ScopeRef(workspace_id=workspace.id),
            )
        member_directory = file_service.create_directory(
            session,
            owner_id=member.id,
            path="docs",
            admission=admission,
            reader=ResourceReader(user_id=member.id),
            scope=ScopeRef(workspace_id=workspace.id),
        )
        session.commit()
        assert member_directory.id != directory_id


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def reset_tables(engine) -> None:
    """Start one concurrency test from a known-empty database."""
    from tests.conftest import FEATURE_TABLES

    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE temporal_dispatches, http_creation_receipts, "
                + ", ".join(FEATURE_TABLES)
            )
        )


@pytest.fixture
def clean_concurrency_tables(database_engine):
    """Empty the database around a concurrency test, before and after it."""
    reset_tables(database_engine)
    yield
    reset_tables(database_engine)


def concurrent_sessions(engine) -> sessionmaker[Session]:
    """One sessionmaker per worker thread over the shared test engine."""
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        info={
            "dispatch_cipher": SecretCipher(key=TEST_KEY, key_id="test"),
            "dispatch_trace_context": lambda: {},
        },
    )


def wait_for_lock_waits(engine, *, expected: int, timeout: float = 20.0) -> None:
    """Block until ``expected`` other backends wait for a lock."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            waiting = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' AND pid <> pg_backend_pid() "
                    "AND datname = current_database()"
                )
            )
        if (waiting or 0) >= expected:
            return
        time.sleep(0.02)
    raise TimeoutError("the expected lock waits did not appear in time")


def test_concurrent_reservations_admit_exactly_one(
    database_engine, clean_concurrency_tables
) -> None:
    """Two concurrent admissions of one unit cannot both succeed.

    The interleaving is forced, not raced: the first reservation commits
    its hold only after the contender is observed waiting on the account
    row lock, so the loser must surface as a capacity error instead of
    oversubscribing the workspace.
    """
    sessions = concurrent_sessions(database_engine)
    owner = open_account(sessions, email="cap-race@example.com", display_name="Race")
    with sessions() as session:
        workspace = workspace_service.create_workspace(
            session,
            actor=as_user(owner),
            name="Race",
            description="",
            capacity_limits={"concurrency": 1, "environment": None},
        )
        session.commit()
        workspace_id = workspace.id
    outcomes: dict[str, str] = {}
    holding = threading.Event()
    release = threading.Event()

    def reserve(key: str, *, hold: bool) -> None:
        with sessions() as session, session.begin():
            capacity_service.reserve(
                session,
                workspace_id=workspace_id,
                kind=CAPACITY_CONCURRENCY,
                amount=1,
                idempotency_scope="run",
                idempotency_key=key,
            )
            if hold:
                holding.set()
                release.wait(timeout=10)

    def holder() -> None:
        try:
            reserve("holder", hold=True)
            outcomes["holder"] = "reserved"
        except Exception as exc:  # noqa: BLE001 - recorded for the assertion
            outcomes["holder"] = type(exc).__name__

    def contender() -> None:
        try:
            reserve("contender", hold=False)
            outcomes["contender"] = "reserved"
        except Exception as exc:  # noqa: BLE001 - recorded for the assertion
            outcomes["contender"] = type(exc).__name__

    holder_thread = threading.Thread(target=holder)
    contender_thread = threading.Thread(target=contender)
    holder_thread.start()
    assert holding.wait(timeout=10)
    contender_thread.start()
    wait_for_lock_waits(database_engine, expected=1)
    release.set()
    holder_thread.join(timeout=15)
    contender_thread.join(timeout=15)
    assert sorted(outcomes.values()) == ["CapacityExceededError", "reserved"]


def test_concurrent_notification_sources_replay_one_row(
    database_engine, clean_concurrency_tables
) -> None:
    """Two concurrent producers of one source land one inbox row.

    The interleaving is forced, not raced: the first producer holds its
    insert uncommitted until the second is observed waiting on the source
    constraint, so the loser of the race must replay the winner's committed
    row — the same id, one row, one creation event — instead of failing the
    retry with a conflict.
    """
    sessions = concurrent_sessions(database_engine)
    owner = open_account(sessions, email="note-race-owner@example.com", display_name="O")
    member = open_account(sessions, email="note-race-member@example.com", display_name="M")
    with sessions() as session:
        workspace = workspace_service.create_workspace(
            session,
            actor=as_user(owner),
            name="NoteRace",
            description="",
            capacity_limits={"concurrency": None, "environment": None},
        )
        session.commit()
        workspace_id = workspace.id
        recipient_id = member.id
    invite(sessions=sessions, workspace_id=workspace_id, inviter=owner, invitee=member)
    accept_latest_invitation(sessions, workspace_id=workspace_id, invitee=member)
    actor = AuditActor(actor_type="user", actor_id=str(owner.id), workspace_id=workspace_id)
    source_id = uuid4()
    outcomes: dict[str, str] = {}
    landed: dict[str, UUID] = {}
    holding = threading.Event()
    release = threading.Event()

    def record(key: str, *, hold: bool) -> None:
        with sessions() as session, session.begin():
            notification = notification_service.record_notification(
                session,
                actor=actor,
                workspace_id=workspace_id,
                recipient_user_id=recipient_id,
                notification_type="workspace.invitation",
                severity="info",
                source_type="workspace_invitation",
                source_id=source_id,
                title="You were invited",
                body="",
                payload={},
            )
            landed[key] = notification.id
            if hold:
                holding.set()
                release.wait(timeout=10)

    def holder() -> None:
        try:
            record("holder", hold=True)
            outcomes["holder"] = "recorded"
        except Exception as exc:  # noqa: BLE001 - recorded for the assertion
            outcomes["holder"] = type(exc).__name__

    def contender() -> None:
        try:
            record("contender", hold=False)
            outcomes["contender"] = "recorded"
        except Exception as exc:  # noqa: BLE001 - recorded for the assertion
            outcomes["contender"] = type(exc).__name__

    holder_thread = threading.Thread(target=holder)
    holder_thread.start()
    assert holding.wait(timeout=10)
    contender_thread = threading.Thread(target=contender)
    contender_thread.start()
    wait_for_lock_waits(database_engine, expected=1)
    release.set()
    holder_thread.join(timeout=15)
    contender_thread.join(timeout=15)
    assert outcomes == {"holder": "recorded", "contender": "recorded"}
    assert landed["holder"] == landed["contender"]
    with sessions() as session:
        rows = session.scalars(
            select(WorkspaceNotification).where(
                WorkspaceNotification.workspace_id == workspace_id,
                WorkspaceNotification.source_id == source_id,
            )
        ).all()
        assert len(rows) == 1
        events = session.scalars(
            select(AuditEvent).where(
                AuditEvent.target_type == "workspace_notification",
                AuditEvent.target_id == str(landed["holder"]),
            )
        ).all()
        assert len(events) == 1, "the replayed producer must not record a second creation"
