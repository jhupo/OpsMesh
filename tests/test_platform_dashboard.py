from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.capabilities.marketplace.models import MarketplaceListing, TalentListing
from opsmesh.governance.costs.models import ModelUsageRecord
from opsmesh.governance.security_events.models import SecurityEvent
from opsmesh.identity.auth.service import AuthenticationService
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task
from opsmesh.platform.operations.activities import AdminOperationActivities
from opsmesh.platform.operations.history import PlatformHistoryService
from opsmesh.platform.operations.models import AdminOperationRecord, PlatformMetricSnapshot
from opsmesh.platform.operations.service import AdminOperationService
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.admin_api import TOKEN, _admin_headers, _client, _seed_workspace
from tests.fixtures.execution import test_agent_id as _test_agent_id


def test_platform_execution_summary_preserves_attention_and_failure_products():
    client, session, _ = _client()
    user, workspace = _seed_workspace(session)
    agent_id = _test_agent_id(session, workspace.id)
    session.add_all(
        [
            AgentRun(
                workspace_id=workspace.id, agent_profile_id=agent_id, status="waiting_approval"
            ),
            AgentRun(
                workspace_id=workspace.id,
                agent_profile_id=agent_id,
                status="failed",
                error={"code": "model_unavailable"},
            ),
            Task(
                workspace_id=workspace.id,
                created_by_user_id=user.id,
                title="Blocked task",
                status="waiting_approval",
            ),
            Approval(
                workspace_id=workspace.id,
                approval_type="workflow.node",
                risk_level="high",
                payload={"reason": "Pending approval"},
                created_at=datetime.now(UTC),
            ),
            SecurityEvent(
                workspace_id=workspace.id,
                action="tool.blocked",
                outcome="denied",
                severity="warning",
                reason="tool_policy_denied",
                path="/tool",
                method="POST",
                created_at=datetime.now(UTC),
            ),
        ]
    )
    session.commit()
    path = "/api/v1/admin/operations/summary"
    response = client.get(path, headers=_admin_headers())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["pending_approvals"] == 1
    assert payload["waiting_runs"] == 1
    assert payload["blocked_tasks"] == 1
    assert payload["top_failure_codes"] == [{"code": "model_unavailable", "count": 1}]
    assert payload["top_security_reasons"] == [{"reason": "tool_policy_denied", "count": 1}]
    assert (
        client.get(
            path, headers={"Authorization": f"Bearer {TOKEN}", "X-User-ID": str(user.id)}
        ).status_code
        == 401
    )


def _token(client, session, user, scopes=None):
    created = AuthenticationService(session).create_user_api_token(
        user_id=user.id,
        name="dashboard",
        settings=client.app.state.settings,
        scopes=scopes,
    )
    return {"Authorization": f"Bearer {created.token}"}, created.record


def test_access_context_intersects_role_token_and_workspace_state():
    client, session, _ = _client()
    user, workspace = _seed_workspace(session)
    _, other = _seed_workspace(session, email="other@example.com", slug="other")
    headers, _ = _token(
        client,
        session,
        user,
        {
            "workspace_ids": [str(workspace.id)],
            "workspace_actions": ["read"],
            "account_actions": [],
        },
    )
    path = f"/api/v1/workspaces/{workspace.id}/access/context"
    response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["allowed_actions"] == ["read"]
    assert (
        client.get(f"/api/v1/workspaces/{other.id}/access/context", headers=headers).status_code
        == 403
    )
    workspace.status = "suspended"
    session.commit()
    assert client.get(path, headers=headers).json()["allowed_actions"] == []
    workspace.status = "active"
    member = session.scalar(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id)
    )
    member.role = "viewer"
    session.commit()
    unrestricted = {"Authorization": f"Bearer {TOKEN}", "X-User-ID": str(user.id)}
    assert client.get(path, headers=unrestricted).json()["allowed_actions"] == ["read"]
    member.status = "disabled"
    session.commit()
    assert client.get(path, headers=unrestricted).status_code == 403


def test_restricted_admin_cannot_escape_token_scope():
    client, session, _ = _client()
    user, workspace = _seed_workspace(session)
    user.platform_admin = True
    session.commit()
    headers, _ = _token(
        client,
        session,
        user,
        {
            "workspace_ids": [str(workspace.id)],
            "workspace_actions": ["read"],
            "account_actions": [],
        },
    )
    for path in ("overview", "costs/summary", "marketplace/reviews", "operations/history"):
        assert client.get(f"/api/v1/admin/{path}", headers=headers).status_code == 401


