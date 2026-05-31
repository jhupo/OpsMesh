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
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.handlers import WorkerJobHandler
from backend.app.workers.queue import RedisQueue, consume_once
from backend.app.workspaces.models import Workspace, WorkspaceMember, WorkspaceQuota
from backend.app.workspaces.quotas import WorkspaceQuotaService

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
    design_gap = TaskStep(
        workspace_id=workspace.id,
        task_id=running_task.id,
        work_package_id="design",
        required_role="designer",
        required_skills=["ux"],
        title="Design console",
        status="queued",
        order_index=15,
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
            design_gap,
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
    assert body["summary"]["step_counts"] == {"completed": 1, "queued": 1, "running": 1}
    assert body["summary"]["run_counts"] == {"running": 1}
    assert body["summary"]["needs_attention_tasks"] == 1
    assert body["summary"]["risk_counts"] == {
        "critical": 0,
        "high": 1,
        "medium": 0,
        "low": 0,
    }
    assert body["summary"]["high_risk_task_count"] == 1
    assert body["summary"]["staffing_gap_count"] == 1
    assert body["summary"]["staffing_gap_step_count"] == 1
    assert body["summary"]["available_member_capacity"] == 2
    assert body["summary"]["delivery_health"] == {
        "status": "critical",
        "score": 40,
        "reasons": ["staffing_gap", "high_risk_tasks", "blocked_tasks"],
        "bottleneck_count": 3,
        "high_risk_task_count": 1,
        "blocked_task_count": 1,
        "overloaded_member_count": 0,
        "capacity_utilization": 0.3333,
        "available_member_capacity": 2,
    }
    bottlenecks = {item["code"]: item for item in body["summary"]["bottlenecks"]}
    assert set(bottlenecks) == {"blocked_tasks", "high_risk_tasks", "staffing_gap"}
    assert bottlenecks["staffing_gap"]["task_ids"] == [str(running_task.id)]
    assert bottlenecks["staffing_gap"]["recommended_action"] == "add_or_hire_team_member"
    actions = {item["action"]: item for item in body["summary"]["recommended_actions"]}
    assert actions["add_or_hire_team_member"]["task_ids"] == [str(running_task.id)]
    assert actions["monitor_specialist_execution"]["task_ids"] == [str(running_task.id)]
    assert actions["request_manager_review"]["task_ids"] == [str(running_task.id)]
    intervention_plan = {
        item["action"]: item for item in body["summary"]["intervention_plan"]
    }
    assert intervention_plan["add_or_hire_team_member"] == {
        "action": "add_or_hire_team_member",
        "category": "staffing",
        "severity": "high",
        "priority": 81,
        "count": 1,
        "task_ids": [str(running_task.id)],
        "task_step_ids": [str(design_gap.id)],
        "automation": "talent_market",
        "operator_action": None,
        "api_route": (
            "POST /api/v1/workspaces/{workspace_id}/tasks/{task_id}/"
            "talent-market/recommendations"
        ),
        "payload_template": {"max_candidates_per_role": 3},
        "reason_codes": [
            "staffing_gap",
            "missing_manager_summary_step",
            "specialist_steps_incomplete",
            "risk:high",
        ],
    }
    assert intervention_plan["request_manager_review"]["automation"] == "operator_action"
    assert intervention_plan["request_manager_review"]["payload_template"] == {
        "action": "request_manager_review",
        "task_step_ids": [],
        "reason": "team_execution_overview",
        "metadata": {"source": "team_execution_overview"},
    }

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
    assert task["risk_level"] == "high"
    assert task["attention_score"] == 170
    assert set(task["recommended_actions"]) == {
        "monitor_specialist_execution",
        "request_manager_review",
    }
    assert "specialist_steps_incomplete" in task["blocked_reasons"]
    assert task["active_run_count"] == 1
    assert body["staffing_gaps"] == [
        {
            "required_role": "designer",
            "required_skills": ["ux"],
            "step_count": 1,
            "task_count": 1,
            "task_ids": [str(running_task.id)],
            "task_step_ids": [str(design_gap.id)],
            "matching_member_count": 0,
            "recommended_action": "add_or_hire_team_member",
        }
    ]

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


def test_team_project_dashboard_aggregates_delivery_progress_and_redacts() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session,
        role="owner",
        email="other-project-dashboard@example.com",
        slug="other-project-dashboard",
    )
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Project Team",
        team_type="software",
        status="active",
    )
    session.add(team)
    session.flush()
    blocked_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Blocked API",
        status="blocked",
        priority=9,
        domain_type="software",
        input={"api_key": "sk-dashboard-task"},
    )
    review_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Review image",
        status="running",
        priority=5,
        domain_type="aigc",
    )
    completed_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Accepted task",
        status="completed",
        priority=1,
        final_output={"summary": "Accepted", "token": "final-token"},
        completed_at=datetime.now(UTC),
    )
    session.add_all([blocked_task, review_task, completed_task])
    session.flush()
    blocked_step = TaskStep(
        workspace_id=workspace.id,
        task_id=blocked_task.id,
        title="Patch API",
        status="blocked",
        expected_artifacts=["patch"],
        dependencies={"blocked_reason": "task_paused", "token": "step-secret"},
        order_index=1,
    )
    review_step = TaskStep(
        workspace_id=workspace.id,
        task_id=review_task.id,
        title="Create image",
        status="completed",
        expected_artifacts=["image"],
        order_index=1,
    )
    completed_step = TaskStep(
        workspace_id=workspace.id,
        task_id=completed_task.id,
        title="Done",
        status="completed",
        expected_artifacts=[],
        order_index=1,
    )
    session.add_all([blocked_step, review_step, completed_step])
    session.flush()
    session.add_all(
        [
            AgentRun(
                workspace_id=workspace.id,
                task_id=review_task.id,
                task_step_id=review_step.id,
                status=RunStatus.RUNNING.value,
                input={"authorization": "Bearer dashboard-run"},
            ),
            Artifact(
                workspace_id=workspace.id,
                task_id=review_task.id,
                task_step_id=review_step.id,
                artifact_type="image",
                filename="image.png",
                content_type="image/png",
                review_status="pending",
                version=1,
                size_bytes=128,
                checksum_sha256="1" * 64,
                storage_key="secret-dashboard-storage",
                artifact_metadata={"api_key": "sk-artifact-dashboard"},
                created_at=datetime.now(UTC),
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=review_task.id,
                message_type="agent.progress",
                sequence=1,
                body="Private progress body",
                payload={"token": "message-token"},
            ),
        ]
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/project-dashboard",
        headers=_headers(owner.id),
    )
    include_completed = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/project-dashboard"
        "?include_completed=true",
        headers=_headers(owner.id),
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/project-dashboard",
        headers=_headers(other_owner.id),
    )
    missing = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{uuid4()}/project-dashboard",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["team"]["name"] == "Project Team"
    assert body["summary"]["total_tasks"] == 2
    assert body["summary"]["status_counts"] == {"blocked": 1, "running": 1}
    assert body["summary"]["delivery_status_counts"] == {
        "incomplete": 1,
        "needs_review": 1,
    }
    assert body["summary"]["blocked_task_count"] == 1
    assert body["summary"]["high_risk_task_count"] == 1
    assert body["summary"]["active_run_count"] == 1
    assert body["summary"]["missing_expected_artifact_count"] == 1
    assert body["summary"]["pending_review_task_count"] == 1
    by_title = {item["title"]: item for item in body["tasks"]}
    blocked = by_title["Blocked API"]
    assert blocked["risk_level"] == "high"
    assert blocked["delivery"]["status"] == "incomplete"
    assert blocked["blocked_reasons"] == [
        "task_blocked",
        "task_paused",
        "missing_expected_artifacts",
    ]
    assert set(blocked["recommended_actions"]) == {"resume_task", "create_correction"}
    review = by_title["Review image"]
    assert review["risk_level"] == "medium"
    assert review["progress"]["completion_ratio"] == 1.0
    assert review["execution"]["active_run_count"] == 1
    assert review["execution"]["latest_message"]["message_type"] == "agent.progress"
    assert review["delivery"]["status"] == "needs_review"
    assert set(review["recommended_actions"]) == {"review_artifacts", "monitor_active_runs"}
    assert include_completed.status_code == 200
    assert include_completed.json()["summary"]["total_tasks"] == 3
    assert include_completed.json()["summary"]["delivery_status_counts"]["accepted"] == 1
    assert forbidden.status_code == 403
    assert missing.status_code == 404

    serialized = str(include_completed.json())
    assert "sk-dashboard-task" not in serialized
    assert "step-secret" not in serialized
    assert "Bearer dashboard-run" not in serialized
    assert "secret-dashboard-storage" not in serialized
    assert "sk-artifact-dashboard" not in serialized
    assert "Private progress body" not in serialized
    assert "message-token" not in serialized
    assert "final-token" not in serialized


