from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from opsmesh.agents.messages.models import AgentMessage, AgentMessageThread
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.governance.security_events.models import SecurityEvent
from opsmesh.identity.users.models import User
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.runs.models import AgentRun, RunEvent
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.runtime.instances.models import RuntimeEvent, WorkspaceRuntime
from opsmesh.teams.management.models import AgentTeam
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.execution import test_agent_id as _test_agent_id
from tests.fixtures.workspace_api import _client, _headers, _seed_workspace


def test_operations_runtime_events_redact_sensitive_metadata() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="runtime",
    )
    session.add(runtime)
    session.flush()
    session.add(
        RuntimeEvent(
            workspace_id=workspace.id,
            workspace_runtime_id=runtime.id,
            event_type="runtime.cleanup",
            message="cleanup",
            event_metadata={
                "cleanup": {
                    "container_id": "container-secret",
                    "base_url": "https://runtime.example.test/private",
                    "success": True,
                },
                "safe": "visible",
            },
            created_at=datetime.now(UTC),
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/runtime-events?event_type=runtime.cleanup",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    metadata = response.json()["items"][0]["event_metadata"]
    assert metadata == {
        "cleanup": {
            "container_id": "[redacted]",
            "base_url": "[redacted]",
            "success": True,
        },
        "safe": "visible",
    }
    assert "container-secret" not in str(metadata)
    assert "runtime.example.test/private" not in str(metadata)


def test_operations_audit_events_redact_sensitive_metadata() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    session.add(
        AuditEvent(
            workspace_id=workspace.id,
            actor_type="user",
            actor_id=str(owner.id),
            user_id=owner.id,
            action="workspace.audit_sensitive",
            target_type="workspace",
            target_id=str(workspace.id),
            audit_metadata={
                "token": "audit-token",
                "nested": {"headers": {"authorization": "Bearer hidden"}},
                "safe": "visible",
            },
            created_at=datetime.now(UTC),
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/audit-events"
        "?action=workspace.audit_sensitive",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    metadata = response.json()["items"][0]["audit_metadata"]
    assert metadata == {
        "token": "[redacted]",
        "nested": {"headers": "[redacted]"},
        "safe": "visible",
    }
    assert "audit-token" not in str(metadata)
    assert "Bearer hidden" not in str(metadata)


def test_operations_scheduler_reports_backlog_and_fairness_inputs() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    workspace.settings = {
        "scheduler": {
            "max_active_runs": 2,
            "max_running_tasks": 1,
            "max_runs_to_start_per_tick": 1,
            "max_steps_per_task_per_tick": 1,
            "starvation_boost_after_seconds": 60,
            "resource_limits": {"cpu": 4},
            "paused": True,
            "pause_reason": "maintenance",
        }
    }
    queued_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="High priority",
        status="queued",
        priority=10,
    )
    running_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Running",
        status="running",
        priority=5,
    )
    waiting_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Waiting",
        status="waiting_approval",
        priority=3,
    )
    session.add_all([queued_task, running_task, waiting_task])
    session.flush()
    queued_step = TaskStep(
        workspace_id=workspace.id,
        task_id=queued_task.id,
        title="Queued step",
        status="queued",
        order_index=0,
    )
    blocked_step = TaskStep(
        workspace_id=workspace.id,
        task_id=queued_task.id,
        title="Blocked step",
        status="queued",
        order_index=1,
        dependencies={
            "scheduling_status": "blocked",
            "blocked_reason": "workspace_run_quota_exceeded",
        },
    )
    running_step = TaskStep(
        workspace_id=workspace.id,
        task_id=running_task.id,
        title="Running step",
        status="running",
        order_index=0,
    )
    session.add_all([queued_step, blocked_step, running_step])
    session.add(
        AgentRun(
            workspace_id=workspace.id,
            task_id=running_task.id,
            status="running",
            input={},
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/scheduler",
        headers=_headers(owner.id),
    )
    blocked_steps = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/blocked-steps?code=workspace_quota_exceeded",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    assert blocked_steps.status_code == 200
    payload = response.json()
    assert payload["backlog"]["queued_steps"] == 2
    assert payload["backlog"]["running_steps"] == 1
    assert payload["backlog"]["waiting_approval_tasks"] == 1
    assert payload["backlog"]["blocked_steps"] == 1
    assert payload["backlog"]["active_runs"] == 1
    assert payload["backlog"]["highest_priority"] == 10
    assert payload["priority_buckets"][0] == {
        "priority": 10,
        "queued_steps": 2,
        "running_steps": 0,
        "blocked_steps": 1,
    }
    assert payload["blocked_reasons"] == [
        {
            "reason": "workspace_run_quota_exceeded",
            "code": "workspace_quota_exceeded",
            "message": "Workspace quota is exhausted.",
            "resource_key": None,
            "count": 1,
        }
    ]
    blocked_payload = blocked_steps.json()
    assert blocked_payload["total"] == 1
    assert blocked_payload["items"][0]["task_step_id"] == str(blocked_step.id)
    assert blocked_payload["items"][0]["task_title"] == "High priority"
    assert blocked_payload["items"][0]["step_title"] == "Blocked step"
    assert blocked_payload["items"][0]["reason"] == "workspace_run_quota_exceeded"
    assert blocked_payload["items"][0]["code"] == "workspace_quota_exceeded"
    assert blocked_payload["items"][0]["message"] == "Workspace quota is exhausted."
    assert payload["policy"] == {
        "paused": True,
        "pause_reason": "maintenance",
        "max_active_runs": 2,
        "max_running_tasks": 1,
        "max_runs_to_start_per_tick": 1,
        "max_steps_per_task_per_tick": 1,
        "starvation_boost_after_seconds": 60,
        "resource_limits": {"cpu": 4.0},
    }

    unblocked = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/blocked-steps/unblock",
        headers=_headers(owner.id),
        json={"code": "workspace_quota_exceeded", "limit": 10},
    )
    bad_unblock = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/blocked-steps/unblock",
        headers=_headers(owner.id),
        json={},
    )

    session.refresh(blocked_step)
    audit_event = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "scheduler.blocked_steps_unblocked",
        )
    )
    assert unblocked.status_code == 200
    assert unblocked.json()["unblocked_steps"] == 1
    assert bad_unblock.status_code == 400
    assert "scheduling_status" not in blocked_step.dependencies
    assert "blocked_reason" not in blocked_step.dependencies
    assert audit_event is not None
    assert audit_event.audit_metadata["code"] == "workspace_quota_exceeded"
    assert audit_event.audit_metadata["unblocked_steps"] == 1


