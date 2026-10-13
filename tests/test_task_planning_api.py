"""Workspace-scoped product API verification on PostgreSQL."""

import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.agents.providers.credentials import ModelProviderCredentialCommandService
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.orchestration.planning.attempt_models import TaskPlanningAttempt
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.state import RunStatus
from opsmesh.orchestration.tasks.models import Task, TaskMessage, TaskStep
from opsmesh.orchestration.tasks.state import TaskStatus
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.teams.management.models import AgentTeam
from tests.fixtures.team_agent_delivery import deliver_team_task
from tests.fixtures.workspace_api import _client, _headers, _pending_dispatch_count, _seed_workspace


def test_create_task_with_team_captures_workspace_team_snapshot() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    manager = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "PM", "role": "project_manager"},
    )
    developer = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "Frontend Dev",
            "role": "frontend_engineer",
            "model_settings": {"model_api": "responses"},
        },
    )
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={
            "name": "Product Team",
            "team_type": "software",
            "manager_agent_profile_id": manager.json()["id"],
        },
    )
    member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": developer.json()["id"],
            "team_role": "frontend_engineer",
            "department": "Engineering",
            "skill_weights": {"react": 0.9},
        },
    )
    created_task = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={
            "input": {"planning_mode": "agent"},
            "title": "Build dashboard",
            "agent_team_id": team.json()["id"],
        },
    )
    assert member.status_code == 201
    assert created_task.status_code == 201
    snapshot = created_task.json()["team_snapshot"]
    project_plan = created_task.json()["project_plan"]
    assert snapshot["snapshot_version"] == 3
    assert snapshot["team"]["capability_policy_version"] == 1
    assert snapshot["team"]["id"] == team.json()["id"]
    assert snapshot["team"]["manager_agent_profile_id"] == manager.json()["id"]
    assert snapshot["members"][0]["agent_profile_id"] == developer.json()["id"]
    assert snapshot["members"][0]["agent"]["model_api"] == "responses"
    assert snapshot["members"][0]["department"] == "Engineering"
    assert snapshot["members"][0]["skill_weights"] == {"react": 0.9}
    assert project_plan["plan_version"] == 1
    assert project_plan["planner_agent_profile_id"] == manager.json()["id"]
    assert [package["package_id"] for package in project_plan["work_packages"]] == [
        "manager-planning"
    ]
    assert project_plan["strategy"] == "agent_planning_pending"
    assert project_plan["work_packages"][0]["review_policy"]["mode"] == "agent_planning"


def test_create_task_matches_requested_work_packages_to_team_members() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    manager = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "PM", "role": "project_manager"},
    )
    designer = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Designer", "role": "ui_designer"},
    )
    developer = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Developer", "role": "frontend_engineer"},
    )
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={
            "name": "Product Team",
            "team_type": "software",
            "manager_agent_profile_id": manager.json()["id"],
        },
    )
    client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": designer.json()["id"],
            "team_role": "ui_designer",
            "skill_weights": {"figma": 1.0},
        },
    )
    client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": developer.json()["id"],
            "team_role": "frontend_engineer",
            "skill_weights": {"react": 0.9},
        },
    )
    created_task = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={
            "title": "Build landing page",
            "agent_team_id": team.json()["id"],
            "input": {
                "planning_mode": "explicit",
                "work_packages": [
                    {
                        "assigned_agent_profile_id": designer.json()["id"],
                        "package_id": "ui-design",
                        "title": "UI Design",
                        "required_role": "ui_designer",
                        "required_skills": ["figma"],
                    },
                    {
                        "assigned_agent_profile_id": developer.json()["id"],
                        "package_id": "frontend-build",
                        "title": "Frontend Build",
                        "required_role": "frontend_engineer",
                        "required_skills": ["react"],
                    },
                ],
            },
        },
    )
    assert created_task.status_code == 201
    work_packages = created_task.json()["project_plan"]["work_packages"]
    by_id = {package["package_id"]: package for package in work_packages}
    assert by_id["ui-design"]["assigned_agent_profile_id"] == designer.json()["id"]
    assert by_id["frontend-build"]["assigned_agent_profile_id"] == developer.json()["id"]


