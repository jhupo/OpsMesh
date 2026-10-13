"""Workspace-scoped product API verification on PostgreSQL."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from opsmesh.agents.providers.models import ModelProviderCredential
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.governance.security_events.models import SecurityEvent
from opsmesh.identity.users.models import User
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.state import RunStatus
from opsmesh.orchestration.tasks.models import Task
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.workspaces.members.models import WorkspaceInvite, WorkspaceMember
from opsmesh.workspaces.quotas.models import WorkspaceQuota
from opsmesh.workspaces.quotas.reservations import WorkspaceQuotaReservationService
from tests.fixtures.workspace_api import _client, _headers, _seed_workspace


def test_workspace_create_is_idempotent_per_user() -> None:
    client, session = _client()
    user = User(email="workspace-owner@example.com", display_name="Owner")
    other = User(email="other-owner@example.com", display_name="Other")
    session.add_all([user, other])
    session.commit()
    headers = _headers(user.id) | {"Idempotency-Key": "create-workspace"}
    first = client.post(
        "/api/v1/workspaces", headers=headers, json={"name": "Acme", "slug": "acme"}
    )
    second = client.post(
        "/api/v1/workspaces", headers=headers, json={"name": "Acme", "slug": "acme"}
    )
    other_user = client.post(
        "/api/v1/workspaces",
        headers=_headers(other.id) | {"Idempotency-Key": "create-workspace"},
        json={"name": "Other Acme", "slug": "other-acme"},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert other_user.status_code == 201
    assert other_user.json()["id"] != first.json()["id"]


def test_create_workspace_assigns_owner_membership() -> None:
    client, session = _client()
    user = User(email="new-owner@example.com", display_name="New Owner")
    session.add(user)
    session.commit()
    response = client.post(
        "/api/v1/workspaces",
        headers=_headers(user.id),
        json={"name": "New AI Company", "slug": "new-ai-company"},
    )
    assert response.status_code == 201
    workspace_id = response.json()["id"]
    members = client.get(f"/api/v1/workspaces/{workspace_id}/members", headers=_headers(user.id))
    assert members.status_code == 200
    assert members.json()["items"][0]["role"] == "owner"


def test_workspace_invite_flow_accepts_and_redacts_tokens() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    invitee = User(email="invitee@example.com", display_name="Invitee")
    session.add(invitee)
    session.commit()
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(owner.id),
        json={
            "email": "Invitee@Example.com",
            "role": "admin",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "invitee_user_id": str(invitee.id),
        },
    )
    listed = client.get(f"/api/v1/workspaces/{workspace.id}/invites", headers=_headers(owner.id))
    accepted = client.post(
        "/api/v1/workspaces/invites/accept",
        headers=_headers(invitee.id),
        json={"token": created.json()["token"]},
    )
    stored_invite = session.get(WorkspaceInvite, UUID(created.json()["id"]))
    assert stored_invite is not None
    assert created.status_code == 201
    assert created.json()["email"] == "invitee@example.com"
    assert created.json()["invitee_user_id"] == str(invitee.id)
    assert created.json()["token"].startswith("ccwi_")
    assert stored_invite.token_hash != created.json()["token"]
    assert stored_invite.fingerprint == created.json()["fingerprint"]
    assert listed.status_code == 200
    assert listed.json()["items"][0]["fingerprint"] == created.json()["fingerprint"]
    assert "token" not in listed.json()["items"][0]
    assert "token_hash" not in listed.json()["items"][0]
    assert created.json()["token"] not in str(listed.json())
    assert accepted.status_code == 200
    assert accepted.json()["invite"]["status"] == "accepted"
    assert accepted.json()["member"]["workspace_id"] == str(workspace.id)
    assert accepted.json()["member"]["user_id"] == str(invitee.id)
    assert accepted.json()["member"]["role"] == "admin"
    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.workspace_id == workspace.id)
        .order_by(AuditEvent.created_at.asc())
    ).all()
    assert [event.action for event in events] == [
        "workspace.invite_created",
        "workspace.invite_accepted",
    ]
    assert events[0].audit_metadata["fingerprint"] == created.json()["fingerprint"]
    assert "token_hash" not in str(events[0].audit_metadata)
    security = session.scalars(
        select(SecurityEvent).where(SecurityEvent.workspace_id == workspace.id)
    ).all()
    assert [event.action for event in security] == ["workspace.invite.accepted"]


def test_workspace_invite_accept_restores_disabled_member() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    invitee = User(email="restore-invitee@example.com", display_name="Restore Invitee")
    disabled_member = WorkspaceMember(
        workspace=workspace, user=invitee, role="viewer", status="disabled"
    )
    session.add_all([invitee, disabled_member])
    session.commit()
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(owner.id),
        json={
            "email": invitee.email,
            "role": "operator",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "invitee_user_id": str(invitee.id),
        },
    )
    accepted = client.post(
        "/api/v1/workspaces/invites/accept",
        headers=_headers(invitee.id),
        json={"token": created.json()["token"]},
    )
    assert created.status_code == 201
    assert accepted.status_code == 200
    assert accepted.json()["member"]["id"] == str(disabled_member.id)
    assert accepted.json()["member"]["role"] == "operator"
    assert accepted.json()["member"]["status"] == "active"


def test_workspace_invite_rejects_revoked_expired_and_wrong_user_tokens() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    invitee = User(email="secure-invitee@example.com", display_name="Secure Invitee")
    wrong_user = User(email="wrong-invitee@example.com", display_name="Wrong Invitee")
    session.add_all([invitee, wrong_user])
    session.commit()
    revoked = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(owner.id),
        json={
            "email": invitee.email,
            "role": "viewer",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "invitee_user_id": str(invitee.id),
        },
    )
    revoked_token = revoked.json()["token"]
    revoked_response = client.delete(
        f"/api/v1/workspaces/{workspace.id}/invites/{revoked.json()['id']}",
        headers=_headers(owner.id),
    )
    revoked_accept = client.post(
        "/api/v1/workspaces/invites/accept",
        headers=_headers(invitee.id),
        json={"token": revoked_token},
    )
    wrong_user_invite = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(owner.id),
        json={
            "email": invitee.email,
            "role": "viewer",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "invitee_user_id": str(invitee.id),
        },
    )
    wrong_user_accept = client.post(
        "/api/v1/workspaces/invites/accept",
        headers=_headers(wrong_user.id),
        json={"token": wrong_user_invite.json()["token"]},
    )
    expired_invite = session.get(WorkspaceInvite, UUID(wrong_user_invite.json()["id"]))
    assert expired_invite is not None
    expired_invite.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    expired_accept = client.post(
        "/api/v1/workspaces/invites/accept",
        headers=_headers(invitee.id),
        json={"token": wrong_user_invite.json()["token"]},
    )
    assert revoked_response.status_code == 200
    assert revoked_response.json()["status"] == "revoked"
    assert revoked_accept.status_code == 409
    assert wrong_user_accept.status_code == 403
    assert expired_accept.status_code == 409
    security_actions = [
        event.action
        for event in session.scalars(select(SecurityEvent).order_by(SecurityEvent.created_at))
    ]
    assert security_actions == [
        "workspace.invite.accept_rejected",
        "workspace.invite.accept_rejected",
        "workspace.invite.accept_rejected",
    ]
    rejected_events = session.scalars(
        select(SecurityEvent).order_by(SecurityEvent.created_at)
    ).all()
    assert [event.workspace_id for event in rejected_events] == [
        workspace.id,
        workspace.id,
        workspace.id,
    ]


def test_workspace_invite_rejects_viewers_and_cross_workspace_revokes() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="invite-other-owner@example.com", slug="invite-other-owner"
    )
    viewer = User(email="invite-viewer@example.com", display_name="Invite Viewer")
    invitee = User(email="invite-target@example.com", display_name="Invite Target")
    session.add_all(
        [viewer, invitee, WorkspaceMember(workspace=workspace, user=viewer, role="viewer")]
    )
    session.commit()
    viewer_create = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(viewer.id),
        json={
            "email": invitee.email,
            "role": "operator",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    other_invite = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/invites",
        headers=_headers(other_owner.id),
        json={
            "email": invitee.email,
            "role": "viewer",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    cross_workspace_revoke = client.delete(
        f"/api/v1/workspaces/{workspace.id}/invites/{other_invite.json()['id']}",
        headers=_headers(owner.id),
    )
    assert viewer_create.status_code == 403
    assert other_invite.status_code == 201
    assert cross_workspace_revoke.status_code == 404


def test_workspace_invite_does_not_auto_bind_email_to_platform_user() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    invitee = User(email="email-only-invitee@example.com", display_name="Email Only")
    session.add(invitee)
    session.commit()
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(owner.id),
        json={
            "email": invitee.email,
            "role": "viewer",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    assert created.status_code == 201
    assert created.json()["invitee_user_id"] is None


def test_workspace_invite_rejects_duplicate_active_invite_but_allows_after_revoke() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    payload = {
        "email": "duplicate-invitee@example.com",
        "role": "viewer",
        "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    }
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites", headers=_headers(owner.id), json=payload
    )
    duplicate = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites", headers=_headers(owner.id), json=payload
    )
    revoked = client.delete(
        f"/api/v1/workspaces/{workspace.id}/invites/{created.json()['id']}",
        headers=_headers(owner.id),
    )
    recreated = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites", headers=_headers(owner.id), json=payload
    )
    assert created.status_code == 201
    assert duplicate.status_code == 409
    assert revoked.status_code == 200
    assert recreated.status_code == 201


def test_workspace_invite_list_persists_expired_status() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(owner.id),
        json={
            "email": "expired-on-list@example.com",
            "role": "viewer",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    invite = session.get(WorkspaceInvite, UUID(created.json()["id"]))
    assert invite is not None
    invite.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    listed = client.get(f"/api/v1/workspaces/{workspace.id}/invites", headers=_headers(owner.id))
    session.expire_all()
    stored = session.get(WorkspaceInvite, UUID(created.json()["id"]))
    assert listed.status_code == 200
    assert listed.json()["items"][0]["status"] == "expired"
    assert stored is not None
    assert stored.status == "expired"


def test_workspace_admin_cannot_grant_or_manage_owner_role() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    admin = User(email="member-admin@example.com", display_name="Member Admin")
    target = User(email="owner-target@example.com", display_name="Owner Target")
    session.add_all([admin, target, WorkspaceMember(workspace=workspace, user=admin, role="admin")])
    session.commit()
    owner_member = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id, WorkspaceMember.user_id == owner.id
        )
    )
    assert owner_member is not None
    owner_invite = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(admin.id),
        json={
            "email": "owner-invite@example.com",
            "role": "owner",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    owner_member_create = client.post(
        f"/api/v1/workspaces/{workspace.id}/members",
        headers=_headers(admin.id),
        json={"user_id": str(target.id), "role": "owner"},
    )
    owner_member_update = client.patch(
        f"/api/v1/workspaces/{workspace.id}/members/{owner_member.id}",
        headers=_headers(admin.id),
        json={"role": "admin"},
    )
    owner_member_disable = client.delete(
        f"/api/v1/workspaces/{workspace.id}/members/{owner_member.id}", headers=_headers(admin.id)
    )
    assert owner_invite.status_code == 403
    assert owner_member_create.status_code == 403
    assert owner_member_update.status_code == 403
    assert owner_member_disable.status_code == 403


def test_workspace_member_api_manages_members_and_records_audit() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    user = User(email="managed-member@example.com", display_name="Managed Member")
    session.add(user)
    session.commit()
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/members",
        headers=_headers(owner.id),
        json={"user_id": str(user.id), "role": "admin"},
    )
    updated = client.patch(
        f"/api/v1/workspaces/{workspace.id}/members/{created.json()['id']}",
        headers=_headers(owner.id),
        json={"role": "operator", "status": "active"},
    )
    disabled = client.delete(
        f"/api/v1/workspaces/{workspace.id}/members/{created.json()['id']}",
        headers=_headers(owner.id),
    )
    assert created.status_code == 201
    assert created.json()["workspace_id"] == str(workspace.id)
    assert created.json()["user_id"] == str(user.id)
    assert created.json()["role"] == "admin"
    assert created.json()["status"] == "active"
    assert updated.status_code == 200
    assert updated.json()["role"] == "operator"
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.workspace_id == workspace.id)
        .order_by(AuditEvent.created_at.asc())
    ).all()
    assert [event.action for event in events] == [
        "workspace.member_created",
        "workspace.member_updated",
        "workspace.member_disabled",
    ]
    assert events[0].audit_metadata["after"]["role"] == "admin"
    assert events[1].audit_metadata["before"]["role"] == "admin"
    assert events[1].audit_metadata["after"]["role"] == "operator"
    assert events[2].audit_metadata["after"]["status"] == "disabled"


def test_workspace_member_api_rejects_viewers_and_cross_workspace_targets() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="other-member-owner@example.com", slug="other-member-owner"
    )
    viewer = User(email="workspace-viewer@example.com", display_name="Workspace Viewer")
    target_user = User(email="target-member@example.com", display_name="Target Member")
    session.add_all(
        [viewer, target_user, WorkspaceMember(workspace=workspace, user=viewer, role="viewer")]
    )
    session.commit()
    foreign_member = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == other_workspace.id,
            WorkspaceMember.user_id == other_owner.id,
        )
    )
    assert foreign_member is not None
    viewer_create = client.post(
        f"/api/v1/workspaces/{workspace.id}/members",
        headers=_headers(viewer.id),
        json={"user_id": str(target_user.id), "role": "operator"},
    )
    cross_workspace_member = client.patch(
        f"/api/v1/workspaces/{workspace.id}/members/{foreign_member.id}",
        headers=_headers(owner.id),
        json={"role": "viewer"},
    )
    cross_workspace_path = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/members",
        headers=_headers(owner.id),
        json={"user_id": str(target_user.id), "role": "operator"},
    )
    assert viewer_create.status_code == 403
    assert cross_workspace_member.status_code == 404
    assert cross_workspace_path.status_code == 403


def test_workspace_member_api_protects_last_active_owner() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    owner_member = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id, WorkspaceMember.user_id == owner.id
        )
    )
    assert owner_member is not None
    demote_last_owner = client.patch(
        f"/api/v1/workspaces/{workspace.id}/members/{owner_member.id}",
        headers=_headers(owner.id),
        json={"role": "admin"},
    )
    disable_last_owner = client.delete(
        f"/api/v1/workspaces/{workspace.id}/members/{owner_member.id}", headers=_headers(owner.id)
    )
    second_owner = User(email="second-owner@example.com", display_name="Second Owner")
    session.add(second_owner)
    session.commit()
    created_second_owner = client.post(
        f"/api/v1/workspaces/{workspace.id}/members",
        headers=_headers(owner.id),
        json={"user_id": str(second_owner.id), "role": "owner"},
    )
    demote_first_owner = client.patch(
        f"/api/v1/workspaces/{workspace.id}/members/{owner_member.id}",
        headers=_headers(owner.id),
        json={"role": "admin"},
    )
    disable_remaining_owner = client.delete(
        f"/api/v1/workspaces/{workspace.id}/members/{created_second_owner.json()['id']}",
        headers=_headers(second_owner.id),
    )
    assert demote_last_owner.status_code == 409
    assert disable_last_owner.status_code == 409
    assert created_second_owner.status_code == 201
    assert demote_first_owner.status_code == 200
    assert demote_first_owner.json()["role"] == "admin"
    assert disable_remaining_owner.status_code == 409


def test_workspace_update_can_pause_scheduler_and_records_audit() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    response = client.patch(
        f"/api/v1/workspaces/{workspace.id}",
        headers=_headers(owner.id),
        json={
            "status": "paused",
            "settings": {
                "scheduler": {"paused": True, "pause_reason": "maintenance"},
                "model_provider": {
                    "api_key": "sk-workspace",
                    "base_url": "https://workspace.example.test/private",
                },
            },
        },
    )
    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.workspace_id == workspace.id)
        .order_by(AuditEvent.created_at.asc())
    ).all()
    actions = [event.action for event in events]
    assert response.status_code == 200
    assert response.json()["status"] == "paused"
    assert response.json()["settings"]["scheduler"]["paused"] is True
    assert response.json()["settings"]["model_provider"] == {
        "api_key": "[redacted]",
        "base_url": "[redacted]",
    }
    assert "sk-workspace" not in str(response.json())
    assert "workspace.example.test/private" not in str(response.json())
    assert actions == ["workspace.status_updated", "workspace.scheduler_policy_updated"]
    assert events[0].audit_metadata["before"] == "active"
    assert events[0].audit_metadata["after"] == "paused"
    assert events[1].audit_metadata["after"]["pause_reason"] == "maintenance"


def test_workspace_update_configures_resource_review_model_and_records_audit() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    encrypted = SecretEncryptionService(secret="unit-test-secret", key_id="test").encrypt_payload(
        {"api_key": "sk-reviewer"}
    )
    credential = ModelProviderCredential(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Reviewer",
        provider="openai",
        default_model="gpt-5.5",
        encrypted_api_key=encrypted.ciphertext,
        api_key_fingerprint=encrypted.fingerprint,
        encryption_key_id=encrypted.key_id,
        is_default=False,
        status="active",
    )
    session.add(credential)
    session.commit()
    response = client.patch(
        f"/api/v1/workspaces/{workspace.id}",
        headers=_headers(owner.id),
        json={
            "settings": {
                "approvals": {
                    "reviewer": "model",
                    "model": {
                        "instructions": "Check authorized scope.",
                        "model_provider_credential_id": str(credential.id),
                        "model": "configured-review-model",
                        "timeout_seconds": 12,
                        "on_error": "human",
                    },
                }
            }
        },
    )
    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.workspace_id == workspace.id)
        .order_by(AuditEvent.created_at.asc())
    ).all()
    assert response.status_code == 200
    semantic = response.json()["settings"]["approvals"]["model"]
    assert semantic["model_provider_credential_id"] == str(credential.id)
    assert semantic["model"] == "configured-review-model"
    assert semantic["timeout_seconds"] == 12
    assert [event.action for event in events] == ["workspace.resource_review_policy_updated"]
    assert events[0].audit_metadata["after"]["model"]["model"] == "configured-review-model"
    assert events[0].audit_metadata["after"]["model"]["model_provider_credential_id"] == str(
        credential.id
    )


def test_workspace_update_rejects_invalid_resource_review_model_provider() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="other-review-owner@example.com", slug="other-review-owner"
    )
    encrypted = SecretEncryptionService(secret="unit-test-secret", key_id="test").encrypt_payload(
        {"api_key": "sk-reviewer"}
    )
    foreign_credential = ModelProviderCredential(
        workspace_id=other_workspace.id,
        created_by_user_id=other_owner.id,
        name="Foreign Reviewer",
        provider="openai",
        default_model="gpt-5.5",
        encrypted_api_key=encrypted.ciphertext,
        api_key_fingerprint=encrypted.fingerprint,
        encryption_key_id=encrypted.key_id,
        is_default=False,
        status="active",
    )
    session.add(foreign_credential)
    session.commit()
    response = client.patch(
        f"/api/v1/workspaces/{workspace.id}",
        headers=_headers(owner.id),
        json={
            "settings": {
                "approvals": {
                    "reviewer": "model",
                    "model": {
                        "model_provider_credential_id": str(foreign_credential.id),
                        "model": "review",
                        "instructions": "Check scope.",
                    },
                }
            }
        },
    )
    fail_open = client.patch(
        f"/api/v1/workspaces/{workspace.id}",
        headers=_headers(owner.id),
        json={"settings": {"approvals": {"reviewer": "model", "model": {"fail_closed": False}}}},
    )
    assert response.status_code == 404
    assert fail_open.status_code == 422


def test_workspace_update_rejects_retired_review_configuration() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    response = client.patch(
        f"/api/v1/workspaces/{workspace.id}",
        headers=_headers(owner.id),
        json={
            "settings": {
                "resource_review": {
                    "public_resources": {
                        "agent_profile": False,
                        "skill": True,
                        "mcp_server": True,
                        "plugin": True,
                    }
                }
            }
        },
    )
    assert response.status_code == 422
    assert "resource_review was replaced by approvals" in str(response.json())


def test_workspace_update_rejects_invalid_scheduler_pause_config() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    response = client.patch(
        f"/api/v1/workspaces/{workspace.id}",
        headers=_headers(owner.id),
        json={"settings": {"scheduler": {"paused": "yes"}}},
    )
    assert response.status_code == 422


def test_workspace_quota_api_manages_runtime_limits() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    viewer, _ = _seed_workspace(
        session, role="viewer", email="viewer@example.com", slug="viewer-space"
    )
    upserted = client.put(
        f"/api/v1/workspaces/{workspace.id}/quotas",
        headers=_headers(owner.id),
        json={
            "quotas": [
                {"quota_key": "active_runs", "limit_value": 2, "unit": "count"},
                {"quota_key": "memory_mb", "limit_value": 4096, "unit": "mb"},
                {"quota_key": "sandbox_sessions", "limit_value": 1, "unit": "count"},
                {"quota_key": "mcp_services", "limit_value": 1, "unit": "count"},
            ]
        },
    )
    memory_quota = session.scalar(
        select(WorkspaceQuota).where(
            WorkspaceQuota.workspace_id == workspace.id, WorkspaceQuota.quota_key == "memory_mb"
        )
    )
    assert memory_quota is not None
    memory_quota.reserved_value = 1024
    session.commit()
    tightened = client.put(
        f"/api/v1/workspaces/{workspace.id}/quotas",
        headers=_headers(owner.id),
        json={"quotas": [{"quota_key": "memory_mb", "limit_value": 512, "unit": "mb"}]},
    )
    listed = client.get(f"/api/v1/workspaces/{workspace.id}/quotas", headers=_headers(owner.id))
    denied = client.put(
        f"/api/v1/workspaces/{workspace.id}/quotas",
        headers=_headers(viewer.id),
        json={"quotas": [{"quota_key": "active_runs", "limit_value": 99}]},
    )
    disabled = client.delete(
        f"/api/v1/workspaces/{workspace.id}/quotas/sandbox_sessions", headers=_headers(owner.id)
    )
    assert upserted.status_code == 200
    assert [item["quota_key"] for item in upserted.json()] == [
        "active_runs",
        "mcp_services",
        "memory_mb",
        "sandbox_sessions",
    ]
    assert tightened.status_code == 200
    assert tightened.json()[0]["quota_key"] == "memory_mb"
    assert tightened.json()[0]["reserved_value"] == 1024
    assert tightened.json()[0]["available_value"] == 0
    assert tightened.json()[0]["utilization"] == 2.0
    assert tightened.json()[0]["saturated"] is True
    assert tightened.json()[0]["over_reserved"] is True
    assert listed.status_code == 200
    quota_by_key = {item["quota_key"]: item for item in listed.json()["items"]}
    assert quota_by_key["memory_mb"]["reserved_value"] == 1024
    assert quota_by_key["memory_mb"]["available_value"] == 0
    assert quota_by_key["memory_mb"]["utilization"] == 2.0
    assert quota_by_key["memory_mb"]["saturated"] is True
    assert quota_by_key["memory_mb"]["over_reserved"] is True
    assert denied.status_code == 403
    assert disabled.status_code == 200
    assert disabled.json()["quota_key"] == "sandbox_sessions"
    assert disabled.json()["status"] == "disabled"
    audit_events = (
        session.query(AuditEvent)
        .filter(AuditEvent.workspace_id == workspace.id)
        .order_by(AuditEvent.created_at)
        .all()
    )
    assert [event.action for event in audit_events] == [
        "workspace.quotas_upserted",
        "workspace.quotas_upserted",
        "workspace.quota_disabled",
    ]
    assert audit_events[1].audit_metadata["quotas"][0]["before"]["reserved_value"] == 1024
    assert audit_events[1].audit_metadata["quotas"][0]["after"]["limit_value"] == 512
    assert audit_events[2].audit_metadata["quota_key"] == "sandbox_sessions"


def test_workspace_execution_slot_summary_reports_active_capacity() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session,
        role="owner",
        email="other-execution-slots@example.com",
        slug="other-execution-slots",
    )
    client.put(
        f"/api/v1/workspaces/{workspace.id}/quotas",
        headers=_headers(owner.id),
        json={
            "quotas": [
                {"quota_key": "active_runs", "limit_value": 4, "unit": "count"},
            ]
        },
    )
    service = WorkspaceQuotaReservationService(session)
    first_task = Task(
        workspace_id=workspace.id, created_by_user_id=owner.id, title="First run", status="running"
    )
    second_task = Task(
        workspace_id=workspace.id, created_by_user_id=owner.id, title="Second run", status="running"
    )
    session.add_all([first_task, second_task])
    session.flush()
    first_run = AgentRun(
        workspace_id=workspace.id, task_id=first_task.id, status=RunStatus.RUNNING.value, input={}
    )
    second_run = AgentRun(
        workspace_id=workspace.id, task_id=second_task.id, status=RunStatus.RUNNING.value, input={}
    )
    session.add_all([first_run, second_run])
    session.flush()
    first_result = service.reserve(
        workspace_id=workspace.id,
        task_id=first_task.id,
        task_step_id=None,
        reservation_key="run:first",
        resource_usage={"active_runs": 1},
    )
    second_result = service.reserve(
        workspace_id=workspace.id,
        task_id=second_task.id,
        task_step_id=None,
        reservation_key="run:second",
        resource_usage={"active_runs": 1},
    )
    assert first_result.reservation is not None
    assert second_result.reservation is not None
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/quotas/execution-summary", headers=_headers(owner.id)
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/quotas/execution-summary",
        headers=_headers(other_owner.id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["active_reservation_count"] == 2
    assert body["reservation_usage"] == {"active_runs": 2}
    assert body["over_reserved_quota_keys"] == []
    quotas = {item["quota_key"]: item for item in body["quotas"]}
    assert quotas["active_runs"]["reserved_value"] == 2
    assert quotas["active_runs"]["available_value"] == 2
    assert [item["reservation_key"] for item in body["active_reservations"]] == [
        "run:first",
        "run:second",
    ]
    assert forbidden.status_code == 403


def test_duplicate_workspace_slug_returns_conflict_error() -> None:
    client, session = _client()
    user = User(email="owner@example.com", display_name="Owner")
    session.add(user)
    session.commit()
    headers = _headers(user.id)
    first = client.post(
        "/api/v1/workspaces", headers=headers, json={"name": "Acme", "slug": "acme"}
    )
    duplicate = client.post(
        "/api/v1/workspaces", headers=headers, json={"name": "Acme Again", "slug": "acme"}
    )
    after_conflict = client.get("/api/v1/workspaces", headers=headers)
    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"
    assert duplicate.json()["error"]["message"] == "Workspace slug already exists"
    assert after_conflict.status_code == 200
    assert after_conflict.json()["total"] == 1
