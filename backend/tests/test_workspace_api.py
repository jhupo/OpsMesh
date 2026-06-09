import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agent_messages.models import AgentMessage, AgentMessageThread
from backend.app.agent_runtime.sessions import PersistentAgentSession, PersistentAgentSessionItem
from backend.app.agents.models import AgentProfile
from backend.app.artifacts.models import Artifact
from backend.app.audit.models import AuditEvent
from backend.app.auth.permissions import ROLE_PERMISSIONS, WorkspaceAction, WorkspaceRole
from backend.app.core.config import Settings, get_settings
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.model_providers import service as model_provider_service_module
from backend.app.model_providers.health import (
    ModelProviderHealthCheck,
    ModelProviderHealthCheckResult,
)
from backend.app.model_providers.service import ModelProviderCredentialService
from backend.app.operations.timeline import TeamRuntimeTimelineService, TimelineFilters
from backend.app.planning.models import TaskPlanningAttempt
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus
from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCommandResult,
    RuntimeCreateRequest,
)
from backend.app.runtime_manager.dependencies import get_docker_runtime_client
from backend.app.runtime_spaces.models import RuntimeSpace
from backend.app.runtimes.models import RuntimeTemplate, WorkspaceRuntime
from backend.app.scheduled_jobs.models import WorkspaceScheduledJob
from backend.app.secrets.service import SecretEncryptionService
from backend.app.security.models import SecurityEvent
from backend.app.tasks.correction_diagnostics import TaskCorrectionDiagnosticsService
from backend.app.tasks.event_outbox import TaskEventOutboxPublisher
from backend.app.tasks.events import RedisTaskEventBus
from backend.app.tasks.execution_diagnostics import TaskExecutionDiagnosticsService
from backend.app.tasks.message_append import (
    TASK_MESSAGE_CREATED_EVENT_TYPE,
    TaskMessageAppendService,
)
from backend.app.tasks.models import Task, TaskEventOutbox, TaskMessage, TaskStep
from backend.app.tasks.observation import TaskObservationService
from backend.app.tasks.status import TaskStatus
from backend.app.tasks.timeline import TaskTimelineService
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.operations_console import TeamOperationsConsoleService
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.handlers import WorkerJobHandler
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue, consume_once
from backend.app.workspaces.models import (
    Workspace,
    WorkspaceInvite,
    WorkspaceMember,
    WorkspaceQuota,
)
from backend.app.workspaces.quotas import WorkspaceQuotaService

TOKEN = "test-token"


class FakeDockerClient(DockerRuntimeClient):
    def __init__(self) -> None:
        self.created_requests: list[RuntimeCreateRequest] = []
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.removed: list[str] = []
        self.executed: list[tuple[str, list[str], int]] = []

    def create_container(self, request: RuntimeCreateRequest) -> str:
        self.created_requests.append(request)
        return f"container-{len(self.created_requests)}"

    def start_container(self, container_id: str) -> None:
        self.started.append(container_id)

    def stop_container(self, container_id: str) -> None:
        self.stopped.append(container_id)

    def remove_container(self, container_id: str) -> None:
        self.removed.append(container_id)

    def remove_volume(self, volume_name: str) -> None:
        return None

    def exec_command(
        self,
        container_id: str,
        command: list[str],
        timeout_seconds: int,
    ) -> RuntimeCommandResult:
        self.executed.append((container_id, command, timeout_seconds))
        return RuntimeCommandResult(exit_code=0, stdout="ok\n", stderr="")


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
    assert intervention_plan["request_manager_review"]["automation"] == "team_operator_action"
    assert intervention_plan["request_manager_review"]["api_route"] == (
        "POST /api/v1/workspaces/{workspace_id}/teams/{team_id}/operator-actions"
    )
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
    replacement_developer = AgentProfile(
        workspace_id=workspace.id,
        name="Replacement Developer",
        role="developer",
    )
    observer = AgentProfile(
        workspace_id=workspace.id,
        name="Observer",
        role="qa",
        model="gpt-4.1-mini",
    )
    session.add_all([manager, developer, replacement_developer, observer])
    session.flush()
    unhealthy_credential = ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Blocked Provider",
        provider="openai-compatible",
        api_key="sk-command-provider-blocked",
        default_model="gpt-4.1-mini",
        base_url="https://provider.example.test/v1",
        is_default=False,
        budget_metadata={"model_api": "chat-completions"},
    )
    unhealthy_credential.health_status = "unhealthy"
    unhealthy_credential.failure_count = 3
    unhealthy_credential.last_failure_code = "permission_denied"
    unhealthy_credential.last_failure_message = "Provider disabled."
    developer.model = "workspace-default"
    developer.model_provider_credential_id = unhealthy_credential.id
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
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=replacement_developer.id,
                team_role="developer",
                max_concurrent_tasks=2,
                order_index=3,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=observer.id,
                team_role="qa",
                max_concurrent_tasks=1,
                accepts_tasks=False,
                order_index=4,
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
    blocked_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=developer.id,
        work_package_id="fix",
        required_role="developer",
        title="Fix command center blocker",
        status="blocked",
        order_index=35,
        dependencies={"blocked_reason": "worker_unavailable", "after_step_ids": []},
    )
    session.add(blocked_step)
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
    assert body["summary"]["runtime_status"] == "stopped"
    assert body["summary"]["runtime_ready"] is False
    assert body["summary"]["provider_readiness"]["status"] == "blocked"
    assert body["summary"]["provider_readiness"]["member_count"] == 4
    assert body["summary"]["provider_readiness"]["runtime_participant_count"] == 3
    assert body["summary"]["provider_readiness"]["blocked_member_count"] == 1
    assert body["summary"]["provider_readiness"]["degraded_member_count"] == 3
    assert body["summary"]["provider_readiness"]["runtime_blocked_member_count"] == 1
    assert body["summary"]["provider_readiness"]["runtime_degraded_member_count"] == 2
    assert body["summary"]["provider_readiness"]["blocking_reasons"] == {
        "model_provider_unhealthy": 1
    }
    assert body["summary"]["provider_readiness"]["warning_reasons"] == {
        "model_provider_credential_not_configured": 3
    }
    assert body["runtime"]["status"] == "stopped"
    assert body["runtime"]["ready"] is False
    assert body["runtime"]["provider_readiness"]["status"] == "blocked"
    provider_readiness = body["provider_readiness"]
    assert provider_readiness["status"] == "blocked"
    assert provider_readiness["blocked_member_count"] == 1
    manager_readiness = next(
        item
        for item in provider_readiness["members"]
        if item["agent_profile_id"] == str(manager.id)
    )
    developer_readiness = next(
        item
        for item in provider_readiness["members"]
        if item["agent_profile_id"] == str(developer.id)
    )
    observer_readiness = next(
        item
        for item in provider_readiness["members"]
        if item["agent_profile_id"] == str(observer.id)
    )
    assert manager_readiness["model"] == "gpt-4.1"
    assert manager_readiness["model_capability"]["provider"] == "openai"
    assert manager_readiness["model_capability"]["supports_tools"] is True
    assert manager_readiness["model_capability"]["supports_json_mode"] is True
    assert developer_readiness["readiness_status"] == "blocked"
    assert developer_readiness["provider"] == "openai-compatible"
    assert developer_readiness["credential_reference"] == (
        f"model_provider_credentials:{unhealthy_credential.id}"
    )
    assert developer_readiness["model"] == "gpt-4.1-mini"
    assert developer_readiness["model_api"] == "chat_completions"
    assert developer_readiness["model_apis"] == ["responses", "chat_completions"]
    assert developer_readiness["default_model_api"] is None
    assert developer_readiness["model_capability"] == {
        "provider": "openai-compatible",
        "model": "*",
        "display_name": "OpenAI-compatible model",
        "capabilities": ["tools", "json_mode", "streaming"],
        "supports_tools": True,
        "supports_vision": False,
        "supports_json_mode": True,
        "supports_streaming": True,
        "context_window_tokens": None,
        "notes": "Actual support depends on the upstream gateway and selected model.",
    }
    assert developer_readiness["credential_health_status"] == "unhealthy"
    assert developer_readiness["budget_exhausted"] is False
    assert developer_readiness["reasons"] == ["model_provider_unhealthy"]
    assert observer_readiness["readiness_status"] == "degraded"
    assert observer_readiness["runtime_participant"] is False
    assert observer_readiness["runtime_degraded"] is False
    assert observer_readiness["warnings"] == ["model_provider_credential_not_configured"]
    assert observer_readiness["model_capability"]["provider"] == "openai"
    assert observer_readiness["model_capability"]["model"] == "gpt-4.1-mini"
    assert body["queues"]["handoff"]["team_id"] == str(team.id)
    assert body["queues"]["manager"]["team_id"] == str(team.id)
    sources = {item["source"] for item in body["action_plan"]}
    assert {
        "provider_readiness",
        "team_runtime",
        "execution_overview",
        "handoff_queue",
        "manager_queue",
    } <= sources
    provider_actions = [
        item for item in body["action_plan"] if item["source"] == "provider_readiness"
    ]
    assert provider_actions[0]["action"] == "review_model_provider"
    assert provider_actions[0]["priority"] == 100
    assert provider_actions[0]["agent_profile_id"] == str(developer.id)
    assert provider_actions[0]["credential_id"] == str(unhealthy_credential.id)
    assert provider_actions[0]["credential_reference"] == (
        f"model_provider_credentials:{unhealthy_credential.id}"
    )
    assert provider_actions[0]["model_api"] == "chat_completions"
    assert provider_actions[0]["model_apis"] == ["responses", "chat_completions"]
    assert provider_actions[0]["default_model_api"] is None
    assert provider_actions[0]["reasons"] == ["model_provider_unhealthy"]
    assert provider_actions[0]["task_ids"] == []
    assert body["summary"]["action_plan_source_counts"]["team_runtime"] == 1
    assert body["summary"]["action_plan_source_counts"]["provider_readiness"] == 4
    assert body["summary"]["action_plan_source_counts"]["execution_overview"] >= 1
    assert body["summary"]["action_plan_source_counts"]["handoff_queue"] >= 1
    assert body["summary"]["action_plan_source_counts"]["manager_queue"] == 1
    overview_actions = [
        item for item in body["action_plan"] if item["source"] == "execution_overview"
    ]
    assert any(item["automation"] == "team_operator_action" for item in overview_actions)
    reassign_actions = [
        item for item in overview_actions if item["action"] == "reassign_step"
    ]
    assert len(reassign_actions) == 1
    assert reassign_actions[0]["task_step_ids"] == [str(blocked_step.id)]
    assert reassign_actions[0]["agent_profile_id"] == str(replacement_developer.id)
    assert reassign_actions[0]["current_agent_profile_id"] == str(developer.id)
    assert reassign_actions[0]["agent_profile_id"] != reassign_actions[0][
        "current_agent_profile_id"
    ]
    assert any(item["action"] == "schedule_downstream_steps" for item in body["action_plan"])
    assert any(item["action"] == "request_manager_review" for item in body["action_plan"])
    assert any(item["action"] == "start_team_runtime" for item in body["action_plan"])
    assert missing.status_code == 404
    assert foreign_team_response.status_code == 404
    assert forbidden.status_code == 403
    serialized = str(body)
    assert "sk-command-manager" not in serialized
    assert "sk-command-task" not in serialized
    assert "sk-command-provider-blocked" not in serialized
    assert "provider.example.test/v1" not in serialized
    assert "hidden-command-token" not in serialized
    assert "Private manager review body." not in serialized
    assert "Foreign command task" not in serialized

    explicit_sources = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/command-center/actions/apply",
        headers=_headers(owner.id),
        json={
            "dry_run": True,
            "sources": [
                "provider_readiness",
                "team_runtime",
                "execution_overview",
                "handoff_queue",
                "manager_queue",
            ],
        },
    )
    assert explicit_sources.status_code == 200
    assert explicit_sources.json()["requested_action_count"] == len(body["action_plan"])

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
    assert dry_run_body["eligible_action_count"] == 4
    assert dry_run_body["skipped_action_count"] >= 1
    assert any(
        item["action"] == "review_model_provider"
        and item["reason"] == "manual_operator_review_required"
        for item in dry_run_body["skipped"]
    )
    assert {item["action"] for item in dry_run_body["results"]} == {
        "request_manager_review",
        "reassign_step",
        "schedule_downstream_steps",
        "start_team_runtime",
    }
    dry_run_reassign = next(
        item for item in dry_run_body["results"] if item["action"] == "reassign_step"
    )
    assert dry_run_reassign["agent_profile_id"] == str(replacement_developer.id)
    assert dry_run_reassign["task_step_ids"] == [str(blocked_step.id)]
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
    assert apply_body["eligible_action_count"] == 4
    assert apply_body["applied_action_count"] == 4
    assert apply_body["scheduled_run_skip_reason"] == "provider_readiness_blocked"
    assert apply_body["scheduled_run_count"] == 0
    applied = {item["action"]: item for item in apply_body["results"]}
    assert applied["start_team_runtime"]["status"] == "applied"
    assert applied["start_team_runtime"]["response"]["status"] == "running"
    assert applied["request_manager_review"]["candidate_count"] >= 2
    assert applied["reassign_step"]["agent_profile_id"] == str(replacement_developer.id)
    assert applied["schedule_downstream_steps"]["candidate_count"] >= 1
    assert apply_body["scheduled_runs"] == []
    assert queue_redis.llen(RedisKeyBuilder("chaincloud").queue("agent_runs")) == 0
    session.expire_all()
    reassigned_step = session.get(TaskStep, blocked_step.id)
    assert reassigned_step is not None
    assert reassigned_step.assigned_agent_profile_id == replacement_developer.id
    assert reassigned_step.status == "queued"
    assert reassigned_step.dependencies["after_step_ids"] == []
    assert "blocked_reason" not in reassigned_step.dependencies
    scheduled_runs = session.scalars(
        select(AgentRun).where(
            AgentRun.workspace_id == workspace.id,
            AgentRun.task_id == task.id,
            AgentRun.status == RunStatus.QUEUED.value,
        )
    ).all()
    assert scheduled_runs == []
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
    assert command_center_audit.audit_metadata["scheduled_run_count"] == 0
    assert (
        command_center_audit.audit_metadata["scheduled_run_skip_reason"]
        == "provider_readiness_blocked"
    )
    assert "sk-command-apply" not in str(apply_body)
    manager_review_steps = session.scalars(
        select(TaskStep).where(
            TaskStep.workspace_id == workspace.id,
            TaskStep.task_id == task.id,
            TaskStep.work_package_id.like("manager-summary-operator-%"),
        )
    ).all()
    assert len(manager_review_steps) == 1


def test_team_command_center_apply_reports_scheduler_blocked_reasons() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    workspace.settings = {"scheduler": {"max_runs_to_start_per_tick": 1}}
    session.add(
        WorkspaceQuota(
            workspace_id=workspace.id,
            quota_key="active_runs",
            limit_value=0,
            reserved_value=0,
            unit="count",
        )
    )
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    developer = AgentProfile(workspace_id=workspace.id, name="Developer", role="developer")
    session.add_all([manager, developer])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Blocked Scheduler Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
        default_task_policy={
            "team_runtime": {
                "status": "running",
                "workspace_runtime_id": str(uuid4()),
                "last_heartbeat_at": datetime.now(UTC).isoformat(),
            }
        },
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
                order_index=1,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=developer.id,
                team_role="developer",
                order_index=2,
            ),
        ]
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Quota blocked delivery",
        status="queued",
        priority=9,
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=developer.id,
        work_package_id="build",
        title="Build quota blocked delivery",
        status="queued",
        order_index=10,
    )
    session.add(step)
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/command-center/actions/apply",
        headers=_headers(owner.id),
        json={
            "dry_run": False,
            "enqueue_runs": True,
            "actions": [],
            "metadata": {"api_key": "sk-blocked-scheduler-apply"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "noop"
    assert body["scheduled_run_count"] == 0
    assert body["scheduled_run_skip_reason"] is None
    assert body["scheduled_run_blocked_reasons"] == {
        "workspace_quota_exceeded:active_runs": 1
    }
    assert body["scheduled_run_blocked_steps"] == [
        {
            "task_id": str(task.id),
            "task_step_id": str(step.id),
            "agent_profile_id": str(developer.id),
            "runtime_space_id": None,
            "blocked_reason": "workspace_quota_exceeded:active_runs",
            "blocked_details": {},
        }
    ]
    session.expire_all()
    blocked_step = session.get(TaskStep, step.id)
    assert blocked_step is not None
    assert blocked_step.dependencies["scheduling_status"] == "blocked"
    assert blocked_step.dependencies["blocked_reason"] == (
        "workspace_quota_exceeded:active_runs"
    )
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "team.command_center.actions_applied",
        )
    )
    assert audit is not None
    assert audit.audit_metadata["scheduled_run_blocked_reasons"] == {
        "workspace_quota_exceeded:active_runs": 1
    }
    assert "sk-blocked-scheduler-apply" not in str(body)
    assert "sk-blocked-scheduler-apply" not in str(audit.audit_metadata)


