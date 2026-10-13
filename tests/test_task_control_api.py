"""Workspace-scoped product API verification on PostgreSQL."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.state import RunStatus
from opsmesh.orchestration.tasks.models import Task, TaskMessage, TaskStep
from opsmesh.orchestration.tasks.state import TaskStatus
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.workspace_api import (
    _api_agent_id,
    _client,
    _dispatch_for,
    _headers,
    _seed_workspace,
)


@pytest.mark.parametrize(
    "path_template",
    [
        "/api/v1/workspaces/{workspace_id}/agents",
        "/api/v1/workspaces/{workspace_id}/teams",
        "/api/v1/workspaces/{workspace_id}/tasks",
        "/api/v1/workspaces/{workspace_id}/runs",
        "/api/v1/workspaces/{workspace_id}/files",
        "/api/v1/workspaces/{workspace_id}/artifacts",
        "/api/v1/workspaces/{workspace_id}/capabilities/workspace-skills",
        "/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers",
        "/api/v1/workspaces/{workspace_id}/capabilities/mcp-tool-call-logs",
    ],
)
def test_workspace_scoped_list_routes_reject_non_members(path_template: str) -> None:
    client, session = _client()
    owner, _ = _seed_workspace(session, role="owner")
    _, other_workspace = _seed_workspace(
        session, role="owner", email="other-owner@example.com", slug="other-owner-space"
    )
    response = client.get(
        path_template.format(workspace_id=other_workspace.id), headers=_headers(owner.id)
    )
    assert response.status_code == 403


def test_workspace_and_resource_api_enforces_scope_and_roles() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner", with_provider=True)
    viewer, other_workspace = _seed_workspace(
        session, role="viewer", email="viewer@example.com", slug="viewer-space", with_provider=True
    )
    response = client.get("/api/v1/workspaces", headers=_headers(owner.id))
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == str(workspace.id)
    forbidden = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/agents", headers=_headers(owner.id)
    )
    assert forbidden.status_code == 403
    created_agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Researcher", "role": "researcher", "instructions": "Research well"},
    )
    assert created_agent.status_code == 201
    assert created_agent.json()["workspace_id"] == str(workspace.id)
    created_team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={"name": "Research Team", "team_type": "research"},
    )
    assert created_team.status_code == 201
    created_task = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": str(_api_agent_id(session, workspace.id)),
            "title": "Q2 Market Analysis",
            "domain_type": "research",
        },
    )
    assert created_task.status_code == 201
    assert created_task.json()["created_by_user_id"] == str(owner.id)
    viewer_create = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/tasks",
        headers=_headers(viewer.id),
        json={
            "agent_profile_id": str(_api_agent_id(session, other_workspace.id)),
            "title": "Should fail",
        },
    )
    assert viewer_create.status_code == 403
    tasks = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks?status=queued", headers=_headers(owner.id)
    )
    assert tasks.status_code == 200
    assert tasks.json()["total"] == 1
    audit = client.get(
        f"/api/v1/workspaces/{workspace.id}/audit-events", headers=_headers(owner.id)
    )
    assert audit.status_code == 200
    actions = {item["action"] for item in audit.json()["items"]}
    assert {"agent.created", "team.created", "task.created"} <= actions
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=viewer.id, role="operator"))
    session.commit()
    base = f"/api/v1/workspaces/{workspace.id}"
    agent_id = created_agent.json()["id"]
    for collection in ("agents", "teams", "tasks"):
        hidden = client.get(f"{base}/{collection}", headers=_headers(viewer.id))
        assert hidden.status_code == 200
        assert hidden.json()["total"] == 0
    assert client.get(f"{base}/agents/{agent_id}", headers=_headers(viewer.id)).status_code == 403
    grants_url = f"{base}/access/agent/{agent_id}/grants/{viewer.id}"
    shared = client.put(grants_url, headers=_headers(owner.id), json={"actions": ["read"]})
    assert shared.status_code == 200, shared.text
    visible = client.get(f"{base}/agents", headers=_headers(viewer.id))
    assert visible.json()["total"] == 1
    assert client.get(f"{base}/agents/{agent_id}", headers=_headers(viewer.id)).status_code == 200
    permissions = client.get(
        f"{base}/access/agent/{agent_id}/permissions", headers=_headers(viewer.id)
    )
    assert permissions.json()["actions"] == ["read"]
    denied_edit = client.patch(
        f"{base}/agents/{agent_id}", headers=_headers(viewer.id), json={"name": "Stolen"}
    )
    assert denied_edit.status_code == 403, denied_edit.text
    denied_share = client.put(grants_url, headers=_headers(viewer.id), json={"actions": ["update"]})
    assert denied_share.status_code == 403
    assert (
        client.put(grants_url, headers=_headers(owner.id), json={"actions": []}).status_code == 200
    )
    assert client.get(f"{base}/agents", headers=_headers(viewer.id)).json()["total"] == 0


def test_workspace_operator_read_models_are_reachable_through_the_api() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner", with_provider=True)
    headers = _headers(owner.id)
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=headers,
        json={"name": "Operations Team", "team_type": "operations"},
    )
    task = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=headers,
        json={
            "agent_profile_id": str(_api_agent_id(session, workspace.id)),
            "title": "Inspect production readiness",
        },
    )
    assert team.status_code == 201
    assert task.status_code == 201
    health = client.get(f"/api/v1/workspaces/{workspace.id}/health", headers=headers)
    snapshot = client.post(f"/api/v1/workspaces/{workspace.id}/health/snapshots", headers=headers)
    snapshots = client.get(f"/api/v1/workspaces/{workspace.id}/health/snapshots", headers=headers)
    trends = client.get(f"/api/v1/workspaces/{workspace.id}/health/trends", headers=headers)
    dashboard = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/project-dashboard",
        headers=headers,
    )
    execution_status = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.json()['id']}/execution-status",
        headers=headers,
    )
    transcript = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.json()['id']}/interaction-transcript",
        headers=headers,
    )
    recovery = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.json()['id']}/collaboration-recovery",
        headers=headers,
    )
    recovery_preview = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.json()['id']}/collaboration-recovery",
        headers=headers,
        json={"dry_run": True},
    )
    assert health.status_code == 200
    assert health.json()["workspace_id"] == str(workspace.id)
    assert snapshot.status_code == 201
    assert snapshots.status_code == 200
    assert snapshots.json()["total"] == 1
    assert trends.status_code == 200
    assert trends.json()["snapshot_count"] == 1
    assert dashboard.status_code == 200
    assert dashboard.json()["team_id"] == team.json()["id"]
    assert execution_status.status_code == 200
    assert execution_status.json()["task_id"] == task.json()["id"]
    assert transcript.status_code == 200
    assert transcript.json()["task_id"] == task.json()["id"]
    assert recovery.status_code == 200
    assert recovery.json()["task_id"] == task.json()["id"]
    assert recovery_preview.status_code == 200
    assert recovery_preview.json()["dry_run"] is True


def test_create_task_is_idempotent_within_workspace() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner", with_provider=True)
    headers = _headers(owner.id) | {"Idempotency-Key": "create-q2-task"}
    first = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=headers,
        json={
            "agent_profile_id": str(_api_agent_id(session, workspace.id)),
            "title": "Q2 Market Analysis",
        },
    )
    second = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=headers,
        json={
            "agent_profile_id": str(_api_agent_id(session, workspace.id)),
            "title": "Q2 Market Analysis",
        },
    )
    tasks = session.scalars(select(Task).where(Task.workspace_id == workspace.id)).all()
    runs = session.scalars(select(AgentRun).where(AgentRun.workspace_id == workspace.id)).all()
    audit = client.get(
        f"/api/v1/workspaces/{workspace.id}/audit-events", headers=_headers(owner.id)
    )
    task_created_events = [
        item for item in audit.json()["items"] if item["action"] == "task.created"
    ]
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert len(tasks) == 1
    assert len(runs) == 1
    assert len(task_created_events) == 1


def test_create_agent_and_team_are_idempotent_within_workspace() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    agent_headers = _headers(owner.id) | {"Idempotency-Key": "create-researcher"}
    team_headers = _headers(owner.id) | {"Idempotency-Key": "create-team"}
    first_agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=agent_headers,
        json={"name": "Researcher", "role": "researcher", "instructions": "Research well"},
    )
    second_agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=agent_headers,
        json={"name": "Researcher", "role": "researcher", "instructions": "Research well"},
    )
    first_team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=team_headers,
        json={"name": "Research Team", "team_type": "research"},
    )
    second_team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=team_headers,
        json={"name": "Research Team", "team_type": "research"},
    )
    audit = client.get(
        f"/api/v1/workspaces/{workspace.id}/audit-events", headers=_headers(owner.id)
    )
    actions = [item["action"] for item in audit.json()["items"]]
    assert first_agent.status_code == 201
    assert second_agent.status_code == 201
    assert first_agent.json()["id"] == second_agent.json()["id"]
    assert first_team.status_code == 201
    assert second_team.status_code == 201
    assert first_team.json()["id"] == second_team.json()["id"]
    assert actions.count("agent.created") == 1
    assert actions.count("team.created") == 1


def test_task_idempotency_key_is_scoped_by_workspace() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(
        session, role="owner", email="owner@example.com", slug="one", with_provider=True
    )
    other, other_workspace = _seed_workspace(
        session, role="owner", email="other@example.com", slug="two", with_provider=True
    )
    key = "same-client-key"
    first = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id) | {"Idempotency-Key": key},
        json={"agent_profile_id": str(_api_agent_id(session, workspace.id)), "title": "Owner task"},
    )
    second = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/tasks",
        headers=_headers(other.id) | {"Idempotency-Key": key},
        json={
            "agent_profile_id": str(_api_agent_id(session, other_workspace.id)),
            "title": "Other task",
        },
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_cancel_task_marks_task_and_active_run_cancelled() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner", with_provider=True)
    task_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={"agent_profile_id": str(_api_agent_id(session, workspace.id)), "title": "Cancel me"},
    )
    task_id = UUID(task_response.json()["id"])
    cancelled = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task_id}/cancel", headers=_headers(owner.id)
    )
    repeat_cancel = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task_id}/cancel", headers=_headers(owner.id)
    )
    session.expire_all()
    task = session.get(Task, task_id)
    run = session.scalar(select(AgentRun).where(AgentRun.task_id == task_id))
    audit = client.get(
        f"/api/v1/workspaces/{workspace.id}/audit-events", headers=_headers(owner.id)
    )
    actions = {item["action"] for item in audit.json()["items"]}
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == TaskStatus.CANCELLED.value
    assert repeat_cancel.status_code == 409
    assert task is not None
    assert task.status == TaskStatus.CANCELLED.value
    assert run is not None
    assert run.status == RunStatus.CANCELLED.value
    assert "task.cancelled" in actions


def test_cancel_run_marks_linked_task_cancelled() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner", with_provider=True)
    task_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={"agent_profile_id": str(_api_agent_id(session, workspace.id)), "title": "Cancel run"},
    )
    task_id = UUID(task_response.json()["id"])
    run = session.scalar(select(AgentRun).where(AgentRun.task_id == task_id))
    assert run is not None
    cancelled = client.post(
        f"/api/v1/workspaces/{workspace.id}/runs/{run.id}/cancel", headers=_headers(owner.id)
    )
    task = session.get(Task, task_id)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == RunStatus.CANCELLED.value
    assert task is not None
    assert task.status == TaskStatus.CANCELLED.value


def test_retry_failed_run_creates_new_queued_run_and_persists_workflow_dispatch() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner", with_provider=True)
    task_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": str(_api_agent_id(session, workspace.id)),
            "title": "Retry failed",
        },
    )
    task_id = UUID(task_response.json()["id"])
    failed_run = session.scalar(select(AgentRun).where(AgentRun.task_id == task_id))
    task = session.get(Task, task_id)
    assert failed_run is not None
    assert task is not None
    initial_dispatch = _dispatch_for(session, failed_run.id)
    assert initial_dispatch is not None
    assert initial_dispatch.execution.resource_id == failed_run.id
    failed_run.status = RunStatus.FAILED.value
    failed_run.started_at = datetime.now(UTC)
    task.status = TaskStatus.FAILED.value
    session.commit()
    blocked = client.post(
        f"/api/v1/workspaces/{workspace.id}/runs/{failed_run.id}/retry", headers=_headers(owner.id)
    )
    assert blocked.status_code == 409
    session.expire_all()
    assert session.get(Task, task_id).status == TaskStatus.FAILED.value
    assert session.scalar(select(func.count(AgentRun.id)).where(AgentRun.task_id == task_id)) == 1
    failed_run.execution_released_at = datetime.now(UTC)
    session.commit()
    retry_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/runs/{failed_run.id}/retry", headers=_headers(owner.id)
    )
    retried_run_id = UUID(retry_response.json()["id"])
    session.expire_all()
    retried_run = session.get(AgentRun, retried_run_id)
    task = session.get(Task, task_id)
    retry_dispatch = _dispatch_for(session, retried_run_id)
    audit = client.get(
        f"/api/v1/workspaces/{workspace.id}/audit-events", headers=_headers(owner.id)
    )
    actions = {item["action"] for item in audit.json()["items"]}
    assert retry_response.status_code == 201
    assert retried_run_id != failed_run.id
    assert retried_run is not None
    assert retried_run.status == RunStatus.QUEUED.value
    assert task is not None
    assert task.status == TaskStatus.QUEUED.value
    assert retry_dispatch is not None
    assert retry_dispatch.execution.resource_id == retried_run.id
    assert "run.retried" in actions
    repeated = client.post(
        f"/api/v1/workspaces/{workspace.id}/runs/{failed_run.id}/retry", headers=_headers(owner.id)
    )
    assert repeated.status_code == 409


def test_create_task_correction_for_step_creates_follow_up_step_and_message() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Revise report",
        status="completed",
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Draft report",
        status="completed",
        order_index=1,
        work_package_id="draft-report",
        expected_artifacts=["report"],
    )
    session.add(step)
    session.commit()
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/corrections",
        headers=_headers(owner.id),
        json={
            "target_type": "step",
            "target_id": str(step.id),
            "mode": "revise",
            "instruction": "Rewrite the executive summary with stronger evidence.",
            "metadata": {"priority": "high"},
        },
    )
    assert response.status_code == 201
    body = response.json()
    created_step = session.get(TaskStep, UUID(body["created_step_id"]))
    message = session.get(TaskMessage, UUID(body["message_id"]))
    session.refresh(task)
    assert created_step is not None
    assert created_step.status == "queued"
    assert created_step.order_index == 2
    assert created_step.dependencies["correction"]["target"]["task_step_id"] == str(step.id)
    assert created_step.acceptance_criteria == [
        "Rewrite the executive summary with stronger evidence."
    ]
    assert message is not None
    assert message.message_type == "task.correction.created"
    assert message.payload["created_step_id"] == str(created_step.id)
    assert task.status == TaskStatus.RUNNING.value


def test_stop_work_correction_cancels_task_without_creating_step() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    task = Task(
        workspace_id=workspace.id, created_by_user_id=owner.id, title="Stop this", status="running"
    )
    session.add(task)
    session.commit()
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/corrections",
        headers=_headers(owner.id),
        json={
            "target_type": "task",
            "mode": "stop_work",
            "instruction": "Stop all work on this task.",
        },
    )
    session.refresh(task)
    assert response.status_code == 201
    assert response.json()["created_step_id"] is None
    assert response.json()["status"] == "cancelled"
    assert task.status == "cancelled"
    assert session.scalar(select(TaskStep).where(TaskStep.task_id == task.id)) is None


def test_task_operator_action_reassigns_and_requeues_blocked_step() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="other-operator@example.com", slug="other-operator"
    )
    original_agent = AgentProfile(
        workspace_id=workspace.id, name="Original Developer", role="developer"
    )
    replacement_agent = AgentProfile(
        workspace_id=workspace.id, name="Replacement Developer", role="developer"
    )
    foreign_agent = AgentProfile(
        workspace_id=other_workspace.id, name="Foreign Developer", role="developer"
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Blocked implementation",
        status="blocked",
    )
    session.add_all([original_agent, replacement_agent, foreign_agent, task])
    session.flush()
    blocked_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=original_agent.id,
        work_package_id="build",
        required_role="developer",
        title="Build feature",
        status="blocked",
        dependencies={
            "blocked_reason": "worker_unavailable",
            "blocked_resource_keys": ["worker:cloud"],
            "after_step_ids": [],
        },
    )
    session.add(blocked_step)
    session.commit()
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/operator-actions",
        headers=_headers(owner.id),
        json={
            "action": "reassign_step",
            "task_step_ids": [str(blocked_step.id)],
            "agent_profile_id": str(replacement_agent.id),
            "reason": "Developer unavailable",
            "metadata": {"token": "operator-secret"},
        },
    )
    foreign_agent_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/operator-actions",
        headers=_headers(owner.id),
        json={
            "action": "reassign_step",
            "task_step_ids": [str(blocked_step.id)],
            "agent_profile_id": str(foreign_agent.id),
        },
    )
    foreign_task_response = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/tasks/{task.id}/operator-actions",
        headers=_headers(other_owner.id),
        json={"action": "requeue_blocked_steps"},
    )
    messages = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/messages", headers=_headers(owner.id)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "reassign_step"
    assert body["task_status"] == "running"
    assert body["changed_step_ids"] == [str(blocked_step.id)]
    assert body["details"]["previous_agent_profile_id"] == str(original_agent.id)
    assert body["details"]["agent_profile_id"] == str(replacement_agent.id)
    assert "operator-secret" not in str(body)
    session.refresh(task)
    session.refresh(blocked_step)
    assert task.status == "running"
    assert blocked_step.status == "queued"
    assert blocked_step.assigned_agent_profile_id == replacement_agent.id
    assert blocked_step.dependencies == {"after_step_ids": []}
    assert foreign_agent_response.status_code == 404
    assert foreign_task_response.status_code == 403
    assert messages.status_code == 200
    message_payload = messages.json()["items"][0]["payload"]
    assert message_payload["metadata"]["token"] == "[redacted]"
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "task.operator.reassign_step",
        )
    )
    assert audit is not None
    assert audit.audit_metadata["changed_step_ids"] == [str(blocked_step.id)]


def test_task_operator_action_schedules_downstream_steps_from_handoff() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    agent = AgentProfile(workspace_id=workspace.id, name="Developer", role="developer")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Schedule downstream",
        status="blocked",
    )
    session.add_all([agent, task])
    session.flush()
    source_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        work_package_id="source",
        title="Source",
        status="completed",
        order_index=10,
        result_summary="Done",
    )
    incomplete_source = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        work_package_id="incomplete-source",
        title="Incomplete source",
        status="queued",
        order_index=20,
    )
    session.add_all([source_step, incomplete_source])
    session.flush()
    blocked_downstream = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        work_package_id="blocked-downstream",
        title="Blocked downstream",
        status="blocked",
        order_index=30,
        dependencies={
            "after_step_ids": [str(source_step.id)],
            "blocked_reason": "runtime_space_paused",
            "blocked_resource_keys": ["runtime-space"],
            "token": "hidden-token",
        },
    )
    failed_downstream = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        work_package_id="failed-downstream",
        title="Failed downstream",
        status="failed",
        order_index=40,
        dependencies={
            "after_step_ids": [str(source_step.id)],
            "scheduler": {"reason": "old-failure"},
        },
    )
    waiting_downstream = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        work_package_id="waiting-downstream",
        title="Waiting downstream",
        status="blocked",
        order_index=50,
        dependencies={
            "after_step_ids": [str(source_step.id), str(incomplete_source.id)],
            "blocked_reason": "dependency_incomplete",
        },
    )
    session.add_all([blocked_downstream, failed_downstream, waiting_downstream])
    session.commit()
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/operator-actions",
        headers=_headers(owner.id),
        json={
            "action": "schedule_downstream_steps",
            "task_step_ids": [str(source_step.id)],
            "reason": "Source handoff is ready",
            "metadata": {"api_key": "sk-operator"},
        },
    )
    messages = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/messages", headers=_headers(owner.id)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "schedule_downstream_steps"
    assert body["task_status"] == "running"
    assert body["changed_step_ids"] == [str(blocked_downstream.id), str(failed_downstream.id)]
    assert body["details"]["source_step_ids"] == [str(source_step.id)]
    assert body["details"]["scheduled_downstream_step_ids"] == [
        str(blocked_downstream.id),
        str(failed_downstream.id),
    ]
    assert body["details"]["cleared_blocking_step_ids"] == [
        str(blocked_downstream.id),
        str(failed_downstream.id),
    ]
    assert body["warnings"] == [f"downstream_dependencies_incomplete:{waiting_downstream.id}"]
    session.refresh(task)
    session.refresh(blocked_downstream)
    session.refresh(failed_downstream)
    session.refresh(waiting_downstream)
    assert task.status == "running"
    assert blocked_downstream.status == "queued"
    assert failed_downstream.status == "queued"
    assert waiting_downstream.status == "blocked"
    assert blocked_downstream.dependencies == {
        "after_step_ids": [str(source_step.id)],
        "token": "hidden-token",
    }
    assert failed_downstream.dependencies == {"after_step_ids": [str(source_step.id)]}
    assert messages.status_code == 200
    message_payload = messages.json()["items"][0]["payload"]
    assert message_payload["metadata"]["api_key"] == "[redacted]"
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "task.operator.schedule_downstream_steps",
        )
    )
    assert audit is not None
    assert audit.audit_metadata["changed_step_ids"] == [
        str(blocked_downstream.id),
        str(failed_downstream.id),
    ]
    serialized = str(body)
    assert "hidden-token" not in serialized
    assert "sk-operator" not in serialized


def test_task_operator_action_requests_manager_review_step() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="custom_delivery_owner")
    developer = AgentProfile(workspace_id=workspace.id, name="Developer", role="developer")
    session.add_all([manager, developer])
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Needs PM review",
        status="running",
        team_snapshot={"team": {"manager_agent_profile_id": str(manager.id)}},
        project_plan={"planner_agent_profile_id": str(manager.id)},
    )
    no_manager_task = Task(
        workspace_id=workspace.id, created_by_user_id=owner.id, title="No manager", status="running"
    )
    session.add_all([task, no_manager_task])
    session.flush()
    session.add(
        TaskStep(
            workspace_id=workspace.id,
            task_id=task.id,
            assigned_agent_profile_id=developer.id,
            work_package_id="build",
            title="Build feature",
            status="completed",
            order_index=10,
        )
    )
    session.commit()
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/operator-actions",
        headers=_headers(owner.id),
        json={
            "action": "request_manager_review",
            "instruction": "Review the implementation and decide whether to accept.",
            "reason": "Operator wants acceptance check",
        },
    )
    missing_manager = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{no_manager_task.id}/operator-actions",
        headers=_headers(owner.id),
        json={"action": "request_manager_review"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["created_step_ids"]
    created_step = session.get(TaskStep, UUID(body["created_step_ids"][0]))
    assert created_step is not None
    assert created_step.assigned_agent_profile_id == manager.id
    assert created_step.work_package_id == "manager-summary-operator-1"
    assert created_step.required_role is None
    assert created_step.required_skills == []
    assert created_step.description == "Review the implementation and decide whether to accept."
    assert created_step.status == "queued"
    assert (
        created_step.dependencies["operator_action"]["reason"] == "Operator wants acceptance check"
    )
    assert body["details"]["manager_agent_profile_id"] == str(manager.id)
    assert missing_manager.status_code == 404
    expected_prompt = task.description or task.title
    default_review = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/operator-actions",
        headers=_headers(owner.id),
        json={"action": "request_manager_review"},
    )
    assert default_review.status_code == 200, default_review.text
    default_step = session.get(TaskStep, UUID(default_review.json()["created_step_ids"][0]))
    assert default_step is not None
    assert default_step.assigned_agent_profile_id == manager.id
    assert default_step.description == expected_prompt


def test_final_output_correction_creates_reconciliation_work() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Final polish",
        status="completed",
        final_output={"summary": "draft"},
    )
    session.add(task)
    session.commit()
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/corrections",
        headers=_headers(owner.id),
        json={
            "target_type": "final_output",
            "mode": "regenerate",
            "instruction": "Regenerate the final answer with a clearer structure.",
        },
    )
    assert response.status_code == 201
    step = session.get(TaskStep, UUID(response.json()["created_step_id"]))
    assert step is not None
    assert step.expected_artifacts == ["final_delivery"]
    assert step.dependencies["correction"]["target"]["target_type"] == "final_output"


def test_task_control_and_delivery_routes_close_manual_intervention_loop() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Manual delivery gate",
        status="running",
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Required delivery",
        status="completed",
        order_index=1,
        expected_artifacts=["final_delivery"],
    )
    session.add(step)
    session.commit()
    diagnostics = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/control-diagnostics",
        headers=_headers(owner.id),
    )
    review = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/delivery-review",
        headers=_headers(owner.id),
    )
    deferred = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/delivery-decision",
        headers=_headers(owner.id),
        json={"action": "approve", "summary": "Approve after review."},
    )
    overridden = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/delivery-decision",
        headers=_headers(owner.id),
        json={
            "action": "approve",
            "summary": "Approve with documented exception.",
            "override": True,
            "override_reason": "Business owner accepted the missing optional delivery artifact.",
        },
    )
    assert diagnostics.status_code == 200
    assert diagnostics.json()["control"] == {}
    assert review.status_code == 200
    assert review.json()["status"] == "incomplete"
    assert deferred.status_code == 200
    assert deferred.json()["details"]["finalization"]["status"] == "deferred"
    assert deferred.json()["details"]["finalization"]["reason"] == "expected_artifacts_missing"
    assert overridden.status_code == 200
    assert overridden.json()["details"]["finalization"]["status"] == "finalized"
    assert overridden.json()["details"]["finalization"]["reason"] == "overridden"
    session.refresh(task)
    assert task.status == "completed"
