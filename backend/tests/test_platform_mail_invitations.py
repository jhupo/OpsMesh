import re
import smtplib
from datetime import UTC, datetime, timedelta
from typing import Any

from pytest import MonkeyPatch, raises
from sqlalchemy import select
from test_admin_api import _admin_headers, _client

from backend.app.governance.security_events.models import SecurityEvent
from backend.app.identity.auth.service import AuthenticationService
from backend.app.identity.invitations.models import UserInvitation
from backend.app.identity.users.models import User
from backend.app.messaging.email.models import PlatformMailSettings
from backend.app.messaging.email.routes import router as email_router
from backend.app.messaging.email.smtp import send_smtp_message

MAIL_SETTINGS = {
    "enabled": True,
    "host": "smtp.example.com",
    "port": 587,
    "security": "starttls",
    "username": "mailer",
    "password": "test-smtp-credential",
    "from_email": "opsmesh@example.com",
    "from_name": "OpsMesh",
    "public_base_url": "https://opsmesh.example.com",
    "invitation_expiry_hours": 72,
}


def test_smtp_transport_requires_verified_tls_and_reports_recipient_rejection(
    monkeypatch: MonkeyPatch,
) -> None:
    calls: list[str] = []

    class SMTPConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            calls.append("ehlo")

        def starttls(self, *, context):
            assert context.check_hostname
            calls.append("starttls")

        def login(self, username, password):
            assert username == "mailer" and password == "smtp-fixture-secret"
            calls.append("login")

        def send_message(self, message):
            assert "smtp-fixture-secret" not in str(message)
            calls.append("send")
            return {"recipient@example.com": (550, b"rejected")}

    monkeypatch.setattr(
        "backend.app.messaging.email.smtp.smtplib.SMTP", lambda *args, **kwargs: SMTPConnection()
    )
    with raises(smtplib.SMTPRecipientsRefused):
        send_smtp_message(
            timeout_seconds=10,
            host="smtp.example.com",
            port=587,
            security="starttls",
            username="mailer",
            password="smtp-fixture-secret",
            from_email="opsmesh@example.com",
            from_name="OpsMesh",
            recipient="recipient@example.com",
            subject="Test",
            body="Test body",
        )
    assert calls == ["ehlo", "starttls", "ehlo", "login", "send"]


def test_mail_settings_protect_credentials_and_admin_boundary(monkeypatch: MonkeyPatch) -> None:
    client, session, _ = _client()
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "backend.app.messaging.email.service.send_smtp_message",
        lambda **values: sent.append(values),
    )
    assert client.get("/api/v1/admin/system/mail").status_code == 401
    assert client.put("/api/v1/admin/system/mail", json=MAIL_SETTINGS).status_code == 401
    response = client.put("/api/v1/admin/system/mail", headers=_admin_headers(), json=MAIL_SETTINGS)
    assert response.status_code == 200
    assert response.json()["password_configured"] is True
    assert MAIL_SETTINGS["password"] not in response.text
    record = session.get(PlatformMailSettings, 1)
    assert record is not None
    assert MAIL_SETTINGS["password"] not in str(record.configuration)
    assert record.password_ciphertext != MAIL_SETTINGS["password"]
    update = {key: value for key, value in MAIL_SETTINGS.items() if key != "password"}
    assert (
        client.put("/api/v1/admin/system/mail", headers=_admin_headers(), json=update).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/admin/system/mail/test",
            headers=_admin_headers(),
            json={"email": "recipient@example.com"},
        ).status_code
        == 200
    )
    assert sent[0]["password"] == MAIL_SETTINGS["password"]
    assert (
        client.post(
            "/api/v1/admin/system/mail/test", json={"email": "recipient@example.com"}
        ).status_code
        == 401
    )
    events = session.scalars(select(SecurityEvent)).all()
    assert all(MAIL_SETTINGS["password"] not in str(event.event_metadata) for event in events)
    disabled = {**update, "enabled": False, "clear_password": True}
    response = client.put("/api/v1/admin/system/mail", headers=_admin_headers(), json=disabled)
    assert response.status_code == 200
    assert response.json()["password_configured"] is False
    assert (
        client.post(
            "/api/v1/admin/user-invitations",
            headers=_admin_headers(),
            json={"email": "new@example.com"},
        ).status_code
        == 409
    )


def test_invitation_activation_is_single_use_and_idempotent(monkeypatch: MonkeyPatch) -> None:
    client, session, _ = _client()
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "backend.app.messaging.email.service.send_smtp_message",
        lambda **values: sent.append(values),
    )
    assert (
        client.put(
            "/api/v1/admin/system/mail", headers=_admin_headers(), json=MAIL_SETTINGS
        ).status_code
        == 200
    )
    payload = {
        "email": "invited@example.com",
        "display_name": "Invited User",
        "platform_admin": False,
    }
    assert client.post("/api/v1/admin/user-invitations", json=payload).status_code == 401
    response = client.post("/api/v1/admin/user-invitations", headers=_admin_headers(), json=payload)
    assert response.status_code == 201
    assert response.json()["delivery_status"] == "sent"
    match = re.search(r"#token=([A-Za-z0-9_-]+)", sent[-1]["body"])
    assert match
    token = match.group(1)
    assert token not in response.text
    duplicate = client.post(
        "/api/v1/admin/user-invitations", headers=_admin_headers(), json=payload
    )
    assert duplicate.json()["id"] == response.json()["id"]
    assert len(sent) == 1
    user = session.scalar(select(User).where(User.email == payload["email"]))
    assert user is not None and user.status == "invited" and user.password_hash is None
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": user.email, "password": "password123"}
        ).status_code
        == 401
    )
    acceptance = {
        "token": token,
        "password": "Activated-password-729",
        "display_name": "Activated",
        "username": "activated",
    }
    accepted = client.post("/api/v1/auth/invitations/accept", json=acceptance)
    assert accepted.status_code == 200
    assert accepted.json()["platform_admin"] is False
    assert token not in accepted.text and acceptance["password"] not in accepted.text
    assert client.post("/api/v1/auth/invitations/accept", json=acceptance).status_code == 400
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "activated", "password": acceptance["password"]}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/admin/user-invitations", headers=_admin_headers(), json=payload
        ).status_code
        == 409
    )