@pytest.mark.skipif(os.getenv("OPSMESH_TEST_TEMPORAL") != "1", reason="Real Temporal required")
def test_api_team_task_e2e_runs_workers_and_accepts_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner", with_provider=True)
    ModelProviderCredentialCommandService(
        session,
        SecretEncryptionService(secret="change-me-credential-encryption-secret", key_id="local"),
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Workspace OpenAI",
        provider="openai",
        api_key="sk-e2e-provider-secret",
        default_model="gpt-4.1",
        base_url=None,
        is_default=True,
    )
    manager = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "PM",
            "role": "project_manager",
            "instructions": "Plan, coordinate, and accept delivery.",
            "model": "manager-model",
        },
    )
    researcher = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "Researcher",
            "role": "researcher",
            "instructions": "Collect market facts.",
            "model": "researcher-model",
        },
    )
    analyst = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "Analyst",
            "role": "analyst",
            "instructions": "Analyze the research output.",
            "model": "analyst-model",
        },
    )
    assert manager.status_code == 201
    assert researcher.status_code == 201
    assert analyst.status_code == 201
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={
            "name": "Market Team",
            "team_type": "research",
            "manager_agent_profile_id": manager.json()["id"],
        },
    )
    assert team.status_code == 201
    team_id = team.json()["id"]
    researcher_member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team_id}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": researcher.json()["id"],
            "team_role": "researcher",
            "department": "Research",
            "skill_weights": {"market_research": 1.0},
            "order_index": 1,
        },
    )
    analyst_member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team_id}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": analyst.json()["id"],
            "team_role": "analyst",
            "department": "Analysis",
            "skill_weights": {"analysis": 1.0},
            "order_index": 2,
        },
    )
    assert researcher_member.status_code == 201
    assert analyst_member.status_code == 201
    org_chart = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team_id}/org-chart", headers=_headers(owner.id)
    )
    assert org_chart.status_code == 200
    assert org_chart.json()["manager_agent"]["id"] == manager.json()["id"]
    assert org_chart.json()["capacity_summary"]["accepting_members"] == 2
    created_task = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={
            "agent_team_id": team_id,
            "domain_type": "research",
            "title": "Q2 market analysis",
            "description": "Produce a concise market analysis.",
            "priority": 7,
            "input": {
                "planning_mode": "explicit",
                "work_packages": [
                    {
                        "package_id": "manager-planning",
                        "title": "Prepare work",
                        "assigned_agent_profile_id": manager.json()["id"],
                    },
                    {
                        "assigned_agent_profile_id": researcher.json()["id"],
                        "package_id": "market-research",
                        "depends_on": ["manager-planning"],
                        "title": "Market research",
                        "required_role": "researcher",
                        "required_skills": ["market_research"],
                    },
                    {
                        "assigned_agent_profile_id": analyst.json()["id"],
                        "package_id": "market-analysis",
                        "title": "Market analysis",
                        "required_role": "analyst",
                        "required_skills": ["analysis"],
                        "depends_on": ["market-research"],
                    },
                    {
                        "package_id": "manager-summary",
                        "title": "Review delivery",
                        "assigned_agent_profile_id": manager.json()["id"],
                        "depends_on": ["market-analysis"],
                        "review_policy": {"mode": "final_acceptance", "reviewer": "user"},
                    },
                ],
            },
        },
    )
    assert created_task.status_code == 201
    task_id = UUID(created_task.json()["id"])
    assert created_task.json()["status"] == TaskStatus.QUEUED.value
    assert _pending_dispatch_count(session, workspace.id) == 1
    model_calls = deliver_team_task(session, task_id)
    session.expire_all()
    task = session.get(Task, task_id)
    steps = session.scalars(
        select(TaskStep).where(TaskStep.task_id == task_id).order_by(TaskStep.order_index)
    ).all()
    runs = session.scalars(select(AgentRun).where(AgentRun.task_id == task_id)).all()
    messages = session.scalars(
        select(TaskMessage).where(TaskMessage.task_id == task_id).order_by(TaskMessage.sequence)
    ).all()
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "task.created",
            AuditEvent.target_id == str(task_id),
        )
    )
    completed_tasks = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks?status=completed", headers=_headers(owner.id)
    )
    timeline = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task_id}/timeline", headers=_headers(owner.id)
    )
    observation = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task_id}/observation", headers=_headers(owner.id)
    )
    assert model_calls == 4
    assert _pending_dispatch_count(session, workspace.id) == 0
    assert task is not None
    assert task.status == TaskStatus.COMPLETED.value
    assert task.final_output is not None
    assert task.final_output["pm_acceptance"]["decision"] == "approved"
    assert [step.work_package_id for step in steps] == [
        "manager-planning",
        "market-research",
        "market-analysis",
        "manager-summary",
    ]
    assert {run.status for run in runs} == {RunStatus.COMPLETED.value}
    message_types = [message.message_type for message in messages]
    assert "planning.completed" in message_types
    assert message_types.count("step.started") == 4
    assert message_types.count("step.completed") == 4
    assert message_types[-1] == "pm.acceptance_decision"
    assert messages[-1].payload["decision"] == "approved"
    assert audit is not None
    assert audit.audit_metadata["initial_run_staged"] is True
    assert completed_tasks.status_code == 200
    assert completed_tasks.json()["total"] == 1
    assert completed_tasks.json()["items"][0]["id"] == str(task_id)
    assert timeline.status_code == 200
    assert timeline.json()["summary"]["returned_events"] >= 1
    assert observation.status_code == 200
    assert observation.json()["summary"]["status"] == TaskStatus.COMPLETED.value


