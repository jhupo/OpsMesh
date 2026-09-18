from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from backend.app.domains.agents.profiles.models import AgentProfile
from backend.app.domains.orchestration.approvals.models import Approval
from backend.app.domains.orchestration.runs.eligibility import RunEligibilityService
from backend.app.domains.orchestration.tasks.models import Task, TaskMessage, TaskStep
from backend.app.domains.orchestration.tasks.observation.execution import (
    TaskExecutionDiagnosticsService,
)
from backend.app.domains.orchestration.workflows.definitions.application import (
    OrchestrationDefinitionApplicationService,
)
from backend.app.domains.orchestration.workflows.definitions.commands import (
    OrchestrationDefinitionCreate,
    OrchestrationDefinitionUpdate,
    OrchestrationEditScope,
)
from backend.app.domains.orchestration.workflows.definitions.conditions import (
    evaluate_task_step_condition,
    validate_condition,
)
from backend.app.domains.orchestration.workflows.definitions.contracts import (
    WorkflowCondition,
    WorkflowNode,
)
from backend.app.domains.orchestration.workflows.definitions.service import (
    OrchestrationDefinitionService,
)
from backend.app.domains.orchestration.workflows.planning.attempt_models import TaskPlanningAttempt
from backend.app.domains.workspace.teams.models import AgentTeam, AgentTeamMember
from backend.app.observability.audit.models import AuditEvent
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
        OrchestrationDefinitionCreate(
            key="plain", name="Plain", nodes=[WorkflowNode(package_id="plain", title="Plain")]
        ),
        user.id,
    )
    service.publish_definition(workspace.id, definition.id, user.id)
    from backend.app.domains.orchestration.models import OrchestrationRevision

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


