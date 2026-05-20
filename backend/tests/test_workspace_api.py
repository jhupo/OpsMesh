from collections.abc import Generator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.artifacts.models import Artifact
from backend.app.core.config import Settings, get_settings
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.planning.models import TaskPlanningAttempt
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.status import TaskStatus
from backend.app.teams.models import AgentTeamMember
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


def test_workspace_and_resource_api_enforces_scope_and_roles() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    viewer, other_workspace = _seed_workspace(
        session,
        role="viewer",
        email="viewer@example.com",
        slug="viewer-space",
    )

    response = client.get("/api/v1/workspaces", headers=_headers(owner.id))
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == str(workspace.id)

    forbidden = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/agents",
        headers=_headers(owner.id),
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
        json={"title": "Q2 Market Analysis", "domain_type": "market_research"},
    )
    assert created_task.status_code == 201
    assert created_task.json()["created_by_user_id"] == str(owner.id)

    viewer_create = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/tasks",
        headers=_headers(viewer.id),
        json={"title": "Should fail"},
    )
    assert viewer_create.status_code == 403

    tasks = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks?status=queued",
        headers=_headers(owner.id),
    )
    assert tasks.status_code == 200
    assert tasks.json()["total"] == 1

    audit = client.get(
        f"/api/v1/workspaces/{workspace.id}/audit-events",
        headers=_headers(owner.id),
    )
    assert audit.status_code == 200
    actions = {item["action"] for item in audit.json()["items"]}
    assert {"agent.created", "team.created", "task.created"} <= actions


def test_create_task_is_idempotent_within_workspace() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    headers = _headers(owner.id) | {"Idempotency-Key": "create-q2-task"}

    first = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=headers,
        json={"title": "Q2 Market Analysis"},
    )
    second = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=headers,
        json={"title": "Q2 Market Analysis"},
    )

    tasks = session.scalars(select(Task).where(Task.workspace_id == workspace.id)).all()
    runs = session.scalars(select(AgentRun).where(AgentRun.workspace_id == workspace.id)).all()
    audit = client.get(
        f"/api/v1/workspaces/{workspace.id}/audit-events",
        headers=_headers(owner.id),
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
        f"/api/v1/workspaces/{workspace.id}/audit-events",
        headers=_headers(owner.id),
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


def test_team_member_api_stores_persistent_org_metadata_and_reporting_line() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    manager_agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "PM", "role": "project_manager"},
    )
    developer_agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Frontend Dev", "role": "frontend_engineer"},
    )
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={"name": "Product Team", "team_type": "software"},
    )
    manager_member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": manager_agent.json()["id"],
            "team_role": "project_manager",
            "department": "Management",
            "position_title": "Project Manager",
            "responsibilities": ["拆解需求", "验收交付"],
            "skill_weights": {"planning": 1.0, "review": 0.9},
            "max_concurrent_tasks": 3,
            "order_index": 0,
        },
    )
    developer_member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": developer_agent.json()["id"],
            "reports_to_member_id": manager_member.json()["id"],
            "team_role": "frontend_engineer",
            "department": "Engineering",
            "position_title": "Senior Frontend Engineer",
            "responsibilities": ["实现 UI", "修复前端缺陷"],
            "skill_weights": {"react": 0.95, "typescript": 0.9},
            "availability": {"timezone": "Asia/Shanghai"},
            "max_concurrent_tasks": 2,
            "accepts_tasks": True,
            "order_index": 1,
        },
    )
    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
    )

    assert manager_member.status_code == 201
    assert developer_member.status_code == 201
    developer_body = developer_member.json()
    assert developer_body["reports_to_member_id"] == manager_member.json()["id"]
    assert developer_body["department"] == "Engineering"
    assert developer_body["position_title"] == "Senior Frontend Engineer"
    assert developer_body["responsibilities"] == ["实现 UI", "修复前端缺陷"]
    assert developer_body["skill_weights"] == {"react": 0.95, "typescript": 0.9}
    assert developer_body["availability"] == {"timezone": "Asia/Shanghai"}
    assert developer_body["max_concurrent_tasks"] == 2
    assert developer_body["status"] == "active"
    assert listed.status_code == 200
    assert listed.json()["total"] == 2

    stored = session.get(AgentTeamMember, UUID(developer_body["id"]))
    assert stored is not None
    assert stored.reports_to_member_id == UUID(manager_member.json()["id"])


