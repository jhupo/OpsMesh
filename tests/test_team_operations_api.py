"""Workspace-scoped product API verification on PostgreSQL."""

import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.agents.providers.credentials import ModelProviderCredentialCommandService
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.users.models import User
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.state import RunStatus
from opsmesh.orchestration.tasks.models import Task, TaskMessage, TaskStep
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.temporal.contracts import DispatchRequest, ExecutionKind
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.teams.management.models import AgentTeam, AgentTeamMember
from opsmesh.workspaces.members.models import WorkspaceMember
from opsmesh.workspaces.quotas.models import WorkspaceQuota
from tests.fixtures.team_orchestration import run_team_request
from tests.fixtures.workspace_api import _client, _headers, _seed_workspace


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
            "responsibilities": ["Break down requirements", "Review delivery"],
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
            "responsibilities": ["Implement UI", "Fix frontend defects"],
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
    assert developer_body["responsibilities"] == ["Implement UI", "Fix frontend defects"]
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
        json={"name": "PM", "role": "project_manager", "model_settings": {"api_key": "sk-hidden"}},
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
        f"/api/v1/workspaces/{workspace.id}/teams/{uuid4()}/org-chart", headers=_headers(owner.id)
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
    assert missing.status_code == 403
    assert cycle_response.status_code == 200
    assert {manager_member.json()["id"], developer_member.json()["id"]} <= set(
        cycle_response.json()["cycle_member_ids"]
    )


def test_team_member_update_rejects_indirect_reporting_cycle() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    ceo_agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "CEO", "role": "ceo"},
    )
    cto_agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "CTO", "role": "cto"},
    )
    lead_agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Engineering Lead", "role": "team_lead"},
    )
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={"name": "Mature Org", "team_type": "company"},
    )
    ceo_member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": ceo_agent.json()["id"],
            "team_role": "ceo",
            "department": "Executive",
            "max_concurrent_tasks": 1,
        },
    )
    cto_member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": cto_agent.json()["id"],
            "reports_to_member_id": ceo_member.json()["id"],
            "team_role": "cto",
            "department": "Engineering",
            "max_concurrent_tasks": 1,
        },
    )
    lead_member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={
            "agent_profile_id": lead_agent.json()["id"],
            "reports_to_member_id": cto_member.json()["id"],
            "team_role": "team_lead",
            "department": "Engineering",
            "max_concurrent_tasks": 2,
        },
    )
    cycle = client.patch(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/{ceo_member.json()['id']}",
        headers=_headers(owner.id),
        json={"reports_to_member_id": lead_member.json()["id"]},
    )
    assert ceo_member.status_code == 201
    assert cto_member.status_code == 201
    assert lead_member.status_code == 201
    assert cycle.status_code == 400
    assert cycle.json()["error"]["code"] == "bad_request"


def test_team_execution_overview_reports_workload_and_attention_items() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session, role="owner", email="other-team-overview@example.com", slug="other-team-overview"
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
                review_policy={"mode": "agent_planning"},
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
                review_policy={"mode": "agent_planning"},
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
                review_policy={"mode": "final_acceptance"},
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
        f"/api/v1/workspaces/{workspace.id}/teams/{team_id}/execution-overview?include_completed=true",
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
    assert body["summary"]["risk_counts"] == {"critical": 0, "high": 1, "medium": 0, "low": 0}
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
    intervention_plan = {item["action"]: item for item in body["summary"]["intervention_plan"]}
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
            "POST /api/v1/workspaces/{workspace_id}/tasks/{task_id}/talent-market/recommendations"
        ),
        "payload_template": {"max_candidates_per_role": 3},
        "reason_codes": ["staffing_gap", "specialist_steps_incomplete", "risk:high"],
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
    assert task["attention_score"] == 165
    assert set(task["recommended_actions"]) == {"monitor_specialist_execution"}
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
    assert not_found.status_code == 403
    assert forbidden.status_code == 403
    serialized = str(include_completed.json())
    assert "sk-manager-overview" not in serialized
    assert "sk-task-overview" not in serialized
    assert "run-hidden" not in serialized
    assert "Approved body should stay private." not in serialized
    assert "sk-approved-overview" not in serialized