def test_retry_task_plan_repairs_blocked_planning_failure() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner", with_provider=True)
    manager = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "PM", "role": "project_manager"},
    )
    developer = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Developer", "role": "frontend_engineer"},
    )
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={
            "name": "Product Team",
            "team_type": "software",
            "manager_agent_profile_id": manager.json()["id"],
        },
    )
    client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": developer.json()["id"],
            "team_role": "frontend_engineer",
            "skill_weights": {"react": 0.9},
        },
    )
    created_task = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={
            "title": "Build dashboard",
            "agent_team_id": team.json()["id"],
            "input": {
                "planning_mode": "explicit",
                "work_packages": [
                    {
                        "assigned_agent_profile_id": developer.json()["id"],
                        "package_id": "build-ui",
                        "title": "Build UI",
                        "required_role": "frontend_engineer",
                    },
                    {
                        "assigned_agent_profile_id": developer.json()["id"],
                        "package_id": "build-ui",
                        "title": "Build UI duplicate",
                        "required_role": "frontend_engineer",
                    },
                ],
            },
        },
    )
    retry = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{created_task.json()['id']}/plan/retry",
        headers=_headers(owner.id),
        json={
            "enqueue": True,
            "input": {
                "planning_mode": "explicit",
                "work_packages": [
                    {
                        "assigned_agent_profile_id": developer.json()["id"],
                        "package_id": "build-ui",
                        "title": "Build UI",
                        "required_role": "frontend_engineer",
                        "required_skills": ["react"],
                    }
                ],
            },
        },
    )
    listed_attempts = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{created_task.json()['id']}/planning-attempts",
        headers=_headers(owner.id),
    )
    failed_attempts = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{created_task.json()['id']}/planning-attempts?status=failed",
        headers=_headers(owner.id),
    )
    attempts = session.scalars(
        select(TaskPlanningAttempt).order_by(TaskPlanningAttempt.attempt_number)
    ).all()
    queued_run = session.scalar(
        select(AgentRun).where(AgentRun.task_id == UUID(retry.json()["id"]))
    )
    assert created_task.status_code == 201
    assert created_task.json()["status"] == "blocked"
    assert created_task.json()["project_plan"] is None
    assert retry.status_code == 200
    assert retry.json()["status"] == "queued"
    assert retry.json()["project_plan"]["work_packages"][0]["package_id"] == "build-ui"
    assert [attempt.status for attempt in attempts] == ["failed", "completed"]
    assert [attempt.retry_count for attempt in attempts] == [0, 1]
    assert listed_attempts.status_code == 200
    assert listed_attempts.json()["total"] == 2
    assert [item["status"] for item in listed_attempts.json()["items"]] == ["completed", "failed"]
    assert failed_attempts.status_code == 200
    assert failed_attempts.json()["total"] == 1
    assert failed_attempts.json()["items"][0]["validation_errors"]
    assert queued_run is not None
    assert _pending_dispatch_count(session, workspace.id) == 1


