import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.definitions.application import (
    OrchestrationDefinitionApplicationService,
)
from opsmesh.orchestration.definitions.commands import (
    OrchestrationDefinitionCreateCommand,
    OrchestrationDefinitionUpdateCommand,
    OrchestrationEditScope,
)
from opsmesh.orchestration.definitions.conditions import (
    evaluate_task_step_condition,
    validate_condition,
)
from opsmesh.orchestration.definitions.contracts import WorkflowCondition, WorkflowNode
from opsmesh.orchestration.definitions.service import OrchestrationDefinitionService
from opsmesh.orchestration.planning.attempt_models import TaskPlanningAttempt
from opsmesh.orchestration.runs.eligibility import RunEligibilityService
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task, TaskMessage, TaskStep
from opsmesh.orchestration.tasks.observation.execution import TaskExecutionDiagnosticsService
from opsmesh.teams.management.models import AgentTeam, AgentTeamMember
from tests.fixtures.agent_product import _seed_workspace, _session
from tests.test_capability_resources import (
    _client as _api_client,
)
from tests.test_capability_resources import (
    _headers as _api_headers,
)
from tests.test_capability_resources import (
    _seed_workspace as _seed_api_workspace,
)


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

    request = OrchestrationDefinitionCreateCommand(
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
        OrchestrationDefinitionUpdateCommand(expected_version=1, name="Unpublished changes"),
        user.id,
    )

    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Ship release",
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        input={"release": True},
    )
    session.add(task)
    session.flush()
    OrchestrationDefinitionApplicationService(session).apply_to_task(
        task,
        definition.id,
        orchestration_version=1,
        actor_user_id=user.id,
    )
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
        OrchestrationDefinitionCreateCommand(
            key="plain", name="Plain", nodes=[WorkflowNode(package_id="plain", title="Plain")]
        ),
        user.id,
    )
    service.publish_definition(workspace.id, definition.id, user.id)
    from opsmesh.orchestration.definitions.models import OrchestrationRevision

    service.update_definition(
        workspace.id,
        definition.id,
        OrchestrationDefinitionUpdateCommand(expected_version=1, name="Draft two"),
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
            OrchestrationDefinitionUpdateCommand(expected_version=1, name="Stale edit"),
            user.id,
        )


def test_authored_plan_cannot_be_replaced_by_automatic_regeneration() -> None:
    from opsmesh.orchestration.planning.lifecycle import (
        TaskPlanLifecycleService,
        TaskPlanRegenerateCommand,
    )

    session = _session()
    user, workspace = _seed_workspace(session)
    team = AgentTeam(workspace_id=workspace.id, name="Authored team")
    session.add(team)
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        agent_team_id=team.id,
        title="Authored",
        project_plan={"strategy": "user_authored", "work_packages": []},
    )
    session.add(task)
    session.commit()
    with pytest.raises(ValueError, match="explicit plan mutation"):
        TaskPlanLifecycleService(session).regenerate_task_plan(
            workspace.id,
            task.id,
            user.id,
            TaskPlanRegenerateCommand(),
        )
    assert task.project_plan["strategy"] == "user_authored"


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
            OrchestrationDefinitionCreateCommand(
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


def test_execution_diagnostics_exposes_node_results_attempts_and_approval_state() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Reviewer",
        role="reviewer",
        model="gpt-5.5",
    )
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Inspect")
    session.add_all([agent, task])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        work_package_id="review",
        assigned_agent_profile_id=agent.id,
        title="Review",
        status="waiting_approval",
        result_summary="Evidence collected",
        result_payload={"artifact": "review.md"},
    )
    session.add(step)
    session.flush()
    from opsmesh.orchestration.runs.models import AgentRun

    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        agent_profile_id=agent.id,
        status="waiting_approval",
        model="gpt-5.5",
        output={"summary": "Evidence collected"},
    )
    session.add(run)
    session.flush()
    session.add(
        Approval(
            workspace_id=workspace.id,
            task_id=task.id,
            agent_run_id=run.id,
            requested_by_agent_profile_id=agent.id,
            approval_type="tool",
            risk_level="medium",
            payload={"tool_name": "publish"},
            status="pending",
            created_at=datetime.now(UTC),
        )
    )
    session.flush()

    diagnostics = TaskExecutionDiagnosticsService(session).get_diagnostics(
        workspace_id=workspace.id,
        task_id=task.id,
    )
    assert diagnostics is not None
    payload = diagnostics["steps"][0]
    assert payload["attempt_count"] == 1
    assert payload["result_payload"] == {"artifact": "review.md"}
    assert payload["approval_state"]["pending_count"] == 1
    assert payload["diagnostics"]["approval_blocked"] is True
    assert payload["runs"][0]["output"] == {"summary": "Evidence collected"}