def test_operations_scheduler_pause_and_resume_control_policy() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    viewer = User(email="viewer@example.com", display_name="Viewer")
    session.add(viewer)
    session.add(WorkspaceMember(workspace=workspace, user=viewer, role="viewer"))
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Queued work",
        status="queued",
        priority=3,
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Queued step",
        status="queued",
        order_index=0,
        dependencies={
            "scheduling_status": "blocked",
            "blocked_reason": "maintenance",
            "priority_score": 10,
        },
    )
    session.add(step)
    session.commit()

    paused = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/scheduler/pause",
        headers=_headers(owner.id),
        json={"reason": "maintenance"},
    )
    denied = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/scheduler/resume",
        headers=_headers(viewer.id),
    )
    resumed = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/scheduler/resume",
        headers=_headers(owner.id),
    )

    session.refresh(workspace)
    session.refresh(step)
    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.workspace_id == workspace.id)
        .order_by(AuditEvent.created_at.asc())
    ).all()

    assert paused.status_code == 200
    assert paused.json()["paused"] is True
    assert paused.json()["pause_reason"] == "maintenance"
    assert denied.status_code == 403
    assert resumed.status_code == 200
    assert resumed.json()["paused"] is False
    assert resumed.json()["cleared_blocked_steps"] == 1
    assert workspace.settings["scheduler"]["paused"] is False
    assert "pause_reason" not in workspace.settings["scheduler"]
    assert step.dependencies == {}
    assert [event.action for event in events] == [
        "workspace.scheduler_paused",
        "workspace.scheduler_resumed",
    ]
    assert events[1].audit_metadata["cleared_blocked_steps"] == 1


def test_operations_outcomes_reports_failure_rate_and_approval_backlog() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    now = datetime.now(UTC)
    completed_run = AgentRun(
        workspace_id=workspace.id,
        status="completed",
        input={},
        updated_at=now - timedelta(seconds=120),
    )
    failed_run = AgentRun(
        workspace_id=workspace.id,
        status="failed",
        input={},
        error={"code": "RuntimeError", "message": "model failed"},
        updated_at=now - timedelta(seconds=90),
    )
    cancelled_run = AgentRun(
        workspace_id=workspace.id,
        status="cancelled",
        input={},
        updated_at=now - timedelta(seconds=60),
    )
    old_failed_run = AgentRun(
        workspace_id=workspace.id,
        status="failed",
        input={},
        error={"code": "OldError"},
        updated_at=now - timedelta(seconds=10_000),
    )
    approval = Approval(
        workspace_id=workspace.id,
        approval_type="mcp.tool",
        risk_level="high",
        payload={"tool_name": "deploy"},
        status="pending",
        created_at=now - timedelta(seconds=300),
    )
    low_risk_approval = Approval(
        workspace_id=workspace.id,
        approval_type="runtime.command",
        risk_level="medium",
        payload={"command": "ls"},
        status="pending",
        created_at=now - timedelta(seconds=120),
    )
    decided_approval = Approval(
        workspace_id=workspace.id,
        approval_type="mcp.tool",
        risk_level="high",
        payload={},
        status="approved",
        created_at=now - timedelta(seconds=400),
    )
    session.add_all(
        [
            completed_run,
            failed_run,
            cancelled_run,
            old_failed_run,
            approval,
            low_risk_approval,
            decided_approval,
        ]
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/outcomes?window_seconds=600",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["runs"] == {
        "window_seconds": 600,
        "total_runs": 3,
        "completed_runs": 1,
        "failed_runs": 1,
        "cancelled_runs": 1,
        "failure_rate": 0.3333,
        "failure_reasons": [{"code": "RuntimeError", "count": 1}],
    }
    assert payload["approvals"]["pending"] == 2
    assert payload["approvals"]["high_risk_pending"] == 1
    assert payload["approvals"]["oldest_pending_age_seconds"] >= 290
    assert payload["approvals"]["pending_by_type"] == {
        "mcp.tool": 1,
        "runtime.command": 1,
    }