def test_planning_routes_reject_foreign_task_ids() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="other@example.com", slug="other-space"
    )
    team = AgentTeam(workspace_id=other_workspace.id, name="Foreign team", team_type="software")
    session.add(team)
    session.flush()
    task = Task(
        workspace_id=other_workspace.id,
        created_by_user_id=other_owner.id,
        title="Foreign team task",
        agent_team_id=team.id,
        project_plan={"plan_id": "foreign-plan", "work_packages": []},
    )
    session.add(task)
    session.flush()
    attempt = TaskPlanningAttempt(
        workspace_id=other_workspace.id,
        task_id=task.id,
        attempt_number=1,
        retry_count=0,
        status="completed",
        input_snapshot={"title": task.title},
        output_snapshot=task.project_plan,
        validation_errors=[],
        created_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    session.add(attempt)
    session.commit()
    retry = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/plan/retry",
        headers=_headers(owner.id),
        json={"enqueue": False},
    )
    regenerate = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/plan/regenerate",
        headers=_headers(owner.id),
        json={"enqueue": False},
    )
    attempts = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/planning-attempts",
        headers=_headers(owner.id),
    )
    session.refresh(task)
    session.refresh(attempt)
    assert retry.status_code == 403
    assert regenerate.status_code == 403
    assert attempts.status_code == 403
    assert task.workspace_id == other_workspace.id
    assert task.project_plan == {"plan_id": "foreign-plan", "work_packages": []}
    assert attempt.workspace_id == other_workspace.id
    assert session.query(TaskPlanningAttempt).count() == 1


def test_regenerate_task_plan_preserves_completed_work_packages() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner", with_provider=True)
    manager = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "PM", "role": "project_manager"},
    )
    developer = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Developer", "role": "frontend_engineer"},
    )
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={
            "name": "Product Team",
            "team_type": "software",
            "manager_agent_profile_id": manager.json()["id"],
        },
    )
    client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": developer.json()["id"],
            "team_role": "frontend_engineer",
            "skill_weights": {"react": 0.9},
        },
    )
    created_task = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={
            "title": "Build dashboard",
            "agent_team_id": team.json()["id"],
            "input": {
                "planning_mode": "explicit",
                "work_packages": [
                    {
                        "assigned_agent_profile_id": developer.json()["id"],
                        "package_id": "frontend-build",
                        "title": "Frontend Build",
                        "required_role": "frontend_engineer",
                    }
                ],
            },
        },
    )
    task_id = UUID(created_task.json()["id"])
    initial_queue_depth = _pending_dispatch_count(session, workspace.id)
    step = session.scalar(
        select(TaskStep).where(
            TaskStep.task_id == task_id, TaskStep.work_package_id == "frontend-build"
        )
    )
    assert step is not None
    step.status = "completed"
    step.result_summary = "Frontend build completed."
    session.commit()
    regenerated = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task_id}/plan/regenerate",
        headers=_headers(owner.id),
        json={
            "enqueue": True,
            "input": {
                "planning_mode": "explicit",
                "work_packages": [
                    {
                        "assigned_agent_profile_id": developer.json()["id"],
                        "package_id": "frontend-build-v2",
                        "title": "Frontend Build V2",
                        "required_role": "frontend_engineer",
                    }
                ],
            },
        },
    )
    attempts_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task_id}/planning-attempts",
        headers=_headers(owner.id),
    )
    session.refresh(step)
    messages = session.scalars(
        select(TaskMessage).where(
            TaskMessage.task_id == task_id, TaskMessage.message_type == "planning.regenerated"
        )
    ).all()
    assert regenerated.status_code == 200
    assert step.status == "completed"
    regeneration = regenerated.json()["project_plan"]["regeneration"]
    assert regeneration["mode"] == "future_only"
    assert regeneration["preserved_completed_work_package_ids"] == ["frontend-build"]
    assert messages[0].payload["preserved_completed_work_package_ids"] == ["frontend-build"]
    assert attempts_response.status_code == 200
    assert attempts_response.json()["total"] == 2
    assert (
        attempts_response.json()["items"][0]["output_snapshot"]["regeneration"]["mode"]
        == "future_only"
    )
    assert initial_queue_depth == 1
    assert _pending_dispatch_count(session, workspace.id) == initial_queue_depth