def test_team_command_center_aggregates_queues_actions_and_preserves_scope() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner", with_provider=True)
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="other-command-center@example.com", slug="other-command-center"
    )
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="PM",
        role="project_manager",
        model_settings={"api_key": "sk-command-manager"},
    )
    developer = AgentProfile(workspace_id=workspace.id, name="Developer", role="developer")
    replacement_developer = AgentProfile(
        workspace_id=workspace.id, name="Replacement Developer", role="developer"
    )
    observer = AgentProfile(
        workspace_id=workspace.id, name="Observer", role="qa", model="gpt-4.1-mini"
    )
    session.add_all([manager, developer, replacement_developer, observer])
    session.flush()
    unhealthy_credential = ModelProviderCredentialCommandService(
        session, SecretEncryptionService(secret="unit-test-secret", key_id="test-key")
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Blocked Provider",
        provider="openai-compatible",
        api_key="sk-command-provider-blocked",
        default_model="gpt-4.1-mini",
        base_url="https://provider.example.test/v1",
        is_default=False,
        budget_metadata={"model_api": "chat_completions"},
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
        workspace_id=other_workspace.id, name="Foreign Command Team", team_type="software"
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
        review_policy={"mode": "final_acceptance"},
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
                review_policy={"mode": "agent_planning"},
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
                "revision_requests": [{"work_package_id": "build", "instruction": "Add API tests"}],
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
        "model_provider_unknown": 3,
        "model_provider_health_check_not_scheduled": 3,
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
    assert manager_readiness["model_capability"] is None
    assert manager_readiness["model_capability"] is None
    assert manager_readiness["model_capability"] is None
    assert developer_readiness["readiness_status"] == "blocked"
    assert developer_readiness["provider"] == "openai-compatible"
    assert (
        developer_readiness["credential_reference"]
        == f"model_provider_credentials:{unhealthy_credential.id}"
    )
    assert developer_readiness["model"] == "gpt-4.1-mini"
    assert developer_readiness["model_api"] == "chat_completions"
    assert developer_readiness["model_apis"] == ["responses", "chat_completions"]
    assert developer_readiness["default_model_api"] is None
    assert developer_readiness["model_capability"] is None
    assert developer_readiness["credential_health_status"] == "unhealthy"
    assert developer_readiness["budget_exhausted"] is False
    assert developer_readiness["reasons"] == ["model_provider_unhealthy"]
    assert observer_readiness["readiness_status"] == "degraded"
    assert observer_readiness["runtime_participant"] is False
    assert observer_readiness["runtime_degraded"] is False
    assert observer_readiness["warnings"] == [
        "model_provider_unknown",
        "model_provider_health_check_not_scheduled",
    ]
    assert observer_readiness["model_capability"] is None
    assert observer_readiness["model_capability"] is None
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
    assert (
        provider_actions[0]["credential_reference"]
        == f"model_provider_credentials:{unhealthy_credential.id}"
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
    reassign_actions = [item for item in overview_actions if item["action"] == "reassign_step"]
    assert len(reassign_actions) == 1
    assert reassign_actions[0]["task_step_ids"] == [str(blocked_step.id)]
    assert reassign_actions[0]["agent_profile_id"] == str(replacement_developer.id)
    assert reassign_actions[0]["current_agent_profile_id"] == str(developer.id)
    assert (
        reassign_actions[0]["agent_profile_id"] != reassign_actions[0]["current_agent_profile_id"]
    )
    assert any(item["action"] == "schedule_downstream_steps" for item in body["action_plan"])
    assert any(item["action"] == "request_manager_review" for item in body["action_plan"])
    assert any(item["action"] == "start_team_runtime" for item in body["action_plan"])
    assert missing.status_code == 403
    assert foreign_team_response.status_code == 403
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
        json={"dry_run": True, "metadata": {"token": "hidden-dry-run-token"}},
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
            AgentRun.workspace_id == workspace.id, AgentRun.task_id == other_local_task.id
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


def test_workspace_team_command_center_aggregates_active_teams_and_agent_load() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-workspace-command@example.com",
        slug="other-workspace-command",
    )
    shared_agent = AgentProfile(
        workspace_id=workspace.id, name="Shared Developer", role="developer"
    )
    designer = AgentProfile(workspace_id=workspace.id, name="Designer", role="designer")
    foreign_agent = AgentProfile(
        workspace_id=other_workspace.id, name="Foreign Agent", role="developer"
    )
    session.add_all([shared_agent, designer, foreign_agent])
    session.flush()
    platform_team = AgentTeam(workspace_id=workspace.id, name="Platform", team_type="engineering")
    product_team = AgentTeam(workspace_id=workspace.id, name="Product", team_type="product")
    inactive_team = AgentTeam(
        workspace_id=workspace.id, name="Inactive Team", team_type="engineering", status="inactive"
    )
    foreign_team = AgentTeam(
        workspace_id=other_workspace.id, name="Foreign Team", team_type="engineering"
    )
    session.add_all([platform_team, product_team, inactive_team, foreign_team])
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=platform_team.id,
                agent_profile_id=shared_agent.id,
                team_role="developer",
                max_concurrent_tasks=1,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=product_team.id,
                agent_profile_id=shared_agent.id,
                team_role="developer",
                max_concurrent_tasks=1,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=product_team.id,
                agent_profile_id=designer.id,
                team_role="designer",
                max_concurrent_tasks=2,
            ),
            AgentTeamMember(
                workspace_id=other_workspace.id,
                agent_team_id=foreign_team.id,
                agent_profile_id=foreign_agent.id,
                team_role="developer",
                max_concurrent_tasks=1,
            ),
        ]
    )
    platform_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=platform_team.id,
        title="Scale control plane",
        status="running",
        priority=8,
        domain_type="platform",
    )
    product_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=product_team.id,
        title="Ship project board",
        status="running",
        priority=7,
        domain_type="product",
    )
    support_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=platform_team.id,
        title="Triage incident",
        status="running",
        priority=9,
        domain_type="platform",
    )
    inactive_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=inactive_team.id,
        title="Inactive team task",
        status="running",
        priority=10,
    )
    foreign_task = Task(
        workspace_id=other_workspace.id,
        created_by_user_id=other_owner.id,
        agent_team_id=foreign_team.id,
        title="Foreign project",
        status="running",
        priority=10,
    )
    session.add_all([platform_task, product_task, support_task, inactive_task, foreign_task])
    session.flush()
    platform_step = TaskStep(
        workspace_id=workspace.id,
        task_id=platform_task.id,
        assigned_agent_profile_id=shared_agent.id,
        work_package_id="build",
        required_role="developer",
        title="Build scale path",
        status="running",
        order_index=10,
    )
    product_step = TaskStep(
        workspace_id=workspace.id,
        task_id=product_task.id,
        assigned_agent_profile_id=shared_agent.id,
        work_package_id="build",
        required_role="developer",
        title="Build board",
        status="running",
        order_index=10,
    )
    blocked_design_step = TaskStep(
        workspace_id=workspace.id,
        task_id=product_task.id,
        work_package_id="design",
        required_role="researcher",
        required_skills=["research"],
        title="Research board",
        status="queued",
        order_index=20,
    )
    support_step = TaskStep(
        workspace_id=workspace.id,
        task_id=support_task.id,
        assigned_agent_profile_id=shared_agent.id,
        work_package_id="triage",
        required_role="developer",
        title="Triage incident",
        status="running",
        order_index=10,
    )
    session.add_all(
        [
            platform_step,
            product_step,
            blocked_design_step,
            support_step,
            TaskStep(
                workspace_id=workspace.id,
                task_id=inactive_task.id,
                assigned_agent_profile_id=shared_agent.id,
                title="Should not aggregate",
                status="running",
            ),
            TaskStep(
                workspace_id=other_workspace.id,
                task_id=foreign_task.id,
                assigned_agent_profile_id=foreign_agent.id,
                title="Foreign step",
                status="running",
            ),
        ]
    )
    session.flush()
    session.add_all(
        [
            AgentRun(
                workspace_id=workspace.id,
                task_id=platform_task.id,
                task_step_id=platform_step.id,
                agent_profile_id=shared_agent.id,
                status=RunStatus.RUNNING.value,
                input={"token": "hidden-workspace-command-token"},
            ),
            AgentRun(
                workspace_id=workspace.id,
                task_id=product_task.id,
                task_step_id=product_step.id,
                agent_profile_id=shared_agent.id,
                status=RunStatus.QUEUED.value,
                input={},
            ),
            AgentRun(
                workspace_id=workspace.id,
                task_id=support_task.id,
                task_step_id=support_step.id,
                agent_profile_id=shared_agent.id,
                status=RunStatus.RUNNING.value,
                input={},
            ),
            AgentRun(
                workspace_id=other_workspace.id,
                task_id=foreign_task.id,
                agent_profile_id=foreign_agent.id,
                status=RunStatus.RUNNING.value,
                input={},
            ),
        ]
    )
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/command-center", headers=_headers(owner.id)
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/command-center", headers=_headers(other_owner.id)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["summary"]["active_team_count"] == 2
    assert body["summary"]["total_tasks"] == 3
    assert body["summary"]["employee_count"] == 2
    assert {item["team_name"] for item in body["team_summaries"]} == {"Platform", "Product"}
    assert {item["title"] for item in body["project_task_items"]} == {
        "Scale control plane",
        "Ship project board",
        "Triage incident",
    }
    shared_load = next(
        item for item in body["employee_load"] if item["agent_profile_id"] == str(shared_agent.id)
    )
    assert shared_load["team_count"] == 2
    assert shared_load["active_team_count"] == 2
    assert shared_load["total_capacity"] == 2
    assert shared_load["active_task_count"] == 3
    assert shared_load["at_capacity"] is True
    assert shared_load["overloaded"] is True
    assert shared_load["utilization"] == 1.5
    assert set(shared_load["active_task_ids"]) == {
        str(platform_task.id),
        str(product_task.id),
        str(support_task.id),
    }
    assert "agent_cross_team_capacity_pressure" in shared_load["blocked_reasons"]
    reasons = {item["code"]: item for item in body["blocked_reasons"]}
    assert "agent_over_capacity" in reasons
    assert "agent_cross_team_capacity_pressure" in reasons
    assert reasons["agent_cross_team_capacity_pressure"]["agent_profile_ids"] == [
        str(shared_agent.id)
    ]
    actions = {item["action"]: item for item in body["cross_project_action_plan"]}
    assert actions["redistribute_agent_work"]["agent_profile_id"] == str(shared_agent.id)
    assert set(actions["redistribute_agent_work"]["team_ids"]) == {
        str(platform_team.id),
        str(product_team.id),
    }
    assert "Foreign project" not in str(body)
    assert "Inactive team task" not in str(body)
    assert "hidden-workspace-command-token" not in str(body)
    assert forbidden.status_code == 403


