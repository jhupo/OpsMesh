from collections.abc import Generator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agents.models import AgentProfile
from backend.app.artifacts.models import Artifact
from backend.app.audit.models import AuditEvent
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
from backend.app.workspaces.models import Workspace, WorkspaceMember, WorkspaceQuota

TOKEN = "test-token"


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
        session,
        role="owner",
        email="other-owner@example.com",
        slug="other-owner-space",
    )

    response = client.get(
        path_template.format(workspace_id=other_workspace.id),
        headers=_headers(owner.id),
    )

    assert response.status_code == 403


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


def test_agent_profile_response_redacts_sensitive_metadata() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "Sensitive Agent",
            "role": "researcher",
            "model_settings": {
                "temperature": 0.2,
                "api_key": "sk-agent",
                "provider": {"base_url": "https://router.example.test/private"},
            },
            "capabilities": {"headers": {"authorization": "Bearer hidden"}},
            "tool_policy": {"token": "tool-token"},
            "runtime_policy": {"docker_container_id": "container-secret"},
        },
    )
    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
    )

    assert created.status_code == 201
    body = created.json()
    assert body["model_settings"] == {
        "temperature": 0.2,
        "api_key": "[redacted]",
        "provider": {"base_url": "[redacted]"},
    }
    assert body["capabilities"] == {"headers": "[redacted]"}
    assert body["tool_policy"] == {"token": "[redacted]"}
    assert body["runtime_policy"] == {"docker_container_id": "[redacted]"}
    assert listed.status_code == 200
    serialized = str(listed.json())
    assert "sk-agent" not in serialized
    assert "router.example.test/private" not in serialized
    assert "container-secret" not in serialized


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


def test_team_org_chart_returns_reporting_tree_and_capacity_summary() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")

    manager_agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "PM",
            "role": "project_manager",
            "model_settings": {"api_key": "sk-hidden"},
        },
    )
    developer_agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Frontend Dev", "role": "frontend_engineer"},
    )
    qa_agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "QA", "role": "qa_engineer"},
    )
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={
            "name": "Product Team",
            "team_type": "software",
            "manager_agent_profile_id": manager_agent.json()["id"],
            "coordination_rules": {"handoff": "manager_review"},
            "default_task_policy": {"priority": 3},
        },
    )
    manager_member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": manager_agent.json()["id"],
            "team_role": "project_manager",
            "department": "Management",
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
            "skill_weights": {"react": 0.9},
            "max_concurrent_tasks": 2,
            "order_index": 1,
        },
    )
    qa_member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": qa_agent.json()["id"],
            "reports_to_member_id": manager_member.json()["id"],
            "team_role": "qa_engineer",
            "accepts_tasks": False,
            "order_index": 2,
        },
    )

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/org-chart",
        headers=_headers(owner.id),
    )
    missing = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{uuid4()}/org-chart",
        headers=_headers(owner.id),
    )
    manager_record = session.get(AgentTeamMember, UUID(manager_member.json()["id"]))
    assert manager_record is not None
    manager_record.reports_to_member_id = UUID(developer_member.json()["id"])
    session.commit()
    cycle_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/org-chart",
        headers=_headers(owner.id),
    )

    assert manager_agent.status_code == 201
    assert developer_member.status_code == 201
    assert qa_member.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["team_id"] == team.json()["id"]
    assert body["manager_agent"] == {
        "id": manager_agent.json()["id"],
        "name": "PM",
        "role": "project_manager",
        "status": "active",
    }
    assert body["coordination_rules"] == {"handoff": "manager_review"}
    assert body["default_task_policy"] == {"priority": 3}
    assert body["capacity_summary"] == {
        "total_members": 3,
        "active_members": 3,
        "accepting_members": 2,
        "required_members": 3,
        "inactive_members": 0,
        "total_max_concurrent_tasks": 5,
    }
    assert body["orphan_member_ids"] == []
    assert body["cycle_member_ids"] == []
    assert [root["id"] for root in body["roots"]] == [manager_member.json()["id"]]
    assert [child["id"] for child in body["roots"][0]["children"]] == [
        developer_member.json()["id"],
        qa_member.json()["id"],
    ]
    assert body["members"][1]["agent"]["name"] == "Frontend Dev"
    assert "sk-hidden" not in str(body)
    assert missing.status_code == 404
    assert cycle_response.status_code == 200
    assert {
        manager_member.json()["id"],
        developer_member.json()["id"],
    } <= set(cycle_response.json()["cycle_member_ids"])