def test_mutate_task_plan_endpoint_adds_future_work_and_audits() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner", with_provider=True)
    manager = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "PM",
            "role": "project_manager",
            "skills": {"planning": 1, "coordination": 1, "review": 1, "synthesis": 1},
        },
    )
    developer = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Developer", "role": "developer"},
    )
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={
            "name": "Product Team",
            "team_type": "software",
            "manager_agent_profile_id": manager.json()["id"],
        },
    )
    client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": manager.json()["id"],
            "team_role": "project_manager",
            "skill_weights": {"planning": 1, "coordination": 1, "review": 1, "synthesis": 1},
        },
    )
    client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={"agent_profile_id": developer.json()["id"], "team_role": "developer"},
    )
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={
            "title": "Plan follow-up",
            "agent_team_id": team.json()["id"],
            "input": {
                "planning_mode": "explicit",
                "work_packages": [
                    {
                        "assigned_agent_profile_id": developer.json()["id"],
                        "package_id": "initial-work",
                        "title": "Initial work",
                        "required_role": "developer",
                    }
                ],
            },
        },
    )
    task_id = created.json()["id"]
    mutation = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task_id}/plan/mutate",
        headers=_headers(owner.id),
        json={
            "mutation_id": "api-add-follow-up",
            "expected_revision": 0,
            "reason": "Add an explicit follow-up",
            "operations": [
                {
                    "operation": "add",
                    "package": {
                        "package_id": "follow-up",
                        "title": "Follow-up",
                        "description": "Complete the follow-up work.",
                        "required_role": "project_manager",
                        "required_skills": [],
                        "assigned_agent_profile_id": manager.json()["id"],
                        "depends_on": [],
                        "expected_artifacts": [],
                        "acceptance_criteria": ["Follow-up is complete."],
                    },
                }
            ],
        },
    )
    task = session.get(Task, UUID(task_id))
    assert mutation.status_code == 200, mutation.text
    assert task is not None
    assert any(
        package["package_id"] == "follow-up" for package in task.project_plan["work_packages"]
    )
    assert (
        session.scalar(
            select(TaskStep).where(
                TaskStep.task_id == task.id, TaskStep.work_package_id == "follow-up"
            )
        )
        is not None
    )
    assert (
        session.scalar(
            select(AuditEvent).where(
                AuditEvent.workspace_id == workspace.id, AuditEvent.action == "task.plan_mutated"
            )
        )
        is not None
    )


def test_create_task_rejects_foreign_team_reference() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="other@example.com", slug="other-space"
    )
    foreign_team = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/teams",
        headers=_headers(other_owner.id),
        json={"name": "Foreign Team"},
    )
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={"title": "Should fail", "agent_team_id": foreign_team.json()["id"]},
    )
    assert response.status_code == 403


