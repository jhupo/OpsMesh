from collections.abc import Generator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.auth.errors import AuthenticationError
from backend.app.auth.service import AuthorizationService
from backend.app.core.config import Settings, get_settings
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User, UserAPIToken
from backend.app.main import create_app
from backend.app.security.models import SecurityEvent
from backend.app.workspaces.models import Workspace, WorkspaceMember

INTERNAL_TOKEN = "test-internal-token"


def test_register_creates_user_with_password_hash_and_does_not_leak_hash() -> None:
    client, session = _client()

    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "  New.User@Example.COM ",
            "display_name": "New User",
            "password": "correct horse battery staple",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new.user@example.com"
    assert body["display_name"] == "New User"
    assert "password_hash" not in body
    stored = session.scalar(select(User).where(User.email == "new.user@example.com"))
    assert stored is not None
    assert stored.password_hash is not None
    assert stored.password_hash != "correct horse battery staple"
    assert stored.password_hash.startswith("$argon2id$")
    assert stored.password_hash not in response.text
    assert "correct horse battery staple" not in response.text


def test_password_verifier_rejects_removed_pbkdf2_format() -> None:
    legacy_hash = "pbkdf2_sha256$260000$c2FsdA==$ZGlnZXN0"

    assert AuthorizationService.verify_password("password", legacy_hash) is False


def test_missing_user_login_still_runs_password_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, session = _client()
    calls: list[tuple[str, str]] = []

    def record_verification(password: str, encoded_hash: str) -> bool:
        calls.append((password, encoded_hash))
        return False

    monkeypatch.setattr(
        AuthorizationService,
        "verify_password",
        staticmethod(record_verification),
    )

    with pytest.raises(AuthenticationError, match="Invalid email or password"):
        AuthorizationService(session).login_with_password(
            email="missing@example.com",
            password="wrong-password",
            settings=Settings(environment="test"),
        )

    assert len(calls) == 1
    assert calls[0][0] == "wrong-password"
    assert calls[0][1].startswith("$argon2id$")


def test_register_rejects_duplicate_email() -> None:
    client, _ = _client()
    payload = {
        "email": "duplicate@example.com",
        "display_name": "Duplicate",
        "password": "correct horse battery staple",
    }
    created = client.post("/api/v1/auth/register", json=payload)

    duplicate = client.post(
        "/api/v1/auth/register",
        json={**payload, "email": "DUPLICATE@example.com"},
    )

    assert created.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["message"] == "Email is already registered"


def test_password_login_issues_user_token_and_auth_me_accepts_it() -> None:
    client, session = _client()
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "email": "login@example.com",
            "display_name": "Login User",
            "password": "correct horse battery staple",
        },
    )
    assert registered.status_code == 201

    logged_in = client.post(
        "/api/v1/auth/login",
        json={"email": "login@example.com", "password": "correct horse battery staple"},
    )

    assert logged_in.status_code == 200
    body = logged_in.json()
    raw_token = body["token"]
    assert raw_token.startswith("ccut_")
    assert "token_hash" not in body
    assert "password_hash" not in body
    stored_token = session.get(UserAPIToken, UUID(body["id"]))
    assert stored_token is not None
    assert stored_token.token_hash != raw_token
    assert stored_token.token_hash not in logged_in.text

    me = client.get("/api/v1/auth/me", headers=_user_token_headers(raw_token))

    assert me.status_code == 200
    assert me.json() == registered.json()


def test_password_login_rejects_invalid_credentials() -> None:
    client, _ = _client()
    client.post(
        "/api/v1/auth/register",
        json={
            "email": "bad-login@example.com",
            "display_name": "Bad Login",
            "password": "correct horse battery staple",
        },
    )

    wrong_password = client.post(
        "/api/v1/auth/login",
        json={"email": "bad-login@example.com", "password": "wrong-password"},
    )
    missing_user = client.post(
        "/api/v1/auth/login",
        json={"email": "missing@example.com", "password": "wrong-password"},
    )

    assert wrong_password.status_code == 401
    assert missing_user.status_code == 401
    assert "token" not in wrong_password.text
    assert "password_hash" not in wrong_password.text


def test_current_user_can_change_password_and_revoke_all_tokens() -> None:
    client, _ = _client()
    client.post(
        "/api/v1/auth/register",
        json={
            "email": "rotate@example.com",
            "display_name": "Rotate User",
            "password": "old password value",
        },
    )
    first_login = client.post(
        "/api/v1/auth/login",
        json={"email": "rotate@example.com", "password": "old password value"},
    )
    assert first_login.status_code == 200
    first_token = first_login.json()["token"]

    changed = client.put(
        "/api/v1/auth/password",
        headers=_user_token_headers(first_token),
        json={
            "current_password": "old password value",
            "new_password": "new password value",
        },
    )

    assert changed.status_code == 200
    expired_session = client.get(
        "/api/v1/auth/me",
        headers=_user_token_headers(first_token),
    )
    assert expired_session.status_code == 401
    old_password = client.post(
        "/api/v1/auth/login",
        json={"email": "rotate@example.com", "password": "old password value"},
    )
    new_password = client.post(
        "/api/v1/auth/login",
        json={"email": "rotate@example.com", "password": "new password value"},
    )
    assert old_password.status_code == 401
    assert new_password.status_code == 200
    second_token = new_password.json()["token"]

    revoked = client.delete("/api/v1/auth/tokens", headers=_user_token_headers(second_token))

    assert revoked.status_code == 200
    assert revoked.json()["revoked"] == 1
    first_token_me = client.get("/api/v1/auth/me", headers=_user_token_headers(first_token))
    second_token_me = client.get("/api/v1/auth/me", headers=_user_token_headers(second_token))

    assert first_token_me.status_code == 401
    assert second_token_me.status_code == 401