def test_team_command_center_aggregates_queues_actions_and_preserves_scope() -> None:
    queue_redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(queue_redis, RedisKeyBuilder("chaincloud"), "agent_runs", 0)
    client, session = _client(queue=queue)
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-command-center@example.com",
        slug="other-command-center",
    )
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="PM",
        role="project_manager",
        model_settings={"api_key": "sk-command-manager"},
    )
    developer = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
    )
    session.add_all([manager, developer])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Command Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
    )
    other_local_team = AgentTeam(
        workspace_id=workspace.id,
        name="Other Local Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
    )
    foreign_team = AgentTeam(
        workspace_id=other_workspace.id,
        name="Foreign Command Team",
        team_type="software",
    )
    session.add_all([team, other_local_team, foreign_team])
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=manager.id,
                team_role="project_manager",
                max_concurrent_tasks=2,
                order_index=1,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=developer.id,
                team_role="developer",
                max_concurrent_tasks=2,
                order_index=2,
            ),
        ]
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Ship command center",
        status="running",
        priority=8,
        domain_type="software",
        input={"api_key": "sk-command-task"},
        team_snapshot={"team": {"manager_agent_profile_id": str(manager.id)}},
        project_plan={"planner_agent_profile_id": str(manager.id)},
    )
    foreign_task = Task(
        workspace_id=other_workspace.id,
        created_by_user_id=other_owner.id,
        agent_team_id=foreign_team.id,
        title="Foreign command task",
        status="running",
    )
    other_local_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=other_local_team.id,
        title="Other local team task",
        status="running",
        priority=10,
    )
    session.add_all([task, foreign_task, other_local_task])
    session.flush()
    design_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=developer.id,
        work_package_id="design",
        required_role="developer",
        title="Design command center",
        status="completed",
        order_index=20,
        result_summary="Design ready",
    )
    summary_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=manager.id,
        work_package_id="manager-summary",
        required_role="project_manager",
        title="Review command center",
        status="completed",
        order_index=40,
    )
    session.add_all(
        [
            TaskStep(
                workspace_id=workspace.id,
                task_id=task.id,
                assigned_agent_profile_id=manager.id,
                work_package_id="manager-planning",
                required_role="project_manager",
                title="Plan command center",
                status="completed",
                order_index=10,
            ),
            design_step,
            summary_step,
        ]
    )
    session.flush()
    session.add(
        TaskStep(
            workspace_id=workspace.id,
            task_id=task.id,
            assigned_agent_profile_id=developer.id,
            work_package_id="build",
            required_role="developer",
            title="Build command center",
            status="queued",
            order_index=30,
            dependencies={"after_step_ids": [str(design_step.id)]},
        )
    )
    session.add(
        TaskStep(
            workspace_id=workspace.id,
            task_id=other_local_task.id,
            assigned_agent_profile_id=developer.id,
            work_package_id="other-build",
            required_role="developer",
            title="Other team queued work",
            status="queued",
            order_index=10,
        )
    )
    session.add(
        TaskMessage(
            workspace_id=workspace.id,
            task_id=task.id,
            task_step_id=summary_step.id,
            agent_profile_id=manager.id,
            message_type="pm.acceptance_decision",
            sequence=1,
            body="Private manager review body.",
            payload={
                "decision": "request_revision",
                "summary": "Needs follow up",
                "revision_requests": [
                    {"work_package_id": "build", "instruction": "Add API tests"}
                ],
                "token": "hidden-command-token",
            },
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/command-center",
        headers=_headers(owner.id),
    )
    missing = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{uuid4()}/command-center",
        headers=_headers(owner.id),
    )
    foreign_team_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{foreign_team.id}/command-center",
        headers=_headers(owner.id),
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/command-center",
        headers=_headers(other_owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["team_id"] == str(team.id)
    assert body["summary"]["total_tasks"] == 1
    assert body["summary"]["needs_attention_tasks"] == 1
    assert body["summary"]["handoff_queue_total"] >= 1
    assert body["summary"]["manager_queue_total"] == 1
    assert body["queues"]["handoff"]["team_id"] == str(team.id)
    assert body["queues"]["manager"]["team_id"] == str(team.id)
    sources = {item["source"] for item in body["action_plan"]}
    assert {"execution_overview", "handoff_queue", "manager_queue"} <= sources
    assert body["summary"]["action_plan_source_counts"]["execution_overview"] >= 1
    assert body["summary"]["action_plan_source_counts"]["handoff_queue"] >= 1
    assert body["summary"]["action_plan_source_counts"]["manager_queue"] == 1
    assert any(item["action"] == "schedule_downstream_steps" for item in body["action_plan"])
    assert any(item["action"] == "request_manager_review" for item in body["action_plan"])
    assert missing.status_code == 404
    assert foreign_team_response.status_code == 404
    assert forbidden.status_code == 403
    serialized = str(body)
    assert "sk-command-manager" not in serialized
    assert "sk-command-task" not in serialized
    assert "hidden-command-token" not in serialized
    assert "Private manager review body." not in serialized
    assert "Foreign command task" not in serialized

    dry_run = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/command-center/actions/apply",
        headers=_headers(owner.id),
        json={
            "dry_run": True,
            "metadata": {"token": "hidden-dry-run-token"},
        },
    )
    assert dry_run.status_code == 200
    dry_run_body = dry_run.json()
    assert dry_run_body["status"] == "dry_run"
    assert dry_run_body["eligible_action_count"] == 2
    assert {item["action"] for item in dry_run_body["results"]} == {
        "request_manager_review",
        "schedule_downstream_steps",
    }
    assert all(item["status"] == "would_apply" for item in dry_run_body["results"])
    assert "hidden-dry-run-token" not in str(dry_run_body)
    manager_review_steps = session.scalars(
        select(TaskStep).where(
            TaskStep.workspace_id == workspace.id,
            TaskStep.task_id == task.id,
            TaskStep.work_package_id.like("manager-summary-operator-%"),
        )
    ).all()
    assert manager_review_steps == []

    apply_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/command-center/actions/apply",
        headers=_headers(owner.id),
        json={
            "dry_run": False,
            "enqueue_runs": True,
            "reason": "command center auto apply",
            "metadata": {"api_key": "sk-command-apply"},
        },
    )
    assert apply_response.status_code == 200
    apply_body = apply_response.json()
    assert apply_body["status"] == "applied"
    assert apply_body["eligible_action_count"] == 2
    assert apply_body["applied_action_count"] == 2
    assert apply_body["scheduled_run_count"] == 2
    applied = {item["action"]: item for item in apply_body["results"]}
    assert applied["request_manager_review"]["candidate_count"] >= 2
    assert applied["schedule_downstream_steps"]["candidate_count"] >= 1
    assert {item["task_id"] for item in apply_body["scheduled_runs"]} == {str(task.id)}
    assert queue_redis.llen(RedisKeyBuilder("chaincloud").queue("agent_runs")) == 2
    session.expire_all()
    scheduled_runs = session.scalars(
        select(AgentRun).where(
            AgentRun.workspace_id == workspace.id,
            AgentRun.task_id == task.id,
            AgentRun.status == RunStatus.QUEUED.value,
        )
    ).all()
    assert len(scheduled_runs) == 2
    other_team_runs = session.scalars(
        select(AgentRun).where(
            AgentRun.workspace_id == workspace.id,
            AgentRun.task_id == other_local_task.id,
        )
    ).all()
    assert other_team_runs == []
    command_center_audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "team.command_center.actions_applied",
        )
    )
    assert command_center_audit is not None
    assert command_center_audit.audit_metadata["scheduled_run_count"] == 2
    assert "sk-command-apply" not in str(apply_body)
    manager_review_steps = session.scalars(
        select(TaskStep).where(
            TaskStep.workspace_id == workspace.id,
            TaskStep.task_id == task.id,
            TaskStep.work_package_id.like("manager-summary-operator-%"),
        )
    ).all()
    assert len(manager_review_steps) == 1


def test_team_execution_loop_status_reports_ready_to_finalize_and_redacts_payloads() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session,
        role="owner",
        email="other-loop-status@example.com",
        slug="other-loop-status",
    )
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="PM",
        role="project_manager",
        model_settings={"api_key": "sk-loop-status-manager"},
    )
    developer = AgentProfile(workspace_id=workspace.id, name="Developer", role="developer")
    session.add_all([manager, developer])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Loop Status Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
    )
    other_team = AgentTeam(
        workspace_id=workspace.id,
        name="Other Loop Status Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
    )
    session.add_all([team, other_team])
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=manager.id,
                team_role="project_manager",
                max_concurrent_tasks=2,
                order_index=1,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=developer.id,
                team_role="developer",
                max_concurrent_tasks=2,
                order_index=2,
            ),
        ]
    )
    finalizable_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Ready to finalize",
        status="running",
        priority=9,
        team_snapshot={"team": {"manager_agent_profile_id": str(manager.id)}},
        project_plan={"planner_agent_profile_id": str(manager.id)},
    )
    blocked_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=other_team.id,
        title="Foreign attention task",
        status="running",
        priority=4,
        team_snapshot={"team": {"manager_agent_profile_id": str(manager.id)}},
        project_plan={"planner_agent_profile_id": str(manager.id)},
    )
    session.add_all([finalizable_task, blocked_task])
    session.flush()

    def add_completed_flow(task: Task, summary: str) -> None:
        planning = TaskStep(
            workspace_id=workspace.id,
            task_id=task.id,
            assigned_agent_profile_id=manager.id,
            work_package_id="manager-planning",
            required_role="project_manager",
            title=f"Plan {task.title}",
            status="completed",
            order_index=10,
        )
        build = TaskStep(
            workspace_id=workspace.id,
            task_id=task.id,
            assigned_agent_profile_id=developer.id,
            work_package_id="build",
            required_role="developer",
            title=f"Build {task.title}",
            status="completed",
            order_index=20,
        )
        summary_step = TaskStep(
            workspace_id=workspace.id,
            task_id=task.id,
            assigned_agent_profile_id=manager.id,
            work_package_id="manager-summary",
            required_role="project_manager",
            title=f"Review {task.title}",
            status="completed",
            order_index=30,
        )
        session.add_all([planning, build, summary_step])
        session.flush()
        session.add(
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=summary_step.id,
                agent_profile_id=manager.id,
                message_type="pm.acceptance_decision",
                sequence=1,
                body=f"Private body for {task.title}",
                payload={
                    "decision": "approved",
                    "summary": summary,
                    "api_key": "sk-loop-status-approval",
                },
            )
        )

    add_completed_flow(finalizable_task, "Ready for release")
    add_completed_flow(blocked_task, "Foreign ready")
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/execution-loop",
        headers=_headers(owner.id),
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/execution-loop",
        headers=_headers(other_owner.id),
    )
    missing = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{uuid4()}/execution-loop",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready_to_finalize"
    assert body["summary"]["action_plan_count"] == 0
    assert body["summary"]["finalizable_task_count"] == 1
    assert body["summary"]["needs_attention_tasks"] == 0
    assert body["command_center"]["summary"]["action_plan_count"] == 0
    assert body["finalization"]["status"] == "dry_run"
    assert body["finalization"]["finalized_task_count"] == 0
    assert body["finalization"]["scanned_task_count"] == 1
    assert body["finalization"]["results"][0]["status"] == "would_finalize"
    serialized = str(body)
    assert "sk-loop-status-manager" not in serialized
    assert "sk-loop-status-approval" not in serialized
    assert "Private body for Ready to finalize" not in serialized
    assert "Foreign attention task" not in serialized
    assert forbidden.status_code == 403
    assert missing.status_code == 404