def test_team_member_api_rejects_foreign_agent_and_reporting_member() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other@example.com",
        slug="other-space",
    )
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={"name": "Team"},
    )
    local_agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Local", "role": "manager"},
    )
    local_member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={"agent_profile_id": local_agent.json()["id"], "team_role": "manager"},
    )
    foreign_agent = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/agents",
        headers=_headers(other_owner.id),
        json={"name": "Foreign", "role": "developer"},
    )
    foreign_team = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/teams",
        headers=_headers(other_owner.id),
        json={"name": "Other Team"},
    )
    foreign_member = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/teams/{foreign_team.json()['id']}/members",
        headers=_headers(other_owner.id),
        json={"agent_profile_id": foreign_agent.json()["id"], "team_role": "developer"},
    )

    bad_agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={"agent_profile_id": foreign_agent.json()["id"], "team_role": "developer"},
    )
    bad_report = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": local_agent.json()["id"],
            "team_role": "developer",
            "reports_to_member_id": foreign_member.json()["id"],
        },
    )

    assert local_member.status_code == 201
    assert bad_agent.status_code == 404
    assert bad_report.status_code == 404


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
        json={"name": "Frontend Dev", "role": "frontend_engineer"},
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
        json={"title": "Build dashboard", "agent_team_id": team.json()["id"]},
    )

    assert member.status_code == 201
    assert created_task.status_code == 201
    snapshot = created_task.json()["team_snapshot"]
    project_plan = created_task.json()["project_plan"]
    assert snapshot["snapshot_version"] == 1
    assert snapshot["team"]["id"] == team.json()["id"]
    assert snapshot["team"]["manager_agent_profile_id"] == manager.json()["id"]
    assert snapshot["members"][0]["agent_profile_id"] == developer.json()["id"]
    assert snapshot["members"][0]["department"] == "Engineering"
    assert snapshot["members"][0]["skill_weights"] == {"react": 0.9}
    assert project_plan["plan_version"] == 1
    assert project_plan["planner_agent_profile_id"] == manager.json()["id"]
    assert [package["package_id"] for package in project_plan["work_packages"]] == [
        "manager-planning",
        "frontend_engineer-1",
        "manager-summary",
    ]
    assert project_plan["work_packages"][1]["required_skills"] == ["react"]


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
                "work_packages": [
                    {
                        "package_id": "ui-design",
                        "title": "UI Design",
                        "required_role": "ui_designer",
                        "required_skills": ["figma"],
                    },
                    {
                        "package_id": "frontend-build",
                        "title": "Frontend Build",
                        "required_role": "frontend_engineer",
                        "required_skills": ["react"],
                    },
                ]
            },
        },
    )

    assert created_task.status_code == 201
    work_packages = created_task.json()["project_plan"]["work_packages"]
    by_id = {package["package_id"]: package for package in work_packages}
    assert by_id["ui-design"]["assigned_agent_profile_id"] == designer.json()["id"]
    assert by_id["frontend-build"]["assigned_agent_profile_id"] == developer.json()["id"]


def test_retry_task_plan_repairs_blocked_planning_failure() -> None:
    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
    )
    client, session = _client(queue=queue)
    owner, workspace = _seed_workspace(session, role="owner")
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
                "work_packages": [
                    {
                        "package_id": "build-ui",
                        "title": "Build UI",
                        "required_role": "frontend_engineer",
                    },
                    {
                        "package_id": "build-ui",
                        "title": "Build UI duplicate",
                        "required_role": "frontend_engineer",
                    },
                ]
            },
        },
    )

    retry = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{created_task.json()['id']}/plan/retry",
        headers=_headers(owner.id),
        json={
            "enqueue": True,
            "input": {
                "work_packages": [
                    {
                        "package_id": "build-ui",
                        "title": "Build UI",
                        "required_role": "frontend_engineer",
                        "required_skills": ["react"],
                    }
                ]
            },
        },
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
    assert retry.json()["project_plan"]["work_packages"][1]["package_id"] == "build-ui"
    assert [attempt.status for attempt in attempts] == ["failed", "completed"]
    assert [attempt.retry_count for attempt in attempts] == [0, 1]
    assert queued_run is not None
    assert queue.count_queued(workspace_id=workspace.id) == 1