def test_team_command_center_apply_reports_blocked_reasons_with_partial_scheduled_runs() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    workspace.settings = {"scheduler": {"max_runs_to_start_per_tick": 2}}
    session.add(
        WorkspaceQuota(
            workspace_id=workspace.id,
            quota_key="active_runs",
            limit_value=1,
            reserved_value=0,
            unit="count",
        )
    )
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    developer = AgentProfile(workspace_id=workspace.id, name="Developer", role="developer")
    session.add_all([manager, developer])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Partially Blocked Scheduler Team",
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
                order_index=1,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=developer.id,
                team_role="developer",
                order_index=2,
            ),
        ]
    )
    first_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="First delivery",
        status="queued",
        priority=10,
    )
    second_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Second delivery",
        status="queued",
        priority=9,
    )
    session.add_all([first_task, second_task])
    session.flush()
    first_step = TaskStep(
        workspace_id=workspace.id,
        task_id=first_task.id,
        assigned_agent_profile_id=developer.id,
        work_package_id="first-build",
        title="Build first delivery",
        status="queued",
        order_index=10,
    )
    second_step = TaskStep(
        workspace_id=workspace.id,
        task_id=second_task.id,
        assigned_agent_profile_id=developer.id,
        work_package_id="second-build",
        title="Build second delivery",
        status="queued",
        order_index=10,
    )
    session.add_all([first_step, second_step])
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/command-center/actions/apply",
        headers=_headers(owner.id),
        json={
            "dry_run": False,
            "enqueue_runs": True,
            "actions": [],
            "metadata": {"token": "hidden-partial-scheduler-token"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["scheduled_run_count"] == 1
    assert body["scheduled_run_skip_reason"] is None
    assert body["scheduled_runs"][0]["task_step_id"] == str(first_step.id)
    assert body["scheduled_run_blocked_reasons"] == {
        "workspace_quota_exceeded:active_runs": 1
    }
    assert body["scheduled_run_blocked_steps"][0]["task_step_id"] == str(second_step.id)
    assert body["scheduled_run_blocked_steps"][0]["blocked_reason"] == (
        "workspace_quota_exceeded:active_runs"
    )
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "team.command_center.actions_applied",
        )
    )
    assert audit is not None
    assert audit.audit_metadata["scheduled_run_count"] == 1
    assert audit.audit_metadata["scheduled_run_blocked_reasons"] == {
        "workspace_quota_exceeded:active_runs": 1
    }
    assert "hidden-partial-scheduler-token" not in str(body)
    assert "hidden-partial-scheduler-token" not in str(audit.audit_metadata)


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


def test_team_execution_loop_enqueue_queues_job_idempotently() -> None:
    queue_redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(queue_redis, RedisKeyBuilder("chaincloud"), "agent_runs", 0)
    client, session = _client(queue=queue)
    owner, workspace = _seed_workspace(session, role="owner")
    viewer = User(email="loop-viewer@example.com", display_name="Loop Viewer")
    session.add(viewer)
    session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=viewer.id, role="viewer"))
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Async Loop Team",
        team_type="software",
    )
    session.add(team)
    session.commit()

    first = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/execution-loop/enqueue",
        headers=_headers(owner.id),
        json={"priority": 7, "metadata": {"source": "test"}},
    )
    duplicate = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/execution-loop/enqueue",
        headers=_headers(owner.id),
        json={"priority": 7, "metadata": {"source": "test"}},
    )
    forbidden = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/execution-loop/enqueue",
        headers=_headers(viewer.id),
        json={},
    )
    missing = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{uuid4()}/execution-loop/enqueue",
        headers=_headers(owner.id),
        json={},
    )

    assert first.status_code == 200
    assert first.json() == {
        "workspace_id": str(workspace.id),
        "team_id": str(team.id),
        "status": "queued",
        "queued": True,
        "job_type": JobType.TEAM_EXECUTION_LOOP.value,
        "queue_name": "agent_runs",
    }
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "skipped"
    assert duplicate.json()["queued"] is False
    assert forbidden.status_code == 403
    assert missing.status_code == 404

    jobs = queue.peek()
    assert len(jobs) == 1
    assert jobs[0].workspace_id == workspace.id
    assert jobs[0].resource_id == team.id
    assert jobs[0].requested_by_user_id == owner.id
    assert jobs[0].job_type == JobType.TEAM_EXECUTION_LOOP
    assert jobs[0].priority == 7


def test_team_runtime_controls_create_sessions_mailbox_and_workspace_runtime() -> None:
    client, session, docker = _client(include_docker=True)
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session,
        role="owner",
        email="other-runtime@example.com",
        slug="other-runtime",
    )
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    developer = AgentProfile(workspace_id=workspace.id, name="Developer", role="developer")
    template = RuntimeTemplate(
        name="team-runtime-template",
        image="python:3.12-slim",
        default_limits={
            "cpu_count": 1,
            "memory_mb": 512,
            "disk_mb": 1024,
            "timeout_seconds": 60,
        },
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([manager, developer, template])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Runtime Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
    )
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="Runtime Team Container",
        status="running",
        connection_status="online",
        limits={},
        network_policy={},
        capabilities={},
    )
    session.add_all([team, runtime])
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=manager.id,
                team_role="project_manager",
                order_index=1,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=developer.id,
                team_role="developer",
                order_index=2,
            ),
        ]
    )
    session.commit()

    initial = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime",
        headers=_headers(owner.id),
    )
    started = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/start",
        headers=_headers(owner.id),
        json={"reason": "open the company", "metadata": {"api_key": "sk-runtime"}},
    )
    paused = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/pause",
        headers=_headers(owner.id),
        json={"reason": "operator pause"},
    )
    continued = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/continue",
        headers=_headers(owner.id),
        json={"instruction": "continue from the last team state"},
    )
    ensured = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/ensure",
        headers=_headers(owner.id),
        json={
            "template_id": str(template.id),
            "name": "Runtime Team Persistent Container",
            "reason": "ensure durable team container",
            "metadata": {"api_key": "sk-ensure-runtime"},
        },
    )
    ensured_again = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/ensure",
        headers=_headers(owner.id),
        json={"template_id": str(template.id), "reason": "reuse durable team container"},
    )
    bound = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/bind",
        headers=_headers(owner.id),
        json={
            "workspace_runtime_id": str(runtime.id),
            "reason": "bind stable container",
            "metadata": {"token": "hidden-runtime-token"},
        },
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime",
        headers=_headers(other_owner.id),
    )

    assert initial.status_code == 200
    assert initial.json()["status"] == "stopped"
    assert initial.json()["runtime_health"] == "stopped"
    assert initial.json()["last_iteration"] is None
    assert initial.json()["last_message_at"] is None
    assert initial.json()["thread_id"] is None
    assert initial.json()["team_session_id"] is None
    assert initial.json()["team_session_key"] is None
    assert initial.json()["member_session_count"] == 0
    assert started.status_code == 200
    assert started.json()["status"] == "running"
    assert started.json()["runtime_health"] == "starting"
    assert started.json()["thread_id"] is not None
    assert started.json()["team_session_id"] is not None
    assert started.json()["team_session_key"] is not None
    assert started.json()["member_session_count"] == 2
    assert started.json()["last_message_at"] is not None
    assert "sk-runtime" not in str(started.json())
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert paused.json()["runtime_health"] == "paused"
    assert continued.status_code == 200
    assert continued.json()["status"] == "running"
    assert continued.json()["runtime_health"] == "starting"
    assert ensured.status_code == 200
    assert ensured.json()["status"] == "running"
    assert ensured.json()["runtime_status"] == "running"
    ensured_runtime_id = ensured.json()["workspace_runtime_id"]
    assert ensured_runtime_id != str(runtime.id)
    assert "sk-ensure-runtime" not in str(ensured.json())
    assert ensured_again.status_code == 200
    assert ensured_again.json()["workspace_runtime_id"] == ensured_runtime_id
    assert len(docker.created_requests) == 1
    assert docker.created_requests[0].image == "python:3.12-slim"
    assert docker.created_requests[0].name.startswith("chaincloud-")
    assert docker.started == ["container-1"]
    assert bound.status_code == 200
    assert bound.json()["workspace_runtime_id"] == str(runtime.id)
    assert bound.json()["runtime_status"] == "running"
    assert bound.json()["runtime_health"] == "starting"
    assert "hidden-runtime-token" not in str(bound.json())
    assert forbidden.status_code == 403

    session.expire_all()
    runtime_thread = session.get(AgentMessageThread, UUID(started.json()["thread_id"]))
    assert runtime_thread is not None
    assert runtime_thread.agent_team_id == team.id
    messages = session.scalars(
        select(AgentMessage)
        .where(
            AgentMessage.workspace_id == workspace.id,
            AgentMessage.thread_id == runtime_thread.id,
        )
        .order_by(AgentMessage.created_at.asc(), AgentMessage.id.asc())
    ).all()
    assert [message.message_type for message in messages] == [
        "team.runtime.started",
        "team.runtime.paused",
        "team.runtime.continued",
        "team.runtime.ensured",
        "team.runtime.ensured",
        "team.runtime.bound",
    ]
    assert {message.agent_team_id for message in messages} == {team.id}
    session.refresh(runtime)
    assert runtime.capabilities["team_runtime"]["team_id"] == str(team.id)
    assert runtime.capabilities["team_runtime"]["bound_at"]
    ensured_runtime = session.get(WorkspaceRuntime, UUID(ensured_runtime_id))
    assert ensured_runtime is not None
    assert ensured_runtime.capabilities["team_runtime"]["team_id"] == str(team.id)
    assert ensured_runtime.capabilities["team_runtime"]["ensured_at"]
    sessions = session.scalars(
        select(PersistentAgentSession).where(
            PersistentAgentSession.workspace_id == workspace.id,
            PersistentAgentSession.agent_team_id == team.id,
        )
    ).all()
    assert {item.scope_type for item in sessions} == {"team_runtime", "team_agent"}
    assert {
        item.agent_profile_id for item in sessions if item.scope_type == "team_agent"
    } == {manager.id, developer.id}


def test_team_session_controls_manage_runtime_and_member_sessions() -> None:
    client, session, _ = _client(include_docker=True)
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-team-sessions@example.com",
        slug="other-team-sessions",
    )
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    developer = AgentProfile(workspace_id=workspace.id, name="Developer", role="developer")
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Session Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
    )
    other_team = AgentTeam(
        workspace_id=other_workspace.id,
        name="Foreign Session Team",
        team_type="software",
    )
    session.add_all([manager, developer, team, other_team])
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=manager.id,
                team_role="project_manager",
                order_index=1,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=developer.id,
                team_role="developer",
                order_index=2,
            ),
        ]
    )
    session.commit()

    started = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/start",
        headers=_headers(owner.id),
        json={"reason": "start team sessions"},
    )
    assert started.status_code == 200
    team_session_id = UUID(started.json()["team_session_id"])
    team_session = session.get(PersistentAgentSession, team_session_id)
    assert team_session is not None
    for index, content in enumerate(("old", "middle", "recent"), start=1):
        session.add(
            PersistentAgentSessionItem(
                workspace_id=workspace.id,
                persistent_session_id=team_session.id,
                sequence=index,
                item={
                    "role": "assistant",
                    "content": f"{content} session content token sk-team-session-secret",
                },
            )
        )
    session.commit()

    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/sessions",
        headers=_headers(owner.id),
    )
    detail = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/sessions/{team_session.id}",
        headers=_headers(owner.id),
    )
    frozen = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/sessions/{team_session.id}/freeze",
        headers=_headers(owner.id),
    )
    active = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/sessions/{team_session.id}/activate",
        headers=_headers(owner.id),
    )
    compacted = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/sessions/{team_session.id}/compact",
        headers=_headers(owner.id),
        json={"fold_first_n": 2, "keep_recent_m": 1, "summary_role": "developer"},
    )
    cleared = client.delete(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/sessions/{team_session.id}/items",
        headers=_headers(owner.id),
    )
    missing_team_session = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{uuid4()}/sessions/{team_session.id}",
        headers=_headers(owner.id),
    )
    foreign_team_session = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{other_team.id}/sessions/{team_session.id}",
        headers=_headers(owner.id),
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/sessions",
        headers=_headers(other_owner.id),
    )

    assert listed.status_code == 200
    assert listed.json()["total"] == 3
    assert {item["scope_type"] for item in listed.json()["items"]} == {
        "team_runtime",
        "team_agent",
    }
    assert detail.status_code == 200
    assert detail.json()["session"]["scope_type"] == "team_runtime"
    assert detail.json()["session"]["agent_team_id"] == str(team.id)
    assert len(detail.json()["items"]) == 3
    assert "sk-team-session-secret" not in json.dumps(detail.json())
    assert frozen.status_code == 200
    assert frozen.json()["status"] == "frozen"
    assert active.status_code == 200
    assert active.json()["status"] == "active"
    assert compacted.status_code == 200
    assert compacted.json()["folded_item_count"] == 2
    assert compacted.json()["retained_item_count"] == 1
    assert compacted.json()["item_count"] == 2
    assert cleared.status_code == 200
    assert cleared.json()["deleted_item_count"] == 2
    assert missing_team_session.status_code == 404
    assert foreign_team_session.status_code == 404
    assert forbidden.status_code == 403


def test_team_runtime_actions_require_manage_runtime_for_write_only_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session, _ = _client(include_docker=True)
    owner, workspace = _seed_workspace(session, role="owner")
    writer = User(email="runtime-writer@example.com", display_name="Runtime Writer")
    session.add(writer)
    session.flush()
    session.add(WorkspaceMember(workspace=workspace, user=writer, role="operator"))
    template = RuntimeTemplate(
        name="permission-runtime-template",
        image="python:3.12-slim",
        default_limits={
            "cpu_count": 1,
            "memory_mb": 512,
            "disk_mb": 1024,
            "timeout_seconds": 60,
        },
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Permission Runtime Team",
        team_type="software",
    )
    session.add_all([template, team])
    session.commit()
    monkeypatch.setitem(
        ROLE_PERMISSIONS,
        WorkspaceRole.OPERATOR,
        {
            WorkspaceAction.READ,
            WorkspaceAction.WRITE,
            WorkspaceAction.APPROVE,
            WorkspaceAction.OPERATE,
        },
    )

    ensure_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/ensure",
        headers=_headers(writer.id),
        json={},
    )
    bind_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/bind",
        headers=_headers(writer.id),
        json={"workspace_runtime_id": str(uuid4())},
    )
    apply_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/command-center/actions/apply",
        headers=_headers(writer.id),
        json={"dry_run": False, "actions": ["ensure_team_runtime"]},
    )
    dry_run_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/command-center/actions/apply",
        headers=_headers(writer.id),
        json={"dry_run": True, "actions": ["ensure_team_runtime"]},
    )
    owner_ensure = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/ensure",
        headers=_headers(owner.id),
        json={"template_id": str(template.id)},
    )

    assert ensure_response.status_code == 403
    assert bind_response.status_code == 403
    assert apply_response.status_code == 403
    assert dry_run_response.status_code == 200
    assert owner_ensure.status_code == 200
    assert owner_ensure.json()["runtime_status"] == "running"


def test_bound_team_runtime_stop_and_resume_control_workspace_runtime() -> None:
    client, session, docker = _client(include_docker=True)
    owner, workspace = _seed_workspace(session, role="owner")
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Bound Runtime Team",
        team_type="software",
        default_task_policy={"team_runtime": {"status": "running"}},
    )
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="Bound Runtime",
        status="running",
        connection_status="online",
        docker_container_id="bound-container",
        limits={},
        network_policy={},
        capabilities={},
    )
    session.add_all([team, runtime])
    session.commit()

    bound = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/bind",
        headers=_headers(owner.id),
        json={"workspace_runtime_id": str(runtime.id), "reason": "bind running runtime"},
    )
    stopped = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/stop",
        headers=_headers(owner.id),
        json={"reason": "stop the bound runtime"},
    )
    resumed = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/resume",
        headers=_headers(owner.id),
        json={"reason": "resume the bound runtime"},
    )

    assert bound.status_code == 200
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"
    assert stopped.json()["runtime_status"] == "stopped"
    assert stopped.json()["metadata"]["workspace_runtime_control"]["mode"] == "lifecycle"
    assert stopped.json()["metadata"]["workspace_runtime_control"]["action"] == "stop"
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "running"
    assert resumed.json()["runtime_status"] == "running"
    assert resumed.json()["metadata"]["workspace_runtime_control"]["mode"] == "lifecycle"
    assert resumed.json()["metadata"]["workspace_runtime_control"]["action"] == "start"
    assert docker.stopped == ["bound-container"]
    assert docker.started == ["bound-container"]
    session.expire_all()
    stored_runtime = session.get(WorkspaceRuntime, runtime.id)
    assert stored_runtime is not None
    assert stored_runtime.status == "running"
    assert stored_runtime.connection_status == "online"


