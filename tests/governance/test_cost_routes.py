"""S6.5 governance cost route tests against real PostgreSQL: the 4xx contract.

These tests require OPSMESH_TEST_DATABASE_URL; they are skipped otherwise
and must run against a real PostgreSQL instance (never SQLite). They drive
the assembled HTTP application over the test database to prove the split
authorization of the cost surface: pricing is platform administration, a
workspace budget and its usage belong to that workspace's own owner or
admin, and a platform administrator gains no tenant data permission from
the pricing surface alone.
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
from opsmesh.governance.audit.service import AuditActor
from opsmesh.governance.security_events.service import SecurityEventLog
from opsmesh.identity.access import PlatformRole
from opsmesh.identity.auth.service import AuthService
from opsmesh.identity.provisioning import (
    AccountDefaults,
    ProvisionedAccountOpening,
    UserIdentityDirectory,
)
from opsmesh.identity.users import service as users
from opsmesh.shared.security.rate_limits import FixedWindowRateLimiter
from tests.conftest import build_test_settings

SESSION_HEADER = "X-Session-ID"
MEMBER_ROLES = frozenset({str(PlatformRole.MEMBER)})
ADMIN_ROLES = frozenset({str(PlatformRole.ADMIN), str(PlatformRole.MEMBER)})
MODEL_SUBJECT = "gpt-route-model"


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


def register_account(
    sessions: sessionmaker, *, email: str, roles: frozenset[str] = MEMBER_ROLES
) -> UUID:
    """Open one account and return the session token its client presents."""
    auth_service = AuthService(
        account_opening=ProvisionedAccountOpening(AccountDefaults()),
        user_directory=UserIdentityDirectory(),
        security_events=SecurityEventLog(),
    )
    with sessions() as session, session.begin():
        result = auth_service.register(
            session, email=email, display_name="Route", password="route-password-123"
        )
    if roles != MEMBER_ROLES:
        with sessions() as session, session.begin():
            users.set_platform_roles(
                session,
                user_id=result.user.user_id,
                roles=roles,
                actor=AuditActor(actor_type="platform"),
            )
    return result.token


def create_workspace(client: TestClient, token: UUID) -> str:
    response = client.post(
        "/api/v1/workspaces", json={"name": "Routed"}, headers={SESSION_HEADER: str(token)}
    )
    assert response.status_code == 201, response.text
    return response.json()["workspace_id"]


def pricing_payload() -> dict[str, object]:
    return {
        "subject_kind": "model",
        "subject_key": MODEL_SUBJECT,
        "prices": {
            "currency": "USD",
            "input_price": "0.001",
            "output_price": "0.002",
            "cached_input_price": "0.0001",
        },
    }


def budget_url(workspace_id: str) -> str:
    return f"/api/v1/governance/costs/workspaces/{workspace_id}/budget"


def test_a_platform_admin_publishes_and_lists_pricing(app_client, database_sessions) -> None:
    token = register_account(
        database_sessions, email="cost-admin@example.com", roles=ADMIN_ROLES
    )
    published = app_client.post(
        "/api/v1/governance/costs/pricing/versions",
        json=pricing_payload(),
        headers={SESSION_HEADER: str(token)},
    )
    assert published.status_code == 201, published.text
    body = published.json()
    assert body["version"] == 1
    assert body["subject_key"] == MODEL_SUBJECT
    assert body["status"] == "active"
    listed = app_client.get(
        "/api/v1/governance/costs/pricing/versions",
        headers={SESSION_HEADER: str(token)},
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1


def test_a_member_cannot_publish_pricing(app_client, database_sessions) -> None:
    token = register_account(
        database_sessions, email="cost-member@example.com", roles=MEMBER_ROLES
    )
    response = app_client.post(
        "/api/v1/governance/costs/pricing/versions",
        json=pricing_payload(),
        headers={SESSION_HEADER: str(token)},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_a_platform_admin_gets_no_tenant_budget_permission(app_client, database_sessions) -> None:
    """The pricing surface grants no workspace data permission by itself."""
    owner_token = register_account(database_sessions, email="cost-owner@example.com")
    admin_token = register_account(
        database_sessions, email="cost-platform@example.com", roles=ADMIN_ROLES
    )
    workspace_id = create_workspace(app_client, owner_token)
    response = app_client.get(
        f"{budget_url(workspace_id)}?currency=USD",
        headers={SESSION_HEADER: str(admin_token)},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_the_workspace_owner_upserts_and_reads_the_budget(app_client, database_sessions) -> None:
    token = register_account(database_sessions, email="budget-owner@example.com")
    workspace_id = create_workspace(app_client, token)
    unconfigured = app_client.get(
        f"{budget_url(workspace_id)}?currency=USD", headers={SESSION_HEADER: str(token)}
    )
    assert unconfigured.status_code == 404
    assert unconfigured.json()["error"]["code"] == "not_found"
    upserted = app_client.put(
        budget_url(workspace_id),
        json={"currency": "USD", "limit_amount": "1.5", "period": "monthly"},
        headers={SESSION_HEADER: str(token)},
    )
    assert upserted.status_code == 200, upserted.text
    body = upserted.json()
    assert body["budget_state"] == "limited"
    assert body["revision"] == 1
    assert body["enforcement"] == "block"
    read = app_client.get(
        f"{budget_url(workspace_id)}?currency=USD", headers={SESSION_HEADER: str(token)}
    )
    assert read.status_code == 200, read.text
    assert read.json()["budget_state"] == "limited"


def test_a_stranger_cannot_touch_the_budget_scope(app_client, database_sessions) -> None:
    owner_token = register_account(database_sessions, email="scope-owner@example.com")
    stranger_token = register_account(database_sessions, email="scope-stranger@example.com")
    workspace_id = create_workspace(app_client, owner_token)
    payload = {"currency": "USD", "unlimited": True}
    upsert = app_client.put(
        budget_url(workspace_id), json=payload, headers={SESSION_HEADER: str(stranger_token)}
    )
    assert upsert.status_code == 403
    for path in (
        f"{budget_url(workspace_id)}?currency=USD",
        f"/api/v1/governance/costs/workspaces/{workspace_id}/usage",
        f"/api/v1/governance/costs/workspaces/{workspace_id}/reservations",
    ):
        response = app_client.get(path, headers={SESSION_HEADER: str(stranger_token)})
        assert response.status_code == 403, path
        assert response.json()["error"]["code"] == "forbidden"


def test_a_budget_declaring_both_limit_and_unlimited_is_refused(
    app_client, database_sessions
) -> None:
    token = register_account(database_sessions, email="budget-both@example.com")
    workspace_id = create_workspace(app_client, token)
    response = app_client.put(
        budget_url(workspace_id),
        json={"currency": "USD", "limit_amount": "1.5", "unlimited": True},
        headers={SESSION_HEADER: str(token)},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_the_owner_lists_the_empty_usage_and_reservation_pages(
    app_client, database_sessions
) -> None:
    token = register_account(database_sessions, email="budget-lists@example.com")
    workspace_id = create_workspace(app_client, token)
    for path in (
        f"/api/v1/governance/costs/workspaces/{workspace_id}/usage",
        f"/api/v1/governance/costs/workspaces/{workspace_id}/reservations",
    ):
        response = app_client.get(path, headers={SESSION_HEADER: str(token)})
        assert response.status_code == 200, path
        body = response.json()
        assert body["total"] == 0