def test_regenerate_task_plan_preserves_completed_work_packages() -> None:
    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
    )
    client, session = _client(queue=queue)
    owner, workspace = _seed_workspace(session, role="owner")
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
                "work_packages": [
                    {
                        "package_id": "frontend-build",
                        "title": "Frontend Build",
                        "required_role": "frontend_engineer",
                    }
                ]
            },
        },
    )
    task_id = UUID(created_task.json()["id"])
    step = session.scalar(
        select(TaskStep).where(
            TaskStep.task_id == task_id,
            TaskStep.work_package_id == "frontend-build",
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
                "work_packages": [
                    {
                        "package_id": "frontend-build-v2",
                        "title": "Frontend Build V2",
                        "required_role": "frontend_engineer",
                    }
                ]
            },
        },
    )

    session.refresh(step)
    messages = session.scalars(
        select(TaskMessage).where(
            TaskMessage.task_id == task_id,
            TaskMessage.message_type == "planning.regenerated",
        )
    ).all()

    assert regenerated.status_code == 200
    assert step.status == "completed"
    regeneration = regenerated.json()["project_plan"]["regeneration"]
    assert regeneration["mode"] == "future_only"
    assert regeneration["preserved_completed_work_package_ids"] == ["frontend-build"]
    assert messages[0].payload["preserved_completed_work_package_ids"] == ["frontend-build"]
    assert queue.count_queued(workspace_id=workspace.id) == 0


def test_create_task_rejects_foreign_team_reference() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other@example.com",
        slug="other-space",
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

    assert response.status_code == 404


def test_model_provider_credentials_are_created_without_returning_secret() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
        json={
            "name": "OpenAI Prod",
            "provider": "openai",
            "api_key": "sk-secret",
            "base_url": "https://api.openai.com/v1",
            "default_model": "gpt-4.1-mini",
            "is_default": True,
        },
    )
    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
    )

    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "OpenAI Prod"
    assert body["is_default"] is True
    assert body["default_model"] == "gpt-4.1-mini"
    assert body["api_key_fingerprint"].startswith("sha256:")
    assert body["health_status"] == "unknown"
    assert body["last_success_at"] is None
    assert body["last_failure_at"] is None
    assert body["last_failure_code"] is None
    assert body["last_failure_message"] is None
    assert "api_key" not in body
    assert "encrypted_api_key" not in body
    assert listed.status_code == 200
    assert listed.json()["total"] == 1


def test_create_agent_can_reference_workspace_model_provider_credential() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    credential = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
        json={
            "name": "OpenRouter",
            "provider": "openai-compatible",
            "api_key": "sk-router",
            "base_url": "https://openrouter.ai/api/v1",
            "default_model": "openai/gpt-4.1-mini",
        },
    )
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other@example.com",
        slug="other",
    )

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "Router Agent",
            "role": "researcher",
            "model": "workspace-default",
            "model_provider_credential_id": credential.json()["id"],
        },
    )
    cross_workspace = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/agents",
        headers=_headers(other_owner.id),
        json={
            "name": "Bad Agent",
            "role": "researcher",
            "model_provider_credential_id": credential.json()["id"],
        },
    )

    assert created.status_code == 201
    assert created.json()["model_provider_credential_id"] == credential.json()["id"]
    assert cross_workspace.status_code == 400


def test_model_provider_credentials_can_be_updated_rotated_defaulted_and_disabled() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    first = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
        json={
            "name": "First",
            "provider": "openai",
            "api_key": "sk-first",
            "default_model": "gpt-4.1",
            "is_default": True,
        },
    )
    second = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
        json={
            "name": "Second",
            "provider": "openai-compatible",
            "api_key": "sk-second",
            "base_url": "https://llm.example.test/v1",
            "default_model": "provider/default",
        },
    )
    assert first.status_code == 201
    assert second.status_code == 201

    updated = client.patch(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/{second.json()['id']}",
        headers=_headers(owner.id),
        json={"name": "Second Updated", "default_model": "provider/new-default"},
    )
    rotated = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/"
        f"{second.json()['id']}/rotate-key",
        headers=_headers(owner.id),
        json={"api_key": "sk-second-rotated"},
    )
    defaulted = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/"
        f"{second.json()['id']}/set-default",
        headers=_headers(owner.id),
    )
    disabled = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/"
        f"{first.json()['id']}/disable",
        headers=_headers(owner.id),
    )
    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
    )

    assert updated.status_code == 200
    assert updated.json()["name"] == "Second Updated"
    assert rotated.status_code == 200
    assert rotated.json()["api_key_fingerprint"] != second.json()["api_key_fingerprint"]
    assert "api_key" not in rotated.json()
    assert defaulted.status_code == 200
    assert defaulted.json()["is_default"] is True
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    by_id = {item["id"]: item for item in listed.json()["items"]}
    assert by_id[first.json()["id"]]["is_default"] is False
    assert by_id[first.json()["id"]]["status"] == "disabled"
    assert by_id[second.json()["id"]]["is_default"] is True