def test_operator_can_use_operations_but_viewer_cannot() -> None:
    client, session = _client()
    operator, workspace = _seed_workspace(
        session,
        email="operator@example.com",
        slug="operator-space",
        role="operator",
    )
    viewer = User(email="viewer@example.com", display_name="viewer")
    session.add(viewer)
    session.flush()
    session.add(WorkspaceMember(workspace=workspace, user=viewer, role="viewer"))
    session.commit()

    operator_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/failed-runs",
        headers=_headers(operator.id),
    )
    viewer_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/failed-runs",
        headers=_headers(viewer.id),
    )

    assert operator_response.status_code == 200
    assert viewer_response.status_code == 403


def test_security_events_are_recorded_and_queryable_for_workspace_denials() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    outsider, _ = _seed_workspace(
        session,
        email="outsider@example.com",
        slug="outsider-space",
        role="owner",
    )

    denied = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/failed-runs",
        headers=_headers(outsider.id),
    )
    assert denied.status_code == 403

    events = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/security-events"
        "?action=auth.workspace.rejected",
        headers=_headers(owner.id),
    )
    assert events.status_code == 200
    payload = events.json()
    assert payload["total"] == 1
    assert payload["items"][0]["workspace_id"] == str(workspace.id)
    assert payload["items"][0]["user_id"] == str(outsider.id)
    assert payload["items"][0]["severity"] == "warning"
    assert payload["items"][0]["event_metadata"]["required_action"] == "operate"