def test_team_execution_loop_finalize_closes_approved_tasks_only() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session,
        role="owner",
        email="other-finalize@example.com",
        slug="other-finalize",
    )
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="PM",
        role="project_manager",
        model_settings={"api_key": "sk-finalize-manager"},
    )
    developer = AgentProfile(workspace_id=workspace.id, name="Developer", role="developer")
    session.add_all([manager, developer])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Finalize Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
    )
    other_team = AgentTeam(
        workspace_id=workspace.id,
        name="Other Finalize Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
    )
    session.add_all([team, other_team])
    session.flush()
    approved_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Approved delivery",
        status="running",
        priority=8,
        team_snapshot={"team": {"manager_agent_profile_id": str(manager.id)}},
        project_plan={"planner_agent_profile_id": str(manager.id)},
    )
    needs_follow_up = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Needs follow up",
        status="running",
        priority=7,
        team_snapshot={"team": {"manager_agent_profile_id": str(manager.id)}},
        project_plan={"planner_agent_profile_id": str(manager.id)},
    )
    other_team_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=other_team.id,
        title="Other approved delivery",
        status="running",
        priority=10,
        team_snapshot={"team": {"manager_agent_profile_id": str(manager.id)}},
        project_plan={"planner_agent_profile_id": str(manager.id)},
    )
    session.add_all([approved_task, needs_follow_up, other_team_task])
    session.flush()

    def add_completed_flow(task: Task, decision: str, summary: str) -> None:
        planning = TaskStep(
            workspace_id=workspace.id,
            task_id=task.id,
            assigned_agent_profile_id=manager.id,
            work_package_id="manager-planning",
            required_role="project_manager",
            title=f"Plan {task.title}",
            status="completed",
            order_index=10,
        )
        build = TaskStep(
            workspace_id=workspace.id,
            task_id=task.id,
            assigned_agent_profile_id=developer.id,
            work_package_id="build",
            required_role="developer",
            title=f"Build {task.title}",
            status="completed",
            order_index=20,
        )
        summary_step = TaskStep(
            workspace_id=workspace.id,
            task_id=task.id,
            assigned_agent_profile_id=manager.id,
            work_package_id="manager-summary",
            required_role="project_manager",
            title=f"Review {task.title}",
            status="completed",
            order_index=30,
        )
        session.add_all([planning, build, summary_step])
        session.flush()
        session.add(
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=summary_step.id,
                agent_profile_id=manager.id,
                message_type="pm.acceptance_decision",
                sequence=1,
                body=f"Private body for {task.title}",
                payload={
                    "decision": decision,
                    "summary": summary,
                    "api_key": "sk-approved-finalize",
                },
            )
        )

    add_completed_flow(approved_task, "approved", "Ready to ship")
    add_completed_flow(needs_follow_up, "request_revision", "Needs tests")
    add_completed_flow(other_team_task, "approved", "Other team ready")
    session.commit()

    dry_run = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/execution-loop/finalize",
        headers=_headers(owner.id),
        json={"dry_run": True},
    )
    forbidden = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/execution-loop/finalize",
        headers=_headers(other_owner.id),
        json={"dry_run": True},
    )
    missing = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{uuid4()}/execution-loop/finalize",
        headers=_headers(owner.id),
        json={"dry_run": True},
    )

    assert dry_run.status_code == 200
    dry_run_body = dry_run.json()
    assert dry_run_body["status"] == "dry_run"
    assert dry_run_body["finalized_task_count"] == 0
    assert dry_run_body["scanned_task_count"] == 2
    by_task = {item["task_id"]: item for item in dry_run_body["results"]}
    assert by_task[str(approved_task.id)]["status"] == "would_finalize"
    assert by_task[str(needs_follow_up.id)]["reason"] == "manager_acceptance_not_healthy"
    assert str(other_team_task.id) not in by_task
    assert forbidden.status_code == 403
    assert missing.status_code == 404
    assert "Private body" not in str(dry_run_body)
    assert "sk-approved-finalize" not in str(dry_run_body)
    session.refresh(approved_task)
    assert approved_task.status == "running"

    applied = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/execution-loop/finalize",
        headers=_headers(owner.id),
        json={"dry_run": False},
    )
    assert applied.status_code == 200
    applied_body = applied.json()
    assert applied_body["status"] == "finalized"
    assert applied_body["finalized_task_count"] == 1
    session.expire_all()
    stored_approved = session.get(Task, approved_task.id)
    stored_follow_up = session.get(Task, needs_follow_up.id)
    stored_other = session.get(Task, other_team_task.id)
    assert stored_approved is not None
    assert stored_follow_up is not None
    assert stored_other is not None
    assert stored_approved.status == "completed"
    assert stored_approved.final_output["summary"] == "Ready to ship"
    assert stored_follow_up.status == "running"
    assert stored_other.status == "running"
    audits = session.scalars(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action.in_(
                [
                    "task.execution_loop.finalized",
                    "team.execution_loop.tasks_finalized",
                ]
            ),
        )
    ).all()
    assert {audit.action for audit in audits} == {
        "task.execution_loop.finalized",
        "team.execution_loop.tasks_finalized",
    }


def test_team_execution_loop_run_advances_actions_runs_and_finalization() -> None:
    queue_redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(queue_redis, RedisKeyBuilder("chaincloud"), "agent_runs", 0)
    client, session = _client(queue=queue)
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session,
        role="owner",
        email="other-loop-run@example.com",
        slug="other-loop-run",
    )
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="PM",
        role="project_manager",
        model_settings={"api_key": "sk-loop-manager"},
    )
    developer = AgentProfile(workspace_id=workspace.id, name="Developer", role="developer")
    session.add_all([manager, developer])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Loop Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
    )
    session.add(team)
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=manager.id,
                team_role="project_manager",
                max_concurrent_tasks=2,
                order_index=1,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=developer.id,
                team_role="developer",
                max_concurrent_tasks=2,
                order_index=2,
            ),
        ]
    )
    approved_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Approved loop delivery",
        status="running",
        priority=9,
        input={"api_key": "sk-loop-task"},
        team_snapshot={"team": {"manager_agent_profile_id": str(manager.id)}},
        project_plan={"planner_agent_profile_id": str(manager.id)},
    )
    handoff_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Continue loop delivery",
        status="running",
        priority=8,
        domain_type="software",
        team_snapshot={"team": {"manager_agent_profile_id": str(manager.id)}},
        project_plan={"planner_agent_profile_id": str(manager.id)},
    )
    session.add_all([approved_task, handoff_task])
    session.flush()

    def add_manager_step(task: Task, work_package_id: str, status: str = "completed") -> TaskStep:
        step = TaskStep(
            workspace_id=workspace.id,
            task_id=task.id,
            assigned_agent_profile_id=manager.id,
            work_package_id=work_package_id,
            required_role="project_manager",
            title=work_package_id,
            status=status,
            order_index=10,
        )
        session.add(step)
        session.flush()
        return step

    add_manager_step(approved_task, "manager-planning")
    approved_summary = add_manager_step(approved_task, "manager-summary")
    session.add(
        TaskStep(
            workspace_id=workspace.id,
            task_id=approved_task.id,
            assigned_agent_profile_id=developer.id,
            work_package_id="build-approved",
            required_role="developer",
            title="Build approved work",
            status="completed",
            order_index=20,
        )
    )
    design_step = TaskStep(
        workspace_id=workspace.id,
        task_id=handoff_task.id,
        assigned_agent_profile_id=developer.id,
        work_package_id="design",
        required_role="developer",
        title="Design next work",
        status="completed",
        order_index=20,
    )
    add_manager_step(handoff_task, "manager-planning")
    handoff_summary = add_manager_step(handoff_task, "manager-summary")
    session.add(design_step)
    session.flush()
    session.add(
        TaskStep(
            workspace_id=workspace.id,
            task_id=handoff_task.id,
            assigned_agent_profile_id=developer.id,
            work_package_id="build",
            required_role="developer",
            title="Build next work",
            status="queued",
            order_index=30,
            dependencies={"after_step_ids": [str(design_step.id)]},
        )
    )
    session.add_all(
        [
            TaskMessage(
                workspace_id=workspace.id,
                task_id=approved_task.id,
                task_step_id=approved_summary.id,
                agent_profile_id=manager.id,
                message_type="pm.acceptance_decision",
                sequence=1,
                body="Private approved body",
                payload={
                    "decision": "approved",
                    "summary": "Approved loop output",
                    "token": "hidden-approved-token",
                },
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=handoff_task.id,
                task_step_id=handoff_summary.id,
                agent_profile_id=manager.id,
                message_type="pm.acceptance_decision",
                sequence=1,
                body="Private revision body",
                payload={
                    "decision": "request_revision",
                    "summary": "Needs next build",
                    "revision_requests": [
                        {"work_package_id": "build", "instruction": "Add endpoint tests"}
                    ],
                    "token": "hidden-revision-token",
                },
            ),
        ]
    )
    session.commit()

    dry_run = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/execution-loop/run",
        headers=_headers(owner.id),
        json={
            "dry_run": True,
            "metadata": {"token": "hidden-loop-dry-run"},
        },
    )
    assert dry_run.status_code == 200
    dry_body = dry_run.json()
    assert dry_body["status"] == "dry_run"
    assert dry_body["summary"]["eligible_action_count"] >= 1
    assert dry_body["summary"]["finalized_task_count"] == 0
    assert dry_body["finalization"]["finalized_task_count"] == 0
    assert queue_redis.llen(RedisKeyBuilder("chaincloud").queue("agent_runs")) == 0
    assert "hidden-loop-dry-run" not in str(dry_body)

    forbidden = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/execution-loop/run",
        headers=_headers(other_owner.id),
        json={"dry_run": True},
    )
    missing = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{uuid4()}/execution-loop/run",
        headers=_headers(owner.id),
        json={"dry_run": True},
    )
    assert forbidden.status_code == 403
    assert missing.status_code == 404

    applied = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/execution-loop/run",
        headers=_headers(owner.id),
        json={
            "dry_run": False,
            "enqueue_runs": True,
            "reason": "advance loop",
            "metadata": {"api_key": "sk-loop-run"},
        },
    )
    assert applied.status_code == 200
    body = applied.json()
    assert body["status"] == "advanced"
    assert body["summary"]["applied_action_count"] >= 1
    assert body["summary"]["scheduled_run_count"] >= 1
    assert body["summary"]["finalized_task_count"] == 1
    assert body["command_center_actions"]["scheduled_run_count"] >= 1
    assert body["finalization"]["finalized_task_count"] == 1
    assert queue_redis.llen(RedisKeyBuilder("chaincloud").queue("agent_runs")) >= 1
    serialized = str(body)
    assert "sk-loop-run" not in serialized
    assert "sk-loop-manager" not in serialized
    assert "sk-loop-task" not in serialized
    assert "hidden-approved-token" not in serialized
    assert "hidden-revision-token" not in serialized
    assert "Private approved body" not in serialized
    assert "Private revision body" not in serialized

    session.expire_all()
    stored_approved = session.get(Task, approved_task.id)
    assert stored_approved is not None
    assert stored_approved.status == "completed"
    scheduled_runs = session.scalars(
        select(AgentRun).where(
            AgentRun.workspace_id == workspace.id,
            AgentRun.task_id == handoff_task.id,
            AgentRun.status == RunStatus.QUEUED.value,
        )
    ).all()
    assert len(scheduled_runs) >= 1
    iteration_audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "team.execution_loop.iteration_ran",
        )
    )
    assert iteration_audit is not None
    assert iteration_audit.audit_metadata["finalized_task_count"] == 1