def test_team_command_center_apply_reports_scheduler_blocked_reasons() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner", with_provider=True)
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
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id, name="Quota test runtime", status="active"
    )
    session.add(runtime)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Blocked Scheduler Team",
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
                max_concurrent_tasks=2,
                order_index=2,
            ),
        ]
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, owner.id),
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
    assert body["scheduled_run_blocked_reasons"] == {"workspace_quota_exceeded:active_runs": 1}
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
    assert blocked_step.dependencies["blocked_reason"] == "workspace_quota_exceeded:active_runs"
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
    owner, workspace = _seed_workspace(session, role="owner", with_provider=True)
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
                max_concurrent_tasks=2,
                order_index=2,
            ),
        ]
    )
    first_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, owner.id),
        agent_team_id=team.id,
        title="First delivery",
        status="queued",
        priority=10,
    )
    second_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, owner.id),
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
    assert body["scheduled_run_blocked_reasons"] == {"workspace_quota_exceeded:active_runs": 1}
    assert body["scheduled_run_blocked_steps"][0]["task_step_id"] == str(second_step.id)
    assert (
        body["scheduled_run_blocked_steps"][0]["blocked_reason"]
        == "workspace_quota_exceeded:active_runs"
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


@pytest.mark.parametrize("system_execution", [False, True])
def test_team_orchestration_finalize_closes_approved_tasks_only(system_execution: bool) -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session, role="owner", email="other-finalize@example.com", slug="other-finalize"
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
            review_policy={"mode": "agent_planning"},
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
            review_policy={"mode": "final_acceptance"},
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
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/orchestration/finalize",
        headers=_headers(owner.id),
        json={"dry_run": True},
    )
    forbidden = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/orchestration/finalize",
        headers=_headers(other_owner.id),
        json={"dry_run": True},
    )
    missing = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{uuid4()}/orchestration/finalize",
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
    assert missing.status_code == 403
    assert "Private body" not in str(dry_run_body)
    assert "sk-approved-finalize" not in str(dry_run_body)
    session.refresh(approved_task)
    assert approved_task.status == "running"
    if system_execution:
        from opsmesh.teams.execution.finalization import TeamExecutionFinalizationService

        applied_body = TeamExecutionFinalizationService(session).finalize_ready_tasks(
            workspace_id=workspace.id, team_id=team.id, actor_user_id=None, dry_run=False
        )
        assert applied_body is not None
    else:
        applied = client.post(
            f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/orchestration/finalize",
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
                ["task.team_orchestration.finalized", "team.orchestration.tasks_finalized"]
            ),
        )
    ).all()
    assert {audit.action for audit in audits} == {
        "task.team_orchestration.finalized",
        "team.orchestration.tasks_finalized",
    }
    for audit in audits:
        assert audit.actor_type == ("system" if system_execution else "user")
        assert audit.actor_id == (
            "opsmesh.team_orchestration" if system_execution else str(owner.id)
        )
        assert audit.user_id == (None if system_execution else owner.id)