def test_task_idempotency_key_is_scoped_by_workspace() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner", email="owner@example.com", slug="one")
    other, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other@example.com",
        slug="two",
    )
    key = "same-client-key"

    first = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id) | {"Idempotency-Key": key},
        json={"title": "Owner task"},
    )
    second = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/tasks",
        headers=_headers(other.id) | {"Idempotency-Key": key},
        json={"title": "Other task"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_workspace_create_is_idempotent_per_user() -> None:
    client, session = _client()
    user = User(email="workspace-owner@example.com", display_name="Owner")
    other = User(email="other-owner@example.com", display_name="Other")
    session.add_all([user, other])
    session.commit()
    headers = _headers(user.id) | {"Idempotency-Key": "create-workspace"}

    first = client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Acme", "slug": "acme"},
    )
    second = client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Acme", "slug": "acme"},
    )
    other_user = client.post(
        "/api/v1/workspaces",
        headers=_headers(other.id) | {"Idempotency-Key": "create-workspace"},
        json={"name": "Other Acme", "slug": "other-acme"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert other_user.status_code == 201
    assert other_user.json()["id"] != first.json()["id"]


def test_cancel_task_marks_task_and_active_run_cancelled() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    task_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={"title": "Cancel me"},
    )
    task_id = UUID(task_response.json()["id"])

    cancelled = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task_id}/cancel",
        headers=_headers(owner.id),
    )
    repeat_cancel = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task_id}/cancel",
        headers=_headers(owner.id),
    )

    session.expire_all()
    task = session.get(Task, task_id)
    run = session.scalar(select(AgentRun).where(AgentRun.task_id == task_id))
    audit = client.get(
        f"/api/v1/workspaces/{workspace.id}/audit-events",
        headers=_headers(owner.id),
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
    owner, workspace = _seed_workspace(session, role="owner")
    task_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={"title": "Cancel run"},
    )
    task_id = UUID(task_response.json()["id"])
    run = session.scalar(select(AgentRun).where(AgentRun.task_id == task_id))
    assert run is not None

    cancelled = client.post(
        f"/api/v1/workspaces/{workspace.id}/runs/{run.id}/cancel",
        headers=_headers(owner.id),
    )

    task = session.get(Task, task_id)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == RunStatus.CANCELLED.value
    assert task is not None
    assert task.status == TaskStatus.CANCELLED.value


def test_task_messages_api_lists_filters_and_enforces_workspace_scope() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other@example.com",
        slug="other-space",
    )
    task = Task(workspace_id=workspace.id, created_by_user_id=owner.id, title="Task")
    session.add(task)
    session.flush()
    session.add_all(
        [
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                message_type="step.started",
                sequence=1,
                body="Started",
                payload={"work_package_id": "research"},
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                message_type="step.completed",
                sequence=2,
                body="Completed",
                payload={"work_package_id": "research"},
            ),
        ]
    )
    session.commit()

    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/messages",
        headers=_headers(owner.id),
    )
    filtered = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/messages"
        "?message_type=step.completed",
        headers=_headers(owner.id),
    )
    foreign = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/tasks/{task.id}/messages",
        headers=_headers(other_owner.id),
    )

    assert listed.status_code == 200
    assert [item["sequence"] for item in listed.json()["items"]] == [1, 2]
    assert listed.json()["items"][0]["payload"] == {"work_package_id": "research"}
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["message_type"] == "step.completed"
    assert foreign.status_code == 404