def test_team_operator_action_requests_manager_review_for_selected_tasks() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session,
        role="owner",
        email="other-team-operator@example.com",
        slug="other-team-operator",
    )
    manager = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "PM", "role": "project_manager"},
    )
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={
            "name": "Operator Team",
            "team_type": "software",
            "manager_agent_profile_id": manager.json()["id"],
        },
    )
    assert manager.status_code == 201
    assert team.status_code == 201
    team_id = UUID(team.json()["id"])
    manager_id = UUID(manager.json()["id"])

    active_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team_id,
        title="Active delivery",
        status="running",
        priority=7,
        team_snapshot={"team": {"manager_agent_profile_id": str(manager_id)}},
    )
    second_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team_id,
        title="Second delivery",
        status="queued",
        priority=5,
        team_snapshot={"team": {"manager_agent_profile_id": str(manager_id)}},
    )
    completed_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team_id,
        title="Completed delivery",
        status="completed",
        priority=1,
        team_snapshot={"team": {"manager_agent_profile_id": str(manager_id)}},
    )
    session.add_all([active_task, second_task, completed_task])
    session.commit()

    missing_task_id = uuid4()
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team_id}/operator-actions",
        headers=_headers(owner.id),
        json={
            "action": "request_manager_review",
            "task_ids": [
                str(active_task.id),
                str(second_task.id),
                str(completed_task.id),
                str(missing_task_id),
            ],
            "instruction": "Review blocked delivery without leaking sk-team-secret.",
            "reason": "team_intervention",
            "metadata": {"api_key": "sk-team-secret"},
        },
    )
    forbidden = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team_id}/operator-actions",
        headers=_headers(other_owner.id),
        json={"action": "request_manager_review", "task_ids": [str(active_task.id)]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "applied"
    assert body["requested_task_count"] == 4
    assert body["applied_count"] == 2
    assert body["skipped_count"] == 2
    results = {item["task_id"]: item for item in body["results"]}
    assert results[str(active_task.id)]["status"] == "applied"
    assert results[str(second_task.id)]["status"] == "applied"
    assert results[str(completed_task.id)]["message"] == "terminal_task"
    assert results[str(missing_task_id)]["message"] == "task_not_found_or_not_in_team"
    assert forbidden.status_code == 403
    assert "sk-team-secret" not in str(body)

    review_steps = session.scalars(
        select(TaskStep).where(
            TaskStep.workspace_id == workspace.id,
            TaskStep.work_package_id == "manager-summary-operator-1",
        )
    ).all()
    assert {step.task_id for step in review_steps} == {active_task.id, second_task.id}
    messages = session.scalars(
        select(TaskMessage).where(
            TaskMessage.workspace_id == workspace.id,
            TaskMessage.message_type == "task.operator.request_manager_review",
        )
    ).all()
    assert {message.task_id for message in messages} == {active_task.id, second_task.id}


def test_team_operator_action_requeues_and_schedules_step_actions() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Developer", "role": "developer"},
    )
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={"name": "Execution Team", "team_type": "software"},
    )
    assert agent.status_code == 201
    assert team.status_code == 201
    team_id = UUID(team.json()["id"])
    agent_id = UUID(agent.json()["id"])

    blocked_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team_id,
        title="Runtime blocked task",
        status="blocked",
        priority=8,
    )
    handoff_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team_id,
        title="Handoff task",
        status="running",
        priority=6,
    )
    session.add_all([blocked_task, handoff_task])
    session.flush()
    blocked_step = TaskStep(
        workspace_id=workspace.id,
        task_id=blocked_task.id,
        assigned_agent_profile_id=agent_id,
        work_package_id="blocked-build",
        title="Blocked build",
        status="blocked",
        order_index=10,
        dependencies={
            "blocked_reason": "runtime_space_paused",
            "blocked_resource_keys": ["active_runs"],
        },
    )
    source_step = TaskStep(
        workspace_id=workspace.id,
        task_id=handoff_task.id,
        assigned_agent_profile_id=agent_id,
        work_package_id="design",
        title="Design ready",
        status="completed",
        order_index=10,
    )
    session.add_all([blocked_step, source_step])
    session.flush()
    downstream_step = TaskStep(
        workspace_id=workspace.id,
        task_id=handoff_task.id,
        assigned_agent_profile_id=agent_id,
        work_package_id="build",
        title="Build downstream",
        status="blocked",
        order_index=20,
        dependencies={
            "after_step_ids": [str(source_step.id)],
            "blocked_reason": "dependency_incomplete",
        },
    )
    session.add(downstream_step)
    session.commit()

    missing_step_id = uuid4()
    requeue = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team_id}/operator-actions",
        headers=_headers(owner.id),
        json={
            "action": "requeue_blocked_steps",
            "task_step_ids": [str(blocked_step.id), str(missing_step_id)],
            "reason": "quota released",
            "metadata": {"token": "team-action-hidden"},
        },
    )
    schedule = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team_id}/operator-actions",
        headers=_headers(owner.id),
        json={
            "action": "schedule_downstream_steps",
            "task_step_ids": [str(source_step.id)],
            "reason": "handoff ready",
        },
    )

    assert requeue.status_code == 200
    requeue_body = requeue.json()
    assert requeue_body["action"] == "requeue_blocked_steps"
    assert requeue_body["applied_count"] == 1
    assert requeue_body["warnings"] == [
        f"task_step_not_found_or_not_in_team:{missing_step_id}"
    ]
    assert "team-action-hidden" not in str(requeue_body)
    session.refresh(blocked_step)
    assert blocked_step.status == "queued"
    assert "blocked_reason" not in blocked_step.dependencies
    assert "blocked_resource_keys" not in blocked_step.dependencies

    assert schedule.status_code == 200
    schedule_body = schedule.json()
    assert schedule_body["action"] == "schedule_downstream_steps"
    assert schedule_body["applied_count"] == 1
    assert schedule_body["results"][0]["changed_step_ids"] == [str(downstream_step.id)]
    session.refresh(downstream_step)
    assert downstream_step.status == "queued"
    assert downstream_step.dependencies == {"after_step_ids": [str(source_step.id)]}


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


def test_api_team_task_e2e_runs_workers_and_accepts_delivery() -> None:
    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
        blocking_timeout_seconds=0,
    )
    client, session = _client(queue=queue)
    owner, workspace = _seed_workspace(session, role="owner")
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
        f"/api/v1/workspaces/{workspace.id}/teams/{team_id}/org-chart",
        headers=_headers(owner.id),
    )
    assert org_chart.status_code == 200
    assert org_chart.json()["manager_agent"]["id"] == manager.json()["id"]
    assert org_chart.json()["capacity_summary"]["accepting_members"] == 2

    created_task = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={
            "agent_team_id": team_id,
            "domain_type": "market_research",
            "title": "Q2 market analysis",
            "description": "Produce a concise market analysis.",
            "priority": 7,
            "input": {
                "work_packages": [
                    {
                        "package_id": "market-research",
                        "title": "Market research",
                        "required_role": "researcher",
                        "required_skills": ["market_research"],
                    },
                    {
                        "package_id": "market-analysis",
                        "title": "Market analysis",
                        "required_role": "analyst",
                        "required_skills": ["analysis"],
                        "depends_on": ["market-research"],
                    },
                ]
            },
        },
    )
    assert created_task.status_code == 201
    task_id = UUID(created_task.json()["id"])
    assert created_task.json()["status"] == TaskStatus.QUEUED.value
    assert queue.count_queued(workspace_id=workspace.id) == 1

    handler = WorkerJobHandler(session, queue)
    handled_jobs = 0
    while consume_once(queue, handler.handle):
        handled_jobs += 1
        assert handled_jobs < 20

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
        f"/api/v1/workspaces/{workspace.id}/tasks?status=completed",
        headers=_headers(owner.id),
    )
    timeline = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task_id}/timeline",
        headers=_headers(owner.id),
    )
    observation = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task_id}/observation",
        headers=_headers(owner.id),
    )

    assert handled_jobs == 4
    assert queue.count_queued(workspace_id=workspace.id) == 0
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
    assert audit.audit_metadata["initial_run_enqueued"] is True
    assert completed_tasks.status_code == 200
    assert completed_tasks.json()["total"] == 1
    assert completed_tasks.json()["items"][0]["id"] == str(task_id)
    assert timeline.status_code == 200
    assert timeline.json()["summary"]["returned_events"] >= 1
    assert observation.status_code == 200
    assert observation.json()["summary"]["status"] == TaskStatus.COMPLETED.value


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
    initial_queue_depth = queue.count_queued(workspace_id=workspace.id)
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
    assert initial_queue_depth == 1
    assert queue.count_queued(workspace_id=workspace.id) == initial_queue_depth


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


def test_task_control_pause_instruction_and_resume_are_audited_and_redacted() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session,
        role="owner",
        email="other-task-control@example.com",
        slug="other-task-control",
    )
    agent = AgentProfile(workspace_id=workspace.id, name="Builder", role="developer")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Interruptible build",
        status="running",
        input={"api_key": "sk-task-control-input"},
    )
    session.add_all([agent, task])
    session.flush()
    running_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        title="Build API",
        status="running",
        dependencies={"token": "hidden-step-token"},
    )
    queued_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        title="Write tests",
        status="queued",
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=running_step.id,
        agent_profile_id=agent.id,
        status=RunStatus.RUNNING.value,
        input={"token": "hidden-run-token"},
    )
    session.add_all([running_step, queued_step, run])
    session.commit()

    pause = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/control",
        headers=_headers(owner.id),
        json={
            "action": "pause",
            "instruction": "Stop before touching production credentials.",
            "reason": "owner review",
            "metadata": {"api_key": "sk-task-control-pause"},
        },
    )
    forbidden = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/control",
        headers=_headers(other_owner.id),
        json={"action": "pause", "reason": "nope"},
    )
    missing = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{uuid4()}/control",
        headers=_headers(owner.id),
        json={"action": "pause"},
    )

    assert pause.status_code == 200
    paused_body = pause.json()
    assert paused_body["action"] == "pause"
    assert paused_body["task_status"] == "blocked"
    assert paused_body["details"]["cancelled_run_count"] == 1
    assert paused_body["details"]["blocked_step_count"] == 2
    assert forbidden.status_code == 403
    assert missing.status_code == 404
    serialized_pause = str(paused_body)
    assert "sk-task-control-pause" not in serialized_pause
    assert "hidden-run-token" not in serialized_pause
    assert "hidden-step-token" not in serialized_pause

    session.expire_all()
    paused_task = session.get(Task, task.id)
    paused_run = session.get(AgentRun, run.id)
    paused_steps = session.scalars(
        select(TaskStep).where(TaskStep.task_id == task.id).order_by(TaskStep.title.asc())
    ).all()
    assert paused_task is not None
    assert paused_run is not None
    assert paused_task.status == TaskStatus.BLOCKED.value
    assert paused_task.generic_state["control"]["paused"] is True
    assert paused_run.status == RunStatus.CANCELLED.value
    assert {step.status for step in paused_steps} == {"blocked"}
    assert all(step.dependencies["blocked_reason"] == "task_paused" for step in paused_steps)

    instruction = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/control",
        headers=_headers(owner.id),
        json={
            "action": "add_instruction",
            "instruction": "Use a mock credential and rerun the tests.",
            "metadata": {"authorization": "Bearer hidden-instruction"},
        },
    )
    assert instruction.status_code == 200
    assert instruction.json()["details"]["instruction_count"] == 1
    assert "hidden-instruction" not in str(instruction.json())

    resume = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/control",
        headers=_headers(owner.id),
        json={
            "action": "resume",
            "enqueue": False,
            "reason": "review complete",
            "metadata": {"token": "hidden-resume-token"},
        },
    )
    assert resume.status_code == 200
    resumed_body = resume.json()
    assert resumed_body["task_status"] == "running"
    assert resumed_body["details"]["unblocked_step_count"] == 2
    assert resumed_body["scheduled_run_ids"] == []
    assert "hidden-resume-token" not in str(resumed_body)

    session.expire_all()
    resumed_task = session.get(Task, task.id)
    resumed_steps = session.scalars(
        select(TaskStep).where(TaskStep.task_id == task.id).order_by(TaskStep.title.asc())
    ).all()
    assert resumed_task is not None
    assert resumed_task.status == TaskStatus.RUNNING.value
    assert resumed_task.generic_state["control"]["paused"] is False
    assert {step.status for step in resumed_steps} == {"queued"}
    assert all("task_control_paused" not in step.dependencies for step in resumed_steps)

    messages = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/messages",
        headers=_headers(owner.id),
    )
    assert messages.status_code == 200
    message_payload = str(messages.json())
    assert "sk-task-control-pause" not in message_payload
    assert "Bearer hidden-instruction" not in message_payload
    assert "hidden-resume-token" not in message_payload
    assert "task.control.pause" in message_payload
    assert "task.control.resume" in message_payload

    audit_actions = {
        event.action
        for event in session.query(AuditEvent)
        .filter(AuditEvent.workspace_id == workspace.id)
        .all()
    }
    assert {
        "task.control.paused",
        "task.control.instruction_added",
        "task.control.resumed",
    } <= audit_actions