def test_team_orchestration_requests_stage_distinct_durable_executions_and_enforce_scope() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    viewer = User(email="loop-viewer@example.com", display_name="Loop Viewer")
    session.add(viewer)
    session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=viewer.id, role="viewer"))
    team = AgentTeam(workspace_id=workspace.id, name="Async Loop Team", team_type="software")
    session.add(team)
    session.commit()
    first = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/orchestration/requests",
        headers=_headers(owner.id),
        json={"metadata": {"source": "test"}},
    )
    duplicate = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/orchestration/requests",
        headers=_headers(owner.id),
        json={"metadata": {"source": "test"}},
    )
    forbidden = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/orchestration/requests",
        headers=_headers(viewer.id),
        json={},
    )
    missing = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{uuid4()}/orchestration/requests",
        headers=_headers(owner.id),
        json={},
    )
    assert first.status_code == 202
    assert first.json()["workspace_id"] == str(workspace.id)
    assert first.json()["team_id"] == str(team.id)
    assert first.json()["status"] == "queued"
    assert duplicate.status_code == 202
    assert duplicate.json()["request_id"] != first.json()["request_id"]
    assert duplicate.json()["workflow_id"] != first.json()["workflow_id"]
    assert forbidden.status_code == 403
    assert missing.status_code == 403
    records = session.scalars(select(TemporalOutboxEntry)).all()
    assert len(records) == 2
    for row in records:
        request = DispatchRequest.model_validate(row.payload)
        assert request.workspace_id == workspace.id
        assert request.execution.resource_id in {
            UUID(first.json()["request_id"]),
            UUID(duplicate.json()["request_id"]),
        }
        assert request.execution.requested_by_user_id == owner.id
        assert request.execution.kind == ExecutionKind.TEAM_ORCHESTRATION


