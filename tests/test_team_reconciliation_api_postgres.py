"""Unknown Team effects require a scoped, audited human confirmation."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from opsmesh.governance.audit.models import AuditEvent
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.users.models import User
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.teams.execution.models import TeamOrchestrationRequest
from opsmesh.teams.management.models import AgentTeam
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.agent_product import _seed_workspace

pytest_plugins = ["tests.fixtures.agent_api"]
pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required"
)


def test_unknown_request_blocks_dispatch_and_human_confirmation_never_reexecutes(agent_api):
    client, session = agent_api
    owner, workspace = _seed_workspace(session)
    team = AgentTeam(workspace_id=workspace.id, name="Review Team")
    session.add(team)
    session.flush()
    command = TeamOrchestrationRequest(
        workspace_id=workspace.id,
        team_id=team.id,
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, owner.id),
        status="unknown",
        last_error_code="WorkerLostAfterCommit",
    )
    session.add(command)
    session.commit()
    base = f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/orchestration/requests"
    headers = {"Authorization": "Bearer test-token", "X-User-ID": str(owner.id)}
    body = {
        "status": "completed",
        "reason": "Inspected the task and confirmed the committed artifact",
        "evidence": {"task_id": str(uuid4()), "api_key": "raw-secret"},
        "observed_result": {"artifact_verified": True},
    }
    assert client.post(base, headers=headers, json={}).status_code == 409
    pending = client.get(f"{base}?request_status=unknown", headers=headers)
    assert pending.status_code == 200 and pending.json()["total"] == 1
    assert pending.json()["items"][0]["request_id"] == str(command.id)
    before = session.scalar(select(func.count()).select_from(TemporalOutboxEntry))
    response = client.post(f"{base}/{command.id}/reconcile", headers=headers, json=body)
    assert response.status_code == 200, response.json()
    assert response.json()["status"] == "completed"
    assert "raw-secret" not in response.text
    assert (
        client.post(f"{base}/{command.id}/reconcile", headers=headers, json=body).status_code == 409
    )
    session.expire_all()
    persisted = session.get(TeamOrchestrationRequest, command.id)
    assert persisted.result["observed_result"]["artifact_verified"]
    assert "raw-secret" not in str(persisted.result)
    assert session.scalar(select(func.count()).select_from(TemporalOutboxEntry)) == before
    audits = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "team.orchestration.reconciled")
    ).all()
    assert len(audits) == 1 and audits[0].actor_id == str(owner.id)
    assert "raw-secret" not in str(audits[0].audit_metadata)


def test_reconciliation_rejects_other_tenant_and_read_only_actor(agent_api):
    client, session = agent_api
    owner, workspace = _seed_workspace(session)
    team = AgentTeam(workspace_id=workspace.id, name="Private Team")
    viewer = User(email="viewer@example.com", display_name="Viewer")
    foreign_owner = User(email="foreign@example.com", display_name="Foreign")
    foreign = Workspace(owner=foreign_owner, name="Foreign", slug="foreign")
    session.add_all([team, viewer, foreign])
    session.flush()
    session.add_all(
        [
            WorkspaceMember(workspace_id=workspace.id, user_id=viewer.id, role="viewer"),
            WorkspaceMember(workspace_id=foreign.id, user_id=foreign_owner.id, role="owner"),
        ]
    )
    command = TeamOrchestrationRequest(
        workspace_id=workspace.id,
        team_id=team.id,
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, owner.id),
        status="unknown",
    )
    session.add(command)
    session.commit()
    body = {"status": "failed", "reason": "Inspected", "evidence": {"checked": True}}
    for actor, target_workspace in [(viewer, workspace), (foreign_owner, foreign)]:
        response = client.post(
            f"/api/v1/workspaces/{target_workspace.id}/teams/{team.id}"
            f"/orchestration/requests/{command.id}/reconcile",
            headers={"Authorization": "Bearer test-token", "X-User-ID": str(actor.id)},
            json=body,
        )
        assert response.status_code in {403, 404}
    session.expire_all()
    assert session.get(TeamOrchestrationRequest, command.id).status == "unknown"
    assert (
        session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "team.orchestration.reconciled")
        )
        == 0
    )


def test_reconciliation_waits_for_inflight_team_effects(agent_api):
    client, session = agent_api
    owner, workspace = _seed_workspace(session)
    team = AgentTeam(workspace_id=workspace.id, name="In-flight Team")
    session.add(team)
    session.flush()
    command = TeamOrchestrationRequest(
        workspace_id=workspace.id,
        team_id=team.id,
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, owner.id),
        status="unknown",
        last_error_code="ActivityOutcomeUnknown",
    )
    session.add(command)
    session.commit()
    url = (
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}"
        f"/orchestration/requests/{command.id}/reconcile"
    )
    headers = {"Authorization": "Bearer test-token", "X-User-ID": str(owner.id)}
    decision = {"status": "failed", "reason": "Inspected effects", "evidence": {"checked": True}}
    key = f"{workspace.id}:team:{team.id}"
    with session.get_bind().connect() as execution:
        execution.execute(text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"), {"key": key})
        execution.commit()
        try:
            response = client.post(url, headers=headers, json=decision)
            assert response.status_code == 409, response.json()
            session.expire_all()
            assert session.get(TeamOrchestrationRequest, command.id).status == "unknown"
        finally:
            execution.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), {"key": key}
            )
            execution.commit()
    assert client.post(url, headers=headers, json=decision).status_code == 200
