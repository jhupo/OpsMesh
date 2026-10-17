"""S6 workspace route tests against real PostgreSQL: the 4xx contract.

These tests require OPSMESH_TEST_DATABASE_URL; they are skipped otherwise
and must run against a real PostgreSQL instance (never SQLite). They drive
the assembled HTTP application over the test database to prove that invalid
credential kinds and provider endpoints are refused with the unified error
envelope — a 4xx with a stable code — instead of leaking a server error,
and that valid input still lands.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast
from uuid import UUID

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import opsmesh.bootstrap.api as api_module
from opsmesh.bootstrap.resources import APIResources
from opsmesh.config import Settings
from opsmesh.governance.security_events.service import SecurityEventLog
from opsmesh.identity.auth.service import AuthService
from opsmesh.identity.provisioning import (
    AccountDefaults,
    ProvisionedAccountOpening,
    UserIdentityDirectory,
)
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
    with TestClient(app) as client:
        yield client


def register_account(sessions: sessionmaker, *, email: str) -> UUID:
    """Open one account and return the token its client presents."""
    auth_service = AuthService(
        account_opening=ProvisionedAccountOpening(AccountDefaults()),
        user_directory=UserIdentityDirectory(),
        security_events=SecurityEventLog(),
    )
    with sessions() as session:
        result = auth_service.register(
            session, email=email, display_name="Route", password="route-password-123"
        )
        session.commit()
    return result.token


def create_workspace(client: TestClient, token: UUID) -> str:
    response = client.post(
        "/api/v1/workspaces", json={"name": "Routed"}, headers={SESSION_HEADER: str(token)}
    )
    assert response.status_code == 201, response.text
    return response.json()["workspace_id"]


def create_provider_credential(client: TestClient, token: UUID, workspace_id: str) -> str:
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/credentials",
        json={"name": "provider-key", "kind": "model_provider", "secret": {"api_key": "x"}},
        headers={SESSION_HEADER: str(token)},
    )
    assert response.status_code == 201, response.text
    return response.json()["credential_id"]


def test_unknown_credential_kind_is_refused_with_the_error_envelope(
    app_client, database_sessions
) -> None:
    token = register_account(database_sessions, email="route-kind@example.com")
    workspace_id = create_workspace(app_client, token)
    response = app_client.post(
        f"/api/v1/workspaces/{workspace_id}/credentials",
        json={"name": "provider-key", "kind": "nonsense", "secret": {"api_key": "x"}},
        headers={SESSION_HEADER: str(token)},
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_argument"
    assert payload["error"]["details"]["known_kinds"] == ["model_provider", "capability"]


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.openai.com/v1",
        "https://localhost/v1",
        "https://user:secret@api.openai.com/v1",
    ],
)
def test_rejected_provider_base_url_is_refused_with_the_error_envelope(
    app_client, database_sessions, base_url: str
) -> None:
    token = register_account(database_sessions, email="route-url@example.com")
    workspace_id = create_workspace(app_client, token)
    credential_id = create_provider_credential(app_client, token, workspace_id)
    response = app_client.post(
        f"/api/v1/workspaces/{workspace_id}/model-providers",
        json={
            "name": "openai",
            "credential_id": credential_id,
            "base_url": base_url,
            "default_model": "gpt-5",
            "models": ["gpt-5"],
        },
        headers={SESSION_HEADER: str(token)},
    )
    assert response.status_code == 400, base_url
    payload = response.json()
    assert payload["error"]["code"] == "invalid_argument", base_url
    assert payload["error"]["message"] == "The provider base URL is not allowed", base_url


def test_rejected_provider_base_url_update_is_refused_with_the_error_envelope(
    app_client, database_sessions
) -> None:
    token = register_account(database_sessions, email="route-url-update@example.com")
    workspace_id = create_workspace(app_client, token)
    credential_id = create_provider_credential(app_client, token, workspace_id)
    created = app_client.post(
        f"/api/v1/workspaces/{workspace_id}/model-providers",
        json={
            "name": "openai",
            "credential_id": credential_id,
            "default_model": "gpt-5",
            "models": ["gpt-5"],
        },
        headers={SESSION_HEADER: str(token)},
    )
    assert created.status_code == 201, created.text
    connection_id = created.json()["connection_id"]
    response = app_client.patch(
        f"/api/v1/workspaces/{workspace_id}/model-providers/{connection_id}",
        json={"base_url": "http://api.openai.com/v1"},
        headers={SESSION_HEADER: str(token)},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_argument"


def test_https_provider_base_url_is_accepted(app_client, database_sessions) -> None:
    """The conversion at the service edge must not refuse valid endpoints."""
    token = register_account(database_sessions, email="route-url-ok@example.com")
    workspace_id = create_workspace(app_client, token)
    credential_id = create_provider_credential(app_client, token, workspace_id)
    response = app_client.post(
        f"/api/v1/workspaces/{workspace_id}/model-providers",
        json={
            "name": "openai",
            "credential_id": credential_id,
            "base_url": "https://api.openai.com/v1",
            "default_model": "gpt-5",
            "models": ["gpt-5"],
        },
        headers={SESSION_HEADER: str(token)},
    )
    assert response.status_code == 201, response.text
    assert response.json()["base_url"] == "https://api.openai.com/v1"