@pytest.mark.skipif(os.getenv("OPSMESH_TEST_TEMPORAL") != "1", reason="Real Temporal required")
def test_team_orchestration_blocks_run_when_provider_is_unhealthy() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    credential = ModelProviderCredentialCommandService(
        session, SecretEncryptionService(secret="unit-test-secret", key_id="test-key")
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
        workspace_id=workspace.id, name="Blocked Loop Runtime", status="active"
    )
    session.add(runtime)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Blocked Loop Team",
        team_type="software",
        manager_agent_profile_id=developer.id,
        default_task_policy={
            "team_runtime": {"status": "running", "workspace_runtime_id": str(runtime.id)}
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
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/orchestration/requests",
        headers=_headers(owner.id),
        json={
            "dry_run": False,
            "enqueue_runs": True,
            "finalize_ready_tasks": False,
            "reason": "blocked provider loop",
        },
    )
    assert response.status_code == 202
    body = run_team_request(session, UUID(response.json()["request_id"]))
    assert body["summary"]["scheduled_run_count"] == 0
    assert body["summary"]["scheduled_run_skip_reason"] == "provider_readiness_blocked"
    assert (
        body["command_center_actions"]["scheduled_run_skip_reason"] == "provider_readiness_blocked"
    )
    assert body["command_center_actions"]["scheduled_run_count"] == 0
    session.expire_all()
    assert session.scalars(select(AgentRun)).all() == []
    stored_team = session.get(AgentTeam, team.id)
    assert stored_team is not None
    runtime_metadata = stored_team.default_task_policy["team_runtime"]
    assert (
        runtime_metadata["last_advancement"]["summary"]["scheduled_run_skip_reason"]
        == "provider_readiness_blocked"
    )
    iteration_audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "team.orchestration.advancement_ran",
        )
    )
    assert iteration_audit is not None
    assert (
        iteration_audit.audit_metadata["scheduled_run_skip_reason"] == "provider_readiness_blocked"
    )
    assert "sk-loop-provider-blocked" not in str(body)
    assert "provider.example.test/v1" not in str(body)