def test_orchestration_api_supports_draft_publish_edit_and_archive() -> None:
    client, session = _api_client()
    owner, workspace = _seed_api_workspace(session, "orchestration@example.com", "orchestration")
    path = f"/api/v1/workspaces/{workspace.id}/orchestrations"
    headers = _api_headers(owner.id)
    payload = {
        "key": "review-flow",
        "name": "Review flow",
        "editor": {"positions": {"review": {"x": 100, "y": 200}}},
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
    assert revision.json()["definition"]["editor"]["positions"]["review"]["x"] == 100
    assert edited.json()["definition"]["editor"] == created.json()["definition"]["editor"]
    contract = client.get(f"{path}/authoring-contract", headers=headers)
    assert contract.status_code == 200
    assert "input_bindings" in contract.json()["node_schema"]["properties"]
    assert edited.status_code == 200
    assert edited.json()["version"] == 2
    assert edited.json()["status"] == "draft"
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert session.query(Task).count() == 0


@pytest.mark.parametrize("trigger_type", ["message", "schedule"])
def test_configured_automation_admits_workflow_and_delivers_reply(
    trigger_type: str,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:

    from temporalio.openai_agents.testing import TestModel, TestModelProvider

    from opsmesh.orchestration.webhooks.http_client import WebhookHTTPResponse
    from opsmesh.shared.config import get_settings
    from opsmesh.shared.security.secrets import SecretEncryptionService
    from tests.fixtures.native_automation_worker import native_automation_worker
    from tests.test_webhooks import _RecordingHTTPClient

    client, session = _api_client()
    owner, workspace = _seed_api_workspace(session, "automation@example.com", "automation")
    headers = _api_headers(owner.id)
    base = f"/api/v1/workspaces/{workspace.id}"
    agent = client.post(
        f"{base}/agents",
        headers=headers,
        json={
            "name": "Team leader",
            "role": "project_manager",
        },
    )
    assert agent.status_code == 201, agent.text
    team = client.post(
        f"{base}/teams",
        headers=headers,
        json={
            "name": "Automation team",
            "manager_agent_profile_id": agent.json()["id"],
        },
    )
    assert team.status_code == 201, team.text
    workflow = client.post(
        f"{base}/orchestrations",
        headers=headers,
        json={
            "key": "automation-flow",
            "name": "Automation flow",
            "nodes": [
                {"package_id": "start", "title": "Start", "node_type": "start"},
                {"package_id": "end", "title": "End", "node_type": "end", "depends_on": ["start"]},
            ],
        },
    )
    assert workflow.status_code == 201, workflow.text
    assert (
        client.post(
            f"{base}/orchestrations/{workflow.json()['id']}/publish",
            headers=headers,
        ).status_code
        == 200
    )
    from opsmesh.messaging.channels.models import ChannelDelivery
    from tests.fixtures.channels import seed_channel

    settings = client.app.dependency_overrides[get_settings]()
    connection, _, _ = seed_channel(
        session,
        owner,
        workspace,
        secret_service=SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
        ),
    )
    config = {
        "name": "Order assistance",
        "trigger_type": trigger_type,
        "orchestration_definition_id": workflow.json()["id"],
        "orchestration_version": 1,
        "agent_team_id": team.json()["id"],
        "reply_connection_id": str(connection.id),
    }
    if trigger_type == "message":
        config["channel_connection_id"] = str(connection.id)
        config["allowed_senders"] = ["employee-1"]
    else:
        config["schedule_type"] = "one_shot"
        config["schedule_config"] = {
            "run_at": (datetime.now(UTC) + timedelta(seconds=10)).isoformat()
        }
    created = client.post(f"{base}/automations", headers=headers, json=config)
    assert created.status_code == 201, created.text
    path = f"{base}/automations/{created.json()['id']}/events"
    if trigger_type == "message":
        bound = client.put(
            f"{base}/automations/{created.json()['id']}/identities/employee-1",
            headers=headers,
            json={"user_id": str(owner.id)},
        )
        assert bound.status_code == 200, bound.text
        message = {
            "event_id": "order-1",
            "conversation_id": "private-1",
            "sender_id": "employee-1",
            "occurred_at": datetime.now(UTC).isoformat(),
            "text": "Assist this order",
        }
        accepted = client.post(path, headers=headers, json=message)
        assert accepted.status_code == 202, accepted.text
        assert (
            client.post(path, headers=headers, json=message).json()["id"] == accepted.json()["id"]
        )
    transport = _RecordingHTTPClient(WebhookHTTPResponse(status_code=200, body="ok", headers={}))
    with native_automation_worker(
        session, settings, TestModelProvider(TestModel.returning_responses([])), transport=transport
    ) as driver:
        events = driver.until(
            lambda: (
                (values := client.get(path, headers=headers).json())
                and values[0]["status"] == "completed"
                and values
            )
        )
    session.expire_all()
    task = session.get(Task, UUID(events[0]["task_id"]))
    assert task is not None and task.status == "completed"
    assert task.orchestration_version == 1
    assert session.query(Task).count() == 1
    assert session.query(ChannelDelivery).count() == 1
    assert session.get(ChannelDelivery, UUID(events[0]["reply_delivery_id"])).status == "delivered"
    assert len(transport.calls) == 1


@pytest.mark.skipif(os.getenv("OPSMESH_TEST_TEMPORAL") != "1", reason="Real Temporal required")
def test_message_conversation_controls_and_continues_work_through_sdk() -> None:
    from datetime import UTC, datetime

    from opsmesh_plugin_sdk.messaging.client import AutomationClient
    from opsmesh_plugin_sdk.messaging.contracts import IncomingMessage
    from temporalio.openai_agents.testing import TestModel, TestModelProvider

    from opsmesh.orchestration.webhooks.http_client import WebhookHTTPResponse
    from opsmesh.shared.config import get_settings
    from tests.fixtures.agent_product import _seed_default_model_provider
    from tests.fixtures.native_agent_worker import answer
    from tests.fixtures.native_automation_worker import native_automation_worker
    from tests.test_webhooks import _RecordingHTTPClient

    client, session = _api_client()
    owner, workspace = _seed_api_workspace(session, "conversation@example.com", "conversation")
    _seed_default_model_provider(session, workspace_id=workspace.id, user_id=owner.id)
    client.headers.update(_api_headers(owner.id))
    client.base_url = "http://testserver/api/v1/"
    base = f"workspaces/{workspace.id}"
    agent = client.post(
        f"{base}/agents",
        json={
            "name": "Coordinator",
            "role": "project_manager",
            "runtime_policy": {"execution_mode": "none"},
        },
    )
    assert agent.status_code == 201, agent.text
    team = client.post(
        f"{base}/teams",
        json={
            "name": "Message team",
            "manager_agent_profile_id": agent.json()["id"],
        },
    )
    assert team.status_code == 201, team.text
    member = client.post(
        f"{base}/teams/{team.json()['id']}/members",
        json={
            "agent_profile_id": agent.json()["id"],
            "team_role": "project_manager",
            "max_concurrent_tasks": 4,
        },
    )
    assert member.status_code == 201, member.text
    workflow = client.post(
        f"{base}/orchestrations",
        json={
            "key": "review-message",
            "name": "Review message",
            "nodes": [
                {
                    "package_id": "review",
                    "title": "Confirm the proposal",
                    "node_type": "approval",
                    "required_role": "project_manager",
                    "assigned_agent_profile_id": agent.json()["id"],
                },
                {"package_id": "end", "title": "End", "node_type": "end", "depends_on": ["review"]},
            ],
        },
    )
    assert workflow.status_code == 201, workflow.text
    assert client.post(f"{base}/orchestrations/{workflow.json()['id']}/publish").status_code == 200
    from opsmesh.shared.security.secrets import SecretEncryptionService
    from tests.fixtures.channels import seed_channel

    settings = client.app.dependency_overrides[get_settings]()
    connection, _, _ = seed_channel(
        session,
        owner,
        workspace,
        secret_service=SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
        ),
    )
    config = {
        "name": "Message collaboration",
        "trigger_type": "message",
        "orchestration_definition_id": workflow.json()["id"],
        "orchestration_version": 1,
        "agent_team_id": team.json()["id"],
        "reply_connection_id": str(connection.id),
        "channel_connection_id": str(connection.id),
        "allowed_senders": ["member-1", "member-2"],
        "notify_progress": True,
        "allowed_message_actions": [
            "start",
            "follow_up",
            "pause",
            "resume",
            "cancel",
        ],
    }
    created = client.post(f"{base}/automations", json=config)
    assert created.status_code == 201, created.text
    automation_id = UUID(created.json()["id"])
    for sender in ("member-1", "member-2"):
        bound = client.put(
            f"{base}/automations/{automation_id}/identities/{sender}",
            json={"user_id": str(owner.id)},
        )
        assert bound.status_code == 200, bound.text
    sdk = AutomationClient(client, workspace.id, automation_id)
    message = IncomingMessage(
        event_id="first",
        conversation_id="thread-1",
        sender_id="member-1",
        occurred_at=datetime.now(UTC),
        text="Analyze the supplied information",
    )
    accepted = sdk.submit(message)
    assert sdk.submit(message).id == accepted.id
    settings = client.app.dependency_overrides[get_settings]()
    transport = _RecordingHTTPClient(WebhookHTTPResponse(status_code=200, body="ok", headers={}))
    with native_automation_worker(
        session,
        settings,
        TestModelProvider(TestModel.returning_responses([answer("Done")])),
        transport=transport,
    ) as driver:
        waiting = driver.until(lambda: (state := sdk.state(accepted.id)).pending_actions and state)
        task_id = waiting.event.task_id
        assert task_id is not None
        approval_id = waiting.pending_actions[0].id
        run = session.scalar(select(AgentRun).where(AgentRun.task_id == task_id))
        assert run is not None and run.status == "waiting_approval"
        session.rollback()
        _assert_message_conversation_controls(
            client,
            session,
            sdk,
            driver,
            owner,
            workspace,
            agent,
            base,
            config,
            automation_id,
            message,
            accepted,
            task_id,
            approval_id,
            run,
            transport,
            settings,
        )