def test_task_control_diagnostics_explains_pause_resume_and_corrections() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session,
        role="owner",
        email="other-control-diagnostics@example.com",
        slug="other-control-diagnostics",
    )
    agent = AgentProfile(workspace_id=workspace.id, name="Worker", role="developer")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Control diagnostics",
        status=TaskStatus.RUNNING.value,
    )
    session.add_all([agent, task])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        title="Build",
        status="running",
        dependencies={"token": "step-token"},
        order_index=1,
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        agent_profile_id=agent.id,
        status=RunStatus.RUNNING.value,
        input={"api_key": "sk-run-control-diagnostics"},
    )
    session.add_all([step, run])
    session.commit()

    pause = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/control",
        headers=_headers(owner.id),
        json={
            "action": "pause",
            "reason": "owner review",
            "metadata": {"api_key": "sk-control-diagnostics-pause"},
        },
    )
    paused = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/control-diagnostics",
        headers=_headers(owner.id),
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/control-diagnostics",
        headers=_headers(other_owner.id),
    )
    missing = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{uuid4()}/control-diagnostics",
        headers=_headers(owner.id),
    )

    assert pause.status_code == 200
    assert paused.status_code == 200
    paused_body = paused.json()
    assert paused_body["status"] == "paused"
    assert paused_body["summary"]["paused"] is True
    assert paused_body["summary"]["paused_blocked_step_count"] == 1
    assert paused_body["summary"]["cancelled_by_pause_run_count"] == 1
    assert paused_body["paused_steps"][0]["blocked_reason"] == "task_paused"
    assert paused_body["paused_steps"][0]["dependencies"]["token"] == "[redacted]"
    assert paused_body["cancelled_runs"][0]["error"]["code"] == "task_paused"
    assert paused_body["cancelled_runs"][0]["input"]["api_key"] == "[redacted]"
    assert paused_body["recent_control_messages"][0]["payload"]["metadata"]["api_key"] == (
        "[redacted]"
    )
    assert {item["action"] for item in paused_body["recommended_actions"]} == {"resume"}
    assert forbidden.status_code == 403
    assert missing.status_code == 404

    resume = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/control",
        headers=_headers(owner.id),
        json={
            "action": "resume",
            "enqueue": True,
            "reason": "review complete",
            "metadata": {"token": "resume-token"},
        },
    )
    correction = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/control",
        headers=_headers(owner.id),
        json={
            "action": "create_correction",
            "instruction": "Add missing validation tests.",
            "correction_mode": "add_missing_work",
            "target_type": "task",
            "metadata": {"authorization": "Bearer correction"},
        },
    )
    resumed = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/control-diagnostics"
        "?message_limit=10",
        headers=_headers(owner.id),
    )

    assert resume.status_code == 200
    assert resume.json()["scheduled_run_ids"]
    assert correction.status_code == 200
    assert resumed.status_code == 200
    resumed_body = resumed.json()
    assert resumed_body["summary"]["paused"] is False
    assert resumed_body["summary"]["paused_blocked_step_count"] == 0
    assert resumed_body["summary"]["scheduled_resume_run_count"] == 1
    assert resumed_body["summary"]["correction_message_count"] == 1
    assert resumed_body["scheduled_runs"][0]["input"]["source"] == "task_control_resume"
    message_types = {
        message["message_type"] for message in resumed_body["recent_control_messages"]
    }
    assert {
        "task.control.pause",
        "task.control.resume",
        "task.correction.created",
    } <= message_types
    action_names = {item["action"] for item in resumed_body["recommended_actions"]}
    assert {"monitor_active_runs", "review_corrections"} <= action_names

    serialized = str(paused_body) + str(resumed_body)
    assert "step-token" not in serialized
    assert "sk-run-control-diagnostics" not in serialized
    assert "sk-control-diagnostics-pause" not in serialized
    assert "resume-token" not in serialized
    assert "Bearer correction" not in serialized


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


def test_task_interaction_transcript_returns_context_and_redacts_payloads() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session,
        role="owner",
        email="other-transcript@example.com",
        slug="other-transcript",
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Builder",
        role="developer",
        model_settings={"api_key": "sk-agent-transcript"},
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Transcript task",
        status=TaskStatus.RUNNING.value,
        input={"base_url": "https://router.example.test/private"},
    )
    session.add_all([agent, task])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        work_package_id="build-api",
        title="Build API",
        status="running",
        order_index=1,
    )
    session.add(step)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        agent_profile_id=agent.id,
        status=RunStatus.RUNNING.value,
        input={"headers": {"authorization": "Bearer run-token"}},
        output={"token": "run-output-token"},
        error={"api_key": "sk-run-transcript"},
        model="gpt-test",
        started_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()
    session.add_all(
        [
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                message_type="planning.created",
                sequence=1,
                body="Plan is ready",
                payload={"token": "planning-token", "safe": "ok"},
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=step.id,
                agent_run_id=run.id,
                agent_profile_id=agent.id,
                message_type="agent.progress",
                sequence=2,
                body="Implemented the endpoint skeleton",
                payload={
                    "progress": "coding",
                    "headers": {"authorization": "Bearer message-token"},
                },
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                message_type="task.control.instruction_added",
                sequence=3,
                body="Please add tests",
                payload={"api_key": "sk-control-transcript"},
            ),
        ]
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/interaction-transcript"
        "?limit=2",
        headers=_headers(owner.id),
    )
    filtered = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/interaction-transcript"
        "?message_type=task.control.instruction_added",
        headers=_headers(owner.id),
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/interaction-transcript",
        headers=_headers(other_owner.id),
    )
    missing = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{uuid4()}/interaction-transcript",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["summary"]["task_status"] == TaskStatus.RUNNING.value
    assert body["summary"]["total_messages"] == 3
    assert body["summary"]["returned_messages"] == 2
    assert body["summary"]["has_more"] is True
    assert body["summary"]["latest_sequence"] == 3
    assert body["summary"]["message_type_counts"] == {
        "agent.progress": 1,
        "planning.created": 1,
        "task.control.instruction_added": 1,
    }
    assert body["participants"] == [
        {
            "id": str(agent.id),
            "name": "Builder",
            "role": "developer",
            "status": "active",
            "message_count": 1,
        }
    ]
    assert [item["sequence"] for item in body["items"]] == [1, 2]
    assert body["items"][0]["source"] == "system"
    assert body["items"][0]["payload"]["token"] == "[redacted]"
    assert body["items"][1]["source"] == "agent"
    assert body["items"][1]["phase"] == "agent"
    assert body["items"][1]["agent"]["name"] == "Builder"
    assert body["items"][1]["task_step"]["work_package_id"] == "build-api"
    assert body["items"][1]["agent_run"]["status"] == RunStatus.RUNNING.value
    assert body["items"][1]["payload"]["headers"] == "[redacted]"
    assert filtered.status_code == 200
    assert filtered.json()["summary"]["total_messages"] == 1
    assert filtered.json()["items"][0]["source"] == "operator"
    assert filtered.json()["items"][0]["payload"]["api_key"] == "[redacted]"
    assert forbidden.status_code == 403
    assert missing.status_code == 404

    serialized = str(body) + str(filtered.json())
    assert "sk-agent-transcript" not in serialized
    assert "router.example.test/private" not in serialized
    assert "Bearer run-token" not in serialized
    assert "run-output-token" not in serialized
    assert "sk-run-transcript" not in serialized
    assert "planning-token" not in serialized
    assert "Bearer message-token" not in serialized
    assert "sk-control-transcript" not in serialized


def test_task_live_status_api_returns_active_runs_and_message_cursor() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Researcher",
        role="researcher",
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Live task",
        status=TaskStatus.RUNNING.value,
        priority=7,
    )
    session.add_all([agent, task])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        title="Research",
        work_package_id="research",
        status="running",
        order_index=1,
    )
    session.add(step)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        agent_profile_id=agent.id,
        status=RunStatus.RUNNING.value,
        input={},
        model="gpt-test",
        started_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()
    session.add_all(
        [
            RunEvent(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                event_type="run.started",
                sequence=1,
                message="Started",
                event_metadata={"api_key": "sk-hidden"},
                created_at=datetime.now(UTC),
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=step.id,
                agent_run_id=run.id,
                agent_profile_id=agent.id,
                message_type="step.started",
                sequence=1,
                body="Started",
                payload={"token": "hidden"},
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=step.id,
                agent_run_id=run.id,
                agent_profile_id=agent.id,
                message_type="agent.progress",
                sequence=2,
                body="Working",
                payload={"progress": "drafting"},
            ),
        ]
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/live-status"
        "?after_sequence=1&message_limit=10",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["status"] == TaskStatus.RUNNING.value
    assert payload["summary"]["active_run_count"] == 1
    assert payload["summary"]["latest_message_sequence"] == 2
    assert payload["summary"]["poll_after_seconds"] == 1
    assert payload["steps"][0]["assigned_agent"]["name"] == "Researcher"
    assert payload["active_runs"][0]["latest_event"]["metadata"]["api_key"] == "[redacted]"
    assert [message["sequence"] for message in payload["recent_messages"]] == [2]
    assert payload["recent_messages"][0]["agent"]["role"] == "researcher"


def test_task_execution_status_reports_focus_actions_and_redacts_metadata() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session,
        role="owner",
        email="other-execution-status@example.com",
        slug="other-execution-status",
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Runtime Specialist",
        role="developer",
        model_settings={"api_key": "sk-agent-status"},
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Observe execution",
        status=TaskStatus.RUNNING.value,
        priority=8,
        input={"base_url": "https://router.example.test/private"},
        generic_state={
            "control": {
                "paused": True,
                "reason": "Need owner check",
                "headers": {"authorization": "Bearer task-control"},
            }
        },
    )
    session.add_all([agent, task])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        work_package_id="build",
        required_role="developer",
        title="Build API",
        status="queued",
        order_index=10,
        dependencies={
            "blocked_reason": "workspace_run_quota_exceeded",
            "headers": {"authorization": "Bearer step-token"},
        },
    )
    session.add(step)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        agent_profile_id=agent.id,
        status="waiting_runtime",
        input={
            "prompt": "Continue work",
            "base_url": "https://runtime.example.test/private",
            "headers": {"authorization": "Bearer runtime"},
        },
        output={"token": "runtime-output-token"},
        error={"api_key": "sk-run-status"},
        model="gpt-test",
        started_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()
    session.add_all(
        [
            RunEvent(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                event_type="runtime.waiting",
                sequence=1,
                message="Waiting for runtime",
                event_metadata={"secret": "event-secret", "safe": "ok"},
                created_at=datetime.now(UTC),
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=step.id,
                agent_run_id=run.id,
                agent_profile_id=agent.id,
                message_type="agent.progress",
                sequence=1,
                body="Working on runtime result",
                payload={"token": "message-token", "progress": "waiting"},
            ),
        ]
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/execution-status"
        "?message_limit=5&event_limit=10",
        headers=_headers(owner.id),
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/execution-status",
        headers=_headers(other_owner.id),
    )
    missing = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{uuid4()}/execution-status",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["task_id"] == str(task.id)
    assert body["status"] == "paused"
    assert body["control"]["paused"] is True
    assert body["control"]["headers"] == "[redacted]"
    assert body["summary"]["active_run_count"] == 1
    assert body["summary"]["blocked_reason_count"] >= 2
    assert body["current_focus"]["kind"] == "run"
    assert body["current_focus"]["status"] == "waiting_runtime"
    assert body["current_focus"]["run_id"] == str(run.id)
    assert body["current_focus"]["agent"]["name"] == "Runtime Specialist"
    assert "task_paused" in body["blocked_reasons"]
    assert "scheduler:workspace_run_quota_exceeded" in body["blocked_reasons"]
    action_names = {item["action"] for item in body["recommended_actions"]}
    assert {"resume", "inspect_runtime", "inspect_scheduler_block"} <= action_names
    assert body["active_runs"][0]["latest_event"]["metadata"]["secret"] == "[redacted]"
    assert body["recent_messages"][0]["payload"]["token"] == "[redacted]"
    events_by_type = {event["event_type"]: event for event in body["recent_events"]}
    assert events_by_type["runtime.waiting"]["metadata"]["secret"] == "[redacted]"
    assert forbidden.status_code == 403
    assert missing.status_code == 404

    serialized = str(body)
    assert "sk-agent-status" not in serialized
    assert "router.example.test/private" not in serialized
    assert "runtime.example.test/private" not in serialized
    assert "Bearer task-control" not in serialized
    assert "Bearer step-token" not in serialized
    assert "Bearer runtime" not in serialized
    assert "runtime-output-token" not in serialized
    assert "sk-run-status" not in serialized
    assert "message-token" not in serialized
    assert "event-secret" not in serialized