def test_team_execution_overview_reports_workload_and_attention_items() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session,
        role="owner",
        email="other-team-overview@example.com",
        slug="other-team-overview",
    )

    manager = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "PM",
            "role": "project_manager",
            "model_settings": {"api_key": "sk-manager-overview"},
        },
    )
    developer = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Developer", "role": "developer"},
    )
    assert manager.status_code == 201
    assert developer.status_code == 201
    manager_id = UUID(manager.json()["id"])
    developer_id = UUID(developer.json()["id"])

    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={
            "name": "Delivery Team",
            "team_type": "software",
            "manager_agent_profile_id": str(manager_id),
        },
    )
    assert team.status_code == 201
    team_id = UUID(team.json()["id"])

    manager_member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team_id}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": str(manager_id),
            "team_role": "project_manager",
            "department": "delivery",
            "max_concurrent_tasks": 2,
            "order_index": 1,
        },
    )
    developer_member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team_id}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": str(developer_id),
            "reports_to_member_id": manager_member.json()["id"],
            "team_role": "developer",
            "department": "engineering",
            "max_concurrent_tasks": 1,
            "order_index": 2,
        },
    )
    assert manager_member.status_code == 201
    assert developer_member.status_code == 201

    running_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team_id,
        title="Build workspace console",
        status="running",
        priority=9,
        domain_type="software",
        input={"api_key": "sk-task-overview"},
        team_snapshot={"team": {"manager_agent_profile_id": str(manager_id)}},
        project_plan={"planner_agent_profile_id": str(manager_id)},
    )
    completed_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team_id,
        title="Ship onboarding flow",
        status="completed",
        priority=1,
        domain_type="software",
        team_snapshot={"team": {"manager_agent_profile_id": str(manager_id)}},
        project_plan={"planner_agent_profile_id": str(manager_id)},
        completed_at=datetime.now(UTC),
    )
    session.add_all([running_task, completed_task])
    session.flush()

    running_build = TaskStep(
        workspace_id=workspace.id,
        task_id=running_task.id,
        assigned_agent_profile_id=developer_id,
        work_package_id="build",
        required_role="developer",
        title="Build console",
        status="running",
        order_index=20,
    )
    session.add_all(
        [
            TaskStep(
                workspace_id=workspace.id,
                task_id=running_task.id,
                assigned_agent_profile_id=manager_id,
                work_package_id="manager-planning",
                required_role="project_manager",
                title="Plan console",
                status="completed",
                order_index=10,
            ),
            running_build,
            TaskStep(
                workspace_id=workspace.id,
                task_id=completed_task.id,
                assigned_agent_profile_id=manager_id,
                work_package_id="manager-planning",
                required_role="project_manager",
                title="Plan onboarding",
                status="completed",
                order_index=10,
            ),
            TaskStep(
                workspace_id=workspace.id,
                task_id=completed_task.id,
                assigned_agent_profile_id=developer_id,
                work_package_id="build",
                required_role="developer",
                title="Build onboarding",
                status="completed",
                order_index=20,
            ),
            TaskStep(
                workspace_id=workspace.id,
                task_id=completed_task.id,
                assigned_agent_profile_id=manager_id,
                work_package_id="manager-summary",
                required_role="project_manager",
                title="Review onboarding",
                status="completed",
                order_index=30,
            ),
        ]
    )
    session.flush()
    session.add_all(
        [
            AgentRun(
                workspace_id=workspace.id,
                task_id=running_task.id,
                task_step_id=running_build.id,
                agent_profile_id=developer_id,
                status=RunStatus.RUNNING.value,
                input={"token": "run-hidden"},
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=completed_task.id,
                agent_profile_id=manager_id,
                message_type="pm.acceptance_decision",
                sequence=1,
                body="Approved body should stay private.",
                payload={"decision": "approved", "api_key": "sk-approved-overview"},
            ),
        ]
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team_id}/execution-overview",
        headers=_headers(owner.id),
    )
    include_completed = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team_id}/execution-overview"
        "?include_completed=true",
        headers=_headers(owner.id),
    )
    not_found = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{uuid4()}/execution-overview",
        headers=_headers(owner.id),
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team_id}/execution-overview",
        headers=_headers(other_owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["team"]["name"] == "Delivery Team"
    assert body["manager_agent"]["name"] == "PM"
    assert body["summary"]["total_tasks"] == 1
    assert body["summary"]["task_counts"] == {"running": 1}
    assert body["summary"]["step_counts"] == {"completed": 1, "running": 1}
    assert body["summary"]["run_counts"] == {"running": 1}
    assert body["summary"]["needs_attention_tasks"] == 1
    assert body["summary"]["available_member_capacity"] == 2

    members = {item["team_role"]: item for item in body["members"]}
    assert members["developer"]["active_task_count"] == 1
    assert members["developer"]["active_step_count"] == 1
    assert members["developer"]["active_run_count"] == 1
    assert members["developer"]["utilization"] == 1.0
    assert members["developer"]["overloaded"] is False

    assert len(body["tasks"]) == 1
    task = body["tasks"][0]
    assert task["task_id"] == str(running_task.id)
    assert task["title"] == "Build workspace console"
    assert task["needs_attention"] is True
    assert task["pending_phase"] == "specialist_execution"
    assert "specialist_steps_incomplete" in task["blocked_reasons"]
    assert task["active_run_count"] == 1

    assert include_completed.status_code == 200
    titles = {item["title"] for item in include_completed.json()["tasks"]}
    assert titles == {"Build workspace console", "Ship onboarding flow"}
    assert not_found.status_code == 404
    assert forbidden.status_code == 403

    serialized = str(include_completed.json())
    assert "sk-manager-overview" not in serialized
    assert "sk-task-overview" not in serialized
    assert "run-hidden" not in serialized
    assert "Approved body should stay private." not in serialized
    assert "sk-approved-overview" not in serialized


def test_team_member_update_changes_future_snapshots_only() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Developer", "role": "frontend_engineer"},
    )
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={"name": "Product Team", "team_type": "software"},
    )
    member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": agent.json()["id"],
            "team_role": "frontend_engineer",
            "skill_weights": {"react": 0.5},
        },
    )
    first_task = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={"title": "Before upgrade", "agent_team_id": team.json()["id"]},
    )

    updated = client.patch(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/"
        f"{member.json()['id']}",
        headers=_headers(owner.id),
        json={
            "skill_weights": {"react": 0.9, "typescript": 0.85},
            "responsibilities": ["Build UI", "Review frontend quality"],
            "availability": {"timezone": "Asia/Shanghai", "weekly_hours": 30},
            "max_concurrent_tasks": 3,
        },
    )
    second_task = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={"title": "After upgrade", "agent_team_id": team.json()["id"]},
    )
    audit = client.get(
        f"/api/v1/workspaces/{workspace.id}/audit-events",
        headers=_headers(owner.id),
    )

    assert member.status_code == 201
    assert first_task.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["skill_weights"] == {"react": 0.9, "typescript": 0.85}
    assert updated.json()["responsibilities"] == ["Build UI", "Review frontend quality"]
    assert updated.json()["availability"] == {
        "timezone": "Asia/Shanghai",
        "weekly_hours": 30,
    }
    assert updated.json()["max_concurrent_tasks"] == 3
    assert second_task.status_code == 201
    assert first_task.json()["team_snapshot"]["members"][0]["skill_weights"] == {"react": 0.5}
    assert second_task.json()["team_snapshot"]["members"][0]["skill_weights"] == {
        "react": 0.9,
        "typescript": 0.85,
    }
    update_audit = next(
        item for item in audit.json()["items"] if item["action"] == "team_member.updated"
    )
    assert update_audit["audit_metadata"]["changed_fields"] == [
        "availability",
        "max_concurrent_tasks",
        "responsibilities",
        "skill_weights",
    ]
    assert update_audit["audit_metadata"]["before"]["skill_weights"] == {"react": 0.5}
    assert update_audit["audit_metadata"]["after"]["skill_weights"] == {
        "react": 0.9,
        "typescript": 0.85,
    }


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
    bad_manager = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={
            "name": "Foreign Managed Team",
            "manager_agent_profile_id": foreign_agent.json()["id"],
        },
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
    self_report = client.patch(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/"
        f"{local_member.json()['id']}",
        headers=_headers(owner.id),
        json={"reports_to_member_id": local_member.json()["id"]},
    )

    assert local_member.status_code == 201
    assert bad_agent.status_code == 404
    assert bad_manager.status_code == 404
    assert bad_report.status_code == 404
    assert self_report.status_code == 400


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
    listed_attempts = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{created_task.json()['id']}"
        "/planning-attempts",
        headers=_headers(owner.id),
    )
    failed_attempts = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{created_task.json()['id']}"
        "/planning-attempts?status=failed",
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
    assert retry.json()["project_plan"]["work_packages"][1]["package_id"] == "build-ui"
    assert [attempt.status for attempt in attempts] == ["failed", "completed"]
    assert [attempt.retry_count for attempt in attempts] == [0, 1]
    assert listed_attempts.status_code == 200
    assert listed_attempts.json()["total"] == 2
    assert [item["status"] for item in listed_attempts.json()["items"]] == [
        "completed",
        "failed",
    ]
    assert failed_attempts.status_code == 200
    assert failed_attempts.json()["total"] == 1
    assert failed_attempts.json()["items"][0]["validation_errors"]
    assert queued_run is not None
    assert queue.count_queued(workspace_id=workspace.id) == 1


def test_planning_routes_reject_foreign_task_ids() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other@example.com",
        slug="other-space",
    )
    task = Task(
        workspace_id=other_workspace.id,
        created_by_user_id=other_owner.id,
        title="Foreign team task",
        agent_team_id=uuid4(),
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

    assert retry.status_code == 404
    assert regenerate.status_code == 404
    assert attempts.status_code == 404
    assert task.workspace_id == other_workspace.id
    assert task.project_plan == {"plan_id": "foreign-plan", "work_packages": []}
    assert attempt.workspace_id == other_workspace.id
    assert session.query(TaskPlanningAttempt).count() == 1


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
    attempts_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task_id}/planning-attempts",
        headers=_headers(owner.id),
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
    assert attempts_response.status_code == 200
    assert attempts_response.json()["total"] == 2
    assert attempts_response.json()["items"][0]["output_snapshot"]["regeneration"][
        "mode"
    ] == "future_only"
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
    assert body["base_url_configured"] is True
    assert body["base_url_host"] == "api.openai.com"
    assert listed.status_code == 200
    assert listed.json()["items"][0]["base_url_configured"] is True
    assert listed.json()["items"][0]["base_url_host"] == "api.openai.com"
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
    assert updated.json()["base_url_configured"] is True
    assert updated.json()["base_url_host"] == "llm.example.test"
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
    assert by_id[second.json()["id"]]["base_url_host"] == "llm.example.test"