def test_task_observation_composes_domain_sections_and_sanitizes_payloads() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other@example.com",
        slug="other-space",
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        domain_type="novel",
        title="Write a mystery novel",
        status="running",
        priority=7,
        input={"outline": ["Act 1", "Act 2"]},
        generic_state={"word_count": 3200},
        domain_state={
            "chapters": [{"title": "Chapter 1", "status": "drafting"}],
            "characters": [{"name": "Lin", "role": "detective"}],
        },
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Draft chapter 1",
        status="running",
        order_index=1,
        work_package_id="chapter-1",
        required_role="writer",
        required_skills=["plotting"],
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        status=RunStatus.RUNNING.value,
        input={},
    )
    session.add_all([step, run])
    session.flush()
    session.add_all(
        [
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=step.id,
                agent_run_id=run.id,
                message_type="step.started",
                sequence=1,
                body="Started drafting.",
                payload={"note": "draft", "api_key": "sk-hidden"},
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                message_type="pm.acceptance_decision",
                sequence=2,
                body="Keep writing.",
                payload={"decision": "continue", "token": "hidden-token"},
            ),
            RunEvent(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                event_type="run.started",
                sequence=1,
                message="Run started",
                event_metadata={},
                created_at=datetime.now(UTC),
            ),
            Artifact(
                workspace_id=workspace.id,
                task_id=task.id,
                agent_run_id=run.id,
                artifact_type="draft",
                filename="chapter-1.md",
                content_type="text/markdown",
                size_bytes=1024,
                checksum_sha256="a" * 64,
                storage_key=f"workspaces/{workspace.id}/artifacts/{uuid4()}",
                artifact_metadata={"work_package_id": "chapter-1"},
                created_at=datetime.now(UTC),
            ),
        ]
    )
    session.commit()

    observed = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/observation",
        headers=_headers(owner.id),
    )
    forced = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/observation?view_type=software",
        headers=_headers(owner.id),
    )
    unsupported = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/observation?view_type=finance",
        headers=_headers(owner.id),
    )
    foreign = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/tasks/{task.id}/observation",
        headers=_headers(other_owner.id),
    )

    assert observed.status_code == 200
    body = observed.json()
    assert body["view_type"] == "novel"
    assert body["summary"]["status"] == "running"
    assert body["summary"]["progress"] == 0.0
    assert body["summary"]["artifact_count"] == 1
    sections = {section["key"]: section for section in body["sections"]}
    assert set(sections) == {"overview", "timeline", "artifacts", "review", "domain"}
    assert sections["domain"]["cards"][0]["card_type"] == "outline"
    assert sections["domain"]["cards"][1]["data"]["value"] == [
        {"title": "Chapter 1", "status": "drafting"}
    ]
    message_payload = sections["timeline"]["cards"][0]["data"]["payload"]
    review_payload = sections["review"]["cards"][0]["data"]["payload"]
    assert message_payload == {"note": "draft"}
    assert review_payload == {"decision": "continue"}
    assert sections["artifacts"]["cards"][0]["title"] == "chapter-1.md"
    assert forced.status_code == 200
    assert forced.json()["view_type"] == "software"
    assert unsupported.status_code == 400
    assert foreign.status_code == 404


def test_retry_failed_run_creates_new_queued_run_and_enqueues_job() -> None:
    queue_redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(queue_redis, RedisKeyBuilder("chaincloud"), "agent_runs", 0)
    client, session = _client(queue=queue)
    owner, workspace = _seed_workspace(session, role="owner")
    task_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={"title": "Retry failed"},
    )
    task_id = UUID(task_response.json()["id"])
    failed_run = session.scalar(select(AgentRun).where(AgentRun.task_id == task_id))
    task = session.get(Task, task_id)
    assert failed_run is not None
    assert task is not None
    failed_run.status = RunStatus.FAILED.value
    task.status = TaskStatus.FAILED.value
    session.commit()

    retry_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/runs/{failed_run.id}/retry",
        headers=_headers(owner.id),
    )

    retried_run_id = UUID(retry_response.json()["id"])
    session.expire_all()
    retried_run = session.get(AgentRun, retried_run_id)
    task = session.get(Task, task_id)
    queued_job = queue.dequeue()
    audit = client.get(
        f"/api/v1/workspaces/{workspace.id}/audit-events",
        headers=_headers(owner.id),
    )
    actions = {item["action"] for item in audit.json()["items"]}
    assert retry_response.status_code == 201
    assert retried_run_id != failed_run.id
    assert retried_run is not None
    assert retried_run.status == RunStatus.QUEUED.value
    assert task is not None
    assert task.status == TaskStatus.QUEUED.value
    assert queued_job is not None
    assert queued_job.resource_id == retried_run.id
    assert "run.retried" in actions


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
    assert task.status == "in_progress"


def test_stop_work_correction_cancels_task_without_creating_step() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Stop this",
        status="running",
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