def _assert_message_conversation_controls(
    client,
    session,
    sdk,
    driver,
    owner,
    workspace,
    agent,
    base,
    config,
    automation_id,
    message,
    accepted,
    task_id,
    approval_id,
    run,
    transport,
    settings,
):
    import json

    from opsmesh_plugin_sdk.messaging.contracts import IncomingMessage

    from opsmesh.shared.security.secrets import SecretEncryptionService

    def control(action: str, event_id: str) -> object:
        response = sdk.submit(
            IncomingMessage(
                event_id=event_id,
                conversation_id="thread-1",
                sender_id="member-1",
                occurred_at=datetime.now(UTC),
                text="Additional facts",
                action=action,
                reply_to_event_id=accepted.id,
            )
        )

        def applied():
            state = sdk.state(response.id)
            assert state.event.status != "rejected", state
            return state if state.event.task_id else None

        state = driver.until(applied)
        assert state.event.task_id == task_id
        return response

    denied = client.post(
        f"{base}/automations/{automation_id}/events",
        json={
            **message.model_dump(mode="json"),
            "event_id": "foreign-sender",
            "sender_id": "member-2",
            "action": "pause",
            "reply_to_event_id": str(accepted.id),
        },
    )
    assert denied.status_code == 400
    followup = sdk.submit(
        IncomingMessage(
            event_id="next",
            conversation_id="thread-1",
            sender_id="member-1",
            occurred_at=datetime.now(UTC),
            text="Please reconsider using these facts",
            action="follow_up",
            reply_to_event_id=accepted.id,
        )
    )
    assert sdk.state(followup.id).event.status == "pending"
    assert sdk.state(followup.id).event.task_id is None
    control("pause", "takeover")
    session.expire_all()
    assert session.get(Approval, approval_id).status == "cancelled"
    assert sdk.state(accepted.id).task_status == "blocked"
    control("resume", "resume")
    driver.until(lambda: sdk.state(accepted.id).pending_actions)
    control("cancel", "cancel")
    assert sdk.state(accepted.id).task_status == "cancelled"
    assert session.query(Task).count() == 2

    next_state = driver.until(lambda: (state := sdk.state(followup.id)).pending_actions and state)
    session.expire_all()
    assert next_state.event.task_id != task_id
    next_task = session.get(Task, next_state.event.task_id)
    assert "previous_context" not in next_task.input
    from opsmesh.orchestration.requests.sessions import RunRequestSessionService

    profile = session.get(AgentProfile, UUID(agent.json()["id"]))
    next_run = session.scalar(select(AgentRun).where(AgentRun.task_id == next_task.id))
    refs = RunRequestSessionService(session)
    first_ref = refs.persistent_session_ref_for_run(run, session.get(Task, task_id), profile)
    next_ref = refs.persistent_session_ref_for_run(next_run, next_task, profile)
    assert first_ref == next_ref
    assert first_ref.scope_type == "automation_agent"
    assert session.query(Task).count() == 2

    # Both sender and external conversation isolate SDK history, even when the
    # external identities resolve to the same workspace user and Agent.
    from uuid import uuid4

    from opsmesh.orchestration.tasks.contracts import TaskControlActionRequest
    from opsmesh.orchestration.tasks.control.service import TaskControlService

    isolated_refs = []
    for sender in ("member-1", "member-2"):
        independent = sdk.submit(
            IncomingMessage(
                event_id=f"independent-{sender}",
                conversation_id="thread-2",
                sender_id=sender,
                occurred_at=datetime.now(UTC),
                text="Independent work",
            )
        )

        def independent_waiting(event_id=independent.id):
            state = sdk.state(event_id)
            assert state.event.status not in {"rejected", "skipped"}, state
            return state if state.pending_actions else None

        independent_state = driver.until(independent_waiting)
        session.expire_all()
        independent_task = session.get(Task, independent_state.event.task_id)
        assert independent_task is not None
        # This approval workflow may not launch a Run before the human gate;
        # Session scope is derived from its admitted task and durable event.
        independent_run = AgentRun(
            id=uuid4(),
            workspace_id=workspace.id,
            task_id=independent_task.id,
            agent_profile_id=profile.id,
        )
        ref = refs.persistent_session_ref_for_run(independent_run, independent_task, profile)
        assert ref.session_key != first_ref.session_key
        isolated_refs.append(ref)
        TaskControlService(session).apply_action(
            workspace_id=workspace.id,
            task_id=independent_task.id,
            actor_user_id=owner.id,
            request=TaskControlActionRequest(action="cancel"),
        )
    assert isolated_refs[0].session_key != isolated_refs[1].session_key

    settled = driver.until(
        lambda: (state := sdk.state(accepted.id)).event.status == "completed" and state
    )
    from opsmesh.messaging.channels.delivery import ChannelDeliveryService
    from opsmesh.messaging.channels.models import ChannelDelivery

    delivery_service = ChannelDeliveryService(
        session,
        SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
        ),
    )
    calls_before = len(transport.calls)
    assert delivery_service.send(workspace.id, settled.event.reply_delivery_id) == "delivered"
    assert not any(
        json.loads(call["body"])["delivery_id"] == str(settled.event.reply_delivery_id)
        for call in transport.calls[calls_before:]
    )
    final_calls = [
        call
        for call in transport.calls
        if json.loads(call["body"])["reply"]["event_id"] == str(accepted.id)
        and json.loads(call["body"])["reply"]["kind"] == "result"
    ]
    assert len(final_calls) == 1
    reply = json.loads(final_calls[0]["body"])["reply"]
    assert reply["sender_id"] == "member-1" and reply["status"] == "cancelled"
    source = next(
        attempt
        for attempt in session.scalars(select(ChannelDelivery))
        if attempt.payload.get("event_id") == str(followup.id)
    )
    pending = ChannelDelivery(
        workspace_id=workspace.id,
        connection_id=source.connection_id,
        event_id=source.event_id,
        sequence=source.sequence + 100,
        connection_revision=source.connection_revision,
        payload=dict(source.payload),
    )
    session.add(pending)
    session.commit()
    updated = client.put(
        f"{base}/automations/{automation_id}",
        json={
            "expected_version": 1,
            "status": "active",
            "configuration": {**config, "allowed_senders": ["member-2"]},
        },
    )
    assert updated.status_code == 200, updated.text
    assert delivery_service.send(workspace.id, pending.id) == "failed"
    assert not any(
        json.loads(call["body"])["delivery_id"] == str(pending.id) for call in transport.calls
    )


