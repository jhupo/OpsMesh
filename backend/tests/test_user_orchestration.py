import pytest
from pydantic import ValidationError
from sqlalchemy import select

from backend.app.agents.models import AgentProfile
from backend.app.orchestration.conditions import (
    evaluate_task_step_condition,
    validate_condition,
)
from backend.app.orchestration.definition_commands import (
    OrchestrationDefinitionCreate,
    OrchestrationDefinitionUpdate,
)
from backend.app.orchestration.definitions import OrchestrationDefinitionService
from backend.app.orchestration.run_eligibility import RunEligibilityService
from backend.app.planning.models import TaskPlanningAttempt
from backend.app.planning.workflow_contracts import WorkflowCondition, WorkflowNode
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.tests.test_capability_resources import (
    _client as _api_client,
)
from backend.tests.test_capability_resources import (
    _headers as _api_headers,
)
from backend.tests.test_capability_resources import (
    _seed_workspace as _seed_api_workspace,
)
from backend.tests.test_worker_run_execution import _seed_workspace, _session


def test_user_orchestration_can_publish_and_apply_to_a_team_task() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
        model="gpt-4.1",
        skills={"skills": ["python"]},
    )
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Delivery",
        manager_agent_profile_id=agent.id,
    )
    session.add_all([agent, team])
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=agent.id,
            team_role="developer",
            skill_weights={"python": 1},
        )
    )
    session.flush()

    request = OrchestrationDefinitionCreate(
        key="release-flow",
        name="Release flow",
        nodes=[
            WorkflowNode(
                package_id="implement",
                title="Implement",
                required_role="developer",
                required_skills=["python"],
                assigned_agent_profile_id=agent.id,
                condition={
                    "path": "task.input.release",
                    "operator": "equals",
                    "value": True,
                },
            )
        ],
    )
    service = OrchestrationDefinitionService(session)
    definition = service.create_definition(workspace.id, request, user.id)
    assert definition.status == "draft"
    service.publish_definition(workspace.id, definition.id, user.id)
    service.update_definition(
        workspace.id,
        definition.id,
        OrchestrationDefinitionUpdate(expected_version=1, name="Unpublished changes"),
        user.id,
    )

    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Ship release",
        input={"release": True},
    )
    session.add(task)
    session.flush()
    service.apply_to_task(task, definition.id, orchestration_version=1, actor_user_id=user.id)
    session.commit()

    assert task.project_plan is not None
    assert task.project_plan["strategy"] == "user_authored"

    assert task.orchestration_definition_id == definition.id
    assert task.orchestration_version == 1
    step = session.scalar(select(TaskStep).where(TaskStep.task_id == task.id))
    assert step is not None
    assert step.assigned_agent_profile_id == agent.id
    assert step.dependencies["condition"]["path"] == "task.input.release"
    attempt = session.scalar(
        select(TaskPlanningAttempt).where(TaskPlanningAttempt.task_id == task.id)
    )
    assert attempt is not None
    assert attempt.strategy == "user_authored"


def test_all_skipped_nodes_finish_without_an_agent_run() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, title="Nothing selected", input={})
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Optional",
        dependencies={"condition": {"path": "task.input.flag", "operator": "exists"}},
    )
    session.add(step)
    session.flush()
    assert RunEligibilityService(session).next_eligible_steps(task.id, workspace.id) == []
    assert step.status == "skipped"
    assert task.status == "completed"
    assert task.final_output["all_nodes_skipped"] is True


def test_unconditional_nodes_publish_and_revisions_survive_draft_edits() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = OrchestrationDefinitionService(session)
    definition = service.create_definition(
        workspace.id,
        OrchestrationDefinitionCreate(
            key="plain", name="Plain", nodes=[WorkflowNode(package_id="plain", title="Plain")]
        ),
        user.id,
    )
    service.publish_definition(workspace.id, definition.id, user.id)
    from backend.app.orchestration.models import OrchestrationRevision

    service.update_definition(
        workspace.id,
        definition.id,
        OrchestrationDefinitionUpdate(expected_version=1, name="Draft two"),
        user.id,
    )
    revision = session.scalar(select(OrchestrationRevision))
    assert revision is not None
    assert revision.version == 1
    assert revision.name == "Plain"
    assert "condition" not in revision.definition["nodes"][0]
    with pytest.raises(ValueError, match="reload"):
        service.update_definition(
            workspace.id,
            definition.id,
            OrchestrationDefinitionUpdate(expected_version=1, name="Stale edit"),
            user.id,
        )