def test_failed_delivery_can_retry_and_rejects_old_expired_or_disabled_tokens(
    monkeypatch: MonkeyPatch,
) -> None:
    client, session, _ = _client()
    sent: list[dict[str, Any]] = []

    def fail_delivery(**values: Any) -> None:
        sent.append(values)
        raise smtplib.SMTPAuthenticationError(535, b"sensitive-provider-response")

    monkeypatch.setattr("backend.app.messaging.email.service.send_smtp_message", fail_delivery)
    client.put("/api/v1/admin/system/mail", headers=_admin_headers(), json=MAIL_SETTINGS)
    response = client.post(
        "/api/v1/admin/user-invitations",
        headers=_admin_headers(),
        json={"email": "retry@example.com"},
    )
    assert response.status_code == 201 and response.json()["delivery_status"] == "failed"
    assert "sensitive-provider-response" not in response.text
    failed_token = re.search(r"#token=([A-Za-z0-9_-]+)", sent[-1]["body"])
    assert failed_token
    user_id = response.json()["user_id"]
    users = client.get("/api/v1/admin/users?status=invited", headers=_admin_headers()).json()[
        "items"
    ]
    assert users[0]["invitation_delivery_status"] == "failed"
    monkeypatch.setattr(
        "backend.app.messaging.email.service.send_smtp_message",
        lambda **values: sent.append(values),
    )
    resent = client.post(
        f"/api/v1/admin/users/{user_id}/invitation/resend", headers=_admin_headers()
    )
    assert resent.status_code == 200 and resent.json()["delivery_status"] == "sent"
    current_token = re.search(r"#token=([A-Za-z0-9_-]+)", sent[-1]["body"])
    assert current_token and current_token.group(1) != failed_token.group(1)
    acceptance = {
        "token": failed_token.group(1),
        "password": "Accepted-password-42",
        "display_name": "Retry",
        "username": "retry",
    }
    assert client.post("/api/v1/auth/invitations/accept", json=acceptance).status_code == 400
    acceptance["token"] = current_token.group(1)
    record = session.scalar(select(UserInvitation))
    assert record
    record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    assert client.post("/api/v1/auth/invitations/accept", json=acceptance).status_code == 400
    record.expires_at = datetime.now(UTC) + timedelta(hours=1)
    session.commit()
    assert (
        client.put(
            f"/api/v1/admin/users/{user_id}/status",
            headers=_admin_headers(),
            json={"status": "disabled"},
        ).status_code
        == 200
    )
    assert client.post("/api/v1/auth/invitations/accept", json=acceptance).status_code == 400
    enabled = client.put(
        f"/api/v1/admin/users/{user_id}/status", headers=_admin_headers(), json={"status": "active"}
    )
    assert enabled.json()["status"] == "invited"
    assert client.post("/api/v1/auth/invitations/accept", json=acceptance).status_code == 200


def test_mail_routes_keep_admin_boundary_without_parent_router(monkeypatch: MonkeyPatch) -> None:
    client, session, _ = _client()
    client.app.include_router(email_router, prefix="/mail-boundary-probe")
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "backend.app.messaging.email.service.send_smtp_message",
        lambda **values: sent.append(values),
    )
    member = User(
        email="mail-member@example.com",
        display_name="Mail member",
        password_hash=AuthenticationService.hash_password("Member-fixture-password-29"),
        platform_admin=False,
    )
    session.add(member)
    session.commit()
    login = client.post(
        "/api/v1/auth/login",
        json={"email": member.email, "password": "Member-fixture-password-29"},
    )
    assert login.status_code == 200
    member_headers = {"Authorization": f"Bearer {login.json()['token']}"}
    for prefix in ("/api/v1/admin", "/mail-boundary-probe"):
        for headers in ({}, member_headers):
            assert client.get(f"{prefix}/system/mail", headers=headers).status_code == 401
            assert (
                client.put(f"{prefix}/system/mail", headers=headers, json=MAIL_SETTINGS).status_code
                == 401
            )
            assert (
                client.post(
                    f"{prefix}/system/mail/test",
                    headers=headers,
                    json={"email": "recipient@example.com"},
                ).status_code
                == 401
            )
    assert session.get(PlatformMailSettings, 1) is None
    assert sent == []
    allowed = client.get("/mail-boundary-probe/system/mail", headers=_admin_headers())
    assert allowed.status_code == 200