def test_team_runtime_and_command_center_expose_policy_and_memory_summary() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    _, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-memory-owner@example.com",
        slug="other-memory-owner",
    )
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="PM",
        role="project_manager",
        model="gpt-4.1",
        model_settings={"temperature": 0.2, "api_key": "sk-manager-model-secret"},
        tool_policy={
            "allowed_tools": ["planner"],
            "headers": {"authorization": "Bearer manager-tool-secret"},
        },
        runtime_policy={
            "runtime": "docker",
            "env": {"OPENAI_API_KEY": "sk-manager-runtime-secret"},
        },
        approval_policy={"requires_approval": True, "token": "manager-approval-token"},
    )
    developer = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
        model="gpt-4.1-mini",
        tool_policy={"allowed_tools": ["code_search"]},
        runtime_policy={"cpu": 2},
        approval_policy={"requires_approval": False},
    )
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Team Runtime Space",
        scope="team",
        policy={"max_active_runs": 4, "api_key": "sk-runtime-space-secret"},
        network_policy={"egress": "restricted", "token": "runtime-network-token"},
        storage_policy={"persistent": True},
        cleanup_policy={"ttl_hours": 24},
    )
    session.add_all([manager, developer, runtime_space])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Memory Team",
        team_type="software",
        description="Runs the company operating cadence.",
        manager_agent_profile_id=manager.id,
        runtime_space_id=runtime_space.id,
        coordination_rules={"daily_sync": "async", "handoff": "manager_review"},
        default_task_policy={"priority": 4, "approval_required": True},
    )
    other_team = AgentTeam(
        workspace_id=workspace.id,
        name="Other Memory Team",
        team_type="support",
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
                department="Operations",
                max_concurrent_tasks=2,
                order_index=1,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=developer.id,
                team_role="developer",
                department="Engineering",
                max_concurrent_tasks=3,
                order_index=2,
            ),
            WorkspaceMemoryEntry(
                workspace_id=workspace.id,
                created_by_user_id=owner.id,
                entry_type="operating_note",
                title="Customer escalation rule",
                content="Escalate enterprise renewal blockers before implementation starts.",
                tags=["operating-policy", "renewal"],
                visibility_scope="company",
                importance=7,
            ),
            WorkspaceMemoryEntry(
                workspace_id=workspace.id,
                created_by_user_id=owner.id,
                entry_type="operating_note",
                title="Workspace deployment preference",
                content="Prefer staged deploys for workspace-wide releases.",
                tags=["workspace"],
                visibility_scope="workspace",
                importance=6,
            ),
            WorkspaceMemoryEntry(
                workspace_id=workspace.id,
                created_by_user_id=owner.id,
                entry_type="operating_note",
                title="Shared incident runbook",
                content="Shared scope memories are available to every team in the workspace.",
                tags=["shared"],
                visibility_scope="shared",
                importance=5,
            ),
            WorkspaceMemoryEntry(
                workspace_id=workspace.id,
                created_by_user_id=owner.id,
                source_type="agent_team",
                source_id=str(team.id),
                entry_type="team_memory",
                title="Team release ritual",
                content=(
                    "Memory Team ships behind flags and records rollout owners. "
                    "Do not expose sk-memory-content-secret or token=plain-token-secret."
                ),
                tags=["team-memory", f"team:{team.id}"],
                visibility_scope="team",
                importance=9,
                memory_metadata={
                    "team_id": str(team.id),
                    "api_key": "sk-memory-secret",
                    "note": "Authorization: Bearer memory-metadata-secret",
                },
            ),
            WorkspaceMemoryEntry(
                workspace_id=workspace.id,
                created_by_user_id=owner.id,
                entry_type="team_memory",
                title="Camel case team memory",
                content="Team metadata can arrive with camelCase identifiers.",
                tags=["team-memory"],
                visibility_scope="team",
                importance=8,
                memory_metadata={"agentTeamId": str(team.id)},
            ),
            WorkspaceMemoryEntry(
                workspace_id=workspace.id,
                created_by_user_id=owner.id,
                source_type="agent_team",
                source_id=str(other_team.id),
                entry_type="team_memory",
                title="Other team private memory",
                content="This other team detail must not enter the Memory Team runtime.",
                tags=["team-memory", f"team:{other_team.id}"],
                visibility_scope="team",
                importance=10,
                memory_metadata={"team_id": str(other_team.id)},
            ),
            WorkspaceMemoryEntry(
                workspace_id=other_workspace.id,
                entry_type="operating_note",
                title="Foreign workspace memory",
                content="This foreign workspace detail must not be visible.",
                tags=["foreign"],
                visibility_scope="company",
                importance=10,
            ),
        ]
    )
    session.add_all(
        [
            WorkspaceMemoryEntry(
                workspace_id=workspace.id,
                created_by_user_id=owner.id,
                source_type="agent_team",
                source_id=str(other_team.id),
                entry_type="team_memory",
                title=f"Other team private memory {index}",
                content="This high-priority other team detail must not displace visible memory.",
                tags=["team-memory", f"team:{other_team.id}"],
                visibility_scope="team",
                importance=100 + index,
                memory_metadata={"team_id": str(other_team.id)},
            )
            for index in range(205)
        ]
    )
    session.commit()

    runtime = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime",
        headers=_headers(owner.id),
    )
    command_center = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/command-center",
        headers=_headers(owner.id),
    )

    assert runtime.status_code == 200
    runtime_body = runtime.json()
    assert runtime_body["operating_policy"]["coordination_rules"] == {
        "daily_sync": "async",
        "handoff": "manager_review",
    }
    assert runtime_body["operating_policy"]["default_task_policy"] == {
        "priority": 4,
        "approval_required": True,
    }
    assert runtime_body["operating_policy"]["runtime_space"] == {
        "id": str(runtime_space.id),
        "name": "Team Runtime Space",
        "scope": "team",
        "status": "active",
        "default_runtime_template_id": None,
        "policy": {"max_active_runs": 4, "api_key": "[redacted]"},
        "network_policy": {"egress": "restricted", "token": "[redacted]"},
        "storage_policy": {"persistent": True},
        "cleanup_policy": {"ttl_hours": 24},
    }
    assert runtime_body["operating_policy"]["staffing"]["active_member_count"] == 2
    assert runtime_body["operating_policy"]["staffing"]["max_concurrent_tasks"] == 5
    assert runtime_body["operating_policy"]["staffing"]["roles"] == [
        "developer",
        "project_manager",
    ]
    member_policies = {
        member["agent_role"]: member["agent_policy"]
        for member in runtime_body["operating_policy"]["members"]
    }
    assert member_policies["project_manager"]["model"] == "gpt-4.1"
    assert member_policies["project_manager"]["model_settings"] == {
        "temperature": 0.2,
        "api_key": "[redacted]",
    }
    assert member_policies["project_manager"]["tool_policy"] == {
        "allowed_tools": ["planner"],
        "headers": "[redacted]",
    }
    assert member_policies["project_manager"]["runtime_policy"] == {
        "runtime": "docker",
        "env": {"OPENAI_API_KEY": "[redacted]"},
    }
    assert member_policies["project_manager"]["approval_policy"] == {
        "requires_approval": True,
        "token": "[redacted]",
    }
    assert runtime_body["memory_summary"]["active_entry_count"] == 5
    assert runtime_body["memory_summary"]["team_entry_count"] == 2
    assert runtime_body["memory_summary"]["shared_entry_count"] == 3
    assert runtime_body["memory_summary"]["scope_counts"] == {
        "company": 1,
        "shared": 1,
        "team": 2,
        "workspace": 1,
    }
    memory_titles = {entry["title"] for entry in runtime_body["memory_summary"]["entries"]}
    assert memory_titles == {
        "Customer escalation rule",
        "Camel case team memory",
        "Shared incident runbook",
        "Team release ritual",
        "Workspace deployment preference",
    }
    team_entry = next(
        entry
        for entry in runtime_body["memory_summary"]["entries"]
        if entry["title"] == "Team release ritual"
    )
    assert "[redacted]" in team_entry["snippet"]
    assert team_entry["metadata"]["api_key"] == "[redacted]"
    assert team_entry["metadata"]["note"] == "[redacted]"
    assert "sk-memory-secret" not in str(runtime_body)
    assert "sk-memory-content-secret" not in str(runtime_body)
    assert "plain-token-secret" not in str(runtime_body)
    assert "memory-metadata-secret" not in str(runtime_body)
    assert "sk-manager-model-secret" not in str(runtime_body)
    assert "manager-tool-secret" not in str(runtime_body)
    assert "sk-manager-runtime-secret" not in str(runtime_body)
    assert "manager-approval-token" not in str(runtime_body)
    assert "sk-runtime-space-secret" not in str(runtime_body)
    assert "runtime-network-token" not in str(runtime_body)
    assert "Other team private memory" not in str(runtime_body)
    assert "Foreign workspace memory" not in str(runtime_body)

    assert command_center.status_code == 200
    command_body = command_center.json()
    assert command_body["operating_policy"] == runtime_body["operating_policy"]
    assert command_body["memory_summary"]["active_entry_count"] == 5
    assert command_body["memory_summary"]["scope_counts"] == (
        runtime_body["memory_summary"]["scope_counts"]
    )
    assert command_body["runtime"]["operating_policy"] == runtime_body["operating_policy"]
    assert command_body["runtime"]["memory_summary"]["team_entry_count"] == 2
    assert command_body["runtime"]["memory_summary"]["scope_counts"] == (
        runtime_body["memory_summary"]["scope_counts"]
    )
    assert command_body["summary"]["memory_entry_count"] == 5
    assert command_body["summary"]["team_memory_entry_count"] == 2
    assert "Other team private memory" not in str(command_body)
    assert "sk-memory-secret" not in str(command_body)


def test_team_runtime_timeline_includes_scheduler_scan_metadata() -> None:
    _, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    session.add(manager)
    session.flush()
    scanned_at = datetime.now(UTC)
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Scheduler Timeline Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
        default_task_policy={
            "team_runtime": {
                "status": "running",
                "last_scheduler_scan": {
                    "status": "skipped",
                    "reason": "provider_readiness_blocked",
                    "scanned_at": scanned_at.isoformat(),
                    "window": 123,
                    "runtime_health": "provider_blocked",
                    "provider_readiness": {
                        "status": "blocked",
                        "runtime_blocked_member_count": 1,
                        "runtime_blocking_reasons": {
                            "model_provider_unhealthy": 1,
                        },
                        "api_key": "sk-scheduler-secret",
                        "base_url": "https://provider.example.test/v1/private",
                    },
                },
            }
        },
    )
    session.add(team)
    session.commit()

    timeline = TeamRuntimeTimelineService(session).timeline(
        workspace_id=workspace.id,
        team_id=team.id,
        filters=TimelineFilters(
            source_type="scheduler_scan",
            event_type="team.runtime.scheduler.skipped",
            include_queue=False,
        ),
    )

    assert timeline is not None
    assert timeline.summary.total_events == 1
    assert timeline.summary.source_counts == {"scheduler_scan": 1}
    assert timeline.summary.event_type_counts == {
        "team.runtime.scheduler.skipped": 1
    }
    item = timeline.items[0]
    assert item.message == (
        "Team runtime scheduler scan skipped: provider_readiness_blocked"
    )
    assert item.metadata["scan"]["reason"] == "provider_readiness_blocked"
    assert item.metadata["scan"]["runtime_health"] == "provider_blocked"
    assert item.metadata["scan"]["provider_readiness"] == {
        "status": "blocked",
        "runtime_blocked_member_count": 1,
        "runtime_blocking_reasons": {"model_provider_unhealthy": 1},
        "api_key": "[redacted]",
        "base_url": "[redacted]",
    }
    assert "sk-scheduler-secret" not in str(timeline)
    assert "provider.example.test/v1/private" not in str(timeline)


def test_team_runtime_timeline_includes_blocked_step_provider_metadata() -> None:
    _, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    credential = ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Blocked Provider",
        provider="openai-compatible",
        api_key="sk-step-provider-secret",
        default_model="gpt-4.1-mini",
        base_url="https://provider.example.test/v1/private",
        is_default=False,
    )
    credential.health_status = "unhealthy"
    developer = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
        model="workspace-default",
        model_provider_credential_id=credential.id,
    )
    session.add(developer)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Blocked Step Timeline Team",
        team_type="software",
        manager_agent_profile_id=developer.id,
    )
    session.add(team)
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Build provider blocked feature",
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=developer.id,
        title="Use blocked provider",
        status="queued",
        dependencies={
            "scheduling_status": "blocked",
            "blocked_reason": "model_provider_unavailable",
            "blocked_details": {
                "error_type": "ValueError",
                "message": "Agent model provider credential not found or unavailable",
                "model_provider": {
                    "source": "agent_override",
                    "agent_profile_id": str(developer.id),
                    "agent_model": "workspace-default",
                    "credential_id": str(credential.id),
                    "credential_reference": f"model_provider_credentials:{credential.id}",
                    "credential_status": "active",
                    "credential_health_status": "unhealthy",
                    "budget_exhausted": False,
                    "api_key": "sk-step-provider-secret",
                    "base_url": "https://provider.example.test/v1/private",
                },
            },
        },
    )
    session.add(step)
    session.commit()

    timeline = TeamRuntimeTimelineService(session).timeline(
        workspace_id=workspace.id,
        team_id=team.id,
        filters=TimelineFilters(
            source_type="task_step",
            event_type="team.runtime.step.scheduling_blocked",
            include_queue=False,
        ),
    )

    assert timeline is not None
    assert timeline.summary.total_events == 1
    assert timeline.summary.source_counts == {"task_step": 1}
    assert timeline.summary.event_type_counts == {
        "team.runtime.step.scheduling_blocked": 1
    }
    item = timeline.items[0]
    assert item.message == (
        "Team runtime step scheduling blocked: model_provider_unavailable"
    )
    assert item.resource_id == str(step.id)
    assert item.metadata["task_id"] == str(task.id)
    assert item.metadata["task_step_id"] == str(step.id)
    assert item.metadata["assigned_agent_profile_id"] == str(developer.id)
    assert item.metadata["blocked_reason"] == "model_provider_unavailable"
    provider = item.metadata["blocked_details"]["model_provider"]
    assert provider["credential_reference"] == f"model_provider_credentials:{credential.id}"
    assert provider["credential_health_status"] == "unhealthy"
    assert provider["api_key"] == "[redacted]"
    assert provider["base_url"] == "[redacted]"
    assert "sk-step-provider-secret" not in str(timeline)
    assert "provider.example.test/v1/private" not in str(timeline)