def test_authored_plan_cannot_be_replaced_by_automatic_regeneration() -> None:
    from backend.app.domains.orchestration.workflows.planning.lifecycle import (
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
    from backend.app.domains.orchestration.runs.models import AgentRun

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
def test_configured_automation_admits_workflow_and_delivers_reply(trigger_type: str) -> None:
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from opsmesh_plugin_sdk.contracts import PluginManifest
    from opsmesh_plugin_sdk.packages import sign_package

    from backend.app.core.config import get_settings
    from backend.app.core.security.secrets import SecretEncryptionService
    from backend.app.domains.integrations.automations import AutomationService
    from backend.app.domains.integrations.webhooks.delivery import WebhookDeliveryService
    from backend.app.domains.integrations.webhooks.http_client import WebhookHttpResponse
    from backend.app.domains.integrations.webhooks.models import WebhookDeliveryAttempt
    from backend.tests.test_webhooks import _RecordingHttpClient

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
    subscription = client.post(
        f"{base}/webhook-subscriptions",
        headers=headers,
        json={
            "name": "Connector replies",
            "target_url": "https://hooks.example.test/replies",
            "event_types": ["automation.reply"],
            "signing_secret": "connector-signing-secret",
        },
    )
    assert subscription.status_code == 201, subscription.text
    config = {
        "name": "Order assistance",
        "trigger_type": trigger_type,
        "orchestration_definition_id": workflow.json()["id"],
        "orchestration_version": 1,
        "agent_team_id": team.json()["id"],
        "reply_subscription_id": subscription.json()["id"],
    }
    if trigger_type == "message":
        config["allowed_senders"] = ["employee-1"]
    else:
        config["schedule_type"] = "one_shot"
        config["schedule_config"] = {"run_at": "2026-01-01T00:00:00Z"}
    created = client.post(f"{base}/automations", headers=headers, json=config)
    assert created.status_code == 201, created.text
    path = f"{base}/automations/{created.json()['id']}/events"
    private = Ed25519PrivateKey.generate()
    publisher = client.post(
        f"{base}/plugins/trust-keys",
        headers=headers,
        json={
            "key_id": "connector-publisher",
            "plugin_key": "order.connector",
            "public_key": base64.b64encode(private.public_key().public_bytes_raw()).decode(),
        },
    )
    assert publisher.status_code == 201, publisher.text
    capabilities = [
        {
            "key": "reply",
            "kind": "reply_channel",
            "title": "Reply",
            "required_permissions": ["messages.send"],
        }
    ]
    bindings = {"reply": {"resource_id": subscription.json()["id"]}}
    if trigger_type == "message":
        capabilities.append({"key": "receive", "kind": "message_trigger", "title": "Receive"})
        bindings["receive"] = {"resource_id": created.json()["id"]}
    manifest = PluginManifest.model_validate(
        {
            "key": "order.connector",
            "version": "1.0.0",
            "name": "Order connector",
            "capabilities": capabilities,
        }
    )
    package = sign_package(manifest, "connector-publisher", private.private_bytes_raw()).model_dump(
        mode="json"
    )
    installation = {
        "package": package,
        "bindings": bindings,
        "approved_permissions": ["messages.send"],
    }
    invalid = {**package, "signature": base64.b64encode(bytes(64)).decode()}
    assert (
        client.post(
            f"{base}/plugins", headers=headers, json={**installation, "package": invalid}
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{base}/plugins", headers=headers, json={**installation, "approved_permissions": []}
        ).status_code
        == 403
    )
    other_owner, other_workspace = _seed_api_workspace(
        session, "other-plugin@example.com", "other-plugin"
    )
    foreign = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/webhook-subscriptions",
        headers=_api_headers(other_owner.id),
        json={
            "name": "Foreign",
            "target_url": "https://hooks.example.test/foreign",
            "event_types": ["automation.reply"],
            "signing_secret": "foreign-signing-secret",
        },
    )
    assert foreign.status_code == 201
    assert (
        client.post(
            f"{base}/plugins",
            headers=headers,
            json={
                **installation,
                "bindings": {**bindings, "reply": {"resource_id": foreign.json()["id"]}},
            },
        ).status_code
        == 400
    )
    installed = client.post(f"{base}/plugins", headers=headers, json=installation)
    assert installed.status_code == 201, installed.text
    action_path = f"{base}/plugins/{installed.json()['id']}/actions"
    disabled = client.post(
        action_path, headers=headers, json={"action": "disable", "expected_generation": 1}
    )
    assert disabled.status_code == 200, disabled.text
    if trigger_type == "message":
        message = {
            "event_id": "message-1",
            "conversation_id": "conversation-1",
            "sender_id": "employee-1",
            "occurred_at": "2026-09-18T00:00:00Z",
            "text": "Check order 123",
            "data": {"order_id": "123"},
        }
        assert client.post(path, headers=headers, json=message).status_code == 403
        enabled = client.post(
            action_path, headers=headers, json={"action": "enable", "expected_generation": 2}
        )
        assert enabled.status_code == 200, enabled.text
        accepted = client.post(path, headers=headers, json=message)
        assert accepted.status_code == 202, accepted.text
        duplicate = client.post(path, headers=headers, json=message)
        assert duplicate.json()["id"] == accepted.json()["id"]
        assert (
            client.post(path, headers=headers, json={**message, "text": "Changed"}).status_code
            == 400
        )
        assert (
            client.post(
                path,
                headers=headers,
                json={
                    **message,
                    "event_id": "denied",
                    "sender_id": "untrusted",
                },
            ).status_code
            == 400
        )

    if trigger_type == "schedule":
        assert (
            client.post(
                action_path, headers=headers, json={"action": "enable", "expected_generation": 2}
            ).status_code
            == 200
        )

    AutomationService(session).maintain()
    events = client.get(path, headers=headers).json()
    assert len(events) == 1
    assert events[0]["status"] == "reply_pending", events
    task = session.get(Task, UUID(events[0]["task_id"]))
    assert task is not None and task.status == "completed"
    assert task.orchestration_version == 1
    AutomationService(session).maintain()
    assert session.query(Task).count() == 1
    assert session.query(WebhookDeliveryAttempt).count() == 1
    transport = _RecordingHttpClient(WebhookHttpResponse(status_code=200, body="ok", headers={}))
    settings = client.app.dependency_overrides[get_settings]()
    delivery_service = WebhookDeliveryService(
        session,
        SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
        ),
        transport,
    )
    delivery = delivery_service.deliver(
        workspace_id=workspace.id, delivery_attempt_id=UUID(events[0]["reply_delivery_id"])
    )
    assert delivery.status == "succeeded"
    assert len(transport.calls) == 1
    assert str(task.id).encode() in transport.calls[0]["body"]
    AutomationService(session).maintain()
    assert client.get(path, headers=headers).json()[0]["status"] == "completed"
    blocked = client.post(
        action_path, headers=headers, json={"action": "uninstall", "expected_generation": 3}
    )
    assert blocked.status_code == 409
    subscription_v2 = client.post(
        f"{base}/webhook-subscriptions",
        headers=headers,
        json={
            "name": "Connector replies v2",
            "target_url": "https://hooks.example.test/v2",
            "event_types": ["automation.reply"],
            "signing_secret": "connector-signing-secret-v2",
        },
    )
    v2 = manifest.model_copy(update={"version": "2.0.0", "capabilities": manifest.capabilities[:1]})
    upgraded = client.post(
        f"{base}/plugins",
        headers=headers,
        json={
            "package": sign_package(
                v2, "connector-publisher", private.private_bytes_raw()
            ).model_dump(mode="json"),
            "bindings": {"reply": {"resource_id": subscription_v2.json()["id"]}},
            "approved_permissions": ["messages.send"],
            "expected_generation": 3,
        },
    )
    assert upgraded.status_code == 201, upgraded.text
    assert upgraded.json()["current_version"] == "2.0.0"
    rollback = client.post(
        action_path,
        headers=headers,
        json={
            "action": "switch_version",
            "version": "1.0.0",
            "expected_generation": 4,
        },
    )
    assert rollback.status_code == 200, rollback.text
    retired = client.post(
        action_path,
        headers=headers,
        json={
            "action": "retire_version",
            "version": "2.0.0",
            "expected_generation": 5,
        },
    )
    assert retired.status_code == 200, retired.text
    pending_reply = delivery_service.enqueue_event(
        workspace_id=workspace.id,
        event_type="automation.reply",
        payload={"task_id": str(task.id)},
        subscription_id=UUID(subscription.json()["id"]),
    )[0]
    session.commit()
    revoked = client.post(
        f"{base}/plugins/trust-keys/{publisher.json()['id']}/revoke", headers=headers
    )
    assert revoked.status_code == 200
    denied_reply = delivery_service.deliver(
        workspace_id=workspace.id,
        delivery_attempt_id=pending_reply.id,
    )
    assert denied_reply.status == "dead_lettered"
    assert len(transport.calls) == 1
    assert (
        client.post(
            action_path,
            headers=headers,
            json={
                "action": "enable",
                "expected_generation": 6,
            },
        ).status_code
        == 403
    )
    if trigger_type == "message":
        assert (
            client.post(
                path, headers=headers, json={**message, "event_id": "after-revoke"}
            ).status_code
            == 403
        )


def test_locked_nodes_and_incident_edges_require_authorized_admin_scope() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = OrchestrationDefinitionService(session)
    definition = service.create_definition(
        workspace.id,
        OrchestrationDefinitionCreate(
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
            OrchestrationDefinitionUpdate(
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
            OrchestrationDefinitionUpdate(
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
        OrchestrationDefinitionUpdate(
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
            OrchestrationDefinitionUpdate(
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
