"""Real PG authorization, policy replacement and immutable governance evidence."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from opsmesh.identity.users.models import UserProfile
from opsmesh.platform.policies import service as policies
from opsmesh.platform.policies.models import PlatformPolicyRevision
from opsmesh.platform.policies.schemas import PolicyWrite
from opsmesh.shared.errors import ConflictError, ForbiddenError
from opsmesh.shared.policies import enforce_operation_policy
from tests.execution.test_session_routes import app_client as app_client
from tests.execution.test_session_routes import register_account
from tests.platform.test_audit_approvals_postgres import Subject
from tests.platform.test_sdk_http import live_api as live_api


@pytest.fixture
def principals(database_sessions):
    admin = register_account(database_sessions, email="policy-admin@example.com")
    member = register_account(database_sessions, email="policy-member@example.com")
    with database_sessions() as session, session.begin():
        user = session.scalar(
            select(UserProfile).where(UserProfile.email == "policy-admin@example.com")
        )
        user.platform_roles = ["admin"]
    return {"X-Session-ID": str(admin)}, {"X-Session-ID": str(member)}


def test_policy_admin_cas_and_nonexecutable_rules(app_client, principals, database_sessions):
    admin, member = principals
    body = {"expected_revision": 0, "policy": {"default": "deny"}}
    endpoint = "/api/v1/platform/policies/commands"
    assert app_client.put(endpoint, headers=member, json=body).status_code == 403
    first = app_client.put(endpoint, headers=admin, json=body)
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == 1
    assert app_client.put(endpoint, headers=admin, json=body).status_code == 409
    assert app_client.post(
        "/api/v1/platform/policies/evaluate",
        headers=admin,
        json={"action": "tool.invoke", "name": "write"},
    ).json() == {"decision": "deny"}
    assert (
        app_client.put(
            endpoint,
            headers=admin,
            json={
                "expected_revision": 1,
                "policy": {"rules": [{"expression": "__import__('os')"}]},
            },
        ).status_code
        == 422
    )
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(PlatformPolicyRevision)) == 1


def test_concurrent_policy_replacement_has_one_winner(database_sessions):
    barrier = Barrier(2)
    subject = Subject(uuid4(), admin=True)

    def replace():
        barrier.wait(timeout=10)
        try:
            with database_sessions() as session, session.begin():
                policies.replace_policy(
                    session,
                    key="concurrent",
                    subject=subject,
                    payload=PolicyWrite(expected_revision=0, policy={"default": "deny"}),
                )
            return "committed"
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: replace(), range(2))) == ["committed", "conflict"]


def test_current_policy_approval_and_revocation(database_sessions):
    subject = Subject(uuid4(), admin=True)
    with database_sessions() as session, session.begin():
        policies.replace_policy(
            session,
            key="tools",
            subject=subject,
            payload=PolicyWrite(expected_revision=0, policy={"default": "require_approval"}),
        )
    with database_sessions() as session:
        with pytest.raises(ForbiddenError, match="approval path"):
            enforce_operation_policy(session, action="tool.invoke", name="write")
        enforce_operation_policy(
            session, action="tool.invoke", name="write", approval_configured=True
        )
        with database_sessions() as other, other.begin():
            policies.replace_policy(
                other,
                key="tools",
                subject=subject,
                payload=PolicyWrite(expected_revision=1, policy={"default": "deny"}),
            )
        with pytest.raises(ForbiddenError, match="denies"):
            enforce_operation_policy(
                session, action="tool.invoke", name="write", approval_configured=True
            )