@pytest.mark.skipif(os.getenv("OPSMESH_TEST_TEMPORAL") != "1", reason="Real Temporal required")
@pytest.mark.parametrize("valid_output", [True, False])
def test_structured_messages_stream_before_model_completion_and_replay(
    monkeypatch: pytest.MonkeyPatch, valid_output: bool
) -> None:
    import asyncio
    import base64
    from datetime import UTC, datetime

    import httpx2 as httpx
    from agents import Model
    from agents.items import TResponseInputItem
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from openai.types.responses import (
        Response,
        ResponseCompletedEvent,
        ResponseFunctionToolCall,
        ResponseOutputItemDoneEvent,
    )
    from opsmesh_plugin_sdk.client import PluginClient
    from opsmesh_plugin_sdk.messaging.client import AsyncAutomationClient, AutomationClient
    from opsmesh_plugin_sdk.messaging.contracts import AttachmentUpload, IncomingMessage
    from opsmesh_plugin_sdk.packaging.manifest import PluginManifest
    from opsmesh_plugin_sdk.packaging.packages import sign_package
    from opsmesh_plugin_sdk.services.identity import PermissionQuery
    from opsmesh_plugin_sdk.services.observability import PluginLog
    from opsmesh_plugin_sdk.services.storage import StoreWrite
    from pydantic import TypeAdapter
    from sqlalchemy.orm import sessionmaker
    from temporalio import activity

    from opsmesh.governance.reviews.tool_execution import (
        ToolExecutionReview,
        ToolExecutionReviewService,
    )
    from opsmesh.identity.users.models import User
    from opsmesh.orchestration.webhooks.http_client import WebhookHTTPResponse
    from opsmesh.shared.config import get_settings
    from opsmesh.workspaces.members.models import WorkspaceMember
    from tests.fixtures.agent_product import _seed_default_model_provider
    from tests.fixtures.native_agent_worker import (
        OfflineModelProvider,
        stream_completed,
        stream_delta,
        stream_item,
    )
    from tests.fixtures.native_automation_worker import native_automation_worker
    from tests.test_webhooks import _RecordingHTTPClient

    client, session = _api_client()
    owner, workspace = _seed_api_workspace(session, "stream@example.com", "stream")
    monkeypatch.setattr(
        ToolExecutionReviewService,
        "review_product_tool_call",
        lambda self, **kwargs: ToolExecutionReview(
            required=True, blocked=False, risk_level="high", reasons=["flow approval"], signals={}
        ),
    )
    client.app.state.settings.credential_encryption_secret = (
        "change-me-credential-encryption-secret"
    )
    _seed_default_model_provider(session, workspace_id=workspace.id, user_id=owner.id)
    headers = _api_headers(owner.id)
    client.headers.update(headers)
    client.base_url = "http://testserver/api/v1/"
    base = f"workspaces/{workspace.id}"
    agent = client.post(
        f"{base}/agents",
        json={
            "name": "Public responder",
            "role": "project_manager",
            "runtime_policy": {"execution_mode": "none"},
            "tool_policy": {"allowed_tools": ["get_agent_inbox"]},
            "model_settings": {
                "output_schema": {
                    "name": "message_answer",
                    "schema": {
                        "type": "object",
                        "required": ["answer"],
                        "properties": {"answer": {"type": "string"}},
                        "additionalProperties": False,
                    },
                }
            },
        },
    )
    assert agent.status_code == 201, agent.text
    team = client.post(
        f"{base}/teams",
        json={
            "name": "Stream team",
            "manager_agent_profile_id": agent.json()["id"],
        },
    )
    assert team.status_code == 201, team.text
    output_schema = {
        "type": "object",
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
        "additionalProperties": False,
    }
    workflow = client.post(
        f"{base}/orchestrations",
        json={
            "key": "public-response",
            "name": "Public response",
            "nodes": [
                {
                    "package_id": "answer",
                    "title": "Answer",
                    "node_type": "agent",
                    "required_role": "project_manager",
                    "assigned_agent_profile_id": agent.json()["id"],
                    "required_tools": ["get_agent_inbox"],
                    "output_schema": output_schema,
                },
                {"package_id": "end", "title": "End", "node_type": "end", "depends_on": ["answer"]},
            ],
        },
    )
    assert workflow.status_code == 201, workflow.text
    assert client.post(f"{base}/orchestrations/{workflow.json()['id']}/publish").status_code == 200
    config = {
        "name": "Structured stream",
        "trigger_type": "message",
        "allowed_senders": ["user-1"],
        "agent_team_id": team.json()["id"],
        "orchestration_definition_id": workflow.json()["id"],
        "orchestration_version": 1,
        "contract_version": 2,
        "input_schema": {
            "type": "object",
            "required": ["question", "user"],
            "properties": {"question": {"type": "string"}, "user": {"type": "object"}},
            "additionalProperties": False,
        },
        "model_input_fields": ["question"],
        "output_schema": output_schema
        if valid_output
        else {
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "integer"}},
        },
        "output_binding": {"reference": "steps.answer.output.structured_output.value"},
        "stream_output_nodes": ["answer"],
        "stream_tool_events": True,
        "allowed_attachment_kinds": ["file", "image", "audio"],
    }
    created = client.post(f"{base}/automations", json=config)
    assert created.status_code == 201, created.text
    automation_id = UUID(created.json()["id"])
    employee = User(email="stream-employee@example.com", display_name="Stream employee")
    session.add(employee)
    session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=employee.id, role="operator"))
    session.commit()
    for kind, identifier in (
        ("agent", agent.json()["id"]),
        ("team", team.json()["id"]),
        ("workflow", workflow.json()["id"]),
        ("automation", str(automation_id)),
    ):
        shared = client.put(
            f"{base}/access/{kind}/{identifier}/grants/{employee.id}",
            json={"actions": ["read", "invoke"]},
        )
        assert shared.status_code == 200, shared.text
    bound = client.put(
        f"{base}/automations/{automation_id}/identities/user-1",
        json={"user_id": str(employee.id)},
    )
    assert bound.status_code == 200, bound.text
    sdk = AutomationClient(client, workspace.id, automation_id)
    private = Ed25519PrivateKey.generate()
    trusted = client.post(
        f"{base}/plugins/trust-keys",
        json={
            "key_id": "stream-publisher",
            "plugin_key": "stream.connector",
            "public_key": base64.b64encode(private.public_key().public_bytes_raw()).decode(),
        },
    )
    assert trusted.status_code == 201, trusted.text
    permissions = [
        "messages.receive",
        "messages.read",
        "approvals.decide",
        "attachments.write",
        "configuration.read",
        "storage.read",
        "storage.write",
        "permissions.read",
        "identity.read",
        "resources.read",
        "knowledge.read",
        "memory.write",
        "logs.write",
    ]
    package = sign_package(
        PluginManifest.model_validate(
            {
                "key": "stream.connector",
                "name": "Stream connector",
                "version": "1.0.0",
                "capabilities": [
                    {
                        "key": "receive",
                        "kind": "channel",
                        "features": ["receive", "send", "attachments"],
                        "title": "Receive",
                        "required_permissions": permissions,
                    }
                ],
            }
        ),
        "stream-publisher",
        private.private_bytes_raw(),
    )
    installed = client.post(
        f"{base}/plugins",
        json={
            "package": package.model_dump(mode="json"),
            "instance_name": "stream",
            "bindings": [],
            "approved_permissions": permissions,
        },
    )
    assert installed.status_code == 201, installed.text
    install_id = UUID(installed.json()["id"])
    channel_response = client.post(
        f"{base}/channels",
        json={
            "install_id": str(install_id),
            "capability_key": "receive",
            "name": "Stream robot",
            "account_id": "stream-robot",
            "endpoint_url": "https://example.com/deliveries",
            "signing_secret": "stream-signing-secret-with-required-length",
        },
    )
    assert channel_response.status_code == 201, channel_response.text
    config["channel_connection_id"] = channel_response.json()["id"]
    rebound = client.put(
        f"{base}/automations/{automation_id}",
        json={"expected_version": 1, "status": "active", "configuration": config},
    )
    assert rebound.status_code == 200, rebound.text
    credential_url = f"{base}/plugins/{install_id}/credentials"
    issued = client.post(credential_url, json={"permissions": permissions})
    assert issued.status_code == 201, issued.text
    plugin_headers = {"Authorization": "Bearer " + issued.json()["token"]}
    assert client.get(f"{base}/teams", headers=plugin_headers).status_code == 401
    assert (
        client.post(
            credential_url, headers=_api_headers(employee.id), json={"permissions": permissions}
        ).status_code
        == 403
    )
    configured = client.put(
        f"{base}/plugins/{install_id}/configuration",
        json={"expected_revision": 0, "value": {"poll_seconds": 3}},
    )
    assert configured.status_code == 200, configured.text

    async def host_services():
        from opsmesh_plugin_sdk.context import UserContext
        from opsmesh_plugin_sdk.services.knowledge import KnowledgeQuery, MemoryWrite
        from opsmesh_plugin_sdk.services.resources import ResourceQuery

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app),
            base_url="http://testserver/api/v1/",
            headers=plugin_headers,
        ) as http:
            host = PluginClient(http, workspace.id, install_id)
            context = UserContext(automation_id=automation_id, sender_id="user-1")
            assert (await host.identity.resolve(context)).user_id == employee.id
            assert set((await host.identity.context()).permissions) == set(permissions)
            with pytest.raises(httpx.HTTPStatusError) as unbound:
                await host.identity.resolve(context.model_copy(update={"sender_id": "unknown"}))
            assert unbound.value.response.status_code == 403
            for kind in (
                "team",
                "agent",
                "project",
                "capability",
                "mcp_server",
                "mcp_tool",
                "skill",
                "workflow",
                "file",
                "knowledge",
                "memory",
                "automation",
            ):
                page = await host.resources.list(
                    ResourceQuery(
                        **context.model_dump(),
                        resource_kind=kind,
                        limit=1,
                    )
                )
                if kind == "team":
                    assert [str(item.id) for item in page.items] == [team.json()["id"]]
            memory = await host.knowledge.remember(
                MemoryWrite(
                    **context.model_dump(),
                    scope_type="workspace",
                    scope_id=workspace.id,
                    memory_key="plugin-answer",
                    knowledge_type="procedure",
                    title="Plugin answer",
                    content="Inspect the authorized task output before responding.",
                    expected_revision=0,
                )
            )
            hits = await host.knowledge.search(
                KnowledgeQuery(
                    **context.model_dump(),
                    query="Plugin answer",
                )
            )
            assert any(hit.id == memory.id for hit in hits)
            with pytest.raises(httpx.HTTPStatusError) as forbidden_scope:
                await host.knowledge.remember(
                    MemoryWrite(
                        **context.model_dump(),
                        scope_type="team",
                        scope_id=UUID(team.json()["id"]),
                        memory_key="team-answer",
                        knowledge_type="fact",
                        title="Denied",
                        content="Invoke permission is not permission to modify team knowledge.",
                        expected_revision=0,
                    )
                )
            assert forbidden_scope.value.response.status_code == 403
            assert await host.configuration.read() == {"poll_seconds": 3}
            saved = await host.storage.write(
                "delivery:1", StoreWrite(expected_revision=0, value={"cursor": "0"})
            )
            assert (await host.storage.read("delivery:1")).revision == saved.revision
            with pytest.raises(httpx.HTTPStatusError) as conflict:
                await host.storage.write("delivery:1", StoreWrite(expected_revision=0, value={}))
            assert conflict.value.response.status_code == 409
            assert len(await host.storage.values(prefix="delivery:")) == 1
            allowed = await host.identity.permissions(
                PermissionQuery(
                    automation_id=automation_id,
                    sender_id="user-1",
                    resource_kind="team",
                    resource_id=UUID(team.json()["id"]),
                )
            )
            assert "invoke" in allowed.actions
            await host.observability.log(
                PluginLog(code="delivery.started", metadata={"authorization": "private-value"})
            )
            await host.storage.delete("delivery:1", expected_revision=saved.revision)
            assert await host.storage.read("delivery:1") is None

    asyncio.run(host_services())

    async def upload_media():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app),
            base_url="http://testserver/api/v1/",
            headers=plugin_headers,
        ) as http:
            host = PluginClient(http, workspace.id, install_id)
            upload = AttachmentUpload(
                sender_id="user-1",
                external_event_id="structured-1",
                slot=0,
                kind="file",
                filename="order.txt",
                content_type="text/plain",
            )
            channel = host.channel(UUID(channel_response.json()["id"]))
            attachment = await channel.upload_attachment(
                upload, b"order 123: timeout", conversation_id="thread-1"
            )
            assert (
                await channel.upload_attachment(
                    upload, b"order 123: timeout", conversation_id="thread-1"
                )
            ) == attachment
            with pytest.raises(httpx.HTTPStatusError) as conflict:
                await channel.upload_attachment(
                    upload, b"different content", conversation_id="thread-1"
                )
            assert conflict.value.response.status_code == 409
            return attachment

    attachment = asyncio.run(upload_media())
    message = IncomingMessage(
        event_id="structured-1",
        conversation_id="thread-1",
        sender_id="user-1",
        occurred_at=datetime.now(UTC),
        contract_version=2,
        data={"question": "What is the result?", "user": {"name": "private-name", "level": "vip"}},
        attachments=[attachment],
    )
    with pytest.raises(httpx.HTTPStatusError):
        sdk.submit(message.model_copy(update={"contract_version": 1}))
    with pytest.raises(httpx.HTTPStatusError):
        sdk.submit(message.model_copy(update={"data": {"question": 123}}))

    async def submit_plugin():
        from opsmesh_plugin_sdk.messaging.contracts import ChannelEvent

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app),
            base_url="http://testserver/api/v1/",
            headers=plugin_headers,
        ) as http:
            connection_id = UUID(channel_response.json()["id"])
            connector = PluginClient(http, workspace.id, install_id).channel(connection_id)

            async def ingest(value):
                return await connector.ingest(
                    ChannelEvent(
                        id=value.event_id,
                        source=f"opsmesh://channels/{connection_id}",
                        data=value,
                    )
                )

            with pytest.raises(httpx.HTTPStatusError) as wrong_event:
                await ingest(message.model_copy(update={"event_id": "different-event"}))
            assert wrong_event.value.response.status_code == 403
            accepted = await ingest(message)
            assert (await ingest(message)).id == accepted.id
            return accepted

    accepted = asyncio.run(submit_plugin())
    observed = []

    async def read_stream(cursor: str = "0") -> list:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app),
            base_url="http://testserver/api/v1/",
            headers=plugin_headers,
        ) as http:
            subscriber = AsyncAutomationClient(
                http, workspace.id, automation_id, install_id=install_id
            )
            return [
                frame async for frame in subscriber.events(accepted.id, cursor=cursor, once=True)
            ]

    from threading import Event
    from uuid import uuid4

    calls = []
    finish_response = Event()

    class StreamingModel(Model):
        def __init__(self, reference):
            self.reference = reference

        async def get_response(self, **kwargs):
            raise AssertionError("This scenario must execute the native streaming Model Activity")

        async def stream_response(self, **kwargs):
            assert activity.in_activity()
            inputs = TypeAdapter(list[TResponseInputItem]).dump_python(kwargs["input"], mode="json")
            serialized = str(inputs)
            assert "private-name" not in serialized
            assert "order 123: timeout" in serialized
            calls.append(self.reference.run_id)
            with sessionmaker(session.get_bind())() as read:
                run = read.get(AgentRun, self.reference.run_id)
                task = read.get(Task, run.task_id)
                assert task.execution_identity["user_id"] == str(employee.id)
            if len(calls) == 1:
                yield stream_delta("Unvalidated text must stay private")
                call = ResponseFunctionToolCall(
                    id="inbox-call",
                    call_id="inbox-1",
                    name="get_agent_inbox",
                    arguments="{}",
                    type="function_call",
                )
                yield ResponseOutputItemDoneEvent(
                    type="response.output_item.done", sequence_number=1, output_index=0, item=call
                )
                yield ResponseCompletedEvent(
                    type="response.completed",
                    sequence_number=2,
                    response=Response.model_validate(
                        {
                            "id": "inbox-response",
                            "created_at": 0,
                            "model": "gpt-4.1",
                            "object": "response",
                            "status": "completed",
                            "output": [call],
                            "parallel_tool_calls": False,
                            "tool_choice": "auto",
                            "tools": [],
                        }
                    ),
                )
            else:
                yield stream_delta('{"answer":"Done"}')
                assert await asyncio.to_thread(finish_response.wait, 45)
                yield stream_item('{"answer":"Done"}')
                yield stream_completed('{"answer":"Done"}')

    settings = client.app.dependency_overrides[get_settings]()
    transport = _RecordingHTTPClient(WebhookHTTPResponse(status_code=200, body="ok", headers={}))
    with native_automation_worker(
        session,
        settings,
        OfflineModelProvider(StreamingModel),
        transport=transport,
        stream=True,
    ) as driver:

        def awaiting_approval():
            state = sdk.state(accepted.id)
            if state.task_status in {"completed", "failed", "cancelled"}:
                with sessionmaker(session.get_bind())() as read:
                    runs = read.scalars(
                        select(AgentRun).where(
                            AgentRun.workspace_id == workspace.id,
                            AgentRun.task_id == state.event.task_id,
                        )
                    ).all()
                    pytest.fail(str([(run.status, run.error) for run in runs]))
            assert state.event.status not in {"rejected", "skipped"}, state
            return state if state.pending_actions else None

        waiting = driver.until(awaiting_approval)
        approval = waiting.pending_actions[0]
        task_id = waiting.event.task_id
        session.expire_all()
        task = session.get(Task, task_id)
        assert task.input["event"]["data"] == {"question": "What is the result?"}
        session.rollback()
        asyncio.run(
            _approve_plugin_message(
                client, workspace, install_id, automation_id, accepted, approval, plugin_headers
            )
        )

        def live_progress():
            frames = asyncio.run(read_stream())
            return frames if any(frame.kind == "tool.completed" for frame in frames) else None

        try:
            before = driver.until(live_progress)
            assert any(frame.kind == "tool.started" for frame in before)
            assert not any(frame.kind in {"output.text", "stream.completed"} for frame in before), [
                (frame.kind, frame.data.get("task_status")) for frame in before
            ]
            observed.extend(before)
        finally:
            finish_response.set()
        completed = driver.until(
            lambda: (state := sdk.state(accepted.id)).task_status == "completed" and state
        )
        assert completed.event.task_id == task_id
        session.expire_all()
        executed = session.get(AgentRun, calls[0])
        assert executed.status == "completed", executed.error
        assert str(employee.id) in executed.session_key
        session.rollback()

    frames = asyncio.run(read_stream(observed[-1].cursor))
    observer_user = User(email="stream-observer@example.com", display_name="Observer")
    session.add(observer_user)
    session.flush()
    session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=observer_user.id, role="operator")
    )
    session.commit()
    assert (
        client.put(
            f"{base}/access/automation/{automation_id}/grants/{observer_user.id}",
            json={"actions": ["read"]},
        ).status_code
        == 200
    )
    event_path = f"{base}/automations/{automation_id}/events"
    sender_state = client.get(f"{event_path}/{accepted.id}", headers=_api_headers(employee.id))
    assert sender_state.status_code == 200, sender_state.text
    assert client.get(
        f"{event_path}/{accepted.id}", headers=_api_headers(observer_user.id)
    ).status_code in {403, 404}
    assert client.get(event_path, headers=_api_headers(observer_user.id)).json() == []
    assert (
        client.get(
            f"{base}/files/{attachment.file_id}/download", headers=_api_headers(observer_user.id)
        ).status_code
        == 403
    )
    assert any(
        frame.kind == "tool.started" and frame.data["call_id"] == "inbox-1"
        for frame in [*observed, *frames]
    )
    assert any(frame.kind == "tool.completed" for frame in [*observed, *frames])
    state = next(frame for frame in frames if frame.kind == "state")
    assert state.data["output"] == ({"answer": "Done"} if valid_output else {}), state
    assert state.data["output_error"] == (None if valid_output else "output_contract_rejected")
    assert any(
        frame.kind == ("output.completed" if valid_output else "output.rejected")
        for frame in frames
    )
    assert frames[-1].kind == "stream.completed"
    assert all("arguments" not in frame.data and "result" not in frame.data for frame in frames)
    assert not any(
        frame.kind in {"output.text", "output.reset"}
        for frame in asyncio.run(read_stream(frames[-1].cursor))
    )
    reset = asyncio.run(read_stream(str(uuid4())))
    assert reset[0].kind == "stream.reset"
    old_headers = plugin_headers
    rotated = client.post(credential_url, json={"permissions": permissions})
    assert rotated.status_code == 201, rotated.text
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(read_stream())
    plugin_headers = {"Authorization": "Bearer " + rotated.json()["token"]}
    assert plugin_headers != old_headers
    assert asyncio.run(read_stream())[-1].kind == "stream.completed"
    foreign_owner, foreign_workspace = _seed_api_workspace(
        session, "stream-other@example.com", "stream-other"
    )
    denied = client.get(
        f"workspaces/{foreign_workspace.id}/automations/{automation_id}/events/{accepted.id}/stream",
        headers=_api_headers(foreign_owner.id),
        params={"once": True},
    )
    assert denied.status_code == 403
    # A subscriber must be authorized again when it reconnects.
    grant_url = f"{base}/access/automation/{automation_id}/grants/{employee.id}"
    assert client.put(grant_url, json={"actions": ["read"]}).status_code == 200
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(read_stream())
    assert client.put(grant_url, json={"actions": ["read", "invoke"]}).status_code == 200
    updated = client.put(
        f"{base}/automations/{automation_id}",
        json={
            "expected_version": 2,
            "status": "active",
            "configuration": {**config, "allowed_senders": ["other"]},
        },
    )
    assert updated.status_code == 200, updated.text
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(read_stream())