def test_task_event_stream_returns_redacted_snapshot() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Streaming task",
        status=TaskStatus.COMPLETED.value,
    )
    session.add(task)
    session.flush()
    session.add(
        TaskMessage(
            workspace_id=workspace.id,
            task_id=task.id,
            message_type="step.completed",
            sequence=1,
            body="Done",
            payload={"authorization": "Bearer hidden"},
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/events/stream?once=true",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: task.snapshot" in response.text
    assert "[redacted]" in response.text
    assert "Bearer hidden" not in response.text


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
    assert body["summary"]["handoff_counts"] == {
        "no_downstream": 2,
        "ready_for_downstream": 1,
        "source_incomplete": 1,
    }
    assert body["summary"]["ready_handoffs"] == 1
    assert body["summary"]["blocked_handoffs"] == 0
    by_package = {step["work_package_id"]: step for step in body["steps"]}
    assert by_package["design"]["blocked_reasons"] == []
    assert by_package["design"]["handoff"]["status"] == "ready_for_downstream"
    assert by_package["design"]["handoff"]["requires_handoff"] is True
    assert by_package["design"]["handoff"]["downstream_step_ids"] == [str(runnable_step.id)]
    assert by_package["design"]["handoff"]["runnable_downstream_step_ids"] == [
        str(runnable_step.id)
    ]
    assert by_package["design"]["handoff"]["recommended_actions"] == [
        "schedule_downstream_steps"
    ]
    assert by_package["build"]["runnable"] is True
    assert by_package["build"]["dependency_state"]["satisfied"] is True
    assert by_package["build"]["handoff"]["status"] == "source_incomplete"
    assert by_package["build"]["handoff"]["upstream_step_ids"] == [str(completed_step.id)]
    assert by_package["build"]["handoff"]["downstream_step_ids"] == [str(blocked_step.id)]
    assert by_package["build"]["handoff"]["blocked_downstream_step_ids"] == [
        str(blocked_step.id)
    ]
    assert by_package["review"]["assignment_status"] == "inactive_agent"
    assert by_package["review"]["dependency_state"]["satisfied"] is False
    assert by_package["review"]["handoff"]["status"] == "no_downstream"
    assert by_package["review"]["handoff"]["upstream_step_ids"] == [str(runnable_step.id)]
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
    assert by_package["release"]["handoff"]["status"] == "no_downstream"
    assert "hidden-token" not in str(body)
    assert "sk-run" not in str(body)
    assert "sk-hidden" not in str(body)
    assert foreign_response.status_code == 404
    assert missing_response.status_code == 404


def test_task_handoff_queue_lists_attention_items_and_preserves_scope() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-handoff@example.com",
        slug="other-handoff",
    )
    active_agent = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
    )
    inactive_agent = AgentProfile(
        workspace_id=workspace.id,
        name="Reviewer",
        role="reviewer",
        status="inactive",
        model_settings={"api_key": "sk-reviewer"},
    )
    session.add_all([active_agent, inactive_agent])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Delivery Team",
        team_type="software",
    )
    foreign_team = AgentTeam(
        workspace_id=other_workspace.id,
        name="Foreign Team",
        team_type="software",
    )
    session.add_all([team, foreign_team])
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Build handoff queue",
        status="running",
        priority=7,
        domain_type="software",
    )
    foreign_task = Task(
        workspace_id=other_workspace.id,
        created_by_user_id=other_owner.id,
        agent_team_id=foreign_team.id,
        title="Foreign task",
        status="running",
        priority=10,
    )
    session.add_all([task, foreign_task])
    session.flush()
    design_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=active_agent.id,
        work_package_id="design",
        title="Design",
        status="completed",
        order_index=10,
        result_summary="Design complete",
    )
    api_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=active_agent.id,
        work_package_id="api",
        title="API",
        status="completed",
        order_index=20,
        result_summary="API complete",
    )
    final_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=active_agent.id,
        work_package_id="final",
        title="Final summary",
        status="completed",
        order_index=50,
        result_summary="Ready for review",
    )
    foreign_step = TaskStep(
        workspace_id=other_workspace.id,
        task_id=foreign_task.id,
        work_package_id="foreign",
        title="Foreign",
        status="completed",
    )
    session.add_all([design_step, api_step, final_step, foreign_step])
    session.flush()
    build_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=active_agent.id,
        work_package_id="build",
        title="Build",
        status="queued",
        order_index=30,
        dependencies={"after_step_ids": [str(design_step.id)]},
    )
    review_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=inactive_agent.id,
        work_package_id="review",
        title="Review",
        status="queued",
        order_index=40,
        dependencies={
            "after_step_ids": [str(api_step.id)],
            "blocked_reason": "worker_unavailable",
            "token": "hidden-token",
        },
    )
    session.add_all([build_step, review_step])
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/handoff-queue?team_id={team.id}",
        headers=_headers(owner.id),
    )
    ready_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/handoff-queue"
        "?handoff_status=ready_for_downstream",
        headers=_headers(owner.id),
    )
    include_terminal_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/handoff-queue?include_terminal=true",
        headers=_headers(owner.id),
    )
    foreign_team_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/handoff-queue?team_id={foreign_team.id}",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["team_id"] == str(team.id)
    assert body["total"] == 3
    assert body["summary"]["handoff_status_counts"] == {
        "downstream_blocked": 1,
        "final_delivery_ready": 1,
        "ready_for_downstream": 1,
    }
    assert body["summary"]["recommended_actions"] == {
        "inspect_blocked_downstream": 1,
        "request_manager_review": 1,
        "schedule_downstream_steps": 1,
    }
    team_action_plan = {
        item["action"]: item for item in body["summary"]["team_operator_action_plan"]
    }
    assert team_action_plan["schedule_downstream_steps"] == {
        "team_id": str(team.id),
        "action": "schedule_downstream_steps",
        "automation": "team_operator_action",
        "api_route": (
            "POST /api/v1/workspaces/{workspace_id}/"
            "teams/{team_id}/operator-actions"
        ),
        "task_ids": [str(task.id)],
        "task_step_ids": [str(design_step.id)],
        "count": 1,
        "reason": "handoff_queue",
        "payload_template": {
            "action": "schedule_downstream_steps",
            "task_ids": [str(task.id)],
            "task_step_ids": [str(design_step.id)],
            "reason": "handoff_queue",
            "metadata": {"source": "handoff_queue"},
        },
    }
    assert team_action_plan["request_manager_review"]["payload_template"] == {
        "action": "request_manager_review",
        "task_ids": [str(task.id)],
        "task_step_ids": [],
        "reason": "handoff_queue",
        "metadata": {"source": "handoff_queue"},
    }
    by_package = {item["work_package_id"]: item for item in body["items"]}
    assert by_package["design"]["handoff_status"] == "ready_for_downstream"
    assert by_package["design"]["runnable_downstream_step_ids"] == [str(build_step.id)]
    assert by_package["api"]["handoff_status"] == "downstream_blocked"
    assert by_package["api"]["blocked_downstream_step_ids"] == [str(review_step.id)]
    assert by_package["final"]["handoff_status"] == "final_delivery_ready"
    assert ready_response.status_code == 200
    assert ready_response.json()["total"] == 1
    assert include_terminal_response.status_code == 200
    assert include_terminal_response.json()["total"] == 5
    assert foreign_team_response.status_code == 404
    serialized = str(body)
    assert "hidden-token" not in serialized
    assert "sk-reviewer" not in serialized
    assert "Foreign task" not in serialized


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
    delivery_team = AgentTeam(
        workspace_id=workspace.id,
        name="Delivery Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
    )
    support_team = AgentTeam(
        workspace_id=workspace.id,
        name="Support Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
    )
    foreign_team = AgentTeam(
        workspace_id=other_workspace.id,
        name="Foreign Team",
        team_type="software",
    )
    session.add_all([delivery_team, support_team, foreign_team])
    session.flush()
    needs_follow_up = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=delivery_team.id,
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
        agent_team_id=support_team.id,
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
        agent_team_id=foreign_team.id,
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
    delivery_filtered = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/manager-queue"
        f"?include_healthy=true&team_id={delivery_team.id}",
        headers=_headers(owner.id),
    )
    support_filtered = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/manager-queue"
        f"?include_healthy=true&team_id={support_team.id}",
        headers=_headers(owner.id),
    )
    foreign_team_filtered = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/manager-queue"
        f"?include_healthy=true&team_id={foreign_team.id}",
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
    assert body["summary"]["team_operator_action_plan"] == [
        {
            "team_id": str(delivery_team.id),
            "action": "request_manager_review",
            "automation": "team_operator_action",
            "api_route": (
                "POST /api/v1/workspaces/{workspace_id}/"
                "teams/{team_id}/operator-actions"
            ),
            "task_ids": [str(needs_follow_up.id)],
            "task_step_ids": [],
            "count": 1,
            "reason": "manager_queue",
            "payload_template": {
                "action": "request_manager_review",
                "task_ids": [str(needs_follow_up.id)],
                "task_step_ids": [],
                "reason": "manager_queue",
                "metadata": {"source": "manager_queue"},
            },
        }
    ]
    item = body["items"][0]
    assert item["task_id"] == str(needs_follow_up.id)
    assert item["team_id"] == str(delivery_team.id)
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
    assert delivery_filtered.status_code == 200
    assert delivery_filtered.json()["team_id"] == str(delivery_team.id)
    assert delivery_filtered.json()["total"] == 1
    assert delivery_filtered.json()["items"][0]["task_id"] == str(needs_follow_up.id)
    assert support_filtered.status_code == 200
    assert support_filtered.json()["team_id"] == str(support_team.id)
    assert support_filtered.json()["total"] == 1
    assert support_filtered.json()["items"][0]["task_id"] == str(healthy.id)
    assert foreign_team_filtered.status_code == 404
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
        input={"outline": ["Act 1", "Act 2"], "api_key": "sk-observation-input"},
        generic_state={"word_count": 3200, "headers": {"authorization": "Bearer generic"}},
        domain_state={
            "chapters": [{"title": "Chapter 1", "status": "drafting"}],
            "characters": [{"name": "Lin", "role": "detective"}],
            "token": "domain-hidden-token",
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
    domain_cards = sections["domain"]["cards"]
    assert domain_cards[0]["card_type"] == "manuscript_status"
    assert domain_cards[0]["status"] == "attention"
    assert domain_cards[0]["data"]["outline_item_count"] == 2
    assert domain_cards[0]["data"]["chapter_count"] == 1
    assert domain_cards[0]["data"]["character_count"] == 1
    assert domain_cards[0]["data"]["word_count"] == 3200
    assert domain_cards[0]["data"]["recommended_actions"] == [
        "check_continuity",
        "request_editorial_review",
    ]
    assert domain_cards[1]["card_type"] == "outline"
    assert domain_cards[2]["data"]["value"] == [
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
    forced_body = forced.json()
    assert forced_body["view_type"] == "software"
    forced_domain = {
        section["key"]: section for section in forced_body["sections"]
    }["domain"]
    assert forced_domain["cards"][0]["card_type"] == "delivery_status"
    assert "capture_requirements" in forced_domain["cards"][0]["data"]["recommended_actions"]
    assert unsupported.status_code == 400
    assert foreign.status_code == 404
    serialized = str(body)
    assert "sk-observation-input" not in serialized
    assert "Bearer generic" not in serialized
    assert "domain-hidden-token" not in serialized


def test_task_observation_status_cards_for_specialized_domains() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    aigc_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        domain_type="aigc",
        title="Create launch visuals",
        input={"prompt": "clean product render"},
        generic_state={"model_settings": {"size": "1024x1024"}},
        domain_state={
            "variants": [{"id": "v1"}, {"id": "v2"}],
            "selected_asset": {"id": "v2", "status": "approved"},
            "review_notes": ["Approved for launch"],
        },
    )
    research_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        domain_type="research",
        title="Research competitors",
        domain_state={
            "sources": [{"url": "https://example.test/a"}],
            "claims": [{"text": "Competitor A is faster"}],
            "citations": [{"source": "source-a"}],
            "report_sections": [{"title": "Summary"}],
            "confidence": "medium",
        },
    )
    software_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        domain_type="software",
        title="Build worker dashboard API",
        domain_state={
            "requirements": ["show worker backlog"],
            "design_tasks": ["add endpoint contract"],
            "branches": ["feature/workers"],
            "patches": ["worker-dashboard.patch"],
            "tests": ["test_worker_dashboard"],
            "build_status": "passed",
            "review_comments": [],
        },
    )
    session.add_all([aigc_task, research_task, software_task])
    session.flush()
    session.add_all(
        [
            Artifact(
                workspace_id=workspace.id,
                task_id=aigc_task.id,
                artifact_type="image",
                filename="launch.png",
                content_type="image/png",
                size_bytes=128,
                checksum_sha256="a" * 64,
                storage_key="aigc-launch",
                created_at=datetime.now(UTC),
            ),
            Artifact(
                workspace_id=workspace.id,
                task_id=research_task.id,
                artifact_type="report",
                filename="research.md",
                content_type="text/markdown",
                size_bytes=128,
                checksum_sha256="b" * 64,
                storage_key="research-report",
                created_at=datetime.now(UTC),
            ),
        ]
    )
    session.commit()

    aigc = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{aigc_task.id}/observation",
        headers=_headers(owner.id),
    )
    research = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{research_task.id}/observation",
        headers=_headers(owner.id),
    )
    software = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{software_task.id}/observation",
        headers=_headers(owner.id),
    )

    assert aigc.status_code == 200
    aigc_domain = {section["key"]: section for section in aigc.json()["sections"]}["domain"]
    assert aigc_domain["cards"][0]["card_type"] == "production_status"
    assert aigc_domain["cards"][0]["status"] == "healthy"
    assert aigc_domain["cards"][0]["data"]["variant_count"] == 2
    assert aigc_domain["cards"][0]["data"]["recommended_actions"] == []

    assert research.status_code == 200
    research_domain = {
        section["key"]: section for section in research.json()["sections"]
    }["domain"]
    assert research_domain["cards"][0]["card_type"] == "research_status"
    assert research_domain["cards"][0]["data"]["source_count"] == 1
    assert research_domain["cards"][0]["data"]["recommended_actions"] == []

    assert software.status_code == 200
    software_domain = {
        section["key"]: section for section in software.json()["sections"]
    }["domain"]
    assert software_domain["cards"][0]["card_type"] == "delivery_status"
    assert software_domain["cards"][0]["status"] == "healthy"
    assert software_domain["cards"][0]["data"]["build_status"] == "passed"


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
    claimed_initial_job = queue.dequeue()
    assert claimed_initial_job is not None
    assert claimed_initial_job.resource_id == failed_run.id
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