def test_task_correction_rejects_foreign_artifact_target() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    _, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-correction@example.com",
        slug="other-correction",
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Replace artifact",
        status="running",
    )
    foreign_task = Task(
        workspace_id=other_workspace.id,
        title="Foreign",
        status="running",
    )
    session.add_all([task, foreign_task])
    session.flush()
    artifact = Artifact(
        workspace_id=other_workspace.id,
        task_id=foreign_task.id,
        artifact_type="document",
        filename="foreign.pdf",
        content_type="application/pdf",
        size_bytes=10,
        checksum_sha256="a" * 64,
        storage_key="foreign",
        created_at=datetime.now(UTC),
    )
    session.add(artifact)
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/corrections",
        headers=_headers(owner.id),
        json={
            "target_type": "artifact",
            "target_id": str(artifact.id),
            "mode": "replace_artifact",
            "instruction": "Replace this file.",
        },
    )

    assert response.status_code == 400
    assert "does not belong" in response.json()["error"]["message"]


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


def test_artifact_correction_creates_replacement_work_without_mutating_artifact() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Replace owned artifact",
        status="running",
    )
    session.add(task)
    session.flush()
    artifact = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        artifact_type="document",
        filename="draft.pdf",
        content_type="application/pdf",
        size_bytes=10,
        checksum_sha256="b" * 64,
        storage_key="owned",
        artifact_metadata={"version": 1},
        created_at=datetime.now(UTC),
    )
    session.add(artifact)
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/corrections",
        headers=_headers(owner.id),
        json={
            "target_type": "artifact",
            "target_id": str(artifact.id),
            "mode": "replace_artifact",
            "instruction": "Replace the PDF with the approved version.",
        },
    )

    assert response.status_code == 201
    session.refresh(artifact)
    step = session.get(TaskStep, UUID(response.json()["created_step_id"]))
    assert artifact.artifact_metadata == {"version": 1}
    assert step is not None
    assert step.expected_artifacts == ["replacement_artifact"]
    assert step.dependencies["correction"]["target"]["artifact_id"] == str(artifact.id)


def test_artifact_list_includes_work_package_version_metadata() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Artifact metadata",
    )
    session.add(task)
    session.flush()
    artifact = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="research-1",
        version=3,
        review_status="approved",
        artifact_type="document",
        filename="report.pdf",
        content_type="application/pdf",
        size_bytes=10,
        checksum_sha256="c" * 64,
        storage_key="owned",
        created_at=datetime.now(UTC),
    )
    session.add(artifact)
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/artifacts",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["work_package_id"] == "research-1"
    assert item["version"] == 3
    assert item["review_status"] == "approved"
    assert item["task_step_id"] is None
    assert item["agent_profile_id"] is None
    assert item["supersedes_artifact_id"] is None


def test_create_workspace_assigns_owner_membership() -> None:
    client, session = _client()
    user = User(email="new-owner@example.com", display_name="New Owner")
    session.add(user)
    session.commit()

    response = client.post(
        "/api/v1/workspaces",
        headers=_headers(user.id),
        json={"name": "New AI Company", "slug": "new-ai-company"},
    )

    assert response.status_code == 201
    workspace_id = response.json()["id"]
    members = client.get(f"/api/v1/workspaces/{workspace_id}/members", headers=_headers(user.id))
    assert members.status_code == 200
    assert members.json()["items"][0]["role"] == "owner"


def test_duplicate_workspace_slug_returns_conflict_error() -> None:
    client, session = _client()
    user = User(email="owner@example.com", display_name="Owner")
    session.add(user)
    session.commit()
    headers = _headers(user.id)

    first = client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Acme", "slug": "acme"},
    )
    duplicate = client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Acme Again", "slug": "acme"},
    )
    after_conflict = client.get("/api/v1/workspaces", headers=headers)

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"
    assert duplicate.json()["error"]["message"] == "Workspace slug already exists"
    assert after_conflict.status_code == 200
    assert after_conflict.json()["total"] == 1


def _client(queue: RedisQueue | None = None) -> tuple[TestClient, Session]:
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    redis = fakeredis.FakeRedis(decode_responses=True)

    app = create_app(
        Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            database_url="sqlite+pysqlite:///:memory:",
        )
    )

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    app.dependency_overrides[get_redis_client] = lambda: redis
    if queue is not None:
        app.dependency_overrides[get_worker_queue] = lambda: queue
    return TestClient(app), session


def _seed_workspace(
    session: Session,
    *,
    role: str,
    email: str = "owner@example.com",
    slug: str = "owner-space",
) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role=role)
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _headers(user_id: object) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-User-ID": str(user_id),
    }


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
