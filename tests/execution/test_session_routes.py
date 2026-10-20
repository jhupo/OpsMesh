"""Session route tests against real PostgreSQL: the V4 client surface.

These tests require OPSMESH_TEST_DATABASE_URL; they are skipped otherwise
and must run against a real PostgreSQL instance (never SQLite). They drive
the assembled HTTP application over the test database through the exact
client surface V4 defines for model sessions — create, get, close,
delete and the four history operations — including the visibility rule,
the idempotency replay over HTTP, and the 503 an unassembled gateway
answers.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from typing import cast
from uuid import UUID

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import opsmesh.bootstrap.api as api_module
from opsmesh.bootstrap.accounts import AccountResources
from opsmesh.bootstrap.connections import APIResources
from opsmesh.config import Settings
from opsmesh.identity.auth.service import AuthService
from opsmesh.identity.provisioning import (
    AccountDefaults,
    ProvisionedAccountOpening,
    UserIdentityDirectory,
)
from opsmesh.orchestration.runs.models import Run
from opsmesh.platform.audit.service import SecurityAuditRecorder
from opsmesh.shared.security.rate_limits import FixedWindowRateLimiter
from tests.conftest import build_test_settings

SESSION_HEADER = "X-Session-ID"


def fake_api_resources(app_settings: Settings) -> APIResources:
    """Replace only Redis; the database under test stays real."""
    redis_client = fakeredis.FakeStrictRedis(decode_responses=True)
    return APIResources(
        redis_client=cast("object", redis_client),
        rate_limiter=FixedWindowRateLimiter.from_redis(
            redis_client, key_prefix=app_settings.redis_key_prefix
        ),
    )


@pytest.fixture
def app_client(
    database_url: str, database_sessions, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """The assembled application over the test database, faking only Redis."""
    monkeypatch.setattr(api_module, "build_api_resources", fake_api_resources)
    app = api_module.create_app(build_test_settings(database_url))
    options = {"loop_factory": asyncio.SelectorEventLoop} if sys.platform == "win32" else {}
    with TestClient(app, backend_options=options) as client:
        yield client


def register_account(sessions: sessionmaker, *, email: str) -> UUID:
    """Open one account and return the token its client presents."""
    auth_service = AuthService(
        account_opening=ProvisionedAccountOpening(
            AccountDefaults(), provision_resources=AccountResources(Settings())
        ),
        user_directory=UserIdentityDirectory(),
        security_events=SecurityAuditRecorder(),
    )
    with sessions() as session:
        result = auth_service.register(
            session, email=email, display_name="Route", password="route-password-123"
        )
        session.commit()
    return result.token


def create_session(client: TestClient, token: UUID) -> str:
    response = client.post(
        "/api/v1/orchestration/sessions", json={}, headers={SESSION_HEADER: str(token)}
    )
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def append_items(client: TestClient, token: UUID, session_id: str, *, key: str) -> dict:
    response = client.post(
        f"/api/v1/orchestration/sessions/{session_id}/history/items",
        params={"idempotency_key": key},
        json={"items": [{"role": "user", "content": "hello"}]},
        headers={SESSION_HEADER: str(token)},
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestSessionRoutes:
    def test_create_returns_the_session_view(self, app_client, database_sessions) -> None:
        token = register_account(database_sessions, email="session-create@example.com")
        response = app_client.post(
            "/api/v1/orchestration/sessions", json={}, headers={SESSION_HEADER: str(token)}
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["status"] == "open"
        assert UUID(payload["principal_id"])
        assert UUID(payload["scope_id"]) == UUID(payload["principal_id"])
        assert payload["owner_ref"] == f"user:{payload['principal_id']}"

    def test_get_reads_one_visible_session(self, app_client, database_sessions) -> None:
        token = register_account(database_sessions, email="session-get@example.com")
        session_id = create_session(app_client, token)
        response = app_client.get(
            f"/api/v1/orchestration/sessions/{session_id}", headers={SESSION_HEADER: str(token)}
        )
        assert response.status_code == 200, response.text
        assert response.json()["session_id"] == session_id

    def test_an_unknown_session_is_not_found(self, app_client, database_sessions) -> None:
        token = register_account(database_sessions, email="session-missing@example.com")
        response = app_client.get(
            f"/api/v1/orchestration/sessions/{UUID(int=99)}", headers={SESSION_HEADER: str(token)}
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_another_principals_session_is_invisible(self, app_client, database_sessions) -> None:
        owner = register_account(database_sessions, email="session-owner@example.com")
        stranger = register_account(database_sessions, email="session-stranger@example.com")
        session_id = create_session(app_client, owner)
        response = app_client.get(
            f"/api/v1/orchestration/sessions/{session_id}",
            headers={SESSION_HEADER: str(stranger)},
        )
        assert response.status_code == 404

    def test_close_then_delete_retires_the_session(self, app_client, database_sessions) -> None:
        token = register_account(database_sessions, email="session-close@example.com")
        session_id = create_session(app_client, token)
        closed = app_client.post(
            f"/api/v1/orchestration/sessions/{session_id}/close",
            headers={SESSION_HEADER: str(token)},
        )
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "closed"
        deleted = app_client.delete(
            f"/api/v1/orchestration/sessions/{session_id}", headers={SESSION_HEADER: str(token)}
        )
        assert deleted.status_code == 204
        again = app_client.get(
            f"/api/v1/orchestration/sessions/{session_id}", headers={SESSION_HEADER: str(token)}
        )
        assert again.status_code == 404

    def test_an_open_session_refuses_to_be_deleted(self, app_client, database_sessions) -> None:
        token = register_account(database_sessions, email="session-delete@example.com")
        session_id = create_session(app_client, token)
        response = app_client.delete(
            f"/api/v1/orchestration/sessions/{session_id}", headers={SESSION_HEADER: str(token)}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "session_not_closed"

    def test_a_request_without_a_session_is_unauthorized(self, app_client) -> None:
        response = app_client.post("/api/v1/orchestration/sessions", json={})
        assert response.status_code == 401


class TestHistoryRoutes:
    def test_append_then_list_returns_the_items(self, app_client, database_sessions) -> None:
        token = register_account(database_sessions, email="history-append@example.com")
        session_id = create_session(app_client, token)
        stored = append_items(app_client, token, session_id, key="append-1")
        assert stored == {"appended": 1}
        listed = app_client.get(
            f"/api/v1/orchestration/sessions/{session_id}/history",
            headers={SESSION_HEADER: str(token)},
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"] == [{"role": "user", "content": "hello"}]

    def test_a_replayed_append_does_not_store_twice(self, app_client, database_sessions) -> None:
        token = register_account(database_sessions, email="history-replay@example.com")
        session_id = create_session(app_client, token)
        assert append_items(app_client, token, session_id, key="append-1") == {"appended": 1}
        assert append_items(app_client, token, session_id, key="append-1") == {"appended": 1}
        listed = app_client.get(
            f"/api/v1/orchestration/sessions/{session_id}/history",
            headers={SESSION_HEADER: str(token)},
        )
        assert listed.json()["items"] == [{"role": "user", "content": "hello"}]

    def test_a_key_reused_with_other_items_conflicts(self, app_client, database_sessions) -> None:
        token = register_account(database_sessions, email="history-reuse@example.com")
        session_id = create_session(app_client, token)
        append_items(app_client, token, session_id, key="append-1")
        response = app_client.post(
            f"/api/v1/orchestration/sessions/{session_id}/history/items",
            params={"idempotency_key": "append-1"},
            json={"items": [{"role": "user", "content": "other"}]},
            headers={SESSION_HEADER: str(token)},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "idempotency_key_reused"

    def test_a_mutation_without_an_idempotency_key_is_refused(
        self, app_client, database_sessions
    ) -> None:
        token = register_account(database_sessions, email="history-nokey@example.com")
        session_id = create_session(app_client, token)
        response = app_client.post(
            f"/api/v1/orchestration/sessions/{session_id}/history/items",
            json={"items": [{"role": "user", "content": "hello"}]},
            headers={SESSION_HEADER: str(token)},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    def test_pop_removes_the_newest_item(self, app_client, database_sessions) -> None:
        token = register_account(database_sessions, email="history-pop@example.com")
        session_id = create_session(app_client, token)
        append_items(app_client, token, session_id, key="append-1")
        append_items(app_client, token, session_id, key="append-2")
        popped = app_client.post(
            f"/api/v1/orchestration/sessions/{session_id}/history/pop",
            params={"idempotency_key": "pop-1"},
            headers={SESSION_HEADER: str(token)},
        )
        assert popped.status_code == 200, popped.text
        assert popped.json() == {"item": {"role": "user", "content": "hello"}}
        listed = app_client.get(
            f"/api/v1/orchestration/sessions/{session_id}/history",
            headers={SESSION_HEADER: str(token)},
        )
        assert listed.json()["items"] == [{"role": "user", "content": "hello"}]

    def test_clear_empties_the_history(self, app_client, database_sessions) -> None:
        token = register_account(database_sessions, email="history-clear@example.com")
        session_id = create_session(app_client, token)
        append_items(app_client, token, session_id, key="append-1")
        cleared = app_client.post(
            f"/api/v1/orchestration/sessions/{session_id}/history/clear",
            params={"idempotency_key": "clear-1"},
            headers={SESSION_HEADER: str(token)},
        )
        assert cleared.status_code == 204
        listed = app_client.get(
            f"/api/v1/orchestration/sessions/{session_id}/history",
            headers={SESSION_HEADER: str(token)},
        )
        assert listed.json()["items"] == []

    @pytest.mark.parametrize("status", ["queued", "running", "waiting_approval", "needs_attention"])
    def test_reset_refuses_unfinished_tasks(self, app_client, database_sessions, status) -> None:
        token = register_account(database_sessions, email="history-busy@example.com")
        session_id = create_session(app_client, token)
        append_items(app_client, token, session_id, key="before-run")
        queued = app_client.post(
            "/api/v1/orchestration/runs",
            params={"idempotency_key": "queued"},
            json={
                "session_id": session_id,
                "input": "Analyze logs",
                "entry_agent": "main",
                "agents": {
                    "main": {
                        "name": "Analyst",
                        "instructions": "Analyze logs",
                        "model": {"connection_id": str(UUID(int=42)), "name": "test"},
                    }
                },
            },
            headers={SESSION_HEADER: str(token)},
        )
        assert queued.status_code == 202, queued.text
        with database_sessions() as session, session.begin():
            session.get(Run, UUID(queued.json()["run_id"])).status = status
        cleared = app_client.post(
            f"/api/v1/orchestration/sessions/{session_id}/history/clear",
            params={"idempotency_key": "reset-busy"},
            headers={SESSION_HEADER: str(token)},
        )
        assert cleared.status_code == 409, cleared.text
        assert cleared.json()["error"]["code"] == "session_has_active_runs"
        listed = app_client.get(
            f"/api/v1/orchestration/sessions/{session_id}/history",
            headers={SESSION_HEADER: str(token)},
        )
        assert listed.json()["items"] == [{"role": "user", "content": "hello"}]

    def test_a_closed_session_refuses_a_mutation(self, app_client, database_sessions) -> None:
        token = register_account(database_sessions, email="history-closed@example.com")
        session_id = create_session(app_client, token)
        app_client.post(
            f"/api/v1/orchestration/sessions/{session_id}/close",
            headers={SESSION_HEADER: str(token)},
        )
        response = app_client.post(
            f"/api/v1/orchestration/sessions/{session_id}/history/items",
            params={"idempotency_key": "append-1"},
            json={"items": [{"role": "user", "content": "hello"}]},
            headers={SESSION_HEADER: str(token)},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "session_not_open"

    def test_an_unassembled_gateway_answers_503(
        self, app_client, database_sessions, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = register_account(database_sessions, email="history-gateway@example.com")
        session_id = create_session(app_client, token)
        monkeypatch.setattr(app_client.app.state, "history_gateway", None)
        response = app_client.get(
            f"/api/v1/orchestration/sessions/{session_id}/history",
            headers={SESSION_HEADER: str(token)},
        )
        assert response.status_code == 503
