from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from opsmesh.bootstrap.worker import build_worker_runner
from opsmesh.capabilities.marketplace.models import MarketplaceListing, TalentListing
from opsmesh.governance.costs.models import ModelUsageRecord
from opsmesh.identity.auth.service import AuthenticationService
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.runtime.operations.admin_requests import AdminOperationService
from opsmesh.runtime.operations.history import PlatformHistoryService
from opsmesh.runtime.operations.models import AdminOperationRequest, PlatformMetricSnapshot
from opsmesh.runtime.queues.dependencies import get_worker_queue
from opsmesh.runtime.workers.models import WorkerRunnerConfig
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.test_admin_api import TOKEN, _admin_headers, _client, _seed_workspace


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
    assert session.query(AdminOperationRequest).count() == 1
    listing = client.get(
        "/api/v1/admin/operations/requests", headers=headers, params={"workspace_id": str(other.id)}
    ).json()
    assert listing["total"] == 0
    if revocation == "role":
        user.platform_admin = False
    elif revocation == "token":
        token.revoked_at = datetime.now(UTC)
    session.commit()
    queue = client.app.dependency_overrides[get_worker_queue]()
    assert AdminOperationService(session).process_one(queue)
    response = client.get(
        f"/api/v1/admin/operations/requests/{body['request_id']}", headers=_admin_headers()
    )
    assert response.status_code == 200
    assert response.json()["status"] == ("completed" if revocation is None else "failed"), (
        response.text
    )
    assert not AdminOperationService(session).process_one(queue)
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
        listing = TalentListing(
            source_workspace_id=workspace.id,
            owner_user_id=user.id,
            source_agent_profile_id=uuid4(),
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
    assert set(result.json()["items"][0]["values"]) == {"runs", "workers", "runtimes", "approvals"}


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
        session.add(
            ModelUsageRecord(
                workspace_id=tenant.id,
                agent_run_id=uuid4(),
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


def test_admin_recovery_is_deferred_and_scoped_to_target_workspace():
    client, session, _ = _client()
    user, workspace = _seed_workspace(session)
    _, other = _seed_workspace(session, email="other@example.com", slug="other")
    user.platform_admin = True
    session.commit()
    headers, _ = _token(client, session, user)
    stale = datetime.now(UTC) - timedelta(hours=2)
    runs = [
        AgentRun(
            workspace_id=tenant.id,
            agent_profile_id=uuid4(),
            model="test",
            input={},
            status="running",
            started_at=stale,
            updated_at=stale,
        )
        for tenant in (workspace, other)
    ]
    session.add_all(runs)
    session.commit()
    diagnostic = client.get(
        "/api/v1/admin/operations/runs",
        headers=headers,
        params={"kind": "stale", "workspace_id": str(workspace.id)},
    )
    assert diagnostic.status_code == 200, diagnostic.text
    assert [row["id"] for row in diagnostic.json()["items"]] == [str(runs[0].id)]
    response = client.post(
        f"/api/v1/admin/workspaces/{workspace.id}/operations/stale-runs/recover",
        headers=headers,
        json={"request_id": str(uuid4()), "reason": "recover stale execution"},
    )
    assert response.status_code == 202, response.text
    session.refresh(runs[0])
    assert runs[0].status == "running"
    assert AdminOperationService(session).process_one(
        client.app.dependency_overrides[get_worker_queue]()
    )
    session.refresh(runs[0])
    session.refresh(runs[1])
    assert runs[0].status == "failed"
    assert runs[1].status == "running"
    result = client.get(
        f"/api/v1/admin/operations/requests/{response.json()['id']}", headers=headers
    )
    assert result.json()["status"] == "completed", result.text


def test_worker_maintenance_consumes_persisted_intents_and_samples_history():
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
    queue = client.app.dependency_overrides[get_worker_queue]()
    runner = build_worker_runner(
        queue=queue,
        session_factory=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
        config=WorkerRunnerConfig(worker_id="dashboard-test"),
        settings=client.app.state.settings,
    )
    runner.run_maintenance()
    session.refresh(row)
    assert row.status == "completed"
    assert session.query(PlatformMetricSnapshot).count() == 1