def test_task_plan_diagnostics_explains_assignment_and_dependency_quality() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="other-plan@example.com", slug="other-plan"
    )
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    developer = AgentProfile(workspace_id=workspace.id, name="Developer", role="developer")
    session.add_all([manager, developer])
    session.flush()
    manager_member_id = uuid4()
    lead_member_id = uuid4()
    member_id = uuid4()
    team_snapshot = {
        "team": {"manager_agent_profile_id": str(manager.id)},
        "members": [
            {
                "id": str(manager_member_id),
                "agent_profile_id": str(manager.id),
                "team_role": "project_manager",
                "department": "Delivery",
                "accepts_tasks": True,
                "is_required": True,
                "max_concurrent_tasks": 3,
                "order_index": 0,
            },
            {
                "id": str(lead_member_id),
                "agent_profile_id": str(developer.id),
                "reports_to_member_id": str(manager_member_id),
                "team_role": "team_lead",
                "department": "Engineering",
                "accepts_tasks": True,
                "is_required": True,
                "max_concurrent_tasks": 2,
                "order_index": 1,
            },
            {
                "id": str(member_id),
                "agent_profile_id": str(developer.id),
                "reports_to_member_id": str(lead_member_id),
                "team_role": "developer",
                "department": "Engineering",
                "skill_weights": {"python": 0.9},
                "accepts_tasks": True,
                "is_required": True,
                "max_concurrent_tasks": 3,
                "order_index": 2,
            },
        ],
    }
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Build backend",
        agent_team_id=None,
        team_snapshot=team_snapshot,
        project_plan={
            "plan_id": "plan-1",
            "strategy": "agent_sdk",
            "work_packages": [
                {
                    "package_id": "manager-planning",
                    "title": "Manager planning",
                    "required_role": "project_manager",
                    "assigned_agent_profile_id": str(manager.id),
                    "depends_on": [],
                },
                {
                    "package_id": "build",
                    "title": "Build API",
                    "required_role": "developer",
                    "required_skills": ["python"],
                    "assigned_agent_profile_id": str(developer.id),
                    "depends_on": ["manager-planning"],
                    "review_policy": {"token": "hidden-token"},
                },
                {
                    "package_id": "design",
                    "title": "Design UI",
                    "required_role": "designer",
                    "required_skills": ["figma"],
                    "assigned_agent_profile_id": None,
                    "depends_on": ["manager-planning"],
                },
                {
                    "package_id": "cycle-a",
                    "title": "Cycle A",
                    "required_role": "developer",
                    "assigned_agent_profile_id": str(developer.id),
                    "depends_on": ["cycle-b"],
                },
                {
                    "package_id": "cycle-b",
                    "title": "Cycle B",
                    "required_role": "developer",
                    "assigned_agent_profile_id": str(developer.id),
                    "depends_on": ["cycle-a"],
                },
                {
                    "package_id": "manager-summary",
                    "title": "Manager summary",
                    "required_role": "project_manager",
                    "assigned_agent_profile_id": str(manager.id),
                    "depends_on": ["build", "design", "missing-package"],
                },
            ],
        },
    )
    no_plan_task = Task(workspace_id=workspace.id, created_by_user_id=owner.id, title="No plan")
    session.add_all([task, no_plan_task])
    session.flush()
    load_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=developer.id,
        title="Existing build",
        status="running",
    )
    session.add(load_step)
    session.flush()
    session.add(
        AgentRun(
            workspace_id=workspace.id,
            task_id=task.id,
            task_step_id=load_step.id,
            agent_profile_id=developer.id,
            status=RunStatus.RUNNING.value,
            input={},
        )
    )
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/plan/diagnostics",
        headers=_headers(owner.id),
    )
    no_plan_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{no_plan_task.id}/plan/diagnostics",
        headers=_headers(owner.id),
    )
    foreign_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/tasks/{task.id}/plan/diagnostics",
        headers=_headers(other_owner.id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["plan_present"] is True
    assert body["plan_id"] == "plan-1"
    assert body["manager"] == {
        "manager_agent_profile_id": str(manager.id),
        "has_manager": True,
        "has_manager_planning": True,
        "has_manager_summary": True,
    }
    assert body["org_health"]["manager_count"] == 1
    assert body["org_health"]["lead_count"] == 1
    assert body["org_health"]["blocked_reasons"] == ["missing_lead_review"]
    assert body["summary"] == {
        "total_packages": 6,
        "assigned_packages": 5,
        "unassigned_packages": 1,
        "unknown_dependencies": 1,
        "cycle_packages": 2,
    }
    assert set(body["blocked_reasons"]) == {
        "unassigned_work_packages",
        "unknown_dependencies",
        "dependency_cycle",
        "missing_lead_review",
    }
    assert body["dependency_graph"]["unknown_dependencies"] == ["missing-package"]
    assert body["dependency_graph"]["cycle_package_ids"] == ["cycle-a", "cycle-b"]
    by_package = {package["package_id"]: package for package in body["packages"]}
    assert by_package["design"]["assignment_status"] == "unassigned"
    assert by_package["build"]["assignment_status"] == "assigned"
    assert by_package["build"]["review_policy"]["token"] == "[redacted]"
    assert by_package["build"]["recommended_matches"][0]["agent_profile_id"] == str(developer.id)
    assert by_package["build"]["recommended_matches"][0]["current_load"] == 1
    assert "hidden-token" not in str(body)
    assert no_plan_response.status_code == 200
    assert no_plan_response.json()["blocked_reasons"] == ["no_project_plan"]
    assert foreign_response.status_code == 403