@pytest.mark.skipif(os.getenv("OPSMESH_TEST_TEMPORAL") != "1", reason="Real Temporal required")
def test_team_orchestration_ignores_non_participating_provider_blockers() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    service = ModelProviderCredentialCommandService(
        session, SecretEncryptionService(secret="unit-test-secret", key_id="test-key")
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
        workspace_id=workspace.id, name="Non Runtime Blocker Loop Runtime", status="active"
    )
    session.add(runtime)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Non Runtime Blocker Team",
        team_type="software",
        manager_agent_profile_id=developer.id,
        default_task_policy={
            "team_runtime": {"status": "running", "workspace_runtime_id": str(runtime.id)}
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
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, owner.id),
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
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/orchestration/requests",
        headers=_headers(owner.id),
        json={
            "dry_run": False,
            "enqueue_runs": True,
            "finalize_ready_tasks": False,
            "reason": "non runtime provider blocker loop",
        },
    )
    assert response.status_code == 202
    body = run_team_request(session, UUID(response.json()["request_id"]))
    readiness = body["command_center_actions"]["summary"]["provider_readiness"]
    assert readiness["status"] == "degraded"
    assert readiness["blocked_member_count"] == 1
    assert readiness["runtime_blocked_member_count"] == 0
    assert readiness["runtime_participant_count"] == 1
    assert body["summary"]["scheduled_run_skip_reason"] is None
    assert body["summary"]["scheduled_run_count"] == 1
    assert body["command_center_actions"]["scheduled_run_skip_reason"] is None
    assert body["command_center_actions"]["scheduled_run_count"] == 1
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
        session, role="owner", email="other-team-operator@example.com", slug="other-team-operator"
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
    assert requeue_body["warnings"] == [f"task_step_not_found_or_not_in_team:{missing_step_id}"]
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
        workspace_id=workspace.id, name="Original Developer", role="developer"
    )
    replacement_agent = AgentProfile(
        workspace_id=workspace.id, name="Replacement Developer", role="developer"
    )
    non_team_agent = AgentProfile(
        workspace_id=workspace.id, name="Non-team Developer", role="developer"
    )
    team = AgentTeam(workspace_id=workspace.id, name="Reassign Team", team_type="software")
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
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/{member.json()['id']}",
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
        f"/api/v1/workspaces/{workspace.id}/audit-events", headers=_headers(owner.id)
    )
    assert member.status_code == 201
    assert first_task.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["skill_weights"] == {"react": 0.9, "typescript": 0.85}
    assert updated.json()["responsibilities"] == ["Build UI", "Review frontend quality"]
    assert updated.json()["availability"] == {"timezone": "Asia/Shanghai", "weekly_hours": 30}
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
        session, role="owner", email="other@example.com", slug="other-space"
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
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/{local_member.json()['id']}",
        headers=_headers(owner.id),
        json={"reports_to_member_id": local_member.json()["id"]},
    )
    assert local_member.status_code == 201
    assert bad_agent.status_code == 404
    assert bad_manager.status_code == 404
    assert bad_report.status_code == 404
    assert self_report.status_code == 400
