from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from backend.app.domains.agents.profiles.models import AgentProfile
from backend.app.domains.orchestration.approvals.models import Approval
from backend.app.domains.orchestration.runs.eligibility import RunEligibilityService
from backend.app.domains.orchestration.runs.models import AgentRun
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
def test_configured_automation_admits_workflow_and_delivers_reply(
    trigger_type: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import base64
    import hashlib
    import socket
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    import httpx
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from opsmesh_plugin_sdk.packaging.distribution import (
        CatalogEntry,
        PluginCatalog,
        PluginReleaseDescriptor,
        sign_release,
    )
    from opsmesh_plugin_sdk.packaging.manifest import PluginManifest
    from opsmesh_plugin_sdk.packaging.packages import SignedPluginPackage, sign_package
    from sqlalchemy.orm import sessionmaker

    from backend.app.core.config import get_settings
    from backend.app.core.security.secrets import SecretEncryptionService
    from backend.app.domains.capabilities.plugins.downloads import PluginDownloadWorker
    from backend.app.domains.capabilities.plugins.models import PluginDownload
    from backend.app.domains.capabilities.plugins.transport import PluginHttpFetcher
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
    if trigger_type == "message":
        from backend.app.domains.access.models import User
        from backend.app.domains.workspace.tenants.models import WorkspaceMember

        employee = User(email="message-user@example.com", display_name="Message user")
        session.add(employee)
        session.flush()
        session.add(
            WorkspaceMember(workspace_id=workspace.id, user_id=employee.id, role="operator")
        )
        session.commit()
        for kind, response in (
            ("agent", agent),
            ("team", team),
            ("workflow", workflow),
            ("automation", created),
        ):
            granted = client.put(
                f"{base}/access/{kind}/{response.json()['id']}/grants/{employee.id}",
                headers=headers,
                json={"actions": ["read", "invoke"]},
            )
            assert granted.status_code == 200, granted.text
        bound = client.put(
            f"{base}/automations/{created.json()['id']}/identities/employee-1",
            headers=headers,
            json={"user_id": str(employee.id)},
        )
        assert bound.status_code == 200, bound.text
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
    remote_content: dict[str, bytes] = {}
    catalog_entries: list[CatalogEntry] = []
    source = None
    redirect_to_private = False
    original_resolve = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        if host == "plugins.example.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]
        return original_resolve(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)

    def download(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "plugins.example.test"
        assert request.extensions["sni_hostname"] == "plugins.example.test"
        if redirect_to_private:
            return httpx.Response(302, headers={"location": "https://127.0.0.1/release.json"})
        return httpx.Response(200, stream=httpx.ByteStream(remote_content[request.url.path]))

    worker = PluginDownloadWorker(
        sessionmaker(bind=session.get_bind(), expire_on_commit=False),
        PluginHttpFetcher(httpx.MockTransport(download)),
    )

    def prepare_candidate(signed_package):
        nonlocal source, redirect_to_private
        descriptor = sign_release(
            PluginReleaseDescriptor(
                package=SignedPluginPackage.model_validate(signed_package),
                platform_requires=">=0.1.0rc1,<1",
                sdk_requires=">=0.2,<1",
                license="Apache-2.0",
                source_repository="https://github.com/example/connector",
                source_commit="a" * 40,
            ),
            private.private_bytes_raw(),
        )
        version = descriptor.release.package.manifest.version
        release_path = f"/{version}.json"
        remote_content[release_path] = descriptor.model_dump_json().encode()
        catalog_entries.append(
            CatalogEntry(
                plugin_key="order.connector",
                version=version,
                publisher_key_id="connector-publisher",
                release_url="https://plugins.example.test" + release_path,
                sha256=hashlib.sha256(remote_content[release_path]).hexdigest(),
            )
        )
        remote_content["/catalog.json"] = (
            PluginCatalog(entries=catalog_entries).model_dump_json().encode()
        )
        settings = {
            "name": "Approved connectors",
            "url": "https://plugins.example.test/catalog.json",
            "sha256": hashlib.sha256(remote_content["/catalog.json"]).hexdigest(),
            "allowed_hosts": ["plugins.example.test", "127.0.0.1"],
        }
        if source is None:
            configured = client.post(f"{base}/plugins/sources", headers=headers, json=settings)
            assert configured.status_code == 201, configured.text
        else:
            configured = client.put(
                f"{base}/plugins/sources/{source['id']}",
                headers=headers,
                json={**settings, "expected_generation": source["generation"]},
            )
            assert configured.status_code == 200, configured.text
        source = configured.json()
        sync_url = f"{base}/plugins/sources/{source['id']}/sync"
        installed_before_sync = client.get(f"{base}/plugins", headers=headers).json()["items"]
        queued = client.post(sync_url, headers=headers, json={"request_key": "sync-" + version})
        assert queued.status_code == 202, queued.text
        assert (
            client.post(sync_url, headers=headers, json={"request_key": "sync-" + version}).json()[
                "id"
            ]
            == queued.json()["id"]
        )
        # Recover a process that died after durable claim, before making its network request.
        job = session.get(PluginDownload, UUID(queued.json()["id"]))
        job.status, job.lease_token = "fetching", uuid4()
        job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        job.attempts = 1
        session.commit()
        assert worker.run_once()
        status = client.get(f"{base}/plugins/downloads/{job.id}", headers=headers)
        assert status.json()["status"] == "succeeded", status.text
        candidates = client.get(f"{base}/plugins/candidates", headers=headers).json()["items"]
        assert client.get(f"{base}/plugins", headers=headers).json()["items"] == installed_before_sync
        candidate = next(item for item in candidates if item["version"] == version)
        candidate_path = f"{base}/plugins/candidates/{candidate['id']}"
        foreign_read = client.post(
            f"/api/v1/workspaces/{other_workspace.id}/plugins/candidates/{candidate['id']}/download",
            headers=_api_headers(other_owner.id),
            json={"request_key": "foreign"},
        )
        assert foreign_read.status_code == 404
        queued = client.post(
            candidate_path + "/download",
            headers=headers,
            json={"request_key": "release-" + version},
        )
        assert queued.status_code == 202, queued.text
        # A corrupted asset fails without modifying installation; retry uses the same pinned intent.
        job_url = f"{base}/plugins/downloads/{queued.json()['id']}"
        redirect_to_private = True
        assert worker.run_once()
        assert client.get(job_url, headers=headers).json()["error_code"] == "non_public_address"
        redirect_to_private = False
        assert client.post(job_url + "/retry", headers=headers).status_code == 202
        expected = remote_content[release_path]
        remote_content[release_path] = b"corrupt asset"
        assert worker.run_once()
        assert client.get(job_url, headers=headers).json()["error_code"] == "checksum_mismatch"
        active = client.get(f"{base}/plugins", headers=headers).json()["items"]
        assert not active or active[0]["current_version"] == "1.0.0"
        remote_content[release_path] = expected
        assert client.post(job_url + "/retry", headers=headers).status_code == 202
        assert worker.run_once()
        status = client.get(job_url, headers=headers)
        assert status.json()["status"] == "succeeded", status.text
        return candidate_path

    candidate_path = prepare_candidate(package)
    preview = client.post(candidate_path + "/preview", headers=headers, json={"bindings": bindings})
    assert preview.status_code == 200, preview.text
    approval = {
        "bindings": bindings,
        "approved_permissions": ["messages.send"],
        "preview_digest": preview.json()["preview_digest"],
    }
    assert (
        client.post(
            candidate_path + "/install",
            headers=headers,
            json={**approval, "preview_digest": "0" * 64},
        ).status_code
        == 409
    )
    assert (
        client.post(
            candidate_path + "/install",
            headers=headers,
            json={**approval, "approved_permissions": []},
        ).status_code
        == 403
    )
    installed = client.post(candidate_path + "/install", headers=headers, json=approval)
    assert installed.status_code == 201, installed.text
    assert (
        client.post(candidate_path + "/install", headers=headers, json=approval).status_code == 409
    )
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
    if trigger_type == "message":
        assert task.created_by_user_id == employee.id
        assert task.execution_identity["user_id"] == str(employee.id)
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
    candidate_path = prepare_candidate(
        sign_package(v2, "connector-publisher", private.private_bytes_raw()).model_dump(mode="json")
    )
    next_bindings = {"reply": {"resource_id": subscription_v2.json()["id"]}}
    preview = client.post(
        candidate_path + "/preview", headers=headers, json={"bindings": next_bindings}
    )
    assert preview.status_code == 200, preview.text
    upgraded = client.post(
        candidate_path + "/install",
        headers=headers,
        json={
            "bindings": next_bindings,
            "preview_digest": preview.json()["preview_digest"],
            "approved_permissions": ["messages.send"],
            "expected_generation": 3,
        },
    )
    assert upgraded.status_code == 201, upgraded.text
    assert upgraded.json()["current_version"] == "2.0.0"
    # Withdrawing a catalog version blocks new installation, not the installed release or rollback.
    catalog_entries[-1] = catalog_entries[-1].model_copy(update={"withdrawn": True})
    remote_content["/catalog.json"] = (
        PluginCatalog(entries=catalog_entries).model_dump_json().encode()
    )
    source_update = client.put(
        f"{base}/plugins/sources/{source['id']}",
        headers=headers,
        json={
            "name": source["name"],
            "url": source["url"],
            "sha256": hashlib.sha256(remote_content["/catalog.json"]).hexdigest(),
            "allowed_hosts": source["allowed_hosts"],
            "expected_generation": source["generation"],
        },
    )
    assert source_update.status_code == 200, source_update.text
    assert (
        client.post(
            f"{base}/plugins/sources/{source['id']}/sync",
            headers=headers,
            json={"request_key": "withdraw-version"},
        ).status_code
        == 202
    )
    assert worker.run_once()
    assert (
        client.post(
            candidate_path + "/preview", headers=headers, json={"bindings": next_bindings}
        ).status_code
        == 403
    )
    assert (
        client.get(f"{base}/plugins", headers=headers).json()["items"][0]["current_version"]
        == "2.0.0"
    )
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


def test_message_conversation_controls_and_continues_work_through_sdk() -> None:
    from datetime import UTC, datetime

    from opsmesh_plugin_sdk.messaging.client import AutomationClient
    from opsmesh_plugin_sdk.messaging.contracts import IncomingMessage
    from opsmesh_plugin_sdk.messaging.webhooks import parse_automation_delivery

    from backend.app.core.config import get_settings
    from backend.app.core.security.secrets import SecretEncryptionService
    from backend.app.domains.integrations.automations import AutomationService
    from backend.app.domains.integrations.webhooks.delivery import WebhookDeliveryService
    from backend.app.domains.integrations.webhooks.http_client import WebhookHttpResponse
    from backend.app.domains.integrations.webhooks.models import WebhookDeliveryAttempt
    from backend.app.domains.orchestration.tasks.models import TaskMessage
    from backend.app.runtime.workers.contracts import JobPayload, JobType
    from backend.tests.test_webhooks import _RecordingHttpClient
    from backend.tests.test_worker_run_execution import (
        _run_agent_sync,
        _seed_default_model_provider,
    )

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
    subscription = client.post(
        f"{base}/webhook-subscriptions",
        json={
            "name": "Replies",
            "target_url": "https://hooks.example.test/reply",
            "event_types": ["automation.reply"],
            "signing_secret": "message-signing-secret",
        },
    )
    assert subscription.status_code == 201, subscription.text
    config = {
        "name": "Message collaboration",
        "trigger_type": "message",
        "orchestration_definition_id": workflow.json()["id"],
        "orchestration_version": 1,
        "agent_team_id": team.json()["id"],
        "reply_subscription_id": subscription.json()["id"],
        "allowed_senders": ["member-1", "member-2"],
        "notify_progress": True,
        "allowed_message_actions": [
            "start",
            "follow_up",
            "add_instruction",
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
    service = AutomationService(session)
    service.maintain()
    state = sdk.state(accepted.id)
    assert state.event.task_id is not None, state
    task_id = state.event.task_id
    run = session.scalar(
        select(AgentRun).where(AgentRun.task_id == task_id, AgentRun.status == "queued")
    )
    assert run is not None
    settings = client.app.dependency_overrides[get_settings]()
    _run_agent_sync(
        session,
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            requested_by_user_id=owner.id,
            idempotency_key=f"run:{run.id}",
        ),
        settings=settings,
    )
    service.maintain()
    waiting = sdk.state(accepted.id)
    assert waiting.pending_actions, waiting
    approval_id = waiting.pending_actions[0].id
    count_before = session.query(WebhookDeliveryAttempt).count()
    service.maintain()
    assert session.query(WebhookDeliveryAttempt).count() == count_before

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
        service.maintain()
        assert sdk.state(response.id).event.task_id == task_id
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
    control("add_instruction", "facts")
    assert (
        session.scalar(
            select(TaskMessage).where(
                TaskMessage.task_id == task_id,
                TaskMessage.message_type == "task.control.add_instruction",
            )
        )
        is not None
    )
    control("pause", "takeover")
    session.expire_all()
    assert session.get(Approval, approval_id).status == "cancelled"
    assert sdk.state(accepted.id).task_status == "blocked"
    control("resume", "resume")
    assert (
        session.scalar(
            select(AgentRun.id).where(
                AgentRun.task_id == task_id,
                AgentRun.status == "queued",
            )
        )
        is not None
    )
    control("cancel", "cancel")
    assert sdk.state(accepted.id).task_status == "cancelled"
    assert session.query(Task).count() == 1

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
    service.maintain()
    next_state = sdk.state(followup.id)
    assert next_state.event.task_id != task_id
    next_task = session.get(Task, next_state.event.task_id)
    assert next_task.input["previous_context"]["task_id"] == str(task_id)
    assert session.query(Task).count() == 2

    transport = _RecordingHttpClient(WebhookHttpResponse(status_code=200, body="ok", headers={}))
    delivery_service = WebhookDeliveryService(
        session,
        SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
        ),
        transport,
    )
    attempt_id = sdk.state(accepted.id).event.reply_delivery_id
    assert attempt_id is not None
    delivery_service.deliver(workspace_id=workspace.id, delivery_attempt_id=attempt_id)
    delivery_service.deliver(workspace_id=workspace.id, delivery_attempt_id=attempt_id)
    assert len(transport.calls) == 1
    parsed = parse_automation_delivery(
        transport.calls[0]["body"],
        transport.calls[0]["headers"],
        secret="message-signing-secret",
        workspace_id=workspace.id,
        automation_id=automation_id,
    )
    assert parsed.data.kind == "result"
    assert parsed.data.sender_id == "member-1"
    assert parsed.data.status == "cancelled"
    service.maintain()
    assert sdk.state(accepted.id).event.status == "completed"

    pending = next(
        attempt
        for attempt in session.scalars(
            select(WebhookDeliveryAttempt).where(WebhookDeliveryAttempt.status == "pending")
        )
        if attempt.payload.get("event_id") == str(followup.id)
    )
    updated = client.put(
        f"{base}/automations/{automation_id}",
        json={
            "expected_version": 1,
            "status": "active",
            "configuration": {**config, "allowed_senders": ["member-2"]},
        },
    )
    assert updated.status_code == 200, updated.text
    refused = delivery_service.deliver(workspace_id=workspace.id, delivery_attempt_id=pending.id)
    assert refused.status == "dead_lettered"
    assert len(transport.calls) == 1


@pytest.mark.parametrize("valid_output", [True, False])
def test_structured_messages_stream_before_model_completion_and_replay(
    monkeypatch: pytest.MonkeyPatch, valid_output: bool
) -> None:
    import asyncio
    import base64
    from datetime import UTC, datetime

    import httpx
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from opsmesh_plugin_sdk.client import PluginClient
    from opsmesh_plugin_sdk.messaging.client import AsyncAutomationClient, AutomationClient
    from opsmesh_plugin_sdk.messaging.contracts import (
        ApprovalDecision,
        AttachmentUpload,
        IncomingMessage,
    )
    from opsmesh_plugin_sdk.packaging.manifest import PluginManifest
    from opsmesh_plugin_sdk.packaging.packages import sign_package
    from opsmesh_plugin_sdk.services.identity import PermissionQuery
    from opsmesh_plugin_sdk.services.observability import PluginLog
    from opsmesh_plugin_sdk.services.storage import StoreWrite

    from backend.app.api.dependencies.queue import get_worker_queue
    from backend.app.api.dependencies.redis import get_redis_client
    from backend.app.core.config import get_settings
    from backend.app.domains.access.models import User
    from backend.app.domains.agents.runtime.contracts import (
        AgentRunResult,
        AgentRuntimeStructuredOutput,
    )
    from backend.app.domains.agents.runtime.observer import AgentRuntimeExecutionObserver
    from backend.app.domains.integrations.automations import AutomationService
    from backend.app.domains.workspace.reviews.model_request import (
        ModelRequestReview,
        ModelRequestReviewService,
    )
    from backend.app.domains.workspace.reviews.models import ResourceReview
    from backend.app.domains.workspace.reviews.service import ResourcePolicyReviewBuilder
    from backend.app.domains.workspace.tenants.models import WorkspaceMember
    from backend.app.runtime.workers.contracts import JobPayload, JobType
    from backend.tests.test_webhooks import _queue
    from backend.tests.test_worker_run_execution import (
        _run_agent_sync,
        _seed_default_model_provider,
    )

    client, session = _api_client()
    owner, workspace = _seed_api_workspace(session, "stream@example.com", "stream")
    monkeypatch.setattr(
        ModelRequestReviewService,
        "review_request",
        lambda self, **kwargs: ModelRequestReview(
            required=False, risk_level="low", reasons=[], signals={}
        ),
    )
    monkeypatch.setattr(
        ResourcePolicyReviewBuilder,
        "review_tool_execution",
        lambda self, **kwargs: ResourceReview(
            required=False, risk_level="low", reasons=[], signals={}
        ),
    )
    client.app.state.settings.credential_encryption_secret = (
        "change-me-credential-encryption-secret"
    )
    _seed_default_model_provider(session, workspace_id=workspace.id, user_id=owner.id)
    queue = _queue()
    client.app.dependency_overrides[get_redis_client] = lambda: queue.redis
    client.app.dependency_overrides[get_worker_queue] = lambda: queue
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
                        "kind": "message_trigger",
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
            "bindings": {"receive": {"resource_id": str(automation_id)}},
            "approved_permissions": permissions,
        },
    )
    assert installed.status_code == 201, installed.text
    install_id = UUID(installed.json()["id"])
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
                "delivery:1", StoreWrite(expected_revision=0, value={"cursor": "0-0"})
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
            attachment = await host.messages.upload_attachment(
                automation_id, upload, b"order 123: timeout"
            )
            assert (
                await host.messages.upload_attachment(automation_id, upload, b"order 123: timeout")
            ) == attachment
            with pytest.raises(httpx.HTTPStatusError) as conflict:
                await host.messages.upload_attachment(automation_id, upload, b"different content")
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
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app),
            base_url="http://testserver/api/v1/",
            headers=plugin_headers,
        ) as http:
            connector = PluginClient(http, workspace.id, install_id).automation(automation_id)
            with pytest.raises(httpx.HTTPStatusError) as wrong_event:
                await connector.submit(message.model_copy(update={"event_id": "different-event"}))
            assert wrong_event.value.response.status_code == 403
            accepted = await connector.submit(message)
            assert (await connector.submit(message)).id == accepted.id
            return accepted

    accepted = asyncio.run(submit_plugin())
    service = AutomationService(session)
    service.maintain()
    task_id = sdk.state(accepted.id).event.task_id
    task = session.get(Task, task_id)
    assert task.input["event"]["data"] == {"question": "What is the result?"}
    observed = []

    async def read_stream(cursor: str = "0-0") -> list:
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

    class StreamingModel:
        async def run(self, request):
            assert len(request.attachments) == 1
            assert request.attachments[0].content == b"order 123: timeout"
            assert request.context.user_id == employee.id
            assert "private-name" not in request.input_text
            assert request.event_sink is not None
            observer = AgentRuntimeExecutionObserver(request)
            observer.start()
            observer.stream("output.text.delta", delta="Working on your question")
            before = await read_stream()
            assert any(frame.kind == "output.text" for frame in before)
            assert not any(frame.kind == "stream.completed" for frame in before)
            observed.extend(before)
            result = await request.tool_executor.execute_tool(
                context=request.context,
                tool_name="get_agent_inbox",
                arguments={},
                tool_call_id="inbox-1",
            )
            assert result.status == "completed", result
            observer.stream("output.text.delta", delta=". Finished.")
            answer = AgentRunResult(
                final_output='{"answer":"Done"}',
                structured_output=AgentRuntimeStructuredOutput(
                    value={"answer": "Done"}, validated=True
                ),
            )
            observer.finish(answer)
            return answer

    run = session.scalar(
        select(AgentRun).where(AgentRun.task_id == task_id, AgentRun.status == "queued")
    )
    assert run is not None
    settings = client.app.dependency_overrides[get_settings]()
    monkeypatch.setattr(
        ModelRequestReviewService,
        "review_request",
        lambda self, **kwargs: ModelRequestReview(
            required=True, risk_level="high", reasons=["flow approval"], signals={}
        ),
    )
    waiting = _run_agent_sync(
        session,
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            requested_by_user_id=employee.id,
            idempotency_key=f"approval-gate:{run.id}",
        ),
        agent_runner=StreamingModel(),
        settings=settings,
        queue=queue,
    )
    assert waiting.status == "waiting_approval"
    approval = sdk.state(accepted.id).pending_actions[0]

    async def approve_message():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app),
            base_url="http://testserver/api/v1/",
            headers=plugin_headers,
        ) as http:
            host = PluginClient(http, workspace.id, install_id)
            with pytest.raises(httpx.HTTPStatusError) as wrong_sender:
                await host.messages.decide_approval(
                    automation_id,
                    accepted.id,
                    approval.id,
                    ApprovalDecision(sender_id="unbound", decision="approve"),
                )
            assert wrong_sender.value.response.status_code == 403
            for _ in range(2):
                result = await host.messages.decide_approval(
                    automation_id,
                    accepted.id,
                    approval.id,
                    ApprovalDecision(sender_id="user-1", decision="approve"),
                )
                assert result.status == "approved"
            with pytest.raises(httpx.HTTPStatusError) as conflict:
                await host.messages.decide_approval(
                    automation_id,
                    accepted.id,
                    approval.id,
                    ApprovalDecision(sender_id="user-1", decision="reject"),
                )
            assert conflict.value.response.status_code == 409

    asyncio.run(approve_message())
    executed = _run_agent_sync(
        session,
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            requested_by_user_id=owner.id,
            idempotency_key=f"stream:{run.id}",
        ),
        agent_runner=StreamingModel(),
        settings=settings,
        queue=queue,
    )
    assert executed.status == "completed", executed.error
    assert str(employee.id) in executed.session_key
    service.maintain()
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
    assert (
        client.get(f"{event_path}/{accepted.id}", headers=_api_headers(employee.id)).status_code
        == 200
    )
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
        frame.kind == "tool.started" and frame.data["call_id"] == "inbox-1" for frame in frames
    )
    assert any(frame.kind == "tool.completed" for frame in frames)
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
    reset = asyncio.run(read_stream("1-0"))
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
            "expected_version": 1,
            "status": "active",
            "configuration": {**config, "allowed_senders": ["other"]},
        },
    )
    assert updated.status_code == 200, updated.text
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(read_stream())


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