def test_profile_update_and_token_rotation_invalidate_the_old_token() -> None:
    client, session = _client()
    user = User(email="profile@example.com", display_name="Before")
    session.add(user)
    session.commit()
    created = client.post(
        "/api/v1/auth/tokens",
        headers=_internal_headers(user.id),
        json={"name": "rotating token"},
    )
    old_token = created.json()["token"]
    token_id = created.json()["id"]

    updated = client.patch(
        "/api/v1/auth/me",
        headers=_user_token_headers(old_token),
        json={"display_name": "After"},
    )
    rotated = client.post(
        f"/api/v1/auth/tokens/{token_id}/rotate",
        headers=_user_token_headers(old_token),
        json={"name": "replacement token"},
    )

    assert updated.status_code == 200
    assert updated.json()["display_name"] == "After"
    assert rotated.status_code == 200
    new_token = rotated.json()["token"]
    assert rotated.json()["name"] == "replacement token"
    assert client.get("/api/v1/auth/me", headers=_user_token_headers(old_token)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=_user_token_headers(new_token)).status_code == 200


def test_platform_admin_disables_user_and_revokes_active_tokens() -> None:
    client, session = _client()
    user = User(email="managed@example.com", display_name="Managed")
    session.add(user)
    session.commit()
    created = client.post(
        "/api/v1/auth/tokens",
        headers=_internal_headers(user.id),
        json={"name": "managed token"},
    )
    raw_token = created.json()["token"]

    disabled = client.put(
        f"/api/v1/admin/users/{user.id}/status",
        headers={"Authorization": "Bearer platform-admin"},
        json={"status": "disabled"},
    )
    rejected = client.get("/api/v1/auth/me", headers=_user_token_headers(raw_token))
    reenabled = client.put(
        f"/api/v1/admin/users/{user.id}/status",
        headers={"Authorization": "Bearer platform-admin"},
        json={"status": "active"},
    )

    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert rejected.status_code == 401
    assert reenabled.status_code == 200
    assert client.get("/api/v1/auth/me", headers=_user_token_headers(raw_token)).status_code == 401


def test_user_token_api_creates_lists_and_revokes_bearer_tokens() -> None:
    client, session = _client()
    user = User(email="api-token-owner@example.com", display_name="API Token Owner")
    session.add(user)
    session.commit()

    created = client.post(
        "/api/v1/auth/tokens",
        headers=_internal_headers(user.id),
        json={"name": "local dev cli"},
    )

    assert created.status_code == 201
    body = created.json()
    raw_token = body["token"]
    assert raw_token.startswith("ccut_")
    assert body["fingerprint"].startswith("sha256:")
    stored = session.get(UserAPIToken, UUID(body["id"]))
    assert stored is not None
    assert stored.token_hash != raw_token
    assert stored.fingerprint == body["fingerprint"]

    listed = client.get("/api/v1/auth/tokens", headers=_user_token_headers(raw_token))

    assert listed.status_code == 200
    list_body = listed.json()
    assert len(list_body) == 1
    assert "token" not in list_body[0]
    assert list_body[0]["id"] == body["id"]
    assert raw_token not in str(list_body)

    revoked = client.delete(
        f"/api/v1/auth/tokens/{body['id']}",
        headers=_user_token_headers(raw_token),
    )

    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    rejected = client.get("/api/v1/auth/tokens", headers=_user_token_headers(raw_token))
    events = session.scalars(
        select(SecurityEvent).where(SecurityEvent.action == "auth.user_token.rejected")
    ).all()
    assert rejected.status_code == 401
    assert events
    assert raw_token not in str(events[0].event_metadata)


def test_auth_me_returns_current_user_for_user_and_internal_tokens() -> None:
    client, session = _client()
    user = User(email="me@example.com", display_name="Me User")
    session.add(user)
    session.commit()
    created = client.post(
        "/api/v1/auth/tokens",
        headers=_internal_headers(user.id),
        json={"name": "me token"},
    )
    assert created.status_code == 201
    raw_token = created.json()["token"]

    via_user_token = client.get("/api/v1/auth/me", headers=_user_token_headers(raw_token))
    via_internal_token = client.get("/api/v1/auth/me", headers=_internal_headers(user.id))

    assert via_user_token.status_code == 200
    assert via_user_token.json() == {
        "user_id": str(user.id),
        "email": "me@example.com",
        "display_name": "Me User",
    }
    assert via_internal_token.status_code == 200
    assert via_internal_token.json() == via_user_token.json()