async def _approve_plugin_message(
    client, workspace, install_id, automation_id, accepted, approval, plugin_headers
):
    import httpx2 as httpx
    from opsmesh_plugin_sdk.client import PluginClient
    from opsmesh_plugin_sdk.messaging.contracts import ApprovalDecision

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client.app),
        base_url="http://testserver/api/v1/",
        headers=plugin_headers,
    ) as http:
        host = PluginClient(http, workspace.id, install_id)
        with pytest.raises(httpx.HTTPStatusError) as forbidden:
            await host.messages.decide_approval(
                automation_id,
                accepted.id,
                approval.id,
                ApprovalDecision(sender_id="unbound", decision="approve"),
            )
        assert forbidden.value.response.status_code == 403
        for _ in range(2):
            decision = await host.messages.decide_approval(
                automation_id,
                accepted.id,
                approval.id,
                ApprovalDecision(sender_id="user-1", decision="approve"),
            )
            assert decision.status == "approved"
        with pytest.raises(httpx.HTTPStatusError) as conflict:
            await host.messages.decide_approval(
                automation_id,
                accepted.id,
                approval.id,
                ApprovalDecision(sender_id="user-1", decision="reject"),
            )
        assert conflict.value.response.status_code == 409


def test_locked_nodes_and_incident_edges_require_authorized_admin_scope() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = OrchestrationDefinitionService(session)
    definition = service.create_definition(
        workspace.id,
        OrchestrationDefinitionCreateCommand(
            key="locked-flow",
            name="Locked flow",
            nodes=[
                WorkflowNode(package_id="source", title="Source", locked=True),
                WorkflowNode(package_id="review", title="Review"),
            ],
        ),
        user.id,
    )

    with pytest.raises(ValueError, match="privileged editor"):
        service.update_definition(
            workspace.id,
            definition.id,
            OrchestrationDefinitionUpdateCommand(
                expected_version=1,
                nodes=[
                    WorkflowNode(package_id="source", title="Changed", locked=True),
                    WorkflowNode(package_id="review", title="Review"),
                ],
            ),
            user.id,
        )
    session.rollback()

    with pytest.raises(ValueError, match="authorized edit scope"):
        service.update_definition(
            workspace.id,
            definition.id,
            OrchestrationDefinitionUpdateCommand(
                expected_version=1,
                nodes=[
                    WorkflowNode(package_id="source", title="Changed", locked=True),
                    WorkflowNode(package_id="review", title="Review"),
                ],
                edit_scope=OrchestrationEditScope(node_ids=["review"]),
            ),
            user.id,
            allow_locked_edits=True,
        )
    session.rollback()

    updated = service.update_definition(
        workspace.id,
        definition.id,
        OrchestrationDefinitionUpdateCommand(
            expected_version=1,
            nodes=[
                WorkflowNode(package_id="source", title="Source", locked=True),
                WorkflowNode(package_id="review", title="Review", depends_on=["source"]),
            ],
            edit_scope=OrchestrationEditScope(edge_ids=["source->review"]),
        ),
        user.id,
        allow_locked_edits=True,
    )
    assert updated.version == 2

    with pytest.raises(ValueError, match="authorized edit scope"):
        service.update_definition(
            workspace.id,
            definition.id,
            OrchestrationDefinitionUpdateCommand(
                expected_version=2,
                nodes=[
                    WorkflowNode(package_id="source", title="Changed", locked=True),
                    WorkflowNode(package_id="review", title="Review", depends_on=["source"]),
                ],
                edit_scope=OrchestrationEditScope(edge_ids=["source->review"]),
            ),
            user.id,
            allow_locked_edits=True,
        )