def test_team_operations_console_aggregates_runtime_members_sessions_and_mailbox() -> None:
    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
        blocking_timeout_seconds=0,
    )
    client, session = _client(queue=queue)
    owner, workspace = _seed_workspace(session, role="owner")
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="Operations Console Runtime",
        status="running",
        connection_status="online",
        limits={},
        network_policy={},
        capabilities={},
    )
    session.add_all([manager, runtime])
    session.flush()
    default_credential = ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Workspace OpenAI",
        provider="openai",
        api_key="sk-default-provider-secret",
        default_model="gpt-4.1-mini",
        base_url=None,
        is_default=True,
    )
    credential = ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Claude Gateway",
        provider="openai-compatible",
        api_key="sk-console-provider-secret",
        default_model="claude-opus-4-6",
        base_url="https://dash.ovload.com/v1",
        is_default=False,
        budget_metadata={"model_api": "chat_completions"},
    )
    developer = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
        model="workspace-default",
        model_provider_credential_id=credential.id,
    )
    session.add(developer)
    session.flush()
    session.add(
        WorkspaceScheduledJob(
            workspace_id=workspace.id,
            created_by_user_id=owner.id,
            name="Claude provider health",
            schedule_type="hourly",
            schedule_config={"minute": 15},
            status="active",
            action_type="queue_job",
            job_type=JobType.MODEL_PROVIDER_HEALTH_CHECK.value,
            resource_id=credential.id,
            routing={
                "probes": ["models"],
                "timeout_seconds": 5,
                "api_key": "sk-never-return",
            },
            priority=3,
            max_attempts=2,
            metadata_={"token": "hidden"},
            next_run_at=datetime.now(UTC) + timedelta(minutes=30),
        )
    )
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Operations Console Team",
        team_type="company",
        manager_agent_profile_id=manager.id,
        coordination_rules={
            "operating_policy": {
                "cadence": "hourly",
                "manager_can_assign": True,
            }
        },
        default_task_policy={
            "priority": 6,
            "team_runtime": {
                "status": "running",
                "workspace_runtime_id": str(runtime.id),
                "scheduling_policy": {
                    "loop_interval_seconds": 120,
                    "priority": 8,
                },
                "last_scheduler_scan": {
                    "status": "skipped",
                    "reason": "scheduled_team_runtime_not_due",
                    "scanned_at": datetime.now(UTC).isoformat(),
                    "window": 123,
                    "token": "hidden-scheduler-token",
                },
            },
            "team_runtime_thread_id": "internal-thread-control",
        },
    )
    other_team = AgentTeam(
        workspace_id=workspace.id,
        name="Other Operations Team",
        team_type="company",
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
                order_index=1,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=developer.id,
                team_role="developer",
                order_index=2,
            ),
        ]
    )
    blocked_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Blocked provider rollout",
        priority=9,
        status=TaskStatus.QUEUED.value,
    )
    session.add(blocked_task)
    session.flush()
    blocked_step = TaskStep(
        workspace_id=workspace.id,
        task_id=blocked_task.id,
        assigned_agent_profile_id=developer.id,
        title="Run with blocked provider",
        status="queued",
        dependencies={
            "scheduling_status": "blocked",
            "blocked_reason": "model_provider_unavailable",
            "blocked_details": {
                "model_provider": {
                    "credential_reference": f"model_provider_credentials:{credential.id}",
                    "credential_health_status": "unhealthy",
                    "api_key": "sk-blocked-step-secret",
                    "base_url": "https://blocked.example.test/v1/private",
                }
            },
        },
    )
    session.add(blocked_step)
    queued_run = AgentRun(
        workspace_id=workspace.id,
        task_id=blocked_task.id,
        task_step_id=blocked_step.id,
        agent_profile_id=developer.id,
        status=RunStatus.QUEUED.value,
        model="claude-opus-4-6",
        input={
            "authorization_snapshot": {
                "model_provider": {
                    "provider": "openai-compatible",
                    "credential_id": str(credential.id),
                    "credential_reference": f"model_provider_credentials:{credential.id}",
                    "model_api": "chat_completions",
                    "readiness_status": "degraded",
                    "reasons": [],
                    "warnings": ["model_provider_health_unknown"],
                    "api_key": "sk-run-provider-secret",
                    "base_url": "https://run-provider.example.test/private",
                }
            }
        },
    )
    session.add(queued_run)
    session.commit()
    session.add(
        WorkspaceMemoryEntry(
            workspace_id=workspace.id,
            created_by_user_id=owner.id,
            entry_type="operating_note",
            title="Console company policy",
            content="Company-level memory is visible in the operations console.",
            tags=["company"],
            visibility_scope="company",
            importance=3,
        )
    )
    session.commit()

    started = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/start",
        headers=_headers(owner.id),
        json={
            "reason": "start operating console",
            "metadata": {
                "api_key": "sk-runtime-metadata",
                "nested": {"authorization": "Bearer runtime-hidden"},
            },
        },
    )
    assert started.status_code == 200
    thread_id = started.json()["thread_id"]
    team_session = session.scalar(
        select(PersistentAgentSession).where(
            PersistentAgentSession.workspace_id == workspace.id,
            PersistentAgentSession.agent_team_id == team.id,
            PersistentAgentSession.scope_type == "team_runtime",
        )
    )
    assert team_session is not None
    team_session.latest_item_metadata = {
        "token": "session-secret-token",
        "visible": "latest",
    }
    message = AgentMessage(
        workspace_id=workspace.id,
        thread_id=UUID(thread_id),
        agent_team_id=team.id,
        sender_agent_profile_id=manager.id,
        recipient_agent_profile_id=developer.id,
        message_type="handoff",
        body="Build the customer dashboard with token sk-console hidden",
        payload={
            "api_key": "sk-console",
            "nested": {"authorization": "Bearer mailbox-hidden"},
            "visible": "handoff",
        },
        status="sent",
    )
    other_thread = AgentMessageThread(
        workspace_id=workspace.id,
        agent_team_id=other_team.id,
        subject="Other team thread",
    )
    session.add_all([message, other_thread])
    session.flush()
    queued_job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
        resource_id=team.id,
        requested_by_user_id=owner.id,
        idempotency_key=f"team.execution_loop:{workspace.id}:{team.id}:console-queued",
        routing={"trigger": "manual_enqueue", "api_key": "sk-queue-secret"},
    )
    retry_job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
        resource_id=team.id,
        requested_by_user_id=owner.id,
        idempotency_key=f"team.execution_loop:{workspace.id}:{team.id}:console-retry",
        attempt=1,
        max_attempts=3,
        last_error="provider failed api_key=sk-retry-secret",
        last_error_type="RuntimeError",
        last_failed_at=datetime.now(UTC),
        routing={"trigger": "scheduled_team_runtime"},
    )
    dead_letter_job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
        resource_id=team.id,
        requested_by_user_id=owner.id,
        idempotency_key=f"team.execution_loop:{workspace.id}:{team.id}:console-dead",
        attempt=3,
        max_attempts=3,
        last_error="base_url=https://dead.example.test/private",
        last_error_type="RuntimeError",
        routing={"token": "dead-token"},
    )
    other_team_job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
        resource_id=other_team.id,
        requested_by_user_id=owner.id,
        idempotency_key=f"team.execution_loop:{workspace.id}:{other_team.id}:console-other",
    )
    other_team_retry_job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
        resource_id=other_team.id,
        requested_by_user_id=owner.id,
        idempotency_key=f"team.execution_loop:{workspace.id}:{other_team.id}:console-retry",
        attempt=1,
        max_attempts=3,
        last_error="other retry",
        last_error_type="RuntimeError",
        last_failed_at=datetime.now(UTC),
    )
    queue.enqueue(queued_job)
    queue.enqueue(other_team_job)
    queue.redis.zadd(
        queue.keys.queue(queue.queue_name) + ":retry",
        {
            other_team_retry_job.model_dump_json(): datetime.now(UTC).timestamp() + 10,
            retry_job.model_dump_json(): datetime.now(UTC).timestamp() + 30,
        },
    )
    queue.redis.rpush(
        queue.keys.dead_letter_queue(queue.queue_name),
        dead_letter_job.model_dump_json(),
    )
    session.add(
        AgentMessage(
            workspace_id=workspace.id,
            thread_id=other_thread.id,
            agent_team_id=other_team.id,
            sender_agent_profile_id=manager.id,
            recipient_agent_profile_id=developer.id,
            message_type="handoff",
            body="This other team unread message must not affect the current console.",
            status="sent",
        )
    )
    session.commit()
    session.expire_all()

    raw_console = TeamOperationsConsoleService(session).get_console(
        workspace_id=workspace.id,
        team_id=team.id,
    )
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/operations-console",
        headers=_headers(owner.id),
    )

    assert raw_console is not None
    limited_queue_console = TeamOperationsConsoleService(session).get_console(
        workspace_id=workspace.id,
        team_id=team.id,
        queue=queue,
        queue_limit=1,
    )
    assert limited_queue_console is not None
    assert limited_queue_console["runtime"]["queue"]["scheduled_retry"] == 1
    raw_serialized = str(raw_console)
    assert "sk-runtime-metadata" not in raw_serialized
    assert "Bearer runtime-hidden" not in raw_serialized
    assert "session-secret-token" not in raw_serialized
    assert "sk-console" not in raw_serialized
    assert "Bearer mailbox-hidden" not in raw_serialized
    assert raw_console["mailbox"]["latest_messages"][0]["payload"] == {
        "api_key": "[redacted]",
        "nested": {"authorization": "[redacted]"},
        "visible": "handoff",
    }
    assert response.status_code == 200
    body = response.json()
    assert body["team"]["name"] == "Operations Console Team"
    assert body["team"]["coordination_rules"]["operating_policy"]["cadence"] == "hourly"
    assert body["team"]["default_task_policy"] == {"priority": 6}
    assert body["runtime"]["status"] == "running"
    assert body["runtime"]["workspace_runtime_id"] == str(runtime.id)
    assert body["runtime"]["runtime_health"] == "starting"
    assert body["runtime"]["ready"] is False
    assert body["runtime"]["scheduling"]["loop_interval_seconds"] == 120
    assert body["runtime"]["scheduling"]["priority"] == 8
    assert body["runtime"]["scheduling"]["due"] is True
    assert body["runtime"]["scheduling"]["last_scheduler_scan"]["status"] == "skipped"
    assert body["runtime"]["scheduling"]["last_scheduler_scan"]["reason"] == (
        "scheduled_team_runtime_not_due"
    )
    assert body["runtime"]["scheduling"]["last_scheduler_scan"]["token"] == "[redacted]"
    assert body["runtime"]["blocked_steps"]["count"] == 1
    assert body["runtime"]["blocked_steps"]["blocked_reasons"] == {
        "model_provider_unavailable": 1
    }
    assert body["runtime"]["blocked_steps"]["latest"][0]["task_id"] == str(blocked_task.id)
    assert body["runtime"]["blocked_steps"]["latest"][0]["task_step_id"] == (
        str(blocked_step.id)
    )
    assert body["runtime"]["blocked_steps"]["latest"][0]["blocked_details"][
        "model_provider"
    ] == {
        "credential_reference": f"model_provider_credentials:{credential.id}",
        "credential_health_status": "unhealthy",
        "api_key": "[redacted]",
        "base_url": "[redacted]",
    }
    assert "sk-blocked-step-secret" not in str(body["runtime"]["blocked_steps"])
    assert "blocked.example.test/v1/private" not in str(body["runtime"]["blocked_steps"])
    assert body["runtime"]["queue"]["available"] is True
    assert body["runtime"]["queue"]["queue_name"] == "agent_runs"
    assert body["runtime"]["queue"]["job_type"] == JobType.TEAM_EXECUTION_LOOP.value
    assert body["runtime"]["queue"]["queued"] == 1
    assert body["runtime"]["queue"]["scheduled_retry"] == 1
    assert body["runtime"]["queue"]["dead_letter"] == 1
    queue_states = {item["state"] for item in body["runtime"]["queue"]["latest_jobs"]}
    assert queue_states == {"queued", "scheduled_retry", "dead_letter"}
    assert "sk-queue-secret" not in str(body["runtime"]["queue"])
    assert "sk-retry-secret" not in str(body["runtime"]["queue"])
    assert "dead.example.test/private" not in str(body["runtime"]["queue"])
    assert "dead-token" not in str(body["runtime"]["queue"])
    assert (
        body["runtime"]["operating_policy"]["coordination_rules"]["operating_policy"]["cadence"]
        == "hourly"
    )
    assert body["runtime"]["memory_summary"]["scope_counts"] == {"company": 1}
    assert body["sessions"]["total"] == 3
    assert body["sessions"]["team_session"]["scope_type"] == "team_runtime"
    assert len(body["sessions"]["member_sessions"]) == 2
    assert {item["team_role"] for item in body["members"]} == {
        "project_manager",
        "developer",
    }
    developer_member = next(
        item for item in body["members"] if item["agent_profile_id"] == str(developer.id)
    )
    model_provider = developer_member["agent"]["model_provider"]
    assert developer_member["agent"]["model"] == "workspace-default"
    assert developer_member["agent"]["model_provider_credential_id"] == str(credential.id)
    assert model_provider["source"] == "agent_override"
    assert model_provider["credential_id"] == str(credential.id)
    assert model_provider["credential_reference"] == (
        f"model_provider_credentials:{credential.id}"
    )
    assert model_provider["credential_name"] == "Claude Gateway"
    assert model_provider["provider"] == "openai-compatible"
    assert model_provider["selected_model"] == "claude-opus-4-6"
    assert model_provider["agent_model"] == "workspace-default"
    assert model_provider["model_capability"] == {
        "provider": "openai-compatible",
        "model": "*",
        "display_name": "OpenAI-compatible model",
        "capabilities": ["tools", "json_mode", "streaming"],
        "supports_tools": True,
        "supports_vision": False,
        "supports_json_mode": True,
        "supports_streaming": True,
        "context_window_tokens": None,
        "notes": "Actual support depends on the upstream gateway and selected model.",
    }
    assert model_provider["default_model"] == "claude-opus-4-6"
    assert model_provider["model_api"] == "chat_completions"
    assert model_provider["base_url_configured"] is True
    assert model_provider["base_url_host"] == "dash.ovload.com"
    assert model_provider["api_key_fingerprint"] == credential.api_key_fingerprint
    assert model_provider["credential_status"] == "active"
    assert model_provider["credential_health_status"] == "unknown"
    assert model_provider["failure_count"] == 0
    assert model_provider["budget_exhausted"] is False
    assert model_provider["readiness_status"] == "degraded"
    assert model_provider["reasons"] == []
    assert model_provider["warnings"] == ["model_provider_unknown"]
    assert model_provider["last_health_check_at"] is None
    assert model_provider["last_success_at"] is None
    assert model_provider["last_failure_at"] is None
    assert model_provider["last_failure_code"] is None
    assert model_provider["last_failure_message"] is None
    assert model_provider["is_default"] is False
    assert model_provider["budget_metadata"] == {"model_api": "chat_completions"}
    assert model_provider["scheduled_health_check"]["configured"] is True
    assert model_provider["scheduled_health_check"]["active_count"] == 1
    assert model_provider["scheduled_health_check"]["paused_count"] == 0
    assert model_provider["scheduled_health_check"]["jobs"][0]["routing"] == {
        "probes": ["models"],
        "timeout_seconds": 5,
    }
    provider_management = body["provider_management"]
    assert provider_management["credential_count"] == 2
    assert provider_management["active_credential_count"] == 2
    assert provider_management["default_credential_id"] == str(default_credential.id)
    run_diagnostics = provider_management["run_diagnostics"]
    assert run_diagnostics["total"] == 1
    assert run_diagnostics["items"][0]["run_id"] == str(queued_run.id)
    assert run_diagnostics["items"][0]["provider_snapshot_source"] == "frozen_run_snapshot"
    assert run_diagnostics["items"][0]["provider"] == "openai-compatible"
    assert run_diagnostics["items"][0]["model_api"] == "chat_completions"
    assert run_diagnostics["items"][0]["credential_reference"] == (
        f"model_provider_credentials:{credential.id}"
    )
    assert "sk-run-provider-secret" not in str(run_diagnostics)
    assert "run-provider.example.test/private" not in str(run_diagnostics)
    provider_options = {
        item["id"]: item for item in provider_management["credentials"]
    }
    assert set(provider_options) == {str(default_credential.id), str(credential.id)}
    assert provider_options[str(default_credential.id)]["provider"] == "openai"
    assert provider_options[str(default_credential.id)]["is_default"] is True
    assert provider_options[str(default_credential.id)]["selectable"] is True
    assert provider_options[str(default_credential.id)]["base_url_configured"] is False
    assert provider_options[str(default_credential.id)]["model_apis"] == [
        "responses",
        "chat_completions",
    ]
    assert provider_options[str(default_credential.id)]["default_model_api"] is None
    assert {
        item["model"] for item in provider_options[str(default_credential.id)]["model_options"]
    }.issuperset({"gpt-5", "gpt-4.1-mini"})
    assert provider_options[str(credential.id)]["provider"] == "openai-compatible"
    assert provider_options[str(credential.id)]["model_api"] == "chat_completions"
    assert provider_options[str(credential.id)]["model_apis"] == [
        "responses",
        "chat_completions",
    ]
    assert provider_options[str(credential.id)]["default_model_api"] is None
    assert provider_options[str(credential.id)]["base_url_host"] == "dash.ovload.com"
    assert provider_options[str(credential.id)]["model_options"] == [
        provider_options[str(credential.id)]["model_capability"]
    ]
    assert provider_options[str(credential.id)]["scheduled_health_check"]["configured"] is True
    developer_binding = next(
        item
        for item in provider_management["agent_bindings"]
        if item["agent_profile_id"] == str(developer.id)
    )
    manager_binding = next(
        item
        for item in provider_management["agent_bindings"]
        if item["agent_profile_id"] == str(manager.id)
    )
    assert developer_binding["credential_id"] == str(credential.id)
    assert developer_binding["provider"] == "openai-compatible"
    assert developer_binding["default_model"] == "claude-opus-4-6"
    assert developer_binding["model_api"] == "chat_completions"
    assert developer_binding["model_apis"] == ["responses", "chat_completions"]
    assert developer_binding["default_model_api"] is None
    assert developer_binding["credential_status"] == "active"
    assert developer_binding["credential_health_status"] == "unknown"
    assert developer_binding["budget_exhausted"] is False
    assert developer_binding["model_capability"] == {
        "provider": "openai-compatible",
        "model": "*",
        "display_name": "OpenAI-compatible model",
        "capabilities": ["tools", "json_mode", "streaming"],
        "supports_tools": True,
        "supports_vision": False,
        "supports_json_mode": True,
        "supports_streaming": True,
        "context_window_tokens": None,
        "notes": "Actual support depends on the upstream gateway and selected model.",
    }
    assert developer_binding["available_credential_ids"] == [
        str(default_credential.id),
        str(credential.id),
    ]
    assert manager_binding["credential_id"] == str(default_credential.id)
    assert manager_binding["source"] == "workspace_default"
    provider_management_actions = provider_management["suggested_actions"]
    assert provider_management_actions[0]["source"] == "provider_management"
    assert provider_management_actions[0]["action"] == "review_model_provider"
    assert provider_management_actions[0]["reason"] == "agent_model_provider_degraded"
    developer_provider_management_action = next(
        item
        for item in provider_management_actions
        if item["credential_id"] == str(credential.id)
    )
    assert developer_provider_management_action["model_api"] == "chat_completions"
    assert developer_member["mailbox"]["unread_count"] == 1
    assert body["mailbox"]["thread_id"] == thread_id
    assert body["mailbox"]["unread_count"] == 2
    assert body["mailbox"]["latest_messages"][0]["task_id"] is None
    assert body["mailbox"]["latest_messages"][0]["agent_team_id"] == str(team.id)
    assert body["mailbox"]["latest_messages"][0]["body_preview"] == "[redacted]"
    assert body["mailbox"]["latest_messages"][0]["payload"] == {
        "api_key": "[redacted]",
        "nested": {"authorization": "[redacted]"},
        "visible": "handoff",
    }
    assert "sk-console" not in str(body)
    assert "sk-runtime-metadata" not in str(body)
    assert "Bearer runtime-hidden" not in str(body)
    assert "session-secret-token" not in str(body)
    assert "Bearer mailbox-hidden" not in str(body)
    assert "hidden-scheduler-token" not in str(body)
    assert "sk-never-return" not in str(body)
    assert "sk-queue-secret" not in str(body)
    assert "sk-retry-secret" not in str(body)
    assert "dead.example.test/private" not in str(body)
    assert "dead-token" not in str(body)
    assert "hidden" not in str(body)
    assert "dash.ovload.com/v1" not in str(body)
    assert body["controls"]["can_pause"] is True
    assert body["controls"]["can_ensure_workspace_runtime"] is False
    suggested_sources = {item["source"] for item in body["controls"]["suggested_actions"]}
    assert "provider_readiness" in suggested_sources
    provider_suggestions = [
        item
        for item in body["controls"]["suggested_actions"]
        if item["source"] == "provider_readiness"
    ]
    provider_management_suggestions = [
        item
        for item in body["controls"]["suggested_actions"]
        if item["source"] == "provider_management"
    ]
    assert provider_suggestions[0]["action"] == "review_model_provider"
    assert provider_suggestions[0]["reason"] == "team_model_provider_degraded"
    developer_provider_suggestion = next(
        item for item in provider_suggestions if item["credential_id"] == str(credential.id)
    )
    assert developer_provider_suggestion["model_api"] == "chat_completions"
    assert developer_provider_suggestion["model_apis"] == [
        "responses",
        "chat_completions",
    ]
    assert developer_provider_suggestion["default_model_api"] is None
    assert provider_management_suggestions[0]["reason"] == "agent_model_provider_degraded"
    developer_provider_management_suggestion = next(
        item
        for item in provider_management_suggestions
        if item["credential_id"] == str(credential.id)
    )
    assert developer_provider_management_suggestion["model_api"] == "chat_completions"
    queue_suggestions = [
        item
        for item in body["controls"]["suggested_actions"]
        if item["source"] == "team_runtime_queue"
    ]
    assert {item["reason"] for item in queue_suggestions} == {
        "team_execution_loop_dead_letter",
        "team_execution_loop_retry_scheduled",
    }
    blocked_step_suggestions = [
        item
        for item in body["controls"]["suggested_actions"]
        if item["reason"] == "team_runtime_step_scheduling_blocked"
    ]
    assert len(blocked_step_suggestions) == 1
    blocked_step_suggestion = blocked_step_suggestions[0]
    assert blocked_step_suggestion["source"] == "team_runtime"
    assert blocked_step_suggestion["automation"] == "team_runtime_control"
    assert blocked_step_suggestion["action"] == "review_scheduling_blocks"
    assert blocked_step_suggestion["priority"] == 90
    assert blocked_step_suggestion["count"] == 1
    assert blocked_step_suggestion["blocked_reasons"] == {
        "model_provider_unavailable": 1
    }
    assert blocked_step_suggestion["task_ids"] == [str(blocked_task.id)]
    assert blocked_step_suggestion["task_step_ids"] == [str(blocked_step.id)]
    assert body["command_center"]["summary"]["runtime_ready"] is True
    assert body["command_center"]["runtime"]["ready"] is True
    assert body["command_center"]["provider_readiness"]["status"] == "degraded"
    assert body["command_center"]["provider_readiness"]["member_count"] == 2
    assert body["command_center"]["provider_readiness"]["warning_reasons"] == {
        "model_provider_health_check_not_scheduled": 1,
        "model_provider_unknown": 2,
    }
    assert body["command_center"]["overview"]["team"]["name"] == "Operations Console Team"
    assert body["command_center"]["queues"]["handoff"]["total"] == 0
    assert body["command_center"]["queues"]["manager"]["total"] == 1
    assert body["command_center"]["operating_policy"]["default_task_policy"] == {
        "priority": 6
    }
    assert body["command_center"]["memory_summary"]["team_id"] == str(team.id)
    assert body["command_center"]["memory_summary"]["scope_counts"] == {"company": 1}
    assert body["command_center"]["runtime"]["memory_summary"]["scope_counts"] == {
        "company": 1
    }
    for policy in (
        body["team"]["default_task_policy"],
        body["runtime"]["operating_policy"]["default_task_policy"],
        body["command_center"]["operating_policy"]["default_task_policy"],
        body["command_center"]["runtime"]["operating_policy"]["default_task_policy"],
    ):
        assert "team_runtime" not in policy
        assert "team_runtime_thread_id" not in policy