def test_model_provider_usage_audit_api_is_scoped_and_redacted() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-provider@example.com",
        slug="other-provider",
    )
    session.add_all(
        [
            AuditEvent(
                workspace_id=workspace.id,
                actor_type="user",
                actor_id=str(owner.id),
                user_id=owner.id,
                action="model_provider.used",
                target_type="agent_run",
                target_id="run-1",
                created_at=datetime.now(UTC),
                audit_metadata={
                    "task_id": "task-1",
                    "task_step_id": "step-1",
                    "agent_profile_id": "agent-1",
                    "model": "backup-model",
                    "credential_id": "credential-1",
                    "fallback_selected": True,
                    "api_key": "sk-secret",
                    "base_url": "https://secret.example.test/v1",
                },
            ),
            AuditEvent(
                workspace_id=workspace.id,
                actor_type="user",
                actor_id=str(owner.id),
                user_id=owner.id,
                action="model_provider.fallback_unavailable",
                target_type="agent_run",
                target_id="run-2",
                created_at=datetime.now(UTC),
                audit_metadata={
                    "reason": {"code": "RuntimeError", "message": "primary unavailable"},
                    "failed_provider": {
                        "model": "primary-model",
                        "credential_id": "credential-2",
                        "api_key": "sk-primary",
                        "base_url": "https://primary.example.test/v1",
                    },
                },
            ),
            AuditEvent(
                workspace_id=other_workspace.id,
                actor_type="user",
                actor_id=str(other_owner.id),
                user_id=other_owner.id,
                action="model_provider.used",
                target_type="agent_run",
                target_id="foreign-run",
                created_at=datetime.now(UTC),
                audit_metadata={"model": "foreign-model"},
            ),
        ]
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/usage-audit",
        headers=_headers(owner.id),
    )
    fallback_only = client.get(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/usage-audit"
        "?action=model_provider.fallback_unavailable",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert {item["run_id"] for item in payload["items"]} == {"run-1", "run-2"}
    serialized = str(payload)
    assert "sk-secret" not in serialized
    assert "sk-primary" not in serialized
    assert "secret.example.test" not in serialized
    assert "primary.example.test" not in serialized
    used = next(item for item in payload["items"] if item["run_id"] == "run-1")
    assert used["model"] == "backup-model"
    assert used["credential_id"] == "credential-1"
    assert used["fallback_selected"] is True
    assert fallback_only.status_code == 200
    assert fallback_only.json()["total"] == 1
    assert fallback_only.json()["items"][0]["run_id"] == "run-2"
    assert fallback_only.json()["items"][0]["failed_provider"] == {
        "model": "primary-model",
        "credential_id": "credential-2",
    }


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
                payload={
                    "work_package_id": "research",
                    "api_key": "sk-hidden",
                    "nested": {"authorization": "Bearer hidden"},
                },
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
    assert listed.json()["items"][0]["payload"] == {
        "work_package_id": "research",
        "api_key": "[redacted]",
        "nested": {"authorization": "[redacted]"},
    }
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["message_type"] == "step.completed"
    assert foreign.status_code == 404


def test_run_api_redacts_sensitive_payloads() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    run = AgentRun(
        workspace_id=workspace.id,
        status=RunStatus.FAILED.value,
        input={"prompt": "draft", "api_key": "sk-hidden"},
        output={
            "result": {"token": "hidden-token"},
            "sdk_continuation": {
                "provider": "openai_agents",
                "mode": "runner_level_fallback",
                "resume_input": [
                    {
                        "role": "tool",
                        "base_url": "https://router.example.test/private",
                        "headers": {"authorization": "Bearer hidden"},
                    }
                ],
            },
        },
        error={"message": "failed", "authorization": "Bearer hidden"},
    )
    session.add(run)
    session.flush()
    event = RunEvent(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        event_type="run.failed",
        sequence=1,
        message="Failed",
        event_metadata={"secret": "hidden", "safe": "ok"},
        created_at=datetime.now(UTC),
    )
    session.add(event)
    session.commit()

    runs = client.get(
        f"/api/v1/workspaces/{workspace.id}/runs",
        headers=_headers(owner.id),
    )
    events = client.get(
        f"/api/v1/workspaces/{workspace.id}/runs/{run.id}/events",
        headers=_headers(owner.id),
    )

    assert runs.status_code == 200
    payload = runs.json()["items"][0]
    assert payload["input"]["api_key"] == "[redacted]"
    assert payload["output"]["result"]["token"] == "[redacted]"
    assert payload["output"]["sdk_continuation"]["resume_input"][0]["base_url"] == "[redacted]"
    assert payload["output"]["sdk_continuation"]["resume_input"][0]["headers"] == "[redacted]"
    assert payload["error"]["authorization"] == "[redacted]"
    assert "router.example.test/private" not in str(payload)
    assert "Bearer hidden" not in str(payload)
    assert events.status_code == 200
    assert events.json()["items"][0]["event_metadata"] == {
        "secret": "[redacted]",
        "safe": "ok",
    }


def test_audit_event_api_redacts_sensitive_metadata() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    session.add(
        AuditEvent(
            workspace_id=workspace.id,
            actor_type="user",
            actor_id=str(owner.id),
            user_id=owner.id,
            action="model_provider.used",
            target_type="agent_run",
            target_id=str(uuid4()),
            audit_metadata={
                "api_key": "sk-hidden",
                "provider": {"base_url": "https://router.example.test/private"},
                "safe": "visible",
            },
            created_at=datetime.now(UTC),
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/audit-events?action=model_provider.used",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    metadata = response.json()["items"][0]["audit_metadata"]
    assert metadata == {
        "api_key": "[redacted]",
        "provider": {"base_url": "[redacted]"},
        "safe": "visible",
    }
    assert "sk-hidden" not in str(metadata)
    assert "router.example.test/private" not in str(metadata)


def test_task_execution_diagnostics_explains_assignments_dependencies_and_blockers() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-exec@example.com",
        slug="other-exec",
    )
    active_agent_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Developer", "role": "developer"},
    )
    inactive_agent_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Reviewer", "role": "reviewer"},
    )
    inactive_agent = session.get(AgentProfile, UUID(inactive_agent_response.json()["id"]))
    assert inactive_agent is not None
    inactive_agent.status = "inactive"
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Build dashboard",
        status=TaskStatus.RUNNING.value,
        priority=4,
        domain_type="software",
        input={"api_key": "sk-hidden"},
    )
    session.add(task)
    session.flush()
    completed_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=UUID(active_agent_response.json()["id"]),
        work_package_id="design",
        required_role="designer",
        title="Design",
        status="completed",
        order_index=10,
        result_summary="Design complete",
    )
    session.add(completed_step)
    session.flush()
    runnable_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=UUID(active_agent_response.json()["id"]),
        work_package_id="build",
        required_role="developer",
        title="Build",
        status="queued",
        order_index=20,
        dependencies={"after_step_ids": [str(completed_step.id)]},
    )
    session.add(runnable_step)
    session.flush()
    blocked_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=UUID(inactive_agent_response.json()["id"]),
        work_package_id="review",
        required_role="reviewer",
        title="Review",
        status="queued",
        order_index=30,
        dependencies={
            "after_step_ids": [str(runnable_step.id)],
            "blocked_reason": "workspace_run_quota_exceeded",
            "token": "hidden-token",
        },
    )
    unassigned_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="release",
        required_role="release_manager",
        title="Release",
        status="queued",
        order_index=40,
        dependencies={"after_step_ids": [str(uuid4())]},
    )
    session.add_all([blocked_step, unassigned_step])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=blocked_step.id,
        agent_profile_id=UUID(inactive_agent_response.json()["id"]),
        status=RunStatus.QUEUED.value,
        input={},
        error={"api_key": "sk-run"},
    )
    session.add(run)
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/execution-diagnostics",
        headers=_headers(owner.id),
    )
    foreign_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/tasks/{task.id}/execution-diagnostics",
        headers=_headers(other_owner.id),
    )
    missing_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{uuid4()}/execution-diagnostics",
        headers=_headers(owner.id),
    )

    assert active_agent_response.status_code == 201
    assert inactive_agent_response.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == str(task.id)
    assert body["task"]["has_project_plan"] is False
    assert body["summary"]["total_steps"] == 4
    assert body["summary"]["runnable_steps"] == 1
    assert body["summary"]["unassigned_steps"] == 1
    assert body["summary"]["active_runs"] == 1
    by_package = {step["work_package_id"]: step for step in body["steps"]}
    assert by_package["design"]["blocked_reasons"] == []
    assert by_package["build"]["runnable"] is True
    assert by_package["build"]["dependency_state"]["satisfied"] is True
    assert by_package["review"]["assignment_status"] == "inactive_agent"
    assert by_package["review"]["dependency_state"]["satisfied"] is False
    assert set(by_package["review"]["blocked_reasons"]) == {
        "assigned_agent_inactive",
        "dependency_incomplete",
        "active_run_exists",
        "scheduler:workspace_run_quota_exceeded",
    }
    assert by_package["review"]["scheduling"]["blocked_reason"] == "workspace_run_quota_exceeded"
    assert by_package["review"]["dependencies"]["token"] == "[redacted]"
    assert by_package["review"]["runs"][0]["error"]["api_key"] == "[redacted]"
    assert by_package["release"]["assignment_status"] == "unassigned"
    assert by_package["release"]["blocked_reasons"] == [
        "agent_unassigned",
        "dependency_missing",
    ]
    assert "hidden-token" not in str(body)
    assert "sk-run" not in str(body)
    assert "sk-hidden" not in str(body)
    assert foreign_response.status_code == 404
    assert missing_response.status_code == 404