def test_api_locked_edit_rejection_is_recorded_in_audit_chain() -> None:
    client, session = _api_client()
    owner, workspace = _seed_api_workspace(session, "locked-audit@example.com", "locked-audit")
    path = f"/api/v1/workspaces/{workspace.id}/orchestrations"
    created = client.post(
        path,
        headers=_api_headers(owner.id),
        json={
            "key": "audit-locked",
            "name": "Audit locked",
            "nodes": [{"package_id": "protected", "title": "Protected", "locked": True}],
        },
    )
    assert created.status_code == 201, created.text
    definition_id = created.json()["id"]

    rejected = client.patch(
        f"{path}/{definition_id}",
        headers=_api_headers(owner.id),
        json={
            "expected_version": 1,
            "nodes": [{"package_id": "protected", "title": "Changed", "locked": True}],
        },
    )

    assert rejected.status_code == 409
    event = session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "orchestration.update_blocked",
            AuditEvent.target_id == str(definition_id),
        )
        .order_by(AuditEvent.created_at.desc())
    )
    assert event is not None
    assert "Changed" not in str(event.audit_metadata)


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
        json={
            "name": "operations-mcp",
            "connection": {"command": "mcp-test", "args": []},
        },
    )
    tool = client.post(
        f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-servers/{server.json()['id']}/tools",
        headers=headers,
        json={
            "tool_name": "execute_operation",
            "capability_key": "operations.execute",
            "connection": {"command": "mcp-test", "args": []},
        },
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
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, owner.id),
        input={"requires_review": True},
    )
    session.add(task)
    session.commit()

    definition_service = OrchestrationDefinitionService(session)
    definition = definition_service.create_definition(
        workspace.id,
        OrchestrationDefinitionCreateCommand(
            key="apply-flow",
            name="Apply flow",
            nodes=[
                WorkflowNode(
                    package_id="manager-review",
                    title="Manager review",
                    assigned_agent_profile_id=agent.id,
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