def test_task_operator_action_schedules_downstream_steps_from_handoff() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
    )
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
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/messages",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "schedule_downstream_steps"
    assert body["task_status"] == "running"
    assert body["changed_step_ids"] == [
        str(blocked_downstream.id),
        str(failed_downstream.id),
    ]
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


def test_task_delivery_review_reports_missing_artifacts_and_redacts_metadata() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session,
        role="owner",
        email="other-delivery-review@example.com",
        slug="other-delivery-review",
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Designer",
        role="designer",
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Review delivery",
        status=TaskStatus.RUNNING.value,
        domain_type="aigc",
        final_output={"summary": "Draft ready", "token": "final-output-token"},
    )
    session.add_all([agent, task])
    session.flush()
    design_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        work_package_id="design",
        title="Design cover",
        status="completed",
        expected_artifacts=["image", "metadata"],
        result_summary="Cover image delivered",
        order_index=1,
    )
    build_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="build",
        title="Build package",
        status="running",
        expected_artifacts=["patch"],
        order_index=2,
    )
    session.add_all([design_step, build_step])
    session.flush()
    session.add_all(
        [
            Artifact(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=design_step.id,
                agent_profile_id=agent.id,
                work_package_id="design",
                version=1,
                review_status="approved",
                artifact_type="image",
                filename="cover.png",
                content_type="image/png",
                size_bytes=128,
                checksum_sha256="1" * 64,
                storage_key="secret-storage-image",
                artifact_metadata={"token": "image-token", "safe": "ok"},
                created_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
            ),
            Artifact(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=build_step.id,
                work_package_id="build",
                version=2,
                review_status="pending",
                artifact_type="patch",
                filename="changes.diff",
                content_type="text/plain",
                size_bytes=256,
                checksum_sha256="2" * 64,
                storage_key="secret-storage-patch",
                artifact_metadata={"headers": {"authorization": "Bearer patch"}},
                created_at=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
            ),
            Artifact(
                workspace_id=workspace.id,
                task_id=task.id,
                version=1,
                review_status="pending",
                artifact_type="brief",
                filename="brief.md",
                content_type="text/markdown",
                size_bytes=64,
                checksum_sha256="3" * 64,
                storage_key="secret-storage-brief",
                artifact_metadata={"api_key": "sk-brief"},
                created_at=datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
            ),
        ]
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/delivery-review",
        headers=_headers(owner.id),
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/delivery-review",
        headers=_headers(other_owner.id),
    )
    missing = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{uuid4()}/delivery-review",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["status"] == "incomplete"
    assert body["final_output"] == {"summary": "Draft ready", "token": "[redacted]"}
    assert body["summary"]["artifact_count"] == 3
    assert body["summary"]["expected_artifact_count"] == 3
    assert body["summary"]["produced_expected_artifact_count"] == 2
    assert body["summary"]["missing_expected_artifact_count"] == 1
    assert body["summary"]["pending_review_artifact_count"] == 2
    assert body["summary"]["approved_artifact_count"] == 1
    assert body["summary"]["artifact_type_counts"] == {
        "brief": 1,
        "image": 1,
        "patch": 1,
    }
    by_package = {step["work_package_id"]: step for step in body["steps"]}
    assert by_package["design"]["missing_expected_artifacts"] == ["metadata"]
    assert by_package["design"]["latest_artifacts"][0]["metadata"]["token"] == "[redacted]"
    assert by_package["design"]["latest_artifacts"][0]["agent"]["name"] == "Designer"
    assert by_package["build"]["missing_expected_artifacts"] == []
    assert by_package["build"]["review_status_counts"] == {"pending": 1}
    assert body["unattached_artifacts"][0]["metadata"]["api_key"] == "[redacted]"
    action_names = {item["action"] for item in body["recommended_actions"]}
    assert {"create_correction", "review_artifacts"} <= action_names
    assert forbidden.status_code == 403
    assert missing.status_code == 404

    serialized = str(body)
    assert "final-output-token" not in serialized
    assert "image-token" not in serialized
    assert "Bearer patch" not in serialized
    assert "sk-brief" not in serialized
    assert "secret-storage-image" not in serialized
    assert "secret-storage-patch" not in serialized
    assert "secret-storage-brief" not in serialized