def test_team_operations_console_read_does_not_initialize_runtime_state() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    session.add(manager)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Read Only Console Team",
        team_type="company",
        manager_agent_profile_id=manager.id,
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=manager.id,
            team_role="project_manager",
            order_index=1,
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/operations-console",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["runtime"]["thread_id"] is None
    assert body["runtime"]["team_session_id"] is None
    assert body["runtime"]["team_session_key"] is None
    assert body["runtime"]["member_session_count"] == 0
    assert body["sessions"]["total"] == 0
    assert body["mailbox"]["thread_id"] is None
    assert body["mailbox"]["unread_count"] == 0
    assert body["mailbox"]["latest_messages"] == []

    session.expire_all()
    assert (
        session.scalar(
            select(func.count(PersistentAgentSession.id)).where(
                PersistentAgentSession.workspace_id == workspace.id,
                PersistentAgentSession.agent_team_id == team.id,
            )
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count(AgentMessageThread.id)).where(
                AgentMessageThread.workspace_id == workspace.id
            )
        )
        == 0
    )


def test_team_operations_console_marks_inactive_provider_not_selectable() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="PM",
        role="project_manager",
        model="workspace-default",
    )
    session.add(manager)
    session.flush()
    credential = ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Inactive Claude Gateway",
        provider="openai-compatible",
        api_key="sk-inactive-console",
        default_model="claude-sonnet-4-6",
        base_url="https://inactive-console.example.test/v1",
        is_default=False,
    )
    credential.status = "inactive"
    manager.model_provider_credential_id = credential.id
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Inactive Provider Team",
        team_type="company",
        manager_agent_profile_id=manager.id,
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=manager.id,
            team_role="project_manager",
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/operations-console",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    option = body["provider_management"]["credentials"][0]
    binding = body["provider_management"]["agent_bindings"][0]
    assert option["id"] == str(credential.id)
    assert option["selectable"] is False
    assert option["not_selectable_reasons"] == ["model_provider_not_active"]
    assert binding["credential_status"] == "inactive"
    assert binding["readiness_status"] == "blocked"
    assert binding["reasons"] == ["model_provider_not_active"]
    assert binding["available_credential_ids"] == []
    assert body["command_center"]["provider_readiness"]["status"] == "blocked"
    readiness_member = body["command_center"]["provider_readiness"]["members"][0]
    assert readiness_member["credential_reference"] == (
        f"model_provider_credentials:{credential.id}"
    )
    assert body["command_center"]["provider_readiness"]["runtime_blocking_reasons"] == {
        "model_provider_not_active": 1
    }
    provider_action = body["command_center"]["action_plan"][0]
    assert provider_action["source"] == "provider_readiness"
    assert provider_action["credential_reference"] == (
        f"model_provider_credentials:{credential.id}"
    )
    assert "sk-inactive-console" not in str(body)
    assert "inactive-console.example.test/v1" not in str(body)


def test_team_operations_console_provider_readiness_blocks_unhealthy_default() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    service = ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
    )
    primary = service.create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Primary Default",
        provider="openai",
        api_key="sk-primary-readiness",
        default_model="primary-model",
        base_url=None,
        is_default=True,
    )
    backup = service.create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Backup Claude Gateway",
        provider="openai-compatible",
        api_key="sk-backup-readiness",
        default_model="backup-model",
        base_url="https://backup-readiness.example.test/v1",
        is_default=False,
        budget_metadata={"model_api": "chat_completions"},
    )
    primary.health_status = "unhealthy"
    primary.failure_count = 3
    primary.last_failure_at = datetime.now(UTC)
    primary.last_failure_code = "InternalServerError"
    primary.last_failure_message = "Provider failed with sk-primary-readiness"
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="PM",
        role="project_manager",
        model="workspace-default",
        model_settings={"model_api": "response"},
    )
    session.add(manager)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Fallback Readiness Team",
        team_type="company",
        manager_agent_profile_id=manager.id,
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=manager.id,
            team_role="project_manager",
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/operations-console",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    binding = body["provider_management"]["agent_bindings"][0]
    readiness = body["command_center"]["provider_readiness"]
    readiness_member = readiness["members"][0]
    provider_action = body["command_center"]["action_plan"][0]
    assert binding["source"] == "agent_model"
    assert binding["credential_id"] is None
    assert binding["credential_reference"] is None
    assert binding["selected_model"] == "workspace-default"
    assert binding["provider"] is None
    assert binding["model_api"] == "responses"
    assert binding["readiness_status"] == "blocked"
    assert binding["reasons"] == ["model_provider_unavailable"]
    assert readiness["status"] == "blocked"
    assert readiness["runtime_blocked_member_count"] == 1
    assert readiness["runtime_degraded_member_count"] == 0
    assert readiness_member["credential_id"] == str(primary.id)
    assert readiness_member["credential_reference"] == (
        f"model_provider_credentials:{primary.id}"
    )
    assert readiness_member["provider"] == "openai"
    assert readiness_member["model"] == "primary-model"
    assert readiness_member["model_api"] == "responses"
    assert readiness_member["readiness_status"] == "blocked"
    assert readiness_member["reasons"] == ["model_provider_unhealthy"]
    assert readiness_member["failure_count"] == 3
    assert readiness_member["last_failure_code"] == "InternalServerError"
    assert readiness_member["last_failure_message"] == "[redacted]"
    assert provider_action["source"] == "provider_readiness"
    assert provider_action["model_api"] == "responses"
    assert provider_action["failure_count"] == 3
    assert provider_action["last_failure_code"] == "InternalServerError"
    assert provider_action["last_failure_message"] == "[redacted]"
    assert backup.status == "active"
    assert "sk-primary-readiness" not in str(body)
    assert "sk-backup-readiness" not in str(body)
    assert "backup-readiness.example.test/v1" not in str(body)


def test_team_operations_console_exposes_anthropic_default_model_api() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    service = ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
    )
    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Default Claude Gateway",
        provider="anthropic",
        api_key="sk-console-anthropic",
        default_model="claude-sonnet-4-6",
        base_url="https://api.anthropic.com/private",
        is_default=True,
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Claude PM",
        role="project_manager",
        model="workspace-default",
        model_settings={"model_api": "response"},
    )
    session.add(agent)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Claude Console Team",
        team_type="company",
        manager_agent_profile_id=agent.id,
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=agent.id,
            team_role="project_manager",
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/operations-console",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    provider_management = body["provider_management"]
    option = provider_management["credentials"][0]
    binding = provider_management["agent_bindings"][0]
    member_provider = body["members"][0]["agent"]["model_provider"]
    readiness_member = body["command_center"]["provider_readiness"]["members"][0]
    assert option["id"] == str(credential.id)
    assert option["provider"] == "anthropic"
    assert option["model_api"] == "anthropic_messages"
    assert option["model_capability"]["model"] == "claude-sonnet-4-6"
    assert binding["source"] == "workspace_default"
    assert binding["credential_id"] == str(credential.id)
    assert binding["provider"] == "anthropic"
    assert binding["selected_model"] == "claude-sonnet-4-6"
    assert binding["model_api"] == "anthropic_messages"
    assert binding["requested_model_api"] == "responses"
    assert "model_api_override_unsupported" in binding["warnings"]
    assert member_provider["source"] == "workspace_default"
    assert member_provider["provider"] == "anthropic"
    assert member_provider["model_api"] == "anthropic_messages"
    assert member_provider["requested_model_api"] == "responses"
    assert "model_api_override_unsupported" in member_provider["warnings"]
    assert member_provider["model_capability"]["supports_tools"] is True
    assert readiness_member["provider"] == "anthropic"
    assert readiness_member["model"] == "claude-sonnet-4-6"
    assert readiness_member["model_api"] == "anthropic_messages"
    assert readiness_member["requested_model_api"] == "responses"
    assert "model_api_override_unsupported" in readiness_member["warnings"]
    assert "sk-console-anthropic" not in str(body)
    assert "api.anthropic.com/private" not in str(body)


def test_team_operations_console_blocks_missing_explicit_provider() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    default_credential = ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Workspace Default",
        provider="openai",
        api_key="sk-default-console",
        default_model="gpt-4.1-mini",
        base_url=None,
        is_default=True,
    )
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="PM",
        role="project_manager",
        model="workspace-default",
        model_provider_credential_id=uuid4(),
    )
    session.add(manager)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Missing Provider Team",
        team_type="company",
        manager_agent_profile_id=manager.id,
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=manager.id,
            team_role="project_manager",
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/operations-console",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    binding = body["provider_management"]["agent_bindings"][0]
    assert binding["source"] == "unavailable"
    assert binding["credential_id"] == str(manager.model_provider_credential_id)
    assert binding["readiness_status"] == "blocked"
    assert binding["reasons"] == ["model_provider_unavailable"]
    assert binding["available_credential_ids"] == [str(default_credential.id)]
    readiness = body["command_center"]["provider_readiness"]
    assert readiness["status"] == "blocked"
    assert readiness["runtime_blocking_reasons"] == {
        "model_provider_unavailable": 1
    }
    assert readiness["members"][0]["credential_id"] == str(
        manager.model_provider_credential_id
    )
    assert readiness["members"][0]["credential_reference"] == (
        f"model_provider_credentials:{manager.model_provider_credential_id}"
    )
    assert readiness["members"][0]["reasons"] == ["model_provider_unavailable"]
    provider_action = body["command_center"]["action_plan"][0]
    assert provider_action["source"] == "provider_readiness"
    assert provider_action["credential_id"] == str(manager.model_provider_credential_id)
    assert provider_action["credential_reference"] == (
        f"model_provider_credentials:{manager.model_provider_credential_id}"
    )
    assert "sk-default-console" not in str(body)


def test_team_runtime_ignores_foreign_team_thread_reference() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    other_manager = AgentProfile(
        workspace_id=workspace.id,
        name="Other PM",
        role="project_manager",
    )
    session.add_all([manager, other_manager])
    session.flush()
    other_team = AgentTeam(
        workspace_id=workspace.id,
        name="Other Runtime Team",
        team_type="company",
        manager_agent_profile_id=other_manager.id,
    )
    session.add(other_team)
    session.flush()
    other_thread = AgentMessageThread(
        workspace_id=workspace.id,
        agent_team_id=other_team.id,
        subject="Other team runtime",
    )
    session.add(other_thread)
    session.flush()
    session.add(
        AgentMessage(
            workspace_id=workspace.id,
            thread_id=other_thread.id,
            agent_team_id=other_team.id,
            sender_agent_profile_id=other_manager.id,
            recipient_agent_profile_id=other_manager.id,
            message_type="team.runtime.started",
            body="Other team runtime message",
            status="sent",
        )
    )
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Scoped Runtime Team",
        team_type="company",
        manager_agent_profile_id=manager.id,
        default_task_policy={
            "team_runtime": {"status": "running"},
            "team_runtime_thread_id": str(other_thread.id),
        },
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=manager.id,
            team_role="project_manager",
            order_index=1,
        )
    )
    session.commit()

    runtime = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime",
        headers=_headers(owner.id),
    )
    console = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/operations-console",
        headers=_headers(owner.id),
    )
    started = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/start",
        headers=_headers(owner.id),
        json={"reason": "create scoped runtime thread"},
    )

    assert runtime.status_code == 200
    assert runtime.json()["thread_id"] is None
    assert runtime.json()["last_message_at"] is None
    assert console.status_code == 200
    assert console.json()["mailbox"]["thread_id"] is None
    assert console.json()["mailbox"]["latest_messages"] == []
    assert "Other team runtime message" not in str(console.json())
    assert started.status_code == 200
    assert started.json()["thread_id"] != str(other_thread.id)

    session.expire_all()
    stored_team = session.get(AgentTeam, team.id)
    assert stored_team is not None
    created_thread_id = UUID(stored_team.default_task_policy["team_runtime_thread_id"])
    assert created_thread_id != other_thread.id
    created_thread = session.get(AgentMessageThread, created_thread_id)
    assert created_thread is not None
    assert created_thread.agent_team_id == team.id


def test_team_runtime_state_recovers_last_iteration_and_continue_context() -> None:
    client, session, docker = _client(include_docker=True)
    owner, workspace = _seed_workspace(session, role="owner")
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    template = RuntimeTemplate(
        name="recovery-runtime-template",
        image="python:3.12-slim",
        default_limits={
            "cpu_count": 1,
            "memory_mb": 512,
            "disk_mb": 1024,
            "timeout_seconds": 60,
        },
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([manager, template])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Recovery Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
        default_task_policy={
            "team_runtime": {
                "status": "running",
                "runtime_template_id": str(template.id),
            }
        },
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=manager.id,
            team_role="project_manager",
            order_index=1,
        )
    )
    session.commit()

    ran = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/execution-loop/run",
        headers=_headers(owner.id),
        json={
            "dry_run": False,
            "apply_command_center_actions": False,
            "finalize_ready_tasks": False,
            "enqueue_runs": False,
        },
    )
    stopped = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/stop",
        headers=_headers(owner.id),
        json={"reason": "simulate restart"},
    )
    continued = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/continue",
        headers=_headers(owner.id),
        json={"instruction": "restore from persisted runtime state"},
    )
    recovered = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime",
        headers=_headers(owner.id),
    )

    assert ran.status_code == 200
    assert ran.json()["status"] == "noop"
    assert stopped.status_code == 200
    assert stopped.json()["runtime_health"] == "stopped"
    assert continued.status_code == 200
    assert continued.json()["status"] == "running"
    assert continued.json()["last_iteration"]["iteration"] == 1
    assert continued.json()["runtime_health"] == "healthy"
    assert recovered.status_code == 200
    recovered_body = recovered.json()
    assert recovered_body["last_iteration"]["status"] == "noop"
    assert recovered_body["last_iteration"]["summary"]["finalize_ready_tasks"] is False
    assert recovered_body["last_message_at"] is not None
    assert recovered_body["runtime_health"] == "healthy"

    session.expire_all()
    stored_team = session.get(AgentTeam, team.id)
    assert stored_team is not None
    runtime_metadata = stored_team.default_task_policy["team_runtime"]
    assert runtime_metadata["iteration_count"] == 1
    assert runtime_metadata["last_heartbeat_at"]
    assert runtime_metadata["continued_from_iteration"]["iteration"] == 1
    assert len(docker.created_requests) == 1


def test_team_runtime_continue_clears_previous_worker_failure() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    session.add(manager)
    session.flush()
    now = datetime.now(UTC).isoformat()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Continue Clears Failure Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
        default_task_policy={
            "team_runtime": {
                "status": "running",
                "last_heartbeat_at": now,
                "heartbeat_status": "retrying",
                "last_worker_failure": {
                    "status": "retrying",
                    "error": "previous worker failure",
                },
                "last_iteration": {
                    "iteration": 4,
                    "status": "noop",
                    "summary": {},
                    "recorded_at": now,
                },
            }
        },
    )
    session.add(team)
    session.commit()

    before = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime",
        headers=_headers(owner.id),
    )
    continued = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/continue",
        headers=_headers(owner.id),
        json={"instruction": "recover after worker retry"},
    )

    assert before.status_code == 200
    assert before.json()["runtime_health"] == "degraded"
    assert continued.status_code == 200
    assert continued.json()["runtime_health"] == "healthy"
    session.expire_all()
    stored_team = session.get(AgentTeam, team.id)
    assert stored_team is not None
    runtime_metadata = stored_team.default_task_policy["team_runtime"]
    assert runtime_metadata["heartbeat_status"] == "running"
    assert "last_worker_failure" not in runtime_metadata
    assert runtime_metadata["continued_from_iteration"]["iteration"] == 4


def test_team_execution_loop_records_skipped_runtime_heartbeat() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    session.add(manager)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Paused Runtime Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
        default_task_policy={"team_runtime": {"status": "paused"}},
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=manager.id,
            team_role="project_manager",
            order_index=1,
        )
    )
    session.commit()

    skipped = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/execution-loop/run",
        headers=_headers(owner.id),
        json={"dry_run": False},
    )
    state = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime",
        headers=_headers(owner.id),
    )

    assert skipped.status_code == 200
    assert skipped.json()["status"] == "skipped"
    assert skipped.json()["summary"]["reason"] == "team_runtime_not_running"
    assert state.status_code == 200
    body = state.json()
    assert body["runtime_health"] == "paused"
    assert body["last_iteration"]["status"] == "skipped"
    assert body["last_iteration"]["summary"]["runtime_status"] == "paused"
    assert body["last_message_at"] is not None