def test_workspace_security_event_response_redacts_sensitive_metadata() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    session.add(
        SecurityEvent(
            workspace_id=workspace.id,
            user_id=owner.id,
            action="mcp_tool.blocked",
            outcome="denied",
            severity="warning",
            path="/api/v1/workspaces/x/mcp",
            method="POST",
            reason="policy",
            event_metadata={
                "authorization": "Bearer secret-token",
                "headers": {"x-api-key": "sk-secret"},
                "nested": {"base_url": "https://router.example.test/private"},
                "safe": "visible",
            },
            created_at=datetime.now(UTC),
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/security-events?action=mcp_tool.blocked",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    metadata = response.json()["items"][0]["event_metadata"]
    assert metadata == {
        "authorization": "[redacted]",
        "headers": "[redacted]",
        "nested": {"base_url": "[redacted]"},
        "safe": "visible",
    }
    assert "secret-token" not in str(metadata)
    assert "router.example.test/private" not in str(metadata)


def test_invalid_internal_token_records_security_event() -> None:
    client, seed_session = _client()
    user, _ = _seed_workspace(seed_session, role="owner")

    response = client.get(
        "/api/v1/workspaces",
        headers={"Authorization": "Bearer wrong-token", "X-User-ID": str(user.id)},
    )

    assert response.status_code == 401
    event = seed_session.query(SecurityEvent).filter_by(action="auth.internal_token.rejected").one()
    assert event.workspace_id is None
    assert event.outcome == "denied"
    assert event.severity == "warning"
    assert event.path == "/api/v1/workspaces"


def test_correlation_is_scoped_sampled_and_does_not_link_unrelated_shared_runtime_runs():
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    _, other = _seed_workspace(session, role="owner", email="other@test.com", slug="other")
    runtime = WorkspaceRuntime(workspace_id=workspace.id, name="shared")
    session.add(runtime)
    session.flush()
    agent_id = _test_agent_id(session, workspace.id)
    runs = [
        AgentRun(workspace_id=workspace.id, agent_profile_id=agent_id, runtime_id=runtime.id)
        for _ in range(2)
    ]
    session.add_all(runs)
    session.flush()
    trace = "1" * 32
    for sequence in range(101):
        session.add(
            RunEvent(
                workspace_id=workspace.id,
                agent_run_id=runs[0].id,
                sequence=sequence,
                event_type="run.progress",
                trace_id=trace,
                request_id="request-evidence",
                created_at=datetime.now(UTC),
            )
        )
    for tenant in (workspace, other):
        session.add(
            AuditEvent(
                workspace_id=tenant.id,
                actor_type="system",
                actor_id="test-observer",
                action="audit.evidence",
                target_type="workspace",
                target_id=str(tenant.id),
                trace_id=trace,
                created_at=datetime.now(UTC),
            )
        )
    session.add(
        RuntimeEvent(
            workspace_id=workspace.id,
            workspace_runtime_id=runtime.id,
            event_type="runtime.observed",
            message="Shared environment",
            trace_id=trace,
            created_at=datetime.now(UTC),
        )
    )
    session.commit()
    path = f"/api/v1/workspaces/{workspace.id}/operations/correlation"
    response = client.get(path, headers=_headers(owner.id), params={"trace_id": trace})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["evidence_counts"]["run_events"] == 101
    assert payload["evidence_counts"]["audit_events"] == 1
    assert payload["sampled_sources"] == ["run_events"]
    assert payload["run_ids"] == [str(runs[0].id)]
    assert payload["runtime_ids"] == [str(runtime.id)]
    assert payload["drilldowns"]["run_events"].endswith(f"?trace_id={trace}")
    assert client.get(path, headers=_headers(owner.id)).status_code == 400
    assert (
        client.get(
            path, headers=_headers(owner.id), params={"trace_id": trace, "request_id": "both"}
        ).status_code
        == 400
    )
    events = client.get(
        payload["drilldowns"]["run_events"], headers=_headers(owner.id), params={"limit": 2}
    )
    assert events.status_code == 200
    assert events.json()["total"] == 101
    assert len(events.json()["items"]) == 2


def test_team_timeline_queries_persisted_scoped_evidence_and_validates_filters():
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    _, other = _seed_workspace(session, role="owner", email="timeline@test.com", slug="other")
    teams = [AgentTeam(workspace_id=tenant.id, name="Team") for tenant in (workspace, other)]
    session.add_all(teams)
    session.flush()
    for team in teams:
        thread = AgentMessageThread(workspace_id=team.workspace_id, agent_team_id=team.id)
        session.add(thread)
        session.flush()
        session.add(
            AgentMessage(
                workspace_id=team.workspace_id,
                agent_team_id=team.id,
                thread_id=thread.id,
                message_type="report",
                body="Audit report",
                payload={"token": "private-token", "safe": "visible"},
            )
        )
    session.add(
        AuditEvent(
            workspace_id=workspace.id,
            actor_type="system",
            actor_id="test-observer",
            action="team.updated",
            target_type="agent_team",
            target_id=str(teams[0].id),
            audit_metadata={"authorization": "Bearer private"},
            created_at=datetime.now(UTC),
        )
    )
    session.commit()
    path = f"/api/v1/workspaces/{workspace.id}/operations/team-runtimes/{teams[0].id}/timeline"
    response = client.get(path, headers=_headers(owner.id), params={"limit": 1})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["summary"]["total_events"] == 2
    assert payload["summary"]["returned_events"] == 1
    assert payload["summary"]["source_counts"] == {"agent_message": 1, "audit_event": 1}
    filtered = client.get(path, headers=_headers(owner.id), params={"source_type": "agent_message"})
    assert filtered.json()["items"][0]["metadata"] == {"token": "[redacted]", "safe": "visible"}
    assert (
        client.get(
            f"/api/v1/workspaces/{workspace.id}/operations/team-runtimes/{teams[1].id}/timeline",
            headers=_headers(owner.id),
        ).status_code
        == 403
    )
    assert (
        client.get(
            path,
            headers=_headers(owner.id),
            params={"since": "2026-10-13T00:00:00Z", "until": "2026-10-12T00:00:00Z"},
        ).status_code
        == 400
    )


def test_workspace_overview_cache_and_counts_are_tenant_scoped():
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other = _seed_workspace(
        session, role="owner", email="overview@test.com", slug="other"
    )
    for tenant in (workspace, other):
        session.add(
            AgentRun(
                workspace_id=tenant.id,
                agent_profile_id=_test_agent_id(session, tenant.id),
                status="failed" if tenant == workspace else "completed",
            )
        )
    session.commit()
    for tenant, user, expected in ((workspace, owner, "failed"), (other, other_owner, "completed")):
        response = client.get(
            f"/api/v1/workspaces/{tenant.id}/operations/overview", headers=_headers(user.id)
        )
        assert response.status_code == 200, response.text
        assert response.json()["runs_by_status"] == {expected: 1}
