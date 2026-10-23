"""Updating concurrency does not require an unassembled scheduler callback."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from opsmesh.shared.http.dependencies import get_db_session
from opsmesh.users.access import CurrentUser
from opsmesh.users.auth.dependencies import current_user
from opsmesh.users.quotas import routes


def test_administrator_can_update_concurrency_without_notifier(monkeypatch):
    user_id = uuid4()
    admin = CurrentUser(uuid4(), "admin@example.test", "Admin", "active", frozenset({"admin"}))
    session = Mock()
    snapshot = SimpleNamespace(kind="concurrency", limit_value=4, reserved_value=0)
    update = Mock()
    monkeypatch.setattr(routes.service, "set_capacity_limit", update)
    monkeypatch.setattr(routes.service, "read_capacity", lambda *args, **kwargs: [snapshot])
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[current_user] = lambda: admin
    app.dependency_overrides[get_db_session] = lambda: session
    with TestClient(app) as client:
        response = client.put(f"/users/{user_id}/capacity/concurrency", json={"limit_value": 4})
    assert response.status_code == 200
    assert response.json()["kinds"][0]["limit_value"] == 4
    update.assert_called_once_with(
        session, actor=admin, user_id=user_id, kind="concurrency", limit_value=4
    )
    session.commit.assert_called_once_with()
