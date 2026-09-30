from uuid import UUID

from test_admin_api import _admin_headers, _client

from backend.app.observability.audit.security_models import SecurityEvent


def test_repeated_admin_password_resets_revoke_previous_credentials() -> None:
    client, session, _ = _client()
    created = client.post(
        "/api/v1/admin/users",
        headers=_admin_headers(),
        json={
            "email": "repeat@example.com",
            "display_name": "Reset User",
            "username": "reset-user",
        },
    )
    assert created.status_code == 201
    user_id = created.json()["id"]
    previous_password = created.json()["initial_password"]
    previous_login = client.post(
        "/api/v1/auth/login",
        json={"username": "reset-user", "password": previous_password},
    )
    assert previous_login.status_code == 200
    for identifier in ({"email": "repeat@example.com"}, {"username": "reset-user"}):
        previous_headers = {"Authorization": f"Bearer {previous_login.json()['token']}"}
        denied = client.post(
            f"/api/v1/admin/users/{user_id}/reset-password", headers=previous_headers
        )
        assert denied.status_code == 401
        reset = client.post(
            f"/api/v1/admin/users/{user_id}/reset-password", headers=_admin_headers()
        )
        assert reset.status_code == 200
        new_password = reset.json()["temporary_password"]
        assert new_password != previous_password
        assert client.get("/api/v1/auth/me", headers=previous_headers).status_code == 401
        assert client.post(
            "/api/v1/auth/login", json={**identifier, "password": previous_password}
        ).status_code == 401
        previous_login = client.post(
            "/api/v1/auth/login", json={**identifier, "password": new_password}
        )
        assert previous_login.status_code == 200
        assert previous_login.json()["user_id"] == user_id
        previous_password = new_password
    events = session.query(SecurityEvent).filter_by(
        action="identity.user_password_reset", user_id=UUID(user_id)
    ).all()
    assert len(events) == 2
    assert all(previous_password not in str(event.event_metadata) for event in events)