def test_team_execution_loop_marks_runtime_stalled_after_repeated_noop() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    credential = ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Workspace OpenAI",
        provider="openai",
        api_key="sk-stall-provider",
        default_model="gpt-4.1-mini",
        base_url=None,
        is_default=True,
    )
    credential.health_status = "healthy"
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="PM",
        role="project_manager",
        model="workspace-default",
    )
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="Stall Runtime",
        status="running",
        connection_status="online",
        limits={},
        network_policy={},
        capabilities={},
    )
    session.add_all([manager, runtime])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Stall Detection Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
        default_task_policy={
            "team_runtime": {
                "status": "running",
                "workspace_runtime_id": str(runtime.id),
            }
        },
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=manager.id,
            team_role="project_manager",
            order_index=1,
        )
    )
    session.commit()

    for _ in range(3):
        response = client.post(
            f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/execution-loop/run",
            headers=_headers(owner.id),
            json={
                "dry_run": False,
                "apply_command_center_actions": False,
                "finalize_ready_tasks": False,
                "enqueue_runs": False,
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "noop"

    runtime_state = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime",
        headers=_headers(owner.id),
    )
    command_center = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/command-center",
        headers=_headers(owner.id),
    )
    console = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/operations-console",
        headers=_headers(owner.id),
    )

    assert runtime_state.status_code == 200
    runtime_body = runtime_state.json()
    assert runtime_body["runtime_health"] == "degraded"
    assert runtime_body["metadata"]["stall_count"] == 3
    assert runtime_body["metadata"]["stall_reason"] == "no_progress"
    assert runtime_body["metadata"]["stalled_at"]
    assert command_center.status_code == 200
    stall_action = next(
        item
        for item in command_center.json()["action_plan"]
        if item["action"] == "review_team_runtime_stall"
    )
    assert stall_action["source"] == "team_runtime"
    assert stall_action["action"] == "review_team_runtime_stall"
    assert stall_action["reason"] == "no_progress"
    assert stall_action["stall_count"] == 3
    assert console.status_code == 200
    readiness = console.json()["readiness"]
    assert readiness["status"] == "stalled"
    assert readiness["ready"] is False
    assert readiness["stall"]["count"] == 3
    assert readiness["next_operator_action"]["action"] == "review_team_runtime_stall"


def test_team_execution_loop_run_advances_actions_runs_and_finalization() -> None:
    queue_redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(queue_redis, RedisKeyBuilder("chaincloud"), "agent_runs", 0)
    client, session, docker = _client(queue=queue, include_docker=True)
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
    template = RuntimeTemplate(
        name="loop-runtime-template",
        image="python:3.12-slim",
        default_limits={
            "cpu_count": 1,
            "memory_mb": 512,
            "disk_mb": 1024,
            "timeout_seconds": 60,
        },
        default_network_policy={"disabled": True},
        created_at=datetime.now(UTC),
    )
    session.add_all([manager, developer, template])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Loop Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
    )
    session.add(team)
    session.flush()
    team.default_task_policy = {
        "team_runtime": {
            "status": "running",
            "runtime_template_id": str(template.id),
        }
    }
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
    created_runtime = session.scalar(
        select(WorkspaceRuntime).where(
            WorkspaceRuntime.workspace_id == workspace.id,
            WorkspaceRuntime.name == "Loop Team team runtime",
        )
    )
    assert created_runtime is not None
    assert created_runtime.status == "running"
    assert created_runtime.connection_status == "online"
    assert created_runtime.capabilities["team_runtime"]["team_id"] == str(team.id)
    assert len(docker.created_requests) == 1
    assert docker.started == ["container-1"]
    assert {run.runtime_id for run in scheduled_runs} == {created_runtime.id}
    queued_jobs = queue.peek()
    assert queued_jobs
    assert all(
        job.routing.get("workspace_runtime_id") == str(created_runtime.id)
        for job in queued_jobs
    )
    iteration_audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "team.execution_loop.iteration_ran",
        )
    )
    assert iteration_audit is not None
    assert iteration_audit.audit_metadata["finalized_task_count"] == 1
    runtime_sessions = session.scalars(
        select(PersistentAgentSession).where(
            PersistentAgentSession.workspace_id == workspace.id,
            PersistentAgentSession.agent_team_id == team.id,
        )
    ).all()
    assert {item.scope_type for item in runtime_sessions} == {"team_runtime", "team_agent"}
    runtime_messages = session.scalars(
        select(AgentMessage).where(
            AgentMessage.workspace_id == workspace.id,
            AgentMessage.message_type == "runtime_iteration",
        )
    ).all()
    assert len(runtime_messages) == 1
    assert runtime_messages[0].payload["status"] == "advanced"


def test_team_execution_loop_skips_enqueue_when_provider_readiness_blocked() -> None:
    queue_redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(queue_redis, RedisKeyBuilder("chaincloud"), "agent_runs", 0)
    client, session = _client(queue=queue)
    owner, workspace = _seed_workspace(session, role="owner")
    credential = ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Blocked provider",
        provider="openai-compatible",
        api_key="sk-loop-provider-blocked",
        default_model="gpt-4.1-mini",
        base_url="https://provider.example.test/v1",
        is_default=False,
    )
    credential.health_status = "unhealthy"
    credential.failure_count = 3
    developer = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
        model="workspace-default",
        model_provider_credential_id=credential.id,
    )
    session.add(developer)
    session.flush()
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="Blocked Loop Runtime",
        status="running",
        connection_status="online",
        limits={},
        network_policy={},
        capabilities={},
    )
    session.add(runtime)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Blocked Loop Team",
        team_type="software",
        manager_agent_profile_id=developer.id,
        default_task_policy={
            "team_runtime": {
                "status": "running",
                "workspace_runtime_id": str(runtime.id),
            }
        },
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=developer.id,
            team_role="developer",
        )
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Blocked loop task",
        status="running",
        priority=8,
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=developer.id,
        work_package_id="build",
        required_role="developer",
        title="Build blocked work",
        status="queued",
        order_index=10,
    )
    session.add(step)
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/execution-loop/run",
        headers=_headers(owner.id),
        json={
            "dry_run": False,
            "enqueue_runs": True,
            "finalize_ready_tasks": False,
            "reason": "blocked provider loop",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["scheduled_run_count"] == 0
    assert body["summary"]["scheduled_run_skip_reason"] == "provider_readiness_blocked"
    assert (
        body["command_center_actions"]["scheduled_run_skip_reason"]
        == "provider_readiness_blocked"
    )
    assert body["command_center_actions"]["scheduled_run_count"] == 0
    assert queue_redis.llen(RedisKeyBuilder("chaincloud").queue("agent_runs")) == 0
    session.expire_all()
    assert session.scalars(select(AgentRun)).all() == []
    stored_team = session.get(AgentTeam, team.id)
    assert stored_team is not None
    runtime_metadata = stored_team.default_task_policy["team_runtime"]
    assert runtime_metadata["last_iteration"]["summary"]["scheduled_run_skip_reason"] == (
        "provider_readiness_blocked"
    )
    iteration_audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "team.execution_loop.iteration_ran",
        )
    )
    assert iteration_audit is not None
    assert iteration_audit.audit_metadata["scheduled_run_skip_reason"] == (
        "provider_readiness_blocked"
    )
    assert "sk-loop-provider-blocked" not in str(body)
    assert "provider.example.test/v1" not in str(body)


def test_team_execution_loop_ignores_non_runtime_provider_blockers() -> None:
    queue_redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(queue_redis, RedisKeyBuilder("chaincloud"), "agent_runs", 0)
    client, session = _client(queue=queue)
    owner, workspace = _seed_workspace(session, role="owner")
    service = ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
    )
    healthy_credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Healthy provider",
        provider="openai-compatible",
        api_key="sk-loop-provider-healthy",
        default_model="gpt-4.1-mini",
        base_url="https://healthy-provider.example.test/v1",
        is_default=False,
    )
    unhealthy_credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Observer blocked provider",
        provider="openai-compatible",
        api_key="sk-loop-observer-blocked",
        default_model="gpt-4.1-mini",
        base_url="https://observer-provider.example.test/v1",
        is_default=False,
    )
    unhealthy_credential.health_status = "unhealthy"
    unhealthy_credential.failure_count = 3
    developer = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
        model="workspace-default",
        model_provider_credential_id=healthy_credential.id,
    )
    observer = AgentProfile(
        workspace_id=workspace.id,
        name="Observer",
        role="observer",
        model="workspace-default",
        model_provider_credential_id=unhealthy_credential.id,
    )
    session.add_all([developer, observer])
    session.flush()
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="Non Runtime Blocker Loop Runtime",
        status="running",
        connection_status="online",
        limits={},
        network_policy={},
        capabilities={},
    )
    session.add(runtime)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Non Runtime Blocker Team",
        team_type="software",
        manager_agent_profile_id=developer.id,
        default_task_policy={
            "team_runtime": {
                "status": "running",
                "workspace_runtime_id": str(runtime.id),
            }
        },
    )
    session.add(team)
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=developer.id,
                team_role="developer",
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=observer.id,
                team_role="observer",
                accepts_tasks=False,
            ),
        ]
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Runnable loop task",
        status="running",
        priority=8,
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=developer.id,
        work_package_id="build",
        required_role="developer",
        title="Build runnable work",
        status="queued",
        order_index=10,
    )
    session.add(step)
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/execution-loop/run",
        headers=_headers(owner.id),
        json={
            "dry_run": False,
            "enqueue_runs": True,
            "finalize_ready_tasks": False,
            "reason": "non runtime provider blocker loop",
        },
    )

    assert response.status_code == 200
    body = response.json()
    readiness = body["command_center_actions"]["summary"]["provider_readiness"]
    assert readiness["status"] == "degraded"
    assert readiness["blocked_member_count"] == 1
    assert readiness["runtime_blocked_member_count"] == 0
    assert readiness["runtime_participant_count"] == 1
    assert body["summary"]["scheduled_run_skip_reason"] is None
    assert body["summary"]["scheduled_run_count"] == 1
    assert body["command_center_actions"]["scheduled_run_skip_reason"] is None
    assert body["command_center_actions"]["scheduled_run_count"] == 1
    assert queue_redis.llen(RedisKeyBuilder("chaincloud").queue("agent_runs")) == 1
    session.expire_all()
    run = session.scalar(select(AgentRun).where(AgentRun.task_step_id == step.id))
    assert run is not None
    assert run.agent_profile_id == developer.id
    assert run.model == "gpt-4.1-mini"
    assert "sk-loop-provider-healthy" not in str(body)
    assert "sk-loop-observer-blocked" not in str(body)


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


def test_team_operator_action_reassigns_selected_specialist_step() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
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
    non_team_agent = AgentProfile(
        workspace_id=workspace.id,
        name="Non-team Developer",
        role="developer",
    )
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Reassign Team",
        team_type="software",
    )
    session.add_all([original_agent, replacement_agent, non_team_agent, team])
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=original_agent.id,
                team_role="developer",
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=replacement_agent.id,
                team_role="developer",
            ),
        ]
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Reassign blocked implementation",
        status="running",
        priority=8,
    )
    session.add(task)
    session.flush()
    blocked_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=original_agent.id,
        work_package_id="build",
        title="Build feature",
        status="blocked",
        order_index=10,
        dependencies={
            "blocked_reason": "worker_unavailable",
            "blocked_resource_keys": ["worker:cloud"],
            "after_step_ids": [],
        },
    )
    session.add(blocked_step)
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/operator-actions",
        headers=_headers(owner.id),
        json={
            "action": "reassign_step",
            "task_step_ids": [str(blocked_step.id)],
            "agent_profile_id": str(replacement_agent.id),
            "reason": "specialist unavailable",
            "metadata": {"api_key": "sk-team-reassign"},
        },
    )
    non_team_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/operator-actions",
        headers=_headers(owner.id),
        json={
            "action": "reassign_step",
            "task_step_ids": [str(blocked_step.id)],
            "agent_profile_id": str(non_team_agent.id),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "reassign_step"
    assert body["agent_profile_id"] == str(replacement_agent.id)
    assert body["status"] == "applied"
    assert body["applied_count"] == 1
    assert body["results"][0]["changed_step_ids"] == [str(blocked_step.id)]
    assert "sk-team-reassign" not in str(body)
    session.refresh(blocked_step)
    session.refresh(task)
    assert blocked_step.assigned_agent_profile_id == replacement_agent.id
    assert blocked_step.status == "queued"
    assert blocked_step.dependencies == {"after_step_ids": []}
    assert task.status == "running"
    assert non_team_response.status_code == 409
    assert non_team_response.json()["error"]["message"] == "Agent profile not found in team"
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "task.operator.reassign_step",
        )
    )
    assert audit is not None
    assert audit.audit_metadata["metadata"]["team_id"] == str(team.id)


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
        json={
            "name": "Frontend Dev",
            "role": "frontend_engineer",
            "model_api": "response",
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
    assert snapshot["members"][0]["agent"]["model_api"] == "responses"
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
            "model_api": "chat-completions",
            "is_default": True,
            "budget_metadata": {
                "limits": {"calls": 10},
                "usage": {"calls": 0},
                "api_key": "sk-budget-secret",
                "nested": {"token": "hidden", "label": "monthly"},
            },
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
    assert body["secret_metadata"] == {
        "storage": "hosted_encrypted",
        "provider": "hosted",
        "configured": True,
        "reference_kind": None,
        "reference_version": None,
        "encryption_key_id": "local",
        "fingerprint_configured": True,
        "rotation_state": "current",
        "raw_secret_exposed": False,
    }
    assert body["health_status"] == "unknown"
    assert body["failure_count"] == 0
    assert body["budget_metadata"] == {
        "limits": {"calls": 10},
        "usage": {"calls": 0},
        "model_api": "chat_completions",
        "nested": {"label": "monthly"},
    }
    assert body["model_api"] == "chat_completions"
    assert body["model_capability"] == {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "display_name": "GPT-4.1 mini",
        "capabilities": ["tools", "vision", "json_mode", "streaming"],
        "supports_tools": True,
        "supports_vision": True,
        "supports_json_mode": True,
        "supports_streaming": True,
        "context_window_tokens": 1_000_000,
        "notes": None,
    }
    assert body["last_success_at"] is None
    assert body["last_failure_at"] is None
    assert body["last_health_check_at"] is None
    assert body["last_failure_code"] is None
    assert body["last_failure_message"] is None
    assert body["scheduled_health_check"] == {
        "configured": False,
        "active_count": 0,
        "paused_count": 0,
        "next_run_at": None,
        "jobs": [],
    }
    assert "api_key" not in body
    assert "encrypted_api_key" not in body
    assert "sk-budget-secret" not in json.dumps(body)
    assert "hidden" not in json.dumps(body)
    assert body["base_url_configured"] is True
    assert body["base_url_host"] == "api.openai.com"
    assert listed.status_code == 200
    assert listed.json()["items"][0]["base_url_configured"] is True
    assert listed.json()["items"][0]["base_url_host"] == "api.openai.com"
    assert listed.json()["items"][0]["model_api"] == "chat_completions"
    assert listed.json()["items"][0]["model_capability"] == body["model_capability"]
    assert listed.json()["items"][0]["last_health_check_at"] is None
    assert listed.json()["items"][0]["scheduled_health_check"]["configured"] is False
    assert listed.json()["total"] == 1


def test_model_provider_health_check_updates_status_without_returning_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")

    async def fake_probe(target, *, probes, timeout_seconds):
        assert target.provider == "openai-compatible"
        assert target.model == "claude-opus-4-6"
        assert target.model_api == "chat_completions"
        assert target.api_key == "sk-health-api-secret"
        assert target.base_url == "https://dash.ovload.com/v1"
        assert probes == ("models", "inference")
        assert timeout_seconds == 5
        return ModelProviderHealthCheckResult(
            status="degraded",
            checks=(
                ModelProviderHealthCheck(
                    name="models",
                    status="passed",
                    metadata={"model_count": 4, "model_present": True},
                ),
                ModelProviderHealthCheck(
                    name="inference",
                    status="failed",
                    code="permission_denied",
                    message="Your request was blocked.",
                    metadata={"status_code": 403},
                ),
            ),
        )

    monkeypatch.setattr(model_provider_service_module, "probe_model_provider", fake_probe)
    credential = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
        json={
            "name": "Claude Gateway",
            "provider": "openai-compatible",
            "api_key": "sk-health-api-secret",
            "base_url": "https://dash.ovload.com/v1",
            "default_model": "claude-opus-4-6",
            "model_api": "chat-completions",
        },
    )
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/"
        f"{credential.json()['id']}/health-check",
        headers=_headers(owner.id),
        json={"probes": ["models", "inference"], "timeout_seconds": 5},
    )
    invalid = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/"
        f"{credential.json()['id']}/health-check",
        headers=_headers(owner.id),
        json={"probes": ["billing"]},
    )

    assert credential.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["credential"]["health_status"] == "degraded"
    assert body["credential"]["model_api"] == "chat_completions"
    assert body["credential"]["failure_count"] == 1
    assert body["credential"]["last_health_check_at"] == body["credential"]["last_failure_at"]
    assert body["credential"]["last_failure_code"] == "permission_denied"
    assert body["credential"]["last_failure_message"] == "Your request was blocked."
    assert body["credential"]["scheduled_health_check"]["configured"] is False
    assert body["checks"][0]["status"] == "passed"
    assert body["checks"][1]["code"] == "permission_denied"
    assert invalid.status_code == 400
    assert "sk-health-api-secret" not in json.dumps(body)
    assert "dash.ovload.com/v1" not in json.dumps(body)