def test_task_plan_diagnostics_explains_assignment_and_dependency_quality() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-plan@example.com",
        slug="other-plan",
    )
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="PM",
        role="project_manager",
    )
    developer = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
    )
    session.add_all([manager, developer])
    session.flush()
    member_id = uuid4()
    team_snapshot = {
        "team": {"manager_agent_profile_id": str(manager.id)},
        "members": [
            {
                "id": str(member_id),
                "agent_profile_id": str(developer.id),
                "team_role": "developer",
                "skill_weights": {"python": 0.9},
                "accepts_tasks": True,
                "is_required": True,
                "max_concurrent_tasks": 3,
            }
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
            "strategy": "test",
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
    no_plan_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="No plan",
    )
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
    assert foreign_response.status_code == 404


def test_task_manager_diagnostics_explains_acceptance_follow_up_and_redacts() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-manager-diagnostics@example.com",
        slug="other-manager-diagnostics",
    )
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="PM",
        role="project_manager",
    )
    developer = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
    )
    session.add_all([manager, developer])
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Manager diagnostics",
        status="running",
        team_snapshot={"team": {"manager_agent_profile_id": str(manager.id)}},
        project_plan={"planner_agent_profile_id": str(manager.id)},
    )
    other_task = Task(
        workspace_id=other_workspace.id,
        created_by_user_id=other_owner.id,
        title="Foreign manager diagnostics",
    )
    session.add_all([task, other_task])
    session.flush()
    planning_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=manager.id,
        work_package_id="manager-planning",
        required_role="project_manager",
        title="Plan work",
        status="completed",
        order_index=10,
    )
    session.add(planning_step)
    session.flush()
    build_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=developer.id,
        work_package_id="build",
        required_role="developer",
        title="Build feature",
        status="completed",
        order_index=20,
        dependencies={"after_step_ids": [str(planning_step.id)]},
    )
    session.add(build_step)
    session.flush()
    summary_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=manager.id,
        work_package_id="manager-summary",
        required_role="project_manager",
        title="Review delivery",
        status="completed",
        order_index=30,
        dependencies={"after_step_ids": [str(build_step.id)]},
    )
    session.add(summary_step)
    session.flush()
    revision_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=developer.id,
        work_package_id="revision-build-1-1",
        required_role="developer",
        title="Revise feature",
        status="queued",
        order_index=40,
        dependencies={
            "revision_of_work_package_id": "build",
            "token": "hidden-token",
        },
    )
    session.add(revision_step)
    session.flush()
    review_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=manager.id,
        work_package_id="manager-summary-revision-1",
        required_role="project_manager",
        title="Review revision",
        status="queued",
        order_index=50,
        dependencies={"after_step_ids": [str(revision_step.id)]},
    )
    session.add(review_step)
    session.flush()
    session.add_all(
        [
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=summary_step.id,
                agent_profile_id=manager.id,
                message_type="pm.acceptance_decision",
                sequence=1,
                body="Private acceptance body should not be returned.",
                payload={
                    "decision": "request_revision",
                    "summary": "Needs one revision",
                    "reasons": ["Missing tests"],
                    "revision_requests": [
                        {
                            "work_package_id": "build",
                            "instruction": "Add tests",
                            "token": "hidden-token",
                        }
                    ],
                    "api_key": "sk-message",
                },
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=summary_step.id,
                agent_profile_id=manager.id,
                message_type="pm.follow_up_created",
                sequence=2,
                body="Follow up created.",
                payload={
                    "decision": "request_revision",
                    "revision_cycle": 1,
                    "follow_up_step_ids": [str(revision_step.id)],
                    "follow_up_work_package_ids": ["revision-build-1-1"],
                    "headers": {"authorization": "Bearer hidden"},
                },
            ),
            TaskMessage(
                workspace_id=other_workspace.id,
                task_id=other_task.id,
                message_type="pm.acceptance_decision",
                sequence=1,
                body="foreign",
                payload={"decision": "approved"},
            ),
        ]
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/manager-diagnostics",
        headers=_headers(owner.id),
    )
    foreign_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/tasks/{task.id}/manager-diagnostics",
        headers=_headers(other_owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["manager"]["agent"]["name"] == "PM"
    assert body["manager"]["planning_step_id"] == str(planning_step.id)
    assert body["summary"]["status"] == "attention"
    assert body["summary"]["decision_counts"] == {"request_revision": 1}
    assert set(body["blocked_reasons"]) == {
        "specialist_steps_incomplete",
        "follow_up_incomplete",
    }
    chain = {item["phase"]: item for item in body["handoff_chain"]}
    assert chain["manager_planning"]["status"] == "completed"
    assert chain["specialist_execution"]["status"] == "in_progress"
    assert chain["manager_acceptance"]["status"] == "completed"
    assert chain["follow_up"]["blocked_reasons"] == ["follow_up_incomplete"]
    decision = body["acceptance_decisions"][0]
    assert decision["decision"] == "request_revision"
    assert decision["status"] == "follow_up_created"
    assert decision["summary"] == "Needs one revision"
    assert decision["revision_requests"][0]["token"] == "[redacted]"
    assert decision["metadata"]["api_key"] == "[redacted]"
    cycle = body["follow_up_cycles"][0]
    assert cycle["revision_cycle"] == 1
    assert cycle["status"] == "in_progress"
    assert cycle["follow_up_step_ids"] == [str(revision_step.id)]
    assert set(cycle["blocked_reasons"]) == {
        "follow_up_steps_incomplete",
        "follow_up_review_incomplete",
    }
    assert cycle["metadata"]["headers"] == "[redacted]"
    assert foreign_response.status_code == 404
    serialized = str(body)
    assert "Private acceptance body should not be returned." not in serialized
    assert "hidden-token" not in serialized
    assert "sk-message" not in serialized
    assert "Bearer hidden" not in serialized
    assert "foreign" not in serialized


def test_task_manager_queue_lists_attention_items_and_preserves_workspace_scope() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-manager-queue@example.com",
        slug="other-manager-queue",
    )
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="PM",
        role="project_manager",
        model_settings={"api_key": "sk-manager"},
    )
    developer = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
    )
    session.add_all([manager, developer])
    session.flush()
    needs_follow_up = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Needs manager follow up",
        status="running",
        priority=8,
        domain_type="software",
        team_snapshot={"team": {"manager_agent_profile_id": str(manager.id)}},
        project_plan={"planner_agent_profile_id": str(manager.id)},
    )
    healthy = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Approved delivery",
        status="completed",
        priority=1,
        domain_type="software",
        team_snapshot={"team": {"manager_agent_profile_id": str(manager.id)}},
        project_plan={"planner_agent_profile_id": str(manager.id)},
    )
    foreign_task = Task(
        workspace_id=other_workspace.id,
        created_by_user_id=other_owner.id,
        title="Foreign manager queue task",
        status="running",
    )
    session.add_all([needs_follow_up, healthy, foreign_task])
    session.flush()

    follow_up_steps = [
        TaskStep(
            workspace_id=workspace.id,
            task_id=needs_follow_up.id,
            assigned_agent_profile_id=manager.id,
            work_package_id="manager-planning",
            required_role="project_manager",
            title="Plan attention task",
            status="completed",
            order_index=10,
        ),
        TaskStep(
            workspace_id=workspace.id,
            task_id=needs_follow_up.id,
            assigned_agent_profile_id=developer.id,
            work_package_id="build",
            required_role="developer",
            title="Build attention task",
            status="completed",
            order_index=20,
        ),
        TaskStep(
            workspace_id=workspace.id,
            task_id=needs_follow_up.id,
            assigned_agent_profile_id=manager.id,
            work_package_id="manager-summary",
            required_role="project_manager",
            title="Review attention task",
            status="completed",
            order_index=30,
        ),
    ]
    healthy_steps = [
        TaskStep(
            workspace_id=workspace.id,
            task_id=healthy.id,
            assigned_agent_profile_id=manager.id,
            work_package_id="manager-planning",
            required_role="project_manager",
            title="Plan healthy task",
            status="completed",
            order_index=10,
        ),
        TaskStep(
            workspace_id=workspace.id,
            task_id=healthy.id,
            assigned_agent_profile_id=developer.id,
            work_package_id="build",
            required_role="developer",
            title="Build healthy task",
            status="completed",
            order_index=20,
        ),
        TaskStep(
            workspace_id=workspace.id,
            task_id=healthy.id,
            assigned_agent_profile_id=manager.id,
            work_package_id="manager-summary",
            required_role="project_manager",
            title="Review healthy task",
            status="completed",
            order_index=30,
        ),
    ]
    session.add_all([*follow_up_steps, *healthy_steps])
    session.flush()
    session.add_all(
        [
            TaskMessage(
                workspace_id=workspace.id,
                task_id=needs_follow_up.id,
                task_step_id=follow_up_steps[2].id,
                agent_profile_id=manager.id,
                message_type="pm.acceptance_decision",
                sequence=1,
                body="Private body should not be serialized.",
                payload={
                    "decision": "request_revision",
                    "summary": "Needs tests",
                    "revision_requests": [
                        {"work_package_id": "build", "instruction": "Add tests"}
                    ],
                    "token": "hidden-token",
                },
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=healthy.id,
                task_step_id=healthy_steps[2].id,
                agent_profile_id=manager.id,
                message_type="pm.acceptance_decision",
                sequence=1,
                body="Approved body should not be serialized.",
                payload={
                    "decision": "approved",
                    "summary": "Approved",
                    "api_key": "sk-approved",
                },
            ),
            TaskMessage(
                workspace_id=other_workspace.id,
                task_id=foreign_task.id,
                message_type="pm.acceptance_decision",
                sequence=1,
                body="foreign",
                payload={"decision": "request_revision"},
            ),
        ]
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/manager-queue",
        headers=_headers(owner.id),
    )
    include_healthy = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/manager-queue?include_healthy=true",
        headers=_headers(owner.id),
    )
    status_filtered = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/manager-queue"
        "?include_healthy=true&status=completed",
        headers=_headers(owner.id),
    )
    foreign_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/tasks/manager-queue?include_healthy=true",
        headers=_headers(other_owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["summary"]["needs_attention"] == 1
    assert body["summary"]["pending_phases"] == {"follow_up": 1}
    item = body["items"][0]
    assert item["task_id"] == str(needs_follow_up.id)
    assert item["manager_agent_profile_id"] == str(manager.id)
    assert item["manager_agent_name"] == "PM"
    assert item["manager_status"] == "active"
    assert item["summary_status"] == "blocked"
    assert item["pending_phase"] == "follow_up"
    assert item["needs_attention"] is True
    assert item["blocked_reasons"] == ["follow_up_missing"]
    assert item["recommended_actions"] == ["request_manager_review"]
    assert item["acceptance_decisions"] == 1
    assert item["follow_up_cycles"] == 0
    assert item["step_status_counts"] == {"completed": 3}

    assert include_healthy.status_code == 200
    assert include_healthy.json()["total"] == 2
    by_title = {item["title"]: item for item in include_healthy.json()["items"]}
    assert by_title["Approved delivery"]["needs_attention"] is False
    assert by_title["Approved delivery"]["pending_phase"] == "none"
    assert status_filtered.status_code == 200
    assert status_filtered.json()["items"][0]["task_id"] == str(healthy.id)
    assert foreign_response.status_code == 200
    assert foreign_response.json()["total"] == 1
    assert foreign_response.json()["items"][0]["title"] == "Foreign manager queue task"
    serialized = str(include_healthy.json())
    assert "Private body should not be serialized." not in serialized
    assert "Approved body should not be serialized." not in serialized
    assert "hidden-token" not in serialized
    assert "sk-approved" not in serialized
    assert "sk-manager" not in serialized
    assert "foreign" not in str(body)


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
    correction_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Revise chapter 1",
        status="queued",
        order_index=2,
        work_package_id="correction-revise-2",
        dependencies={
            "correction": {
                "mode": "revise",
                "target": {"target_type": "step", "task_step_id": str(step.id)},
                "instruction": "Make the opening more suspenseful.",
                "metadata": {"risk_level": "medium"},
            },
            "blocked_reason": "workspace_quota_exceeded:active_runs",
            "blocked_resource_keys": ["active_runs"],
        },
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        status=RunStatus.RUNNING.value,
        input={},
    )
    session.add_all([step, correction_step, run])
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
                payload={
                    "decision": "request_revision",
                    "summary": "Opening is too flat.",
                    "reasons": ["Weak hook"],
                    "revision_requests": [
                        {"work_package_id": "chapter-1", "instruction": "Raise tension."}
                    ],
                    "risk_level": "medium",
                    "token": "hidden-token",
                },
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
    assert set(sections) == {"overview", "timeline", "artifacts", "review", "quality", "domain"}
    assert sections["domain"]["cards"][0]["card_type"] == "outline"
    assert sections["domain"]["cards"][1]["data"]["value"] == [
        {"title": "Chapter 1", "status": "drafting"}
    ]
    message_payload = sections["timeline"]["cards"][0]["data"]["payload"]
    review_payload = sections["review"]["cards"][0]["data"]["payload"]
    assert message_payload == {"note": "draft"}
    assert "token" not in review_payload
    assert review_payload["decision"] == "request_revision"
    quality_cards = sections["quality"]["cards"]
    assert any(
        card["card_type"] == "revision_history"
        and card["data"]["instruction"] == "Make the opening more suspenseful."
        for card in quality_cards
    )
    assert any(
        card["card_type"] == "revision_history"
        and card["data"]["source"] == "pm_acceptance"
        and card["data"]["revision_requests"][0]["instruction"] == "Raise tension."
        for card in quality_cards
    )
    assert any(
        card["card_type"] == "risk_flag"
        and card["data"]["source"] == "scheduler"
        and card["data"]["reason"] == "workspace_quota_exceeded:active_runs"
        for card in quality_cards
    )
    assert any(
        card["card_type"] == "risk_flag"
        and card["data"]["source"] == "pm.acceptance_decision"
        and card["data"]["risk"]["severity"] == "medium"
        for card in quality_cards
    )
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