def test_branch_skip_propagates_and_selected_join_becomes_ready() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Branches")
    session.add(task)
    session.flush()
    source = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Selected",
        status="completed",
        dependencies={},
    )
    skipped = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Not selected",
        status="skipped",
        dependencies={},
    )
    session.add_all([source, skipped])
    session.flush()
    branch_child = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Branch child",
        dependencies={"after_step_ids": [str(skipped.id)]},
        order_index=20,
    )
    session.add(branch_child)
    session.flush()
    join = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Join",
        order_index=0,
        dependencies={
            "after_step_ids": [str(source.id), str(branch_child.id)],
            "join_policy": "all_selected",
        },
    )
    session.add(join)
    session.flush()
    assert RunEligibilityService(session).next_eligible_steps(task.id, workspace.id) == [join]
    assert branch_child.status == "skipped"


@pytest.mark.parametrize("target", ["missing", "self"])
def test_condition_reference_rejects_missing_and_self_dependencies(target: str) -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    with pytest.raises(ValueError):
        OrchestrationDefinitionService(session).create_definition(
            workspace.id,
            OrchestrationDefinitionCreate(
                key="invalid",
                name="Invalid",
                nodes=[
                    WorkflowNode(
                        package_id="self",
                        title="Self",
                        condition={
                            "path": f"steps.{target}.status",
                            "operator": "equals",
                            "value": "completed",
                        },
                    )
                ],
            ),
            user.id,
        )


def test_condition_scheduler_skips_false_nodes_and_keeps_audit_message() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Branching task",
        status="queued",
        input={"mode": "safe"},
    )
    session.add(task)
    session.flush()
    skipped = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="risky-path",
        title="Risky path",
        status="queued",
        order_index=1,
        dependencies={
            "after_step_ids": [],
            "condition": {
                "path": "task.input.mode",
                "operator": "equals",
                "value": "risky",
            },
        },
    )
    safe = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="safe-path",
        title="Safe path",
        status="queued",
        order_index=2,
        dependencies={"after_step_ids": []},
    )
    session.add_all([skipped, safe])
    session.flush()

    eligible = RunEligibilityService(session).next_eligible_steps(task.id, workspace.id)

    assert [step.work_package_id for step in eligible] == ["safe-path"]
    assert skipped.status == "skipped"
    assert skipped.dependencies["condition_result"] == "false"
    assert (
        session.scalar(
            select(TaskMessage).where(
                TaskMessage.task_id == task.id,
                TaskMessage.message_type == "orchestration.step_skipped",
            )
        )
        is not None
    )


def test_condition_language_is_bounded_and_reports_pending_step_state() -> None:
    with pytest.raises(ValidationError):
        WorkflowCondition(
            path="task.input.flag",
            operator="exists",
            value=False,
        )

    validate_condition(
        {
            "path": "task.input",
            "operator": "exists",
        }
    )
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Pending")
    session.add(task)
    session.flush()
    source = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="source.node",
        title="Source",
        status="running",
        order_index=1,
        dependencies={},
    )
    dependent = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="dependent",
        title="Dependent",
        status="queued",
        order_index=2,
        dependencies={
            "condition": {
                "path": "steps.source.node.status",
                "operator": "equals",
                "value": "completed",
            }
        },
    )
    session.add_all([source, dependent])
    session.flush()

    result = evaluate_task_step_condition(session, task, dependent)

    assert result.state == "pending"