def test_model_provider_capabilities_are_listed_without_secrets() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")

    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/model-provider-capabilities",
        headers=_headers(owner.id),
    )
    anthropic_tools = client.get(
        f"/api/v1/workspaces/{workspace.id}/model-provider-capabilities",
        headers=_headers(owner.id),
        params={"provider": "anthropic", "capability": "tools"},
    )

    assert listed.status_code == 200
    assert anthropic_tools.status_code == 200
    compatible = next(
        item for item in listed.json() if item["provider"] == "openai-compatible"
    )
    assert compatible["model_apis"] == ["responses", "chat_completions"]
    assert compatible["default_model_api"] is None
    assert anthropic_tools.json()
    assert {item["provider"] for item in anthropic_tools.json()} == {"anthropic"}
    assert all("tools" in item["capabilities"] for item in anthropic_tools.json())
    assert all(
        item["model_apis"] == ["anthropic_messages"]
        and item["default_model_api"] == "anthropic_messages"
        for item in anthropic_tools.json()
    )
    assert "api_key" not in json.dumps(listed.json())
    assert "base_url" not in json.dumps(listed.json())


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


def test_team_member_model_provider_can_be_bound_from_operations_context() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-provider-bind@example.com",
        slug="other-provider-bind",
    )
    credential = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
        json={
            "name": "Claude Gateway",
            "provider": "openai-compatible",
            "api_key": "sk-router",
            "base_url": "https://openrouter.ai/api/v1",
            "default_model": "anthropic/claude-sonnet",
        },
    )
    disabled = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
        json={
            "name": "Disabled",
            "provider": "openai",
            "api_key": "sk-disabled",
            "default_model": "gpt-4.1",
        },
    )
    foreign = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/model-provider-credentials",
        headers=_headers(other_owner.id),
        json={
            "name": "Foreign",
            "provider": "openai",
            "api_key": "sk-foreign",
            "default_model": "gpt-4.1",
        },
    )
    disabled_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/"
        f"{disabled.json()['id']}/disable",
        headers=_headers(owner.id),
    )
    agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Team Dev", "role": "developer"},
    )
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={"name": "Provider Ops", "team_type": "company"},
    )
    member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": agent.json()["id"],
            "team_role": "developer",
        },
    )
    persistent_session = PersistentAgentSession(
        workspace_id=workspace.id,
        session_key=f"{workspace.id}:team_agent:{team.json()['id']}:{agent.json()['id']}",
        scope_type="team_agent",
        scope_id=f"{team.json()['id']}:{agent.json()['id']}",
        agent_profile_id=UUID(agent.json()["id"]),
        agent_team_id=UUID(team.json()["id"]),
        openai_conversation_id="conv_old_provider",
        session_metadata={"source": "test"},
    )
    session.add(persistent_session)
    session.flush()
    session.add(
        PersistentAgentSessionItem(
            workspace_id=workspace.id,
            persistent_session_id=persistent_session.id,
            sequence=1,
            item={"role": "user", "content": "old provider context"},
        )
    )
    session.commit()

    bound = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/"
        f"{member.json()['id']}/model-provider",
        headers=_headers(owner.id),
        json={
            "model_provider_credential_id": credential.json()["id"],
            "model": "workspace-default",
        },
    )
    disabled_bind = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/"
        f"{member.json()['id']}/model-provider",
        headers=_headers(owner.id),
        json={"model_provider_credential_id": disabled.json()["id"]},
    )
    foreign_bind = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/"
        f"{member.json()['id']}/model-provider",
        headers=_headers(owner.id),
        json={"model_provider_credential_id": foreign.json()["id"]},
    )
    missing_member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/"
        f"{uuid4()}/model-provider",
        headers=_headers(owner.id),
        json={"model_provider_credential_id": credential.json()["id"]},
    )
    empty_update = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/"
        f"{member.json()['id']}/model-provider",
        headers=_headers(owner.id),
        json={},
    )
    model_only = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/"
        f"{member.json()['id']}/model-provider",
        headers=_headers(owner.id),
        json={"model": "anthropic/claude-opus", "reset_session": False},
    )
    model_api_only = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/"
        f"{member.json()['id']}/model-provider",
        headers=_headers(owner.id),
        json={"model_api": "response"},
    )
    clear_credential = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/"
        f"{member.json()['id']}/model-provider",
        headers=_headers(owner.id),
        json={"model_provider_credential_id": None, "reset_session": False},
    )

    assert credential.status_code == 201
    assert disabled_response.status_code == 200
    assert bound.status_code == 200
    assert bound.json()["model_provider_credential_id"] == credential.json()["id"]
    assert bound.json()["model"] == "workspace-default"
    assert bound.json()["model_provider"]["source"] == "agent_override"
    assert bound.json()["model_provider"]["credential_name"] == "Claude Gateway"
    assert bound.json()["model_provider"]["session_reset"] == {
        "reset_session_count": 1,
        "session_ids": [str(persistent_session.id)],
        "reason": "team_member_model_provider_updated",
    }
    assert disabled_bind.status_code == 400
    assert foreign_bind.status_code == 404
    assert missing_member.status_code == 404
    assert empty_update.status_code == 400
    assert "model_api" in str(empty_update.json())
    assert model_only.status_code == 200
    assert model_only.json()["model"] == "anthropic/claude-opus"
    assert model_only.json()["model_provider_credential_id"] == credential.json()["id"]
    assert model_api_only.status_code == 200
    assert model_api_only.json()["model_settings"]["model_api"] == "responses"
    assert model_api_only.json()["model_provider"]["model_api"] == "responses"
    assert model_api_only.json()["model_provider"]["session_reset"] == {
        "reset_session_count": 1,
        "session_ids": [str(persistent_session.id)],
        "reason": "team_member_model_provider_updated",
    }
    assert clear_credential.status_code == 200
    assert clear_credential.json()["model_provider_credential_id"] is None
    assert clear_credential.json()["model_settings"]["model_api"] == "responses"
    session.refresh(persistent_session)
    assert persistent_session.openai_conversation_id is None
    assert persistent_session.status == "active"
    assert persistent_session.session_metadata["last_reset"]["reason"] == (
        "team_member_model_provider_updated"
    )
    assert persistent_session.session_metadata["last_reset"]["metadata"]["before"][
        "model"
    ] == "anthropic/claude-opus"
    assert persistent_session.session_metadata["last_reset"]["metadata"]["after"][
        "model"
    ] == "anthropic/claude-opus"
    assert persistent_session.session_metadata["last_reset"]["metadata"]["before"][
        "model_api"
    ] is None
    assert persistent_session.session_metadata["last_reset"]["metadata"]["after"][
        "model_api"
    ] == "responses"
    assert session.scalar(
        select(func.count(PersistentAgentSessionItem.id)).where(
            PersistentAgentSessionItem.persistent_session_id == persistent_session.id
        )
    ) == 0
    provider_message = session.scalar(
        select(AgentMessage).where(
            AgentMessage.workspace_id == workspace.id,
            AgentMessage.agent_team_id == UUID(team.json()["id"]),
            AgentMessage.message_type == "team.runtime.model_provider.updated",
        )
    )
    assert provider_message is not None
    assert provider_message.thread_id is not None
    assert provider_message.payload["team_member_id"] == member.json()["id"]
    assert provider_message.payload["agent_profile_id"] == agent.json()["id"]
    assert provider_message.payload["reset_session_count"] == 1
    provider_messages = session.scalars(
        select(AgentMessage).where(
            AgentMessage.workspace_id == workspace.id,
            AgentMessage.agent_team_id == UUID(team.json()["id"]),
            AgentMessage.message_type == "team.runtime.model_provider.updated",
        )
    ).all()
    model_api_message = next(
        item
        for item in provider_messages
        if item.payload["after"]["model_api"] == "responses"
    )
    assert model_api_message.payload["before"]["model_api"] is None
    assert model_api_message.payload["after"]["model_api"] == "responses"
    assert model_api_message.payload["reset_session_count"] == 1
    timeline = TeamRuntimeTimelineService(session).timeline(
        workspace_id=workspace.id,
        team_id=UUID(team.json()["id"]),
        filters=TimelineFilters(
            event_type="team.runtime.model_provider.updated",
            include_queue=False,
        ),
    )
    assert timeline is not None
    assert timeline.summary.event_type_counts["team.runtime.model_provider.updated"] == 4
    assert timeline.items[0].event_type == "team.runtime.model_provider.updated"
    assert timeline.items[0].metadata["payload"]["reset_session_count"] == 0
    audit_timeline = TeamRuntimeTimelineService(session).timeline(
        workspace_id=workspace.id,
        team_id=UUID(team.json()["id"]),
        filters=TimelineFilters(
            source_type="audit_event",
            event_type="team.member_model_provider.updated",
            include_queue=False,
        ),
    )
    assert audit_timeline is not None
    assert audit_timeline.summary.event_type_counts[
        "team.member_model_provider.updated"
    ] == 4
    reset_audit_timeline_item = next(
        item
        for item in audit_timeline.items
        if item.metadata["audit_metadata"]["after"]["model_api"] == "responses"
    )
    assert reset_audit_timeline_item.resource_id is not None
    assert reset_audit_timeline_item.metadata["target_type"] == "agent_team_member"
    assert reset_audit_timeline_item.metadata["target_id"] == member.json()["id"]
    assert "sk-router" not in str(audit_timeline)
    assert "openrouter.ai/api/v1" not in str(audit_timeline)
    audits = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.workspace_id == workspace.id)
        .order_by(AuditEvent.created_at.desc())
    ).all()
    audit = next(
        (
            item
            for item in audits
            if item.action == "agent.updated"
            and item.audit_metadata.get("changed_fields")
            == ["model", "model_provider_credential_id"]
        ),
        None,
    )
    assert audit is not None
    assert audit.action == "agent.updated"
    assert audit.audit_metadata["changed_fields"] == [
        "model",
        "model_provider_credential_id",
    ]
    team_audit = next(
        (
            item
            for item in audits
            if item.action == "team.member_model_provider.updated"
            and item.audit_metadata.get("reset_session_count") == 1
            and item.audit_metadata["before"]["model"] == "gpt-4.1"
        ),
        None,
    )
    assert team_audit is not None
    assert team_audit.target_type == "agent_team_member"
    assert team_audit.target_id == str(member.json()["id"])
    assert team_audit.audit_metadata["team_id"] == team.json()["id"]
    assert team_audit.audit_metadata["agent_profile_id"] == agent.json()["id"]
    assert team_audit.audit_metadata["before"]["model"] == "gpt-4.1"
    assert team_audit.audit_metadata["after"]["model"] == "workspace-default"
    assert team_audit.audit_metadata["before"]["model_api"] is None
    assert team_audit.audit_metadata["after"]["model_api"] is None
    model_api_team_audit = next(
        (
            item
            for item in audits
            if item.action == "team.member_model_provider.updated"
            and item.audit_metadata["after"]["model_api"] == "responses"
            and item.audit_metadata["reset_session_count"] == 1
        ),
        None,
    )
    assert model_api_team_audit is not None
    assert model_api_team_audit.audit_metadata["changed"] is True
    assert model_api_team_audit.audit_metadata["reset_session_count"] == 1
    assert model_api_team_audit.audit_metadata["before"]["model_api"] is None
    assert model_api_team_audit.audit_metadata["after"]["model_api"] == "responses"
    assert "sk-router" not in str(team_audit.audit_metadata)
    assert "openrouter.ai/api/v1" not in str(team_audit.audit_metadata)