def test_task_operator_action_reassigns_and_requeues_blocked_step() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-operator@example.com",
        slug="other-operator",
    )
    original_agent = AgentProfile(
        workspace_id=workspace.id,
        name="Original Developer",
        role="developer",
    )
    replacement_agent = AgentProfile(
        workspace_id=workspace.id,
        name="Replacement Developer",
        role="developer",
    )
    foreign_agent = AgentProfile(
        workspace_id=other_workspace.id,
        name="Foreign Developer",
        role="developer",
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
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/messages",
        headers=_headers(owner.id),
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
    assert foreign_task_response.status_code == 404
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


def test_task_operator_action_requests_manager_review_step() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="PM",
        role="project_manager",
    )
    developer = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
    )
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
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="No manager",
        status="running",
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
    assert created_step.required_role == "project_manager"
    assert created_step.status == "queued"
    assert created_step.dependencies["operator_action"]["reason"] == (
        "Operator wants acceptance check"
    )
    assert body["details"]["manager_agent_profile_id"] == str(manager.id)
    assert missing_manager.status_code == 404


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


def test_task_correction_diagnostics_tracks_follow_up_status_and_redacts_metadata() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-correction-diagnostics@example.com",
        slug="other-correction-diagnostics",
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Correction diagnostics",
        status="running",
    )
    session.add(task)
    session.flush()
    draft_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Draft",
        status="completed",
        order_index=1,
    )
    session.add(draft_step)
    session.flush()
    artifact = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=draft_step.id,
        artifact_type="document",
        filename="draft.pdf",
        content_type="application/pdf",
        size_bytes=10,
        checksum_sha256="a" * 64,
        storage_key="draft",
        created_at=datetime.now(UTC),
    )
    session.add(artifact)
    session.commit()

    replace = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/corrections",
        headers=_headers(owner.id),
        json={
            "target_type": "artifact",
            "target_id": str(artifact.id),
            "mode": "replace_artifact",
            "instruction": "Replace the draft PDF.",
            "metadata": {"token": "hidden-token"},
        },
    )
    revise = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/corrections",
        headers=_headers(owner.id),
        json={
            "target_type": "step",
            "target_id": str(draft_step.id),
            "mode": "revise",
            "instruction": "Revise the draft text.",
        },
    )
    replace_step = session.get(TaskStep, UUID(replace.json()["created_step_id"]))
    assert replace_step is not None
    replace_step.status = "completed"
    replace_step.result_summary = "Replacement complete"
    session.add(
        AgentRun(
            workspace_id=workspace.id,
            task_id=task.id,
            task_step_id=replace_step.id,
            status=RunStatus.COMPLETED.value,
            input={},
            error={"api_key": "sk-hidden"},
        )
    )
    replacement_artifact = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=replace_step.id,
        artifact_type="document",
        filename="replacement.pdf",
        content_type="application/pdf",
        size_bytes=12,
        checksum_sha256="b" * 64,
        storage_key="replacement",
        created_at=datetime.now(UTC),
    )
    session.add(replacement_artifact)
    session.commit()
    stop = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/corrections",
        headers=_headers(owner.id),
        json={
            "target_type": "task",
            "mode": "stop_work",
            "instruction": "Stop remaining work.",
        },
    )

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/corrections/diagnostics",
        headers=_headers(owner.id),
    )
    foreign_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/tasks/{task.id}/corrections/diagnostics",
        headers=_headers(other_owner.id),
    )

    assert replace.status_code == 201
    assert revise.status_code == 201
    assert stop.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_corrections"] == 3
    assert body["summary"]["status_counts"] == {
        "cancelled": 1,
        "completed": 1,
        "pending": 1,
    }
    assert body["summary"]["mode_counts"] == {
        "replace_artifact": 1,
        "revise": 1,
        "stop_work": 1,
    }
    assert body["summary"]["blocked_corrections"] == 1
    by_mode = {item["mode"]: item for item in body["corrections"]}
    assert by_mode["replace_artifact"]["status"] == "completed"
    assert by_mode["replace_artifact"]["metadata"]["token"] == "[redacted]"
    assert by_mode["replace_artifact"]["created_step"]["result_summary"] == (
        "Replacement complete"
    )
    assert by_mode["replace_artifact"]["artifacts"][0]["filename"] == "replacement.pdf"
    assert by_mode["replace_artifact"]["blocked_reasons"] == []
    assert by_mode["revise"]["status"] == "pending"
    assert by_mode["revise"]["blocked_reasons"] == ["follow_up_waiting_to_start"]
    assert by_mode["stop_work"]["status"] == "cancelled"
    assert "hidden-token" not in str(body)
    assert "sk-hidden" not in str(body)
    assert foreign_response.status_code == 404