@pytest.mark.parametrize("revocation", [None, "role", "token"])
def test_durable_audit_request_idempotency_and_execution_authorization(revocation):
    client, session, _ = _client()
    user, workspace = _seed_workspace(session)
    _, other = _seed_workspace(session, email="other@example.com", slug="other")
    user.platform_admin = True
    session.commit()
    headers, token = _token(client, session, user)
    path = f"/api/v1/admin/workspaces/{workspace.id}/operations/audit-integrity/verify"
    body = {"request_id": str(uuid4()), "reason": "Verify chain"}
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "pending"
    assert client.post(path, headers=headers, json=body).json()["id"] == body["request_id"]
    assert client.post(path, headers=headers, json={**body, "reason": "changed"}).status_code == 409
    assert (
        client.post(
            path.replace(str(workspace.id), str(other.id)), headers=headers, json=body
        ).status_code
        == 409
    )
    assert session.query(AdminOperationRecord).count() == 1
    listing = client.get(
        "/api/v1/admin/operations/requests", headers=headers, params={"workspace_id": str(other.id)}
    ).json()
    assert listing["total"] == 0
    if revocation == "role":
        user.platform_admin = False
    elif revocation == "token":
        token.revoked_at = datetime.now(UTC)
    session.commit()
    activities = AdminOperationActivities(
        ActivityDependencies(sessionmaker(session.get_bind()), client.app.state.settings)
    )
    execution = ExecutionRequest(
        execution_id=UUID(body["request_id"]),
        workspace_id=workspace.id,
        resource_id=UUID(body["request_id"]),
        kind=ExecutionKind.ADMIN_OPERATION,
    )
    activities.execute(execution)
    response = client.get(
        f"/api/v1/admin/operations/requests/{body['request_id']}", headers=_admin_headers()
    )
    assert response.status_code == 200
    assert response.json()["status"] == ("completed" if revocation is None else "failed"), (
        response.text
    )
    activities.execute(execution)
    integrity = client.get(
        "/api/v1/admin/operations/audit-integrity", headers=_admin_headers()
    ).json()
    by_workspace = {item["workspace_id"]: item for item in integrity["items"]}
    assert by_workspace[str(other.id)]["status"] == "missing"
    assert by_workspace[str(workspace.id)]["status"] == (
        "valid" if revocation is None else "missing"
    )


@pytest.mark.parametrize("talent", [False, True])
@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_market_review_tenant_version_and_idempotent_decision(talent, decision):
    client, session, _ = _client()
    user, workspace = _seed_workspace(session)
    user.platform_admin = True
    session.commit()
    headers, _ = _token(client, session, user)
    if talent:
        profile = AgentProfile(workspace_id=workspace.id, name="Listed Agent", role="researcher")
        session.add(profile)
        session.flush()
        listing = TalentListing(
            source_workspace_id=workspace.id,
            owner_user_id=user.id,
            source_agent_profile_id=profile.id,
            title="Agent",
            role="researcher",
            status="pending_approval",
            version=1,
        )
    else:
        listing = MarketplaceListing(
            workspace_id=workspace.id,
            owner_user_id=user.id,
            listing_type="skill",
            name="Skill",
            visibility="public",
            status="pending_approval",
            version="1.0.0",
        )
    session.add(listing)
    session.flush()
    approval = Approval(
        workspace_id=workspace.id,
        approval_type="resource_review",
        risk_level="low",
        created_at=datetime.now(UTC),
        payload={
            "kind": "resource_review",
            "target_type": "talent_listing" if talent else "marketplace_listing",
            "target_id": str(listing.id),
            "snapshot": {"version": listing.version},
        },
    )
    session.add(approval)
    session.commit()
    path = f"/api/v1/admin/workspaces/{workspace.id}/marketplace/reviews/{approval.id}/decision"
    body = {"decision": decision, "reason": "Publication review"}
    assert (
        client.post(
            path.replace(str(workspace.id), str(uuid4())), headers=headers, json=body
        ).status_code
        == 404
    )
    original_version = listing.version
    listing.version = 2 if talent else "2.0.0"
    session.commit()
    assert client.post(path, headers=headers, json=body).status_code == 409
    listing.version = original_version
    session.commit()
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert client.post(path, headers=headers, json=body).status_code == 200
    opposite = "rejected" if decision == "approved" else "approved"
    assert (
        client.post(path, headers=headers, json={**body, "decision": opposite}).status_code == 409
    )
    session.refresh(listing)
    assert listing.status == ("public" if decision == "approved" else "rejected")
    assert client.get("/api/v1/admin/marketplace/reviews", headers=headers).json()["total"] == 0