def test_orchestration_api_supports_draft_publish_edit_and_archive() -> None:
    client, session = _api_client()
    owner, workspace = _seed_api_workspace(session, "orchestration@example.com", "orchestration")
    path = f"/api/v1/workspaces/{workspace.id}/orchestrations"
    headers = _api_headers(owner.id)
    payload = {
        "key": "review-flow",
        "name": "Review flow",
        "nodes": [
            {
                "package_id": "review",
                "title": "Review",
                "required_role": "reviewer",
                "condition": {
                    "path": "task.input.requires_review",
                    "operator": "equals",
                    "value": True,
                },
            }
        ],
    }

    created = client.post(path, headers=headers, json=payload)
    validated = client.post(f"{path}/{created.json()['id']}/validate", headers=headers)
    published = client.post(f"{path}/{created.json()['id']}/publish", headers=headers)
    edited = client.patch(
        f"{path}/{created.json()['id']}",
        headers=headers,
        json={"name": "Review flow v2", "expected_version": 1},
    )
    archived = client.post(f"{path}/{created.json()['id']}/archive", headers=headers)

    assert created.status_code == 201
    assert created.json()["status"] == "draft"
    assert validated.status_code == 200
    assert validated.json()["valid"] is True
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    history = client.get(f"{path}/{created.json()['id']}/revisions", headers=headers)
    revision = client.get(f"{path}/{created.json()['id']}/revisions/1", headers=headers)
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert revision.status_code == 200
    assert revision.json()["name"] == "Review flow"
    assert edited.status_code == 200
    assert edited.json()["version"] == 2
    assert edited.json()["status"] == "draft"
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert session.query(Task).count() == 0


def test_workspace_user_can_edit_team_capability_and_mcp_tool_properties() -> None:
    client, session = _api_client()
    owner, workspace = _seed_api_workspace(session, "properties@example.com", "properties")
    headers = _api_headers(owner.id)

    agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=headers,
        json={"name": "Operator", "role": "operator"},
    )
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=headers,
        json={
            "name": "Operations",
            "manager_agent_profile_id": agent.json()["id"],
        },
    )
    team_update = client.patch(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}",
        headers=headers,
        json={"description": "Updated operating team", "coordination_rules": {"mode": "serial"}},
    )
    capability = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities",
        headers=headers,
        json={
            "key": "operations.execute",
            "name": "Execute operations",
            "category": "operations",
        },
    )
    capability_update = client.patch(
        f"/api/v1/workspaces/{workspace.id}/capabilities/{capability.json()['id']}",
        headers=headers,
        json={"name": "Execute approved operations"},
    )
    server = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers",
        headers=headers,
        json={"name": "operations-mcp"},
    )
    tool = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/{server.json()['id']}/tools",
        headers=headers,
        json={"tool_name": "execute_operation", "capability_key": "operations.execute"},
    )
    tool_update = client.patch(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/{server.json()['id']}"
        f"/tools/{tool.json()['id']}",
        headers=headers,
        json={"description": "Execute a reviewed operation", "requires_approval": True},
    )

    assert agent.status_code == 201
    assert team.status_code == 201
    assert team_update.status_code == 200
    assert team_update.json()["description"] == "Updated operating team"
    assert capability.status_code == 201
    assert capability_update.status_code == 200
    assert capability_update.json()["name"] == "Execute approved operations"
    assert server.status_code == 201
    assert tool.status_code == 201
    assert tool_update.status_code == 200
    assert tool_update.json()["description"] == "Execute a reviewed operation"
    assert tool_update.json()["requires_approval"] is True


def test_task_api_can_apply_a_published_orchestration_without_queueing() -> None:
    client, session = _api_client()
    owner, workspace = _seed_api_workspace(session, "apply@example.com", "apply")
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Manager",
        role="project_manager",
        model="gpt-5.5",
    )
    session.add(agent)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Delivery",
        team_type="delivery",
        manager_agent_profile_id=agent.id,
    )
    session.add(team)
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Apply authored plan",
        input={"requires_review": True},
    )
    session.add(task)
    session.commit()

    definition_service = OrchestrationDefinitionService(session)
    definition = definition_service.create_definition(
        workspace.id,
        OrchestrationDefinitionCreate(
            key="apply-flow",
            name="Apply flow",
            nodes=[
                WorkflowNode(
                    package_id="manager-review",
                    title="Manager review",
                    required_role="project_manager",
                    condition={
                        "path": "task.input.requires_review",
                        "operator": "equals",
                        "value": True,
                    },
                )
            ],
        ),
        owner.id,
    )
    definition_service.publish_definition(workspace.id, definition.id, owner.id)

    applied = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.id}/plan/apply-orchestration",
        headers=_api_headers(owner.id),
        json={
            "orchestration_definition_id": str(definition.id),
            "enqueue": False,
        },
    )

    assert applied.status_code == 200, applied.text
    assert applied.json()["orchestration_definition_id"] == str(definition.id)
    assert applied.json()["orchestration_version"] == 1
    session.refresh(task)
    assert task.project_plan is not None
    assert task.project_plan["strategy"] == "user_authored"
