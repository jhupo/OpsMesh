"""Invitation credentials are handed out once, never dependent on core mail."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from opsmesh.identity.invitations import service
from opsmesh.identity.invitations.models import AccountInvitation
from opsmesh.identity.users.models import UserProfile
from opsmesh.platform.audit.models import AuditEvent
from opsmesh.shared.audit import AuditActor
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.pagination import PageParams
from tests.execution.test_session_routes import app_client as app_client
from tests.execution.test_session_routes import register_account


def test_admin_issues_rotates_and_accepts_without_a_mail_sender(app_client, database_sessions):
    admin_token = register_account(database_sessions, email="issuer@example.com")
    member_token = register_account(database_sessions, email="member@example.com")
    with database_sessions() as session, session.begin():
        administrator = session.scalar(
            select(UserProfile).where(UserProfile.email == "issuer@example.com")
        )
        administrator.platform_roles = ["admin", "member"]
    app_client.app.state.settings.platform_base_url = "https://client.example"
    headers = {"X-Session-ID": str(admin_token)}
    url = "/api/v1/invitations"
    denied = app_client.post(
        url,
        headers={"X-Session-ID": str(member_token)},
        json={"email": "invitee@example.com"},
    )
    assert denied.status_code == 403
    issued = app_client.post(url, headers=headers, json={"email": "invitee@example.com"})
    assert issued.status_code == 201, issued.text
    invitation = issued.json()
    original = UUID(invitation["token"])
    invitation_id = UUID(invitation["invitation_id"])
    assert invitation["acceptance_url"] == (
        f"https://client.example/accept-invitation?token={original}"
    )
    for view in (
        app_client.get(f"{url}/{invitation_id}", headers=headers).json(),
        app_client.get(url, headers=headers).json()["invitations"][0],
    ):
        assert "token" not in view and "acceptance_url" not in view
        assert "token_hash" not in view
    reissued = app_client.post(f"{url}/{invitation_id}/resend", headers=headers)
    assert reissued.status_code == 200, reissued.text
    replacement = UUID(reissued.json()["token"])
    assert replacement != original
    assert reissued.json()["token_revision"] == 2
    acceptance = {"display_name": "Invitee", "password": "StrongInvitationPassword123!"}
    rejected = app_client.post(f"{url}/accept", json={**acceptance, "token": str(original)})
    assert rejected.status_code == 404
    accepted = app_client.post(f"{url}/accept", json={**acceptance, "token": str(replacement)})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["session_token"]
    assert (
        app_client.post(f"{url}/accept", json={**acceptance, "token": str(replacement)}).status_code
        == 409
    )
    with database_sessions() as session:
        stored = session.get(AccountInvitation, invitation_id)
        assert stored.token_hash == service.token_digest(replacement)
        assert stored.status == "accepted"
        for event in session.scalars(select(AuditEvent)):
            assert str(original) not in str(event.details)
            assert str(replacement) not in str(event.details)


def test_concurrent_creation_cannot_issue_two_live_invitations(database_sessions):
    def issue(_):
        try:
            with database_sessions() as session, session.begin():
                invitation, _ = service.create_invitation(
                    session, email="duplicate@example.com", invited_by=AuditActor("platform")
                )
                return invitation.id
        except ConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(issue, range(2)))
    assert sum(item is not None for item in outcomes) == 1


def test_pending_and_expired_filters_match_the_returned_status(database_sessions):
    with database_sessions() as session, session.begin():
        expired, _ = service.create_invitation(
            session, email="expired@example.com", invited_by=AuditActor("platform")
        )
        expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        pending, _ = service.create_invitation(
            session, email="pending@example.com", invited_by=AuditActor("platform")
        )
        for status, expected in (("pending", pending.id), ("expired", expired.id)):
            rows, total = service.list_invitations(session, page=PageParams(), status=status)
            assert total == 1
            assert [row.id for row in rows] == [expected]
            assert service.effective_status(rows[0]) == status