def test_task_timeline_returns_execution_events_and_redacts_metadata() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-timeline@example.com",
        slug="other-timeline",
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Build timeline",
        status="running",
        priority=9,
        domain_type="software",
        input={"api_key": "sk-task"},
        created_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
    )
    session.add_all([agent, task])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        work_package_id="build",
        required_role="developer",
        required_skills=["python"],
        expected_artifacts=["patch"],
        acceptance_criteria=["tests pass"],
        review_policy={"headers": {"authorization": "Bearer hidden"}},
        title="Build API",
        description="Implement the API",
        status="running",
        order_index=10,
        dependencies={"token": "hidden-token"},
        created_at=datetime(2026, 1, 1, 9, 5, tzinfo=UTC),
    )
    session.add(step)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        agent_profile_id=agent.id,
        status=RunStatus.COMPLETED.value,
        input={
            "prompt": "build",
            "base_url": "https://router.example.test/private",
            "headers": {"authorization": "Bearer hidden"},
        },
        output={"result": "ok", "token": "hidden-token"},
        error={"api_key": "sk-run"},
        model="gpt-test",
        started_at=datetime(2026, 1, 1, 9, 10, tzinfo=UTC),
        completed_at=datetime(2026, 1, 1, 9, 20, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, 9, 6, tzinfo=UTC),
    )
    session.add(run)
    session.flush()
    session.add_all(
        [
            RunEvent(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                event_type="tool.completed",
                sequence=1,
                message="Tool completed",
                event_metadata={"secret": "tool-secret", "safe": "ok"},
                created_at=datetime(2026, 1, 1, 9, 15, tzinfo=UTC),
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=step.id,
                agent_run_id=run.id,
                agent_profile_id=agent.id,
                message_type="pm.acceptance_decision",
                sequence=1,
                body="Do not expose this full message body.",
                payload={
                    "summary": "Needs review",
                    "api_key": "sk-message",
                    "nested": {"authorization": "Bearer hidden"},
                },
                created_at=datetime(2026, 1, 1, 9, 25, tzinfo=UTC),
            ),
            TaskMessage(
                workspace_id=other_workspace.id,
                task_id=task.id,
                message_type="foreign.message",
                sequence=2,
                body="foreign",
                payload={},
                created_at=datetime(2026, 1, 1, 9, 30, tzinfo=UTC),
            ),
            Artifact(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=step.id,
                agent_run_id=run.id,
                agent_profile_id=agent.id,
                work_package_id="build",
                version=1,
                review_status="approved",
                artifact_type="patch",
                filename="patch.diff",
                content_type="text/x-diff",
                size_bytes=128,
                checksum_sha256="b" * 64,
                storage_key="secret-storage-key",
                artifact_metadata={"token": "artifact-token", "safe": "ok"},
                created_at=datetime(2026, 1, 1, 9, 30, tzinfo=UTC),
            ),
        ]
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/timeline",
        headers=_headers(owner.id),
    )
    limited = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/timeline?limit=3",
        headers=_headers(owner.id),
    )
    foreign_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/tasks/{task.id}/timeline",
        headers=_headers(other_owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["summary"]["total_events"] == 8
    assert body["summary"]["source_counts"] == {
        "artifact": 1,
        "message": 1,
        "run": 3,
        "run_event": 1,
        "step": 1,
        "task": 1,
    }
    event_types = [event["event_type"] for event in body["events"]]
    assert event_types == [
        "task.created",
        "step.created",
        "run.created",
        "run.started",
        "tool.completed",
        "run.completed",
        "pm.acceptance_decision",
        "artifact.created",
    ]
    by_type = {event["event_type"]: event for event in body["events"]}
    assert by_type["step.created"]["agent"]["name"] == "Developer"
    assert by_type["step.created"]["metadata"]["review_policy"]["headers"] == "[redacted]"
    assert by_type["step.created"]["metadata"]["dependencies"]["token"] == "[redacted]"
    assert by_type["run.created"]["metadata"]["input"]["base_url"] == "[redacted]"
    assert by_type["run.created"]["metadata"]["input"]["headers"] == "[redacted]"
    assert by_type["run.created"]["metadata"]["error"]["api_key"] == "[redacted]"
    assert by_type["tool.completed"]["metadata"]["secret"] == "[redacted]"
    assert by_type["pm.acceptance_decision"]["summary"] == "Needs review"
    assert by_type["pm.acceptance_decision"]["metadata"]["api_key"] == "[redacted]"
    assert by_type["artifact.created"]["metadata"]["artifact_metadata"]["token"] == "[redacted]"
    assert limited.status_code == 200
    assert limited.json()["summary"]["returned_events"] == 3
    assert limited.json()["summary"]["truncated_count"] == 5
    assert foreign_response.status_code == 404
    serialized = str(body)
    assert "sk-task" not in serialized
    assert "router.example.test/private" not in serialized
    assert "hidden-token" not in serialized
    assert "tool-secret" not in serialized
    assert "sk-message" not in serialized
    assert "artifact-token" not in serialized
    assert "secret-storage-key" not in serialized
    assert "Do not expose this full message body." not in serialized
    assert "foreign" not in serialized


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
    assert "storage_key" not in item
    assert item["has_storage_object"] is True


def test_artifact_history_lists_versions_for_work_package_only() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-artifacts@example.com",
        slug="other-artifacts",
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Artifact history",
    )
    other_task = Task(
        workspace_id=other_workspace.id,
        created_by_user_id=other_owner.id,
        title="Other artifact history",
    )
    session.add_all([task, other_task])
    session.flush()
    first = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="research-1",
        version=1,
        review_status="superseded",
        artifact_type="document",
        filename="report-v1.pdf",
        content_type="application/pdf",
        size_bytes=10,
        checksum_sha256="d" * 64,
        storage_key="owned-v1",
        created_at=datetime.now(UTC),
    )
    session.add(first)
    session.flush()
    second = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="research-1",
        version=2,
        supersedes_artifact_id=first.id,
        review_status="approved",
        artifact_type="document",
        filename="report-v2.pdf",
        content_type="application/pdf",
        size_bytes=12,
        checksum_sha256="e" * 64,
        storage_key="owned-v2",
        created_at=datetime.now(UTC),
    )
    other_package = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="other-package",
        version=1,
        artifact_type="document",
        filename="other.pdf",
        content_type="application/pdf",
        size_bytes=12,
        checksum_sha256="f" * 64,
        storage_key="other-package",
        created_at=datetime.now(UTC),
    )
    foreign = Artifact(
        workspace_id=other_workspace.id,
        task_id=other_task.id,
        work_package_id="research-1",
        version=99,
        artifact_type="document",
        filename="foreign.pdf",
        content_type="application/pdf",
        size_bytes=12,
        checksum_sha256="0" * 64,
        storage_key="foreign",
        created_at=datetime.now(UTC),
    )
    mismatched_task = Artifact(
        workspace_id=other_workspace.id,
        task_id=task.id,
        work_package_id="research-1",
        version=100,
        artifact_type="document",
        filename="mismatched-task.pdf",
        content_type="application/pdf",
        size_bytes=12,
        checksum_sha256="1" * 64,
        storage_key="mismatched-task",
        created_at=datetime.now(UTC),
    )
    session.add_all([second, other_package, foreign, mismatched_task])
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/artifacts/history"
        f"?task_id={task.id}&work_package_id=research-1",
        headers=_headers(owner.id),
    )
    foreign_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/artifacts/history"
        f"?task_id={task.id}&work_package_id=research-1",
        headers=_headers(other_owner.id),
    )
    foreign_owned_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/artifacts/history"
        f"?task_id={other_task.id}&work_package_id=research-1",
        headers=_headers(other_owner.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["latest_artifact_id"] == str(second.id)
    assert payload["latest_version"] == 2
    assert [item["version"] for item in payload["items"]] == [2, 1]
    assert [item["filename"] for item in payload["items"]] == [
        "report-v2.pdf",
        "report-v1.pdf",
    ]
    assert foreign_response.status_code == 200
    assert foreign_response.json()["total"] == 0
    assert foreign_owned_response.status_code == 200
    assert foreign_owned_response.json()["total"] == 1
    assert foreign_owned_response.json()["items"][0]["filename"] == "foreign.pdf"


def test_final_output_artifact_history_lists_final_acceptance_outputs_only() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-final-artifacts@example.com",
        slug="other-final-artifacts",
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Final output artifact history",
        final_output={"final_output": "Approved delivery"},
    )
    other_task = Task(
        workspace_id=other_workspace.id,
        created_by_user_id=other_owner.id,
        title="Foreign final output artifact history",
    )
    session.add_all([task, other_task])
    session.flush()
    draft_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="draft",
        title="Draft",
        order_index=10,
        expected_artifacts=["draft"],
    )
    summary_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="manager-summary",
        title="Manager summary",
        order_index=20,
        expected_artifacts=["final_delivery"],
        review_policy={"reviewer": "user", "mode": "final_acceptance"},
    )
    revision_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="manager-summary-revision-1",
        title="Manager summary revision",
        order_index=30,
        expected_artifacts=["final_delivery"],
        review_policy={"reviewer": "user", "mode": "final_acceptance"},
    )
    foreign_step = TaskStep(
        workspace_id=other_workspace.id,
        task_id=other_task.id,
        work_package_id="manager-summary",
        title="Foreign manager summary",
        order_index=20,
        expected_artifacts=["final_delivery"],
        review_policy={"reviewer": "user", "mode": "final_acceptance"},
    )
    session.add_all([draft_step, summary_step, revision_step, foreign_step])
    session.flush()
    first_final = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=summary_step.id,
        work_package_id="manager-summary",
        version=1,
        artifact_type="document",
        filename="final-v1.pdf",
        content_type="application/pdf",
        size_bytes=10,
        checksum_sha256="1" * 64,
        storage_key="final-v1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    revision_final = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=revision_step.id,
        work_package_id="manager-summary-revision-1",
        version=1,
        artifact_type="document",
        filename="final-v2.pdf",
        content_type="application/pdf",
        size_bytes=12,
        checksum_sha256="2" * 64,
        storage_key="final-v2",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    draft = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=draft_step.id,
        work_package_id="draft",
        version=7,
        artifact_type="document",
        filename="draft.pdf",
        content_type="application/pdf",
        size_bytes=12,
        checksum_sha256="3" * 64,
        storage_key="draft",
        created_at=datetime(2026, 1, 3, tzinfo=UTC),
    )
    foreign = Artifact(
        workspace_id=other_workspace.id,
        task_id=other_task.id,
        task_step_id=foreign_step.id,
        work_package_id="manager-summary",
        version=1,
        artifact_type="document",
        filename="foreign-final.pdf",
        content_type="application/pdf",
        size_bytes=12,
        checksum_sha256="4" * 64,
        storage_key="foreign-final",
        created_at=datetime(2026, 1, 4, tzinfo=UTC),
    )
    session.add_all([first_final, revision_final, draft, foreign])
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/artifacts/final-output/history"
        f"?task_id={task.id}",
        headers=_headers(owner.id),
    )
    foreign_task_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/artifacts/final-output/history"
        f"?task_id={task.id}",
        headers=_headers(other_owner.id),
    )
    foreign_owned_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/artifacts/final-output/history"
        f"?task_id={other_task.id}",
        headers=_headers(other_owner.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["final_output"] == {"final_output": "Approved delivery"}
    assert payload["final_work_package_ids"] == [
        "manager-summary",
        "manager-summary-revision-1",
    ]
    assert payload["total"] == 2
    assert payload["latest_artifact_id"] == str(revision_final.id)
    assert payload["latest_version"] == 1
    assert [item["filename"] for item in payload["items"]] == [
        "final-v2.pdf",
        "final-v1.pdf",
    ]
    assert foreign_task_response.status_code == 200
    assert foreign_task_response.json()["total"] == 0
    assert foreign_task_response.json()["final_output"] is None
    assert foreign_owned_response.status_code == 200
    assert foreign_owned_response.json()["items"][0]["filename"] == "foreign-final.pdf"


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


def test_workspace_update_can_pause_scheduler_and_records_audit() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")

    response = client.patch(
        f"/api/v1/workspaces/{workspace.id}",
        headers=_headers(owner.id),
        json={
            "status": "paused",
            "settings": {
                "scheduler": {
                    "paused": True,
                    "pause_reason": "maintenance",
                },
                "model_provider": {
                    "api_key": "sk-workspace",
                    "base_url": "https://workspace.example.test/private",
                },
            },
        },
    )

    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.workspace_id == workspace.id)
        .order_by(AuditEvent.created_at.asc())
    ).all()
    actions = [event.action for event in events]

    assert response.status_code == 200
    assert response.json()["status"] == "paused"
    assert response.json()["settings"]["scheduler"]["paused"] is True
    assert response.json()["settings"]["model_provider"] == {
        "api_key": "[redacted]",
        "base_url": "[redacted]",
    }
    assert "sk-workspace" not in str(response.json())
    assert "workspace.example.test/private" not in str(response.json())
    assert actions == ["workspace.status_updated", "workspace.scheduler_policy_updated"]
    assert events[0].audit_metadata["before"] == "active"
    assert events[0].audit_metadata["after"] == "paused"
    assert events[1].audit_metadata["after"]["pause_reason"] == "maintenance"