def test_team_member_model_provider_update_does_not_initialize_runtime_sessions() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    credential = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
        json={
            "name": "Claude API",
            "provider": "anthropic",
            "api_key": "sk-claude",
            "default_model": "claude-sonnet-4-6",
        },
    )
    agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Runtime Quiet Agent", "role": "developer"},
    )
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={"name": "Runtime Quiet Team", "team_type": "company"},
    )
    member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": agent.json()["id"],
            "team_role": "developer",
        },
    )

    assert credential.status_code == 201
    assert agent.status_code == 201
    assert team.status_code == 201
    assert member.status_code == 201
    team_id = UUID(team.json()["id"])
    assert session.scalar(
        select(func.count(PersistentAgentSession.id)).where(
            PersistentAgentSession.workspace_id == workspace.id,
            PersistentAgentSession.agent_team_id == team_id,
        )
    ) == 0

    updated = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team_id}/members/"
        f"{member.json()['id']}/model-provider",
        headers=_headers(owner.id),
        json={
            "model_provider_credential_id": credential.json()["id"],
            "model": "workspace-default",
        },
    )

    assert updated.status_code == 200
    assert updated.json()["model_provider_credential_id"] == credential.json()["id"]
    assert session.scalar(
        select(func.count(PersistentAgentSession.id)).where(
            PersistentAgentSession.workspace_id == workspace.id,
            PersistentAgentSession.agent_team_id == team_id,
        )
    ) == 0
    provider_message = session.scalar(
        select(AgentMessage).where(
            AgentMessage.workspace_id == workspace.id,
            AgentMessage.agent_team_id == team_id,
            AgentMessage.message_type == "team.runtime.model_provider.updated",
        )
    )
    assert provider_message is not None
    assert provider_message.thread_id is not None
    assert session.get(AgentMessageThread, provider_message.thread_id) is not None


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
        json={
            "name": "Second Updated",
            "default_model": "provider/new-default",
            "model_api": "response",
        },
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
    assert updated.json()["model_api"] == "responses"
    assert updated.json()["budget_metadata"]["model_api"] == "responses"
    assert updated.json()["base_url_configured"] is True
    assert updated.json()["base_url_host"] == "llm.example.test"
    assert updated.json()["model_capability"] == {
        "provider": "openai-compatible",
        "model": "*",
        "display_name": "OpenAI-compatible model",
        "capabilities": ["tools", "json_mode", "streaming"],
        "supports_tools": True,
        "supports_vision": False,
        "supports_json_mode": True,
        "supports_streaming": True,
        "context_window_tokens": None,
        "notes": "Actual support depends on the upstream gateway and selected model.",
    }
    assert rotated.status_code == 200
    assert rotated.json()["api_key_fingerprint"] != second.json()["api_key_fingerprint"]
    assert rotated.json()["secret_metadata"]["rotation_state"] == "current"
    assert rotated.json()["secret_metadata"]["raw_secret_exposed"] is False
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
    assert by_id[second.json()["id"]]["model_api"] == "responses"
    assert by_id[second.json()["id"]]["model_capability"] == updated.json()[
        "model_capability"
    ]


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
                    "provider": "openai-compatible",
                    "model": "backup-model",
                    "model_api": "response",
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
                        "provider": "openai",
                        "model": "primary-model",
                        "model_api": "chat-completions",
                        "credential_id": "credential-2",
                        "api_key": "sk-primary",
                        "base_url": "https://primary.example.test/v1",
                    },
                },
            ),
            AuditEvent(
                workspace_id=workspace.id,
                actor_type="user",
                actor_id=str(owner.id),
                user_id=owner.id,
                action="model_provider.request_failed",
                target_type="agent_run",
                target_id="run-3",
                created_at=datetime.now(UTC),
                audit_metadata={
                    "task_id": "task-3",
                    "task_step_id": "step-3",
                    "agent_profile_id": "agent-3",
                    "provider": "openai-compatible",
                    "model": "gpt-5.5",
                    "model_api": "chat_completions",
                    "credential_id": "credential-3",
                    "reason": {
                        "code": "InternalServerError",
                        "message": "upstream failed with sk-hidden token",
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
    failed_only = client.get(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/usage-audit"
        "?action=model_provider.request_failed",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert {item["run_id"] for item in payload["items"]} == {"run-1", "run-2", "run-3"}
    serialized = str(payload)
    assert "sk-secret" not in serialized
    assert "sk-primary" not in serialized
    assert "sk-hidden" not in serialized
    assert "secret.example.test" not in serialized
    assert "primary.example.test" not in serialized
    used = next(item for item in payload["items"] if item["run_id"] == "run-1")
    assert used["provider"] == "openai-compatible"
    assert used["model"] == "backup-model"
    assert used["model_api"] == "responses"
    assert used["credential_id"] == "credential-1"
    assert used["fallback_selected"] is True
    assert fallback_only.status_code == 200
    assert fallback_only.json()["total"] == 1
    assert fallback_only.json()["items"][0]["run_id"] == "run-2"
    assert fallback_only.json()["items"][0]["failed_provider"] == {
        "provider": "openai",
        "model": "primary-model",
        "model_api": "chat_completions",
        "credential_id": "credential-2",
    }
    assert failed_only.status_code == 200
    assert failed_only.json()["total"] == 1
    failed = failed_only.json()["items"][0]
    assert failed["run_id"] == "run-3"
    assert failed["provider"] == "openai-compatible"
    assert failed["model"] == "gpt-5.5"
    assert failed["model_api"] == "chat_completions"
    assert failed["credential_id"] == "credential-3"
    assert failed["reason"] == {
        "code": "InternalServerError",
        "message": "[redacted]",
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


def test_task_event_stream_reads_message_created_events_from_bus() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(
        redis=redis,
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
        blocking_timeout_seconds=0,
    )
    client, session = _client(queue)
    owner, workspace = _seed_workspace(session, role="owner")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Streaming task",
        status=TaskStatus.RUNNING.value,
    )
    session.add(task)
    session.commit()

    event_bus = RedisTaskEventBus(redis=redis, key_prefix="chaincloud")
    message = TaskMessageAppendService(session).append_for_task(
        task,
        message_type="agent.progress",
        body="Sensitive body should not be streamed in the bus event",
        payload={"authorization": "Bearer hidden", "progress": "drafting"},
    )
    session.commit()
    publish_summary = TaskEventOutboxPublisher(session, event_bus).publish_pending()
    outbox_event = session.scalar(
        select(TaskEventOutbox).where(TaskEventOutbox.task_id == task.id)
    )

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/events/stream"
        "?after_sequence=1&event_cursor=0-0&once=true",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    assert outbox_event is not None
    assert publish_summary.published == 1
    assert publish_summary.failed == 0
    assert "event: task.event" in response.text
    assert TASK_MESSAGE_CREATED_EVENT_TYPE in response.text
    assert str(message.id) in response.text
    task_events = _sse_data_events(response.text, "task.event")
    assert len(task_events) == 1
    task_event = task_events[0]
    assert task_event["id"] == task_event["stream"]["event_cursor"]
    assert task_event["event_id"] == str(outbox_event.event_id)
    assert task_event["outbox_id"] == str(outbox_event.id)
    assert task_event["payload"]["event_id"] == str(outbox_event.event_id)
    assert task_event["payload"]["outbox_id"] == str(outbox_event.id)
    assert "Sensitive body" not in response.text
    assert "Bearer hidden" not in response.text


def test_task_event_stream_replays_previously_published_redis_event() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(
        redis=redis,
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
        blocking_timeout_seconds=0,
    )
    client, session = _client(queue)
    owner, workspace = _seed_workspace(session, role="owner")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Streaming replay task",
        status=TaskStatus.RUNNING.value,
    )
    session.add(task)
    session.commit()
    event_id = RedisTaskEventBus(redis=redis, key_prefix="chaincloud").publish(
        workspace_id=workspace.id,
        task_id=task.id,
        event_type=TASK_MESSAGE_CREATED_EVENT_TYPE,
        payload={
            "message_id": str(uuid4()),
            "message_type": "agent.progress",
            "sequence": 7,
            "api_key": "sk-hidden",
            "nested": {"authorization": "Bearer hidden"},
        },
    )

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/events/stream"
        "?event_cursor=0-0&once=true",
        headers=_headers(owner.id),
    )
    resumed = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/events/stream"
        f"?event_cursor={event_id}&once=true",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    assert "event: task.event" in response.text
    assert event_id in response.text
    assert TASK_MESSAGE_CREATED_EVENT_TYPE in response.text
    task_events = _sse_data_events(response.text, "task.event")
    assert len(task_events) == 1
    task_event = task_events[0]
    assert task_event["event_id"] == event_id
    assert task_event["id"] == event_id
    assert task_event["stream"]["event_cursor"] == event_id
    assert task_event["payload"]["api_key"] == "[redacted]"
    assert task_event["payload"]["nested"]["authorization"] == "[redacted]"
    assert "outbox_id" not in task_event
    assert resumed.status_code == 200
    assert _sse_data_events(resumed.text, "task.event") == []
    assert "[redacted]" in response.text
    assert "sk-hidden" not in response.text
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
                "mode": "sdk_continuation_snapshot",
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
    raw_diagnostics = TaskExecutionDiagnosticsService(session).get_diagnostics(
        workspace_id=workspace.id,
        task_id=task.id,
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
    assert raw_diagnostics is not None
    assert "hidden-token" not in str(raw_diagnostics)
    assert "sk-run" not in str(raw_diagnostics)
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
    raw_observation = TaskObservationService(session).get_observation(
        workspace_id=workspace.id,
        task_id=task.id,
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
    assert message_payload == {"note": "draft", "api_key": "[redacted]"}
    assert review_payload["token"] == "[redacted]"
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
    assert raw_observation is not None
    assert "sk-hidden" not in str(raw_observation)
    assert "hidden-token" not in str(raw_observation)


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
    raw_diagnostics = TaskCorrectionDiagnosticsService(session).get_diagnostics(
        workspace_id=workspace.id,
        task_id=task.id,
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
    assert raw_diagnostics is not None
    assert "hidden-token" not in str(raw_diagnostics)
    assert "sk-hidden" not in str(raw_diagnostics)
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
    raw_timeline = TaskTimelineService(session).get_timeline(
        workspace_id=workspace.id,
        task_id=task.id,
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
    assert raw_timeline is not None
    assert "Bearer hidden" not in str(raw_timeline)
    assert "hidden-token" not in str(raw_timeline)
    assert "tool-secret" not in str(raw_timeline)


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


def test_workspace_invite_flow_accepts_and_redacts_tokens() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    invitee = User(email="invitee@example.com", display_name="Invitee")
    session.add(invitee)
    session.commit()

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(owner.id),
        json={
            "email": "Invitee@Example.com",
            "role": "admin",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "invitee_user_id": str(invitee.id),
        },
    )
    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(owner.id),
    )
    accepted = client.post(
        "/api/v1/workspaces/invites/accept",
        headers=_headers(invitee.id),
        json={"token": created.json()["token"]},
    )

    stored_invite = session.get(WorkspaceInvite, UUID(created.json()["id"]))
    assert stored_invite is not None
    assert created.status_code == 201
    assert created.json()["email"] == "invitee@example.com"
    assert created.json()["invitee_user_id"] == str(invitee.id)
    assert created.json()["token"].startswith("ccwi_")
    assert stored_invite.token_hash != created.json()["token"]
    assert stored_invite.fingerprint == created.json()["fingerprint"]
    assert listed.status_code == 200
    assert listed.json()["items"][0]["fingerprint"] == created.json()["fingerprint"]
    assert "token" not in listed.json()["items"][0]
    assert "token_hash" not in listed.json()["items"][0]
    assert created.json()["token"] not in str(listed.json())
    assert accepted.status_code == 200
    assert accepted.json()["invite"]["status"] == "accepted"
    assert accepted.json()["member"]["workspace_id"] == str(workspace.id)
    assert accepted.json()["member"]["user_id"] == str(invitee.id)
    assert accepted.json()["member"]["role"] == "admin"

    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.workspace_id == workspace.id)
        .order_by(AuditEvent.created_at.asc())
    ).all()
    assert [event.action for event in events] == [
        "workspace.invite_created",
        "workspace.invite_accepted",
    ]
    assert events[0].audit_metadata["fingerprint"] == created.json()["fingerprint"]
    assert "token_hash" not in str(events[0].audit_metadata)
    security = session.scalars(
        select(SecurityEvent).where(SecurityEvent.workspace_id == workspace.id)
    ).all()
    assert [event.action for event in security] == ["workspace.invite.accepted"]


def test_workspace_invite_accept_restores_disabled_member() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    invitee = User(email="restore-invitee@example.com", display_name="Restore Invitee")
    disabled_member = WorkspaceMember(
        workspace=workspace,
        user=invitee,
        role="viewer",
        status="disabled",
    )
    session.add_all([invitee, disabled_member])
    session.commit()

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(owner.id),
        json={
            "email": invitee.email,
            "role": "operator",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "invitee_user_id": str(invitee.id),
        },
    )
    accepted = client.post(
        "/api/v1/workspaces/invites/accept",
        headers=_headers(invitee.id),
        json={"token": created.json()["token"]},
    )

    assert created.status_code == 201
    assert accepted.status_code == 200
    assert accepted.json()["member"]["id"] == str(disabled_member.id)
    assert accepted.json()["member"]["role"] == "operator"
    assert accepted.json()["member"]["status"] == "active"


def test_workspace_invite_rejects_revoked_expired_and_wrong_user_tokens() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    invitee = User(email="secure-invitee@example.com", display_name="Secure Invitee")
    wrong_user = User(email="wrong-invitee@example.com", display_name="Wrong Invitee")
    session.add_all([invitee, wrong_user])
    session.commit()

    revoked = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(owner.id),
        json={
            "email": invitee.email,
            "role": "viewer",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "invitee_user_id": str(invitee.id),
        },
    )
    revoked_token = revoked.json()["token"]
    revoked_response = client.delete(
        f"/api/v1/workspaces/{workspace.id}/invites/{revoked.json()['id']}",
        headers=_headers(owner.id),
    )
    revoked_accept = client.post(
        "/api/v1/workspaces/invites/accept",
        headers=_headers(invitee.id),
        json={"token": revoked_token},
    )

    wrong_user_invite = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(owner.id),
        json={
            "email": invitee.email,
            "role": "viewer",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "invitee_user_id": str(invitee.id),
        },
    )
    wrong_user_accept = client.post(
        "/api/v1/workspaces/invites/accept",
        headers=_headers(wrong_user.id),
        json={"token": wrong_user_invite.json()["token"]},
    )
    expired_invite = session.get(WorkspaceInvite, UUID(wrong_user_invite.json()["id"]))
    assert expired_invite is not None
    expired_invite.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    expired_accept = client.post(
        "/api/v1/workspaces/invites/accept",
        headers=_headers(invitee.id),
        json={"token": wrong_user_invite.json()["token"]},
    )

    assert revoked_response.status_code == 200
    assert revoked_response.json()["status"] == "revoked"
    assert revoked_accept.status_code == 409
    assert wrong_user_accept.status_code == 403
    assert expired_accept.status_code == 409
    security_actions = [
        event.action
        for event in session.scalars(select(SecurityEvent).order_by(SecurityEvent.created_at))
    ]
    assert security_actions == [
        "workspace.invite.accept_rejected",
        "workspace.invite.accept_rejected",
        "workspace.invite.accept_rejected",
    ]
    rejected_events = session.scalars(
        select(SecurityEvent).order_by(SecurityEvent.created_at)
    ).all()
    assert [event.workspace_id for event in rejected_events] == [
        workspace.id,
        workspace.id,
        workspace.id,
    ]


def test_workspace_invite_rejects_viewers_and_cross_workspace_revokes() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="invite-other-owner@example.com",
        slug="invite-other-owner",
    )
    viewer = User(email="invite-viewer@example.com", display_name="Invite Viewer")
    invitee = User(email="invite-target@example.com", display_name="Invite Target")
    session.add_all(
        [
            viewer,
            invitee,
            WorkspaceMember(workspace=workspace, user=viewer, role="viewer"),
        ]
    )
    session.commit()

    viewer_create = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(viewer.id),
        json={
            "email": invitee.email,
            "role": "operator",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    other_invite = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/invites",
        headers=_headers(other_owner.id),
        json={
            "email": invitee.email,
            "role": "viewer",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    cross_workspace_revoke = client.delete(
        f"/api/v1/workspaces/{workspace.id}/invites/{other_invite.json()['id']}",
        headers=_headers(owner.id),
    )

    assert viewer_create.status_code == 403
    assert other_invite.status_code == 201
    assert cross_workspace_revoke.status_code == 404


def test_workspace_invite_does_not_auto_bind_email_to_platform_user() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    invitee = User(email="email-only-invitee@example.com", display_name="Email Only")
    session.add(invitee)
    session.commit()

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(owner.id),
        json={
            "email": invitee.email,
            "role": "viewer",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )

    assert created.status_code == 201
    assert created.json()["invitee_user_id"] is None


def test_workspace_invite_rejects_duplicate_active_invite_but_allows_after_revoke() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    payload = {
        "email": "duplicate-invitee@example.com",
        "role": "viewer",
        "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    }

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(owner.id),
        json=payload,
    )
    duplicate = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(owner.id),
        json=payload,
    )
    revoked = client.delete(
        f"/api/v1/workspaces/{workspace.id}/invites/{created.json()['id']}",
        headers=_headers(owner.id),
    )
    recreated = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(owner.id),
        json=payload,
    )

    assert created.status_code == 201
    assert duplicate.status_code == 409
    assert revoked.status_code == 200
    assert recreated.status_code == 201


def test_workspace_invite_list_persists_expired_status() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(owner.id),
        json={
            "email": "expired-on-list@example.com",
            "role": "viewer",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    invite = session.get(WorkspaceInvite, UUID(created.json()["id"]))
    assert invite is not None
    invite.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()

    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(owner.id),
    )
    session.expire_all()
    stored = session.get(WorkspaceInvite, UUID(created.json()["id"]))

    assert listed.status_code == 200
    assert listed.json()["items"][0]["status"] == "expired"
    assert stored is not None
    assert stored.status == "expired"


def test_workspace_admin_cannot_grant_or_manage_owner_role() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    admin = User(email="member-admin@example.com", display_name="Member Admin")
    target = User(email="owner-target@example.com", display_name="Owner Target")
    session.add_all(
        [
            admin,
            target,
            WorkspaceMember(workspace=workspace, user=admin, role="admin"),
        ]
    )
    session.commit()
    owner_member = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == owner.id,
        )
    )
    assert owner_member is not None

    owner_invite = client.post(
        f"/api/v1/workspaces/{workspace.id}/invites",
        headers=_headers(admin.id),
        json={
            "email": "owner-invite@example.com",
            "role": "owner",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    owner_member_create = client.post(
        f"/api/v1/workspaces/{workspace.id}/members",
        headers=_headers(admin.id),
        json={"user_id": str(target.id), "role": "owner"},
    )
    owner_member_update = client.patch(
        f"/api/v1/workspaces/{workspace.id}/members/{owner_member.id}",
        headers=_headers(admin.id),
        json={"role": "admin"},
    )
    owner_member_disable = client.delete(
        f"/api/v1/workspaces/{workspace.id}/members/{owner_member.id}",
        headers=_headers(admin.id),
    )

    assert owner_invite.status_code == 403
    assert owner_member_create.status_code == 403
    assert owner_member_update.status_code == 403
    assert owner_member_disable.status_code == 403


def test_workspace_member_api_manages_members_and_records_audit() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    user = User(email="managed-member@example.com", display_name="Managed Member")
    session.add(user)
    session.commit()

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/members",
        headers=_headers(owner.id),
        json={"user_id": str(user.id), "role": "admin"},
    )
    updated = client.patch(
        f"/api/v1/workspaces/{workspace.id}/members/{created.json()['id']}",
        headers=_headers(owner.id),
        json={"role": "operator", "status": "active"},
    )
    disabled = client.delete(
        f"/api/v1/workspaces/{workspace.id}/members/{created.json()['id']}",
        headers=_headers(owner.id),
    )

    assert created.status_code == 201
    assert created.json()["workspace_id"] == str(workspace.id)
    assert created.json()["user_id"] == str(user.id)
    assert created.json()["role"] == "admin"
    assert created.json()["status"] == "active"
    assert updated.status_code == 200
    assert updated.json()["role"] == "operator"
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.workspace_id == workspace.id)
        .order_by(AuditEvent.created_at.asc())
    ).all()
    assert [event.action for event in events] == [
        "workspace.member_created",
        "workspace.member_updated",
        "workspace.member_disabled",
    ]
    assert events[0].audit_metadata["after"]["role"] == "admin"
    assert events[1].audit_metadata["before"]["role"] == "admin"
    assert events[1].audit_metadata["after"]["role"] == "operator"
    assert events[2].audit_metadata["after"]["status"] == "disabled"


def test_workspace_member_api_rejects_viewers_and_cross_workspace_targets() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-member-owner@example.com",
        slug="other-member-owner",
    )
    viewer = User(email="workspace-viewer@example.com", display_name="Workspace Viewer")
    target_user = User(email="target-member@example.com", display_name="Target Member")
    session.add_all(
        [
            viewer,
            target_user,
            WorkspaceMember(workspace=workspace, user=viewer, role="viewer"),
        ]
    )
    session.commit()
    foreign_member = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == other_workspace.id,
            WorkspaceMember.user_id == other_owner.id,
        )
    )
    assert foreign_member is not None

    viewer_create = client.post(
        f"/api/v1/workspaces/{workspace.id}/members",
        headers=_headers(viewer.id),
        json={"user_id": str(target_user.id), "role": "operator"},
    )
    cross_workspace_member = client.patch(
        f"/api/v1/workspaces/{workspace.id}/members/{foreign_member.id}",
        headers=_headers(owner.id),
        json={"role": "viewer"},
    )
    cross_workspace_path = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/members",
        headers=_headers(owner.id),
        json={"user_id": str(target_user.id), "role": "operator"},
    )

    assert viewer_create.status_code == 403
    assert cross_workspace_member.status_code == 404
    assert cross_workspace_path.status_code == 403


def test_workspace_member_api_protects_last_active_owner() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    owner_member = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == owner.id,
        )
    )
    assert owner_member is not None

    demote_last_owner = client.patch(
        f"/api/v1/workspaces/{workspace.id}/members/{owner_member.id}",
        headers=_headers(owner.id),
        json={"role": "admin"},
    )
    disable_last_owner = client.delete(
        f"/api/v1/workspaces/{workspace.id}/members/{owner_member.id}",
        headers=_headers(owner.id),
    )

    second_owner = User(email="second-owner@example.com", display_name="Second Owner")
    session.add(second_owner)
    session.commit()
    created_second_owner = client.post(
        f"/api/v1/workspaces/{workspace.id}/members",
        headers=_headers(owner.id),
        json={"user_id": str(second_owner.id), "role": "owner"},
    )
    demote_first_owner = client.patch(
        f"/api/v1/workspaces/{workspace.id}/members/{owner_member.id}",
        headers=_headers(owner.id),
        json={"role": "admin"},
    )
    disable_remaining_owner = client.delete(
        f"/api/v1/workspaces/{workspace.id}/members/{created_second_owner.json()['id']}",
        headers=_headers(second_owner.id),
    )

    assert demote_last_owner.status_code == 409
    assert disable_last_owner.status_code == 409
    assert created_second_owner.status_code == 201
    assert demote_first_owner.status_code == 200
    assert demote_first_owner.json()["role"] == "admin"
    assert disable_remaining_owner.status_code == 409


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


def _client(
    queue: RedisQueue | None = None,
    *,
    include_docker: bool = False,
) -> tuple[TestClient, Session] | tuple[TestClient, Session, FakeDockerClient]:
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
    redis = queue.redis if queue is not None else fakeredis.FakeRedis(decode_responses=True)

    docker = FakeDockerClient()
    app = create_app(
        Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            database_url="sqlite+pysqlite:///:memory:",
            runtime_allowed_images=["python:3.12-slim"],
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
    app.dependency_overrides[get_docker_runtime_client] = lambda: docker
    worker_queue = queue or RedisQueue(
        redis=redis,
        keys=RedisKeyBuilder(app.state.settings.redis_key_prefix),
        queue_name=app.state.settings.worker_queue_name,
        blocking_timeout_seconds=0,
    )
    app.dependency_overrides[get_worker_queue] = lambda: worker_queue
    client = TestClient(app)
    if include_docker:
        return client, session, docker
    return client, session


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


def _sse_data_events(text: str, event_name: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for block in text.strip().split("\n\n"):
        name: str | None = None
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            if line.startswith("data: "):
                data_lines.append(line.removeprefix("data: "))
        if name == event_name and data_lines:
            events.append(json.loads("\n".join(data_lines)))
    return events


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