def test_history_deduplicates_samples_retains_window_and_paginates():
    client, session, _ = _client()
    settings = client.app.state.settings
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    history = PlatformHistoryService(session)
    history.capture(settings, now=now - timedelta(days=40))
    session.commit()
    history.capture(settings, now=now)
    session.commit()
    history.capture(settings, now=now + timedelta(seconds=1))
    session.commit()
    assert session.query(PlatformMetricSnapshot).count() == 1
    history.capture(settings, now=now + timedelta(minutes=2))
    session.commit()
    result = client.get(
        "/api/v1/admin/operations/history",
        headers=_admin_headers(),
        params={
            "start_at": (now - timedelta(minutes=1)).isoformat(),
            "end_at": (now + timedelta(minutes=3)).isoformat(),
            "limit": 1,
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["total"] == 2
    assert len(result.json()["items"]) == 1
    assert set(result.json()["items"][0]["values"]) == {
        "runs",
        "temporal_dispatch",
        "runtimes",
        "approvals",
    }


def test_global_costs_respect_currency_tenant_and_pagination():
    client, session, _ = _client()
    _, workspace = _seed_workspace(session)
    _, other = _seed_workspace(session, email="other@example.com", slug="other")
    now = datetime.now(UTC)
    for tenant, currency, tokens in [
        (workspace, "USD", 10),
        (other, "USD", 20),
        (workspace, "EUR", 90),
        (workspace, None, 5),
    ]:
        profile = AgentProfile(workspace_id=tenant.id, name="Metered", role="expert")
        session.add(profile)
        session.flush()
        run = AgentRun(
            workspace_id=tenant.id,
            agent_profile_id=profile.id,
            model="test",
            input={},
            status="completed",
        )
        session.add(run)
        session.flush()
        session.add(
            ModelUsageRecord(
                workspace_id=tenant.id,
                agent_run_id=run.id,
                provider="test",
                model="test",
                currency=currency,
                metering_status="unpriced",
                input_tokens=tokens,
                total_tokens=tokens,
                occurred_at=now,
            )
        )
    session.commit()
    path = "/api/v1/admin/costs/summary"
    response = client.get(path, headers=_admin_headers(), params={"limit": 1})
    assert response.status_code == 200, response.text
    assert response.json()["totals"]["total_tokens"] == 35
    assert response.json()["has_more"] is True
    assert len(response.json()["groups"]) == 1
    scoped = client.get(path, headers=_admin_headers(), params={"workspace_id": str(other.id)})
    assert scoped.json()["totals"]["total_tokens"] == 20
    euros = client.get(path, headers=_admin_headers(), params={"currency": "EUR"})
    assert euros.json()["totals"]["total_tokens"] == 95


def test_admin_cancellation_is_deferred_and_scoped_to_target_workspace():
    client, session, _ = _client()
    user, workspace = _seed_workspace(session)
    _, other = _seed_workspace(session, email="other@example.com", slug="other")
    user.platform_admin = True
    session.commit()
    headers, _ = _token(client, session, user)
    stale = datetime.now(UTC) - timedelta(hours=2)
    runs = []
    for tenant in (workspace, other):
        profile = AgentProfile(workspace_id=tenant.id, name="Expert", role="expert")
        session.add(profile)
        session.flush()
        runs.append(
            AgentRun(
                workspace_id=tenant.id,
                agent_profile_id=profile.id,
                model="test",
                input={},
                status="running",
                started_at=stale,
                updated_at=stale,
            )
        )
    session.add_all(runs)
    session.commit()
    response = client.post(
        f"/api/v1/admin/workspaces/{workspace.id}/operations/runs/{runs[0].id}/cancel",
        headers=headers,
        json={"request_id": str(uuid4()), "reason": "Cancel target execution"},
    )
    assert response.status_code == 202, response.text
    session.refresh(runs[0])
    assert runs[0].status == "running"
    AdminOperationActivities(
        ActivityDependencies(sessionmaker(session.get_bind()), client.app.state.settings)
    ).execute(
        ExecutionRequest(
            execution_id=UUID(response.json()["id"]),
            workspace_id=workspace.id,
            resource_id=UUID(response.json()["id"]),
            kind=ExecutionKind.ADMIN_OPERATION,
        )
    )
    session.refresh(runs[0])
    session.refresh(runs[1])
    assert runs[0].status == "cancelled"
    assert runs[1].status == "running"
    result = client.get(
        f"/api/v1/admin/operations/requests/{response.json()['id']}", headers=headers
    )
    assert result.json()["status"] == "completed", result.text


def test_admin_activity_consumes_persisted_intent_once():
    client, session, _ = _client()
    user, workspace = _seed_workspace(session)
    user.platform_admin = True
    session.commit()
    row = AdminOperationService(session).submit(
        request_id=uuid4(),
        workspace_id=workspace.id,
        actor=AuthenticatedUser.from_model(user),
        operation="verify_audit",
        parameters={"reason": "Worker integration"},
    )
    session.commit()
    activities = AdminOperationActivities(
        ActivityDependencies(sessionmaker(session.get_bind()), client.app.state.settings)
    )
    request = ExecutionRequest(
        execution_id=row.id,
        workspace_id=workspace.id,
        resource_id=row.id,
        kind=ExecutionKind.ADMIN_OPERATION,
    )
    activities.execute(request)
    activities.execute(request)
    session.refresh(row)
    assert row.status == "completed"
    assert row.result["valid"] is True