def test_task_delivery_decision_approves_or_requests_follow_up_and_redacts() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session,
        role="owner",
        email="other-delivery-decision@example.com",
        slug="other-delivery-decision",
    )
    approved_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Approve delivery",
        status=TaskStatus.RUNNING.value,
    )
    follow_up_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Needs delivery changes",
        status=TaskStatus.RUNNING.value,
    )
    session.add_all([approved_task, follow_up_task])
    session.flush()
    session.add_all(
        [
            TaskStep(
                workspace_id=workspace.id,
                task_id=approved_task.id,
                title="Completed delivery",
                status="completed",
                result_summary="Done",
                order_index=1,
            ),
            TaskStep(
                workspace_id=workspace.id,
                task_id=follow_up_task.id,
                title="Draft delivery",
                status="completed",
                result_summary="Draft",
                order_index=1,
            ),
        ]
    )
    session.commit()

    approved = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{approved_task.id}/delivery-decision",
        headers=_headers(owner.id),
        json={
            "action": "approve",
            "summary": "Approved for release.",
            "metadata": {"api_key": "sk-delivery-approve"},
        },
    )
    forbidden = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{approved_task.id}/delivery-decision",
        headers=_headers(other_owner.id),
        json={"action": "approve", "summary": "Nope"},
    )
    missing = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{uuid4()}/delivery-decision",
        headers=_headers(owner.id),
        json={"action": "approve", "summary": "Missing"},
    )
    invalid_follow_up = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{follow_up_task.id}/delivery-decision",
        headers=_headers(owner.id),
        json={"action": "request_changes", "summary": "Needs changes"},
    )
    changes = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{follow_up_task.id}/delivery-decision",
        headers=_headers(owner.id),
        json={
            "action": "request_changes",
            "summary": "Needs stronger validation.",
            "instruction": "Add validation tests before final acceptance.",
            "correction_mode": "add_missing_work",
            "target_type": "task",
            "metadata": {"authorization": "Bearer delivery-change"},
        },
    )

    assert approved.status_code == 200
    approved_body = approved.json()
    assert approved_body["decision"] == "approved"
    assert approved_body["status"] == "approved"
    assert approved_body["task_status"] == TaskStatus.COMPLETED.value
    assert approved_body["details"]["finalization"] == {
        "status": "finalized",
        "reason": "ready",
    }
    assert approved_body["final_output"]["decision"] == "approved"
    assert approved_body["final_output"]["source"] == "delivery_decision"
    assert forbidden.status_code == 403
    assert missing.status_code == 404
    assert invalid_follow_up.status_code == 409
    assert changes.status_code == 200
    changes_body = changes.json()
    assert changes_body["decision"] == "request_revision"
    assert changes_body["status"] == "follow_up_created"
    assert changes_body["task_status"] == TaskStatus.BLOCKED.value
    assert changes_body["created_step_id"] is not None
    assert changes_body["details"]["correction_mode"] == "add_missing_work"

    session.expire_all()
    stored_approved = session.get(Task, approved_task.id)
    stored_follow_up = session.get(Task, follow_up_task.id)
    created_step = session.get(TaskStep, UUID(changes_body["created_step_id"]))
    acceptance_messages = session.scalars(
        select(TaskMessage)
        .where(TaskMessage.task_id.in_([approved_task.id, follow_up_task.id]))
        .order_by(TaskMessage.sequence.asc())
    ).all()
    audit_actions = {
        event.action
        for event in session.query(AuditEvent)
        .filter(AuditEvent.workspace_id == workspace.id)
        .all()
    }
    assert stored_approved is not None
    assert stored_approved.status == TaskStatus.COMPLETED.value
    assert stored_approved.final_output["summary"] == "Approved for release."
    assert stored_follow_up is not None
    assert stored_follow_up.status == TaskStatus.BLOCKED.value
    assert created_step is not None
    assert created_step.dependencies["correction"]["metadata"]["decision"] == "request_changes"
    assert [message.message_type for message in acceptance_messages] == [
        "pm.acceptance_decision",
        "pm.acceptance_decision",
        "task.correction.created",
    ]
    assert acceptance_messages[0].payload["decision"] == "approved"
    assert acceptance_messages[1].payload["decision"] == "request_revision"
    assert {
        "task.delivery.approved",
        "task.delivery.request_changes",
        "task.correction.created",
    } <= audit_actions

    serialized = str(approved_body) + str(changes_body)
    assert "sk-delivery-approve" not in serialized
    assert "Bearer delivery-change" not in serialized


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


def test_workspace_health_reports_operational_risks_and_redacts() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session,
        role="owner",
        email="other-workspace-health@example.com",
        slug="other-workspace-health",
    )
    team = AgentTeam(workspace_id=workspace.id, name="Health Team", team_type="software")
    blocked_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Blocked delivery",
        status="blocked",
        input={"api_key": "sk-health-task"},
    )
    review_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Review delivery",
        status="running",
    )
    completed_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Done delivery",
        status="completed",
        final_output={"summary": "Done", "token": "health-final-token"},
        completed_at=datetime.now(UTC),
    )
    session.add_all([team, blocked_task, review_task, completed_task])
    session.flush()
    blocked_step = TaskStep(
        workspace_id=workspace.id,
        task_id=blocked_task.id,
        title="Missing patch",
        status="blocked",
        expected_artifacts=["patch"],
        dependencies={"blocked_reason": "task_paused", "token": "health-step-token"},
        order_index=1,
    )
    review_step = TaskStep(
        workspace_id=workspace.id,
        task_id=review_task.id,
        title="Review image",
        status="completed",
        expected_artifacts=["image"],
        order_index=1,
    )
    session.add_all([blocked_step, review_step])
    session.flush()
    session.add_all(
        [
            AgentRun(
                workspace_id=workspace.id,
                task_id=review_task.id,
                task_step_id=review_step.id,
                status=RunStatus.WAITING_RUNTIME.value,
                input={"authorization": "Bearer health-run"},
            ),
            Artifact(
                workspace_id=workspace.id,
                task_id=review_task.id,
                task_step_id=review_step.id,
                artifact_type="image",
                filename="review.png",
                content_type="image/png",
                review_status="pending",
                version=1,
                size_bytes=42,
                checksum_sha256="4" * 64,
                storage_key="secret-health-storage",
                artifact_metadata={"api_key": "sk-health-artifact"},
                created_at=datetime.now(UTC),
            ),
            TaskMessage(
                workspace_id=workspace.id,
                task_id=blocked_task.id,
                message_type="task.control.pause",
                sequence=1,
                body="Pause with secret body",
                payload={"token": "health-control-token"},
            ),
        ]
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/health",
        headers=_headers(owner.id),
    )
    snapshot = client.post(
        f"/api/v1/workspaces/{workspace.id}/health/snapshots",
        headers=_headers(owner.id),
    )
    snapshots = client.get(
        f"/api/v1/workspaces/{workspace.id}/health/snapshots",
        headers=_headers(owner.id),
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/health",
        headers=_headers(other_owner.id),
    )
    forbidden_snapshot = client.post(
        f"/api/v1/workspaces/{workspace.id}/health/snapshots",
        headers=_headers(other_owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["status"] == "degraded"
    assert body["score"] == 56
    assert body["summary"]["team_count"] == 1
    assert body["summary"]["task_count"] == 3
    assert body["summary"]["active_task_count"] == 2
    assert body["summary"]["blocked_task_count"] == 1
    assert body["summary"]["completed_task_count"] == 1
    assert body["summary"]["active_run_count"] == 1
    assert body["summary"]["waiting_runtime_run_count"] == 1
    assert body["summary"]["missing_expected_artifact_count"] == 1
    assert body["summary"]["pending_review_artifact_count"] == 1
    assert body["summary"]["final_output_task_count"] == 1
    assert body["summary"]["control_message_count"] == 1
    risks = {item["code"]: item for item in body["risk_items"]}
    assert risks["blocked_tasks"]["severity"] == "high"
    assert risks["waiting_runtime_runs"]["recommended_action"] == "inspect_runtime_capacity"
    assert risks["missing_expected_artifacts"]["count"] == 1
    assert risks["pending_artifact_review"]["severity"] == "medium"
    assert risks["recent_control_activity"]["severity"] == "low"
    assert body["recommended_actions"] == [
        "inspect_project_dashboard",
        "inspect_runtime_capacity",
        "create_corrections",
        "review_artifacts",
        "inspect_control_diagnostics",
    ]
    assert body["trend_basis"]["mode"] == "snapshot"
    assert snapshot.status_code == 201
    snapshot_body = snapshot.json()
    assert snapshot_body["workspace_id"] == str(workspace.id)
    assert snapshot_body["status"] == "degraded"
    assert snapshot_body["score"] == 56
    assert snapshot_body["trend_basis"]["mode"] == "persisted_snapshot"
    assert snapshots.status_code == 200
    assert snapshots.json()["total"] == 1
    assert snapshots.json()["items"][0]["id"] == snapshot_body["id"]
    assert snapshots.json()["items"][0]["summary"]["task_count"] == 3
    assert forbidden.status_code == 403
    assert forbidden_snapshot.status_code == 403

    serialized = str(body) + str(snapshot_body) + str(snapshots.json())
    assert "sk-health-task" not in serialized
    assert "health-final-token" not in serialized
    assert "health-step-token" not in serialized
    assert "Bearer health-run" not in serialized
    assert "secret-health-storage" not in serialized
    assert "sk-health-artifact" not in serialized
    assert "Pause with secret body" not in serialized
    assert "health-control-token" not in serialized


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


def test_workspace_execution_slot_summary_reports_active_capacity() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session,
        role="owner",
        email="other-execution-slots@example.com",
        slug="other-execution-slots",
    )

    client.put(
        f"/api/v1/workspaces/{workspace.id}/quotas",
        headers=_headers(owner.id),
        json={
            "quotas": [
                {"quota_key": "active_runs", "limit_value": 4, "unit": "count"},
                {"quota_key": "docker_runtimes", "limit_value": 2, "unit": "count"},
                {"quota_key": "self_hosted_jobs", "limit_value": 2, "unit": "count"},
            ]
        },
    )

    service = WorkspaceQuotaService(session)
    first_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="First run",
        status="running",
    )
    second_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Second run",
        status="running",
    )
    session.add_all([first_task, second_task])
    session.flush()

    first_run = AgentRun(
        workspace_id=workspace.id,
        task_id=first_task.id,
        status=RunStatus.RUNNING.value,
        input={},
    )
    second_run = AgentRun(
        workspace_id=workspace.id,
        task_id=second_task.id,
        status=RunStatus.RUNNING.value,
        input={},
    )
    session.add_all([first_run, second_run])
    session.flush()

    first_result = service.reserve(
        workspace_id=workspace.id,
        task_id=first_task.id,
        task_step_id=None,
        reservation_key="run:first",
        resource_usage={"active_runs": 1, "docker_runtimes": 1},
    )
    second_result = service.reserve(
        workspace_id=workspace.id,
        task_id=second_task.id,
        task_step_id=None,
        reservation_key="run:second",
        resource_usage={"active_runs": 1, "self_hosted_jobs": 1},
    )
    assert first_result.reservation is not None
    assert second_result.reservation is not None
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/quotas/execution-summary",
        headers=_headers(owner.id),
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/quotas/execution-summary",
        headers=_headers(other_owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["active_reservation_count"] == 2
    assert body["reservation_usage"] == {
        "active_runs": 2,
        "docker_runtimes": 1,
        "self_hosted_jobs": 1,
    }
    assert body["over_reserved_quota_keys"] == []
    quotas = {item["quota_key"]: item for item in body["quotas"]}
    assert quotas["active_runs"]["reserved_value"] == 2
    assert quotas["active_runs"]["available_value"] == 2
    assert quotas["docker_runtimes"]["reserved_value"] == 1
    assert quotas["self_hosted_jobs"]["reserved_value"] == 1
    assert [item["reservation_key"] for item in body["active_reservations"]] == [
        "run:first",
        "run:second",
    ]
    assert forbidden.status_code == 403


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
    worker_queue = queue or RedisQueue(
        redis=redis,
        keys=RedisKeyBuilder(app.state.settings.redis_key_prefix),
        queue_name=app.state.settings.worker_queue_name,
        blocking_timeout_seconds=0,
    )
    app.dependency_overrides[get_worker_queue] = lambda: worker_queue
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