def test_user_token_api_rejects_token_when_user_is_disabled() -> None:
    client, session = _client()
    user = User(email="disabled-api-token@example.com", display_name="Disabled API")
    session.add(user)
    session.commit()
    created = client.post(
        "/api/v1/auth/tokens",
        headers=_internal_headers(user.id),
        json={"name": "will be disabled"},
    )
    assert created.status_code == 201
    raw_token = created.json()["token"]
    user.status = "disabled"
    session.commit()

    rejected = client.get("/api/v1/auth/tokens", headers=_user_token_headers(raw_token))

    assert rejected.status_code == 401


def test_restricted_user_token_enforces_account_and_workspace_scopes() -> None:
    client, session = _client()
    user = User(email="scoped@example.com", display_name="Scoped User")
    workspace = Workspace(owner=user, name="Allowed", slug="allowed", settings={})
    other_workspace = Workspace(owner=user, name="Other", slug="other", settings={})
    session.add_all(
        [
            user,
            workspace,
            other_workspace,
            WorkspaceMember(workspace=workspace, user=user, role="owner"),
            WorkspaceMember(workspace=other_workspace, user=user, role="owner"),
        ]
    )
    session.commit()
    created = client.post(
        "/api/v1/auth/tokens",
        headers=_internal_headers(user.id),
        json={
            "name": "read only automation",
            "scopes": {
                "workspace_ids": [str(workspace.id)],
                "workspace_actions": ["read"],
                "account_actions": ["profile:read"],
            },
        },
    )

    assert created.status_code == 201
    raw_token = created.json()["token"]
    headers = _user_token_headers(raw_token)
    assert created.json()["scopes"] == {
        "workspace_ids": [str(workspace.id)],
        "workspace_actions": ["read"],
        "account_actions": ["profile:read"],
    }
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    assert client.get("/api/v1/auth/tokens", headers=headers).status_code == 403
    listed = client.get("/api/v1/workspaces", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [str(workspace.id)]
    assert (
        client.post(
            "/api/v1/workspaces",
            headers=headers,
            json={"name": "Forbidden", "slug": "forbidden"},
        ).status_code
        == 403
    )
    assert client.get(f"/api/v1/workspaces/{workspace.id}", headers=headers).status_code == 200
    assert (
        client.get(f"/api/v1/workspaces/{other_workspace.id}", headers=headers).status_code == 403
    )
    assert (
        client.post(
            f"/api/v1/workspaces/{workspace.id}/tasks",
            headers=headers,
            json={"title": "Must be denied"},
        ).status_code
        == 403
    )


def test_restricted_token_cannot_delegate_broader_scope() -> None:
    client, session = _client()
    user = User(email="delegate@example.com", display_name="Delegate User")
    workspace = Workspace(owner=user, name="Delegation", slug="delegation", settings={})
    session.add_all(
        [
            user,
            workspace,
            WorkspaceMember(workspace=workspace, user=user, role="owner"),
        ]
    )
    session.commit()
    parent = client.post(
        "/api/v1/auth/tokens",
        headers=_internal_headers(user.id),
        json={
            "name": "delegating reader",
            "scopes": {
                "workspace_ids": [str(workspace.id)],
                "workspace_actions": ["read"],
                "account_actions": ["tokens:manage"],
            },
        },
    )
    parent_headers = _user_token_headers(parent.json()["token"])

    unrestricted = client.post(
        "/api/v1/auth/tokens",
        headers=parent_headers,
        json={"name": "unrestricted"},
    )
    elevated = client.post(
        "/api/v1/auth/tokens",
        headers=parent_headers,
        json={
            "name": "writer",
            "scopes": {
                "workspace_ids": [str(workspace.id)],
                "workspace_actions": ["write"],
            },
        },
    )
    delegated_read = client.post(
        "/api/v1/auth/tokens",
        headers=parent_headers,
        json={
            "name": "reader",
            "scopes": {
                "workspace_ids": [str(workspace.id)],
                "workspace_actions": ["read"],
            },
        },
    )

    assert unrestricted.status_code == 403
    assert elevated.status_code == 403
    assert delegated_read.status_code == 201


def test_internal_token_with_x_user_id_authenticates_user() -> None:
    client, session = _client()
    user = User(email="internal-auth@example.com", display_name="Internal Auth")
    session.add(user)
    session.commit()

    response = client.get("/api/v1/auth/tokens", headers=_internal_headers(user.id))

    assert response.status_code == 200
    assert response.json() == []


def _client() -> tuple[TestClient, Session]:
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed_session = session_factory()

    app = create_app(
        Settings(
            environment="test",
            log_format="text",
            internal_api_token=INTERNAL_TOKEN,
            platform_admin_token="platform-admin",
            token_hash_pepper="api-test-pepper",
            database_url="sqlite+pysqlite:///:memory:",
        )
    )

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    return TestClient(app), seed_session


def _internal_headers(user_id: object) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {INTERNAL_TOKEN}",
        "X-User-ID": str(user_id),
    }


def _user_token_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