def test_workspace_update_rejects_invalid_scheduler_pause_config() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")

    response = client.patch(
        f"/api/v1/workspaces/{workspace.id}",
        headers=_headers(owner.id),
        json={"settings": {"scheduler": {"paused": "yes"}}},
    )

    assert response.status_code == 422


def test_workspace_quota_api_manages_runtime_limits() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    viewer, _ = _seed_workspace(
        session,
        role="viewer",
        email="viewer@example.com",
        slug="viewer-space",
    )

    upserted = client.put(
        f"/api/v1/workspaces/{workspace.id}/quotas",
        headers=_headers(owner.id),
        json={
            "quotas": [
                {"quota_key": "active_runs", "limit_value": 2, "unit": "count"},
                {"quota_key": "memory_mb", "limit_value": 4096, "unit": "mb"},
                {"quota_key": "docker_runtimes", "limit_value": 1, "unit": "count"},
                {"quota_key": "self_hosted_jobs", "limit_value": 1, "unit": "count"},
            ]
        },
    )
    memory_quota = session.scalar(
        select(WorkspaceQuota).where(
            WorkspaceQuota.workspace_id == workspace.id,
            WorkspaceQuota.quota_key == "memory_mb",
        )
    )
    assert memory_quota is not None
    memory_quota.reserved_value = 1024
    session.commit()

    tightened = client.put(
        f"/api/v1/workspaces/{workspace.id}/quotas",
        headers=_headers(owner.id),
        json={"quotas": [{"quota_key": "memory_mb", "limit_value": 512, "unit": "mb"}]},
    )
    listed = client.get(f"/api/v1/workspaces/{workspace.id}/quotas", headers=_headers(owner.id))
    denied = client.put(
        f"/api/v1/workspaces/{workspace.id}/quotas",
        headers=_headers(viewer.id),
        json={"quotas": [{"quota_key": "active_runs", "limit_value": 99}]},
    )
    disabled = client.delete(
        f"/api/v1/workspaces/{workspace.id}/quotas/docker_runtimes",
        headers=_headers(owner.id),
    )

    assert upserted.status_code == 200
    assert [item["quota_key"] for item in upserted.json()] == [
        "active_runs",
        "docker_runtimes",
        "memory_mb",
        "self_hosted_jobs",
    ]
    assert tightened.status_code == 200
    assert tightened.json()[0]["quota_key"] == "memory_mb"
    assert tightened.json()[0]["reserved_value"] == 1024
    assert tightened.json()[0]["available_value"] == 0
    assert tightened.json()[0]["utilization"] == 2.0
    assert tightened.json()[0]["saturated"] is True
    assert tightened.json()[0]["over_reserved"] is True
    assert listed.status_code == 200
    quota_by_key = {item["quota_key"]: item for item in listed.json()["items"]}
    assert quota_by_key["memory_mb"]["reserved_value"] == 1024
    assert quota_by_key["memory_mb"]["available_value"] == 0
    assert quota_by_key["memory_mb"]["utilization"] == 2.0
    assert quota_by_key["memory_mb"]["saturated"] is True
    assert quota_by_key["memory_mb"]["over_reserved"] is True
    assert denied.status_code == 403
    assert disabled.status_code == 200
    assert disabled.json()["quota_key"] == "docker_runtimes"
    assert disabled.json()["status"] == "disabled"
    audit_events = (
        session.query(AuditEvent)
        .filter(AuditEvent.workspace_id == workspace.id)
        .order_by(AuditEvent.created_at)
        .all()
    )
    assert [event.action for event in audit_events] == [
        "workspace.quotas_upserted",
        "workspace.quotas_upserted",
        "workspace.quota_disabled",
    ]
    assert audit_events[1].audit_metadata["quotas"][0]["before"]["reserved_value"] == 1024
    assert audit_events[1].audit_metadata["quotas"][0]["after"]["limit_value"] == 512
    assert audit_events[2].audit_metadata["quota_key"] == "docker_runtimes"


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
