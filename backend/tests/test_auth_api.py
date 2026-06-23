from collections.abc import Generator
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import Settings, get_settings
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User, UserAPIToken
from backend.app.main import create_app
from backend.app.security.models import SecurityEvent

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
    assert stored.password_hash not in response.text
    assert "correct horse battery staple" not in response.text


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
    assert revoked.json()["revoked"] == 2
    first_token_me = client.get("/api/v1/auth/me", headers=_user_token_headers(first_token))
    second_token_me = client.get("/api/v1/auth/me", headers=_user_token_headers(second_token))

    assert first_token_me.status_code == 401
    assert second_token_me.status_code == 401


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
