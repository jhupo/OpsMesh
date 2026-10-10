from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.bootstrap.job_handlers import WorkerJobHandler
from opsmesh.capabilities.references.models import CapabilityResource
from opsmesh.capabilities.tools.contracts import ToolContext
from opsmesh.capabilities.tools.conversations import ConversationProductTools
from opsmesh.identity.authorization.models import ResourceGrant, SecuredResource
from opsmesh.identity.authorization.resources import ResourceAccessDenied
from opsmesh.identity.users.models import User
from opsmesh.orchestration.conversations.models import ConversationExecution, ConversationTurn
from opsmesh.orchestration.requests.builder import RunRequestBuilder
from opsmesh.orchestration.requests.sessions import RunRequestSessionService
from opsmesh.orchestration.runs.authorization.policy import RunRuntimeAuthorizationError
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task
from opsmesh.runtime.backends.docker import DockerRuntimeBackend
from opsmesh.runtime.backends.registry import RuntimeBackendRegistry
from opsmesh.runtime.instances.models import RuntimeTemplate, WorkspaceRuntime
from opsmesh.runtime.instances.run_environment import RunRuntimeEnvironmentService
from opsmesh.runtime.queues.contracts import JobPayload, JobType
from opsmesh.runtime.queues.dependencies import get_worker_queue
from opsmesh.runtime.queues.dispatch import QueueDispatchPublisher
from opsmesh.runtime.recovery.rehydration import QueueRehydrationService
from opsmesh.teams.management.models import AgentTeam, AgentTeamMember
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.test_agent_management_api import _client, _headers, _seed_workspace
from tests.test_run_runtime_environment import FakeDockerClient


def _advance(client, session) -> bool:
    queue = client.app.dependency_overrides[get_worker_queue]()
    QueueDispatchPublisher(session, queue).publish_pending()
    handled = False
    while lease := queue.dequeue_matching_with_lease(
        lambda job: job.job_type == JobType.CONVERSATION_ADVANCE
    ):
        WorkerJobHandler(session, queue, settings=client.app.state.settings).handle(lease.job)
        session.commit()
        queue.ack(lease.job, lease_token=lease.lease_token)
        QueueDispatchPublisher(session, queue).publish_pending()
        handled = True
    return handled


@pytest.mark.parametrize("mode", ["isolated", "shared"])
def test_chat_request_uses_the_leased_execution_runtime_and_releases_it(mode: str) -> None:
    from datetime import UTC, datetime

    client, session = _client()
    owner, workspace = _seed_workspace(session)
    template = RuntimeTemplate(
        name="Chat SDK",
        image="python@sha256:" + "0" * 64,
        default_limits={},
        default_network_policy={"mode": "none"},
        created_at=datetime.now(UTC),
    )
    session.add(template)
    session.flush()
    parent = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_template_id=template.id,
        name="Chat placement",
        execution_mode=mode,
        status="running",
        connection_status="online",
        docker_container_id="chat-container",
        network_policy={"mode": "none"},
        limits={
            "cpu_count": 1,
            "memory_mb": 512,
            "disk_mb": 1024,
            "timeout_seconds": 30,
            "max_output_bytes": 256000,
            "max_processes": 64,
            "max_concurrent_executions": 2,
        },
        capabilities={"isolation": {"workspace_mount": {"target": "/workspace"}}},
    )
    session.add(parent)
    session.flush()
    resource = CapabilityResource(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        key="chat-runtime",
        name="Chat runtime",
        resource_type="runtime",
        access_mode="execute",
        locator={"workspace_runtime_id": str(parent.id)},
    )
    session.add(resource)
    session.flush()
    profile = AgentProfile(
        workspace_id=workspace.id,
        name="Chat manager",
        role="manager",
        capabilities={"resource_ids": [str(resource.id)]},
    )
    session.add(profile)
    session.commit()
    root = f"/api/v1/workspaces/{workspace.id}/conversations"
    created = client.post(
        root,
        headers=_headers(owner.id),
        json={"mode": "agent", "agent_profile_id": str(profile.id)},
    )
    assert created.status_code == 201, created.text
    accepted = client.post(
        f"{root}/{created.json()['id']}/messages",
        headers={**_headers(owner.id), "Idempotency-Key": "runtime-chat"},
        json={"body": "Inspect the incident"},
    )
    assert accepted.status_code == 202, accepted.text
    assert _advance(client, session)
    session.commit()
    link = session.scalar(
        select(ConversationExecution).where(
            ConversationExecution.turn_id == UUID(accepted.json()["id"])
        )
    )
    run = session.scalar(select(AgentRun).where(AgentRun.task_id == link.task_id))
    docker = FakeDockerClient()
    environment = RunRuntimeEnvironmentService(session, docker)
    result = environment.ensure_for_run(run)
    session.commit()
    job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=run.id,
        idempotency_key="chat-sdk",
    )
    backends = RuntimeBackendRegistry({"cloud_docker": DockerRuntimeBackend(docker, lambda: 30)})
    request = RunRequestBuilder(
        session, client.app.state.settings, docker_client=docker, runtime_backends=backends
    ).build_agent_request(
        run,
        job,
        model_provider_override={
            "provider": "openai-compatible",
            "model": "offline",
            "model_api": "responses",
            "base_url": "http://offline.test/v1",
            "api_key": "offline",
            "model_provider_credential_id": None,
        },
    )
    assert request.sandbox is not None
    assert request.sandbox.session.executor.container_id == result.runtime.docker_container_id
    assert request.sandbox.session.persistent is (mode == "shared")
    assert request.context.metadata["runtime_execution"]["execution_runtime_id"] == str(
        result.runtime.id
    )
    if mode == "shared":
        from opsmesh.orchestration.runs.async_execution import AsyncAgentRunExecutor
        from opsmesh.orchestration.runs.control import stale_recovery_anchor
        from opsmesh.runtime.instances.models import RuntimeAllocation

        parent.limits = {**parent.limits, "max_concurrent_executions": 1}
        waiting = AgentRun(
            workspace_id=workspace.id, runtime_id=parent.id, status="queued", input={}
        )
        session.add(waiting)
        session.commit()
        queue = client.app.dependency_overrides[get_worker_queue]()
        executor = AsyncAgentRunExecutor(
            lambda: session, queue, client.app.state.settings, docker_client=docker
        )
        execution = executor._service(session)
        waiting_job = JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=waiting.id,
            idempotency_key="capacity-wait",
        )
        assert execution.prepare_run(waiting, waiting_job) is None
        assert waiting.status == "waiting_runtime" and waiting.started_at is None
        assert stale_recovery_anchor(waiting) is None
        assert QueueRehydrationService(session, queue).rehydrate_queued_runs().requeued_runs == 0
        assert environment.cleanup_for_run(run)
        session.commit()
        assert (
            session.scalar(select(RuntimeAllocation).where(RuntimeAllocation.owner_id == run.id))
            is None
        )
        assert QueueRehydrationService(session, queue).rehydrate_queued_runs().requeued_runs == 1
        assert waiting.status == "queued" and "runtime_capacity_waiting" not in waiting.input
        assert environment.ensure_for_run(waiting).runtime.id == parent.id
        assert environment.cleanup_for_run(waiting)
    assert environment.cleanup_for_run(run)
    if mode == "shared":
        assert docker.created == docker.removed == docker.removed_volumes == []
        with pytest.raises(RunRuntimeAuthorizationError) as error:
            RunRequestBuilder(
                session, client.app.state.settings, docker_client=docker, runtime_backends=backends
            ).build_agent_request(run, job, model_provider_override={})
        assert error.value.code == "runtime_execution_binding_invalid"


def test_conversation_failure_exposes_redacted_run_evidence_only_to_owner() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    other, _ = _seed_workspace(session, email="other-error@test.com", slug="other-error")
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=other.id, role="admin"))
    profile = AgentProfile(workspace_id=workspace.id, name="Manager", role="manager")
    session.add(profile)
    session.commit()
    root = f"/api/v1/workspaces/{workspace.id}/conversations"
    created = client.post(
        root,
        headers=_headers(owner.id),
        json={
            "mode": "agent",
            "agent_profile_id": str(profile.id),
        },
    ).json()
    url = f"{root}/{created['id']}"
    accepted = client.post(
        f"{url}/messages",
        headers={
            **_headers(owner.id),
            "Idempotency-Key": "provider-failure",
        },
        json={"body": "Check an incident"},
    ).json()
    assert _advance(client, session)
    session.commit()
    link = session.scalar(
        select(ConversationExecution).where(ConversationExecution.turn_id == UUID(accepted["id"]))
    )
    task = session.get(Task, link.task_id)
    run = session.scalar(select(AgentRun).where(AgentRun.task_id == task.id))
    task.status = run.status = "failed"
    run.error = {
        "code": "provider_request_failed",
        "message": "api_key=sk-error-secret",
        "retryable": False,
    }
    session.commit()
    assert _advance(client, session)
    session.commit()
    response = client.get(f"{url}/messages", headers=_headers(owner.id))
    turn = response.json()["items"][0]
    assert turn["error_code"] == "provider_request_failed"
    assert turn["error"]["code"] == "provider_request_failed"
    assert "sk-error-secret" not in response.text
    evidence = client.get(
        f"{url}/executions", params={"turn_id": accepted["id"]}, headers=_headers(owner.id)
    )
    assert evidence.json()["items"][0]["runs"][0]["id"] == str(run.id)
    assert "sk-error-secret" not in evidence.text
    assert client.get(f"{url}/messages", headers=_headers(other.id)).status_code == 404
    assert client.get(f"{url}/executions", headers=_headers(other.id)).status_code == 404
    assert (
        client.get(
            f"{url}/executions", params={"turn_id": str(uuid4())}, headers=_headers(owner.id)
        ).json()["items"]
        == []
    )


def test_conversation_delegation_recovery_and_idempotency() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="Manager",
        role="manager",
        instructions="Delegate as needed",
        tool_policy={
            "allowed_tools": ["discover_conversation_targets", "delegate_conversation_task"]
        },
    )
    expert = AgentProfile(
        workspace_id=workspace.id,
        name="Security",
        role="expert",
        description="Review security",
        instructions="Review security",
    )
    session.add_all([manager, expert])
    session.commit()
    team = AgentTeam(
        workspace_id=workspace.id, name="Security team", manager_agent_profile_id=expert.id
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=expert.id,
            team_role="manager",
        )
    )
    session.commit()
    prefix = f"/api/v1/workspaces/{workspace.id}/conversations"
    created = client.post(
        prefix,
        headers=_headers(owner.id),
        json={
            "mode": "auto",
            "agent_profile_id": str(manager.id),
        },
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    headers = {**_headers(owner.id), "Idempotency-Key": "message-1"}
    url = f"{prefix}/{cid}/messages"
    accepted = client.post(url, headers=headers, json={"body": "Review security"})
    assert accepted.status_code == 202, accepted.text
    assert (
        client.post(url, headers=headers, json={"body": "Review security"}).json()["id"]
        == accepted.json()["id"]
    )
    assert client.post(url, headers=headers, json={"body": "different"}).status_code == 409
    turn_id = UUID(accepted.json()["id"])
    assert _advance(client, session)
    session.commit()
    turn = session.get(ConversationTurn, turn_id)
    assert turn.status == "running", turn.error_code
    link = session.scalar(
        select(ConversationExecution).where(ConversationExecution.turn_id == turn_id)
    )
    run = session.scalar(select(AgentRun).where(AgentRun.task_id == link.task_id))
    assert run.agent_profile_id == manager.id
    queued = client.post(
        url,
        headers={**_headers(owner.id), "Idempotency-Key": "message-2"},
        json={"body": "What did you find?"},
    )
    assert queued.status_code == 202
    _advance(client, session)
    session.commit()
    assert session.get(ConversationTurn, UUID(queued.json()["id"])).status == "queued"
    queue = client.app.dependency_overrides[get_worker_queue]()
    assert QueueRehydrationService(session, queue).rehydrate_queued_runs().requeued_runs == 0
    queue.redis.flushall()
    assert QueueRehydrationService(session, queue).rehydrate_queued_runs().requeued_runs == 1
    assert QueueRehydrationService(session, queue).rehydrate_queued_runs().requeued_runs == 0
    with pytest.raises(RunRuntimeAuthorizationError, match="approved"):
        RunRequestBuilder(session, None).build_agent_request(
            run,
            JobPayload(
                workspace_id=workspace.id,
                job_type=JobType.AGENT_RUN,
                resource_id=run.id,
                idempotency_key="test",
            ),
        )
    run.status = "running"
    session.commit()
    context = ToolContext(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        task_id=link.task_id,
        allowed_tools=frozenset({"discover_conversation_targets", "delegate_conversation_task"}),
    )
    gateway = ConversationProductTools(session)
    assert any(item["id"] == str(expert.id) for item in gateway.discover(context)["items"])
    result = gateway.delegate(
        context, kind="agent", target_id=expert.id, body="Review permissions", request_key="review"
    )
    session.commit()
    assert (
        gateway.delegate(
            context,
            kind="agent",
            target_id=expert.id,
            body="Review permissions",
            request_key="review",
        )["task_id"]
        == result["task_id"]
    )
    team_result = gateway.delegate(
        context, kind="team", target_id=team.id, body="Review project", request_key="team-review"
    )
    parent = session.get(Task, link.task_id)
    parent.status = "completed"
    parent.final_output = {"final_output": "Submitted; waiting for real results"}
    session.commit()
    _advance(client, session)
    session.commit()
    assert turn.status == "waiting_tasks"
    child = session.get(Task, UUID(result["task_id"]))
    assert child.created_by_agent_run_id == run.id
    child.status = "completed"
    child.final_output = {"final_output": "No permission defect found"}
    team_task = session.get(Task, UUID(team_result["task_id"]))
    assert team_task.project_plan is not None, team_task.generic_state
    # Check session isolation independently of the team's model-provider admission gate.
    team_run = AgentRun(
        id=uuid4(), workspace_id=workspace.id, task_id=team_task.id, agent_profile_id=expert.id
    )
    reference = RunRequestSessionService(session).persistent_session_ref_for_run(
        team_run, team_task, expert
    )
    assert reference.scope_type == "task_agent"
    assert str(team_task.id) in reference.scope_id
    team_task.status = "completed"
    team_task.final_output = {"final_output": "Team review complete"}
    session.commit()
    _advance(client, session)
    session.commit()
    assert turn.round == 1
    resumed = session.scalar(
        select(Task)
        .join(ConversationExecution, ConversationExecution.task_id == Task.id)
        .where(ConversationExecution.turn_id == turn.id, ConversationExecution.round == 1)
    )
    assert "No permission defect found" in resumed.description
    resumed.status = "completed"
    resumed.final_output = {"final_output": "Reviewed permissions successfully"}
    session.commit()
    _advance(client, session)
    session.commit()
    response = client.get(url, headers=_headers(owner.id))
    assert response.json()["items"][0]["reply"] == "Reviewed permissions successfully"
    events_url = f"{prefix}/{cid}/events"
    events = client.get(events_url, headers=_headers(owner.id)).json()
    assert [item["status"] for item in events if item["turn_id"] == str(turn_id)] == [
        "queued",
        "running",
        "waiting_tasks",
        "running",
        "completed",
    ]
    assert (
        client.get(
            events_url, params={"after_id": events[-1]["id"]}, headers=_headers(owner.id)
        ).json()
        == []
    )


def test_conversation_is_private_and_cancelled_messages_are_not_dispatched() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    other, other_workspace = _seed_workspace(session, email="other@test.com", slug="other")
    profile = AgentProfile(
        workspace_id=workspace.id, name="Helper", role="assistant", instructions="Help"
    )
    session.add(profile)
    session.commit()
    prefix = f"/api/v1/workspaces/{workspace.id}/conversations"
    response = client.post(
        prefix,
        headers=_headers(owner.id),
        json={
            "mode": "agent",
            "agent_profile_id": str(profile.id),
        },
    )
    assert response.status_code == 201, response.text
    cid = response.json()["id"]
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=other.id, role="admin"))
    session.commit()
    assert client.get(f"{prefix}/{cid}", headers=_headers(other.id)).status_code == 404
    assert client.get(prefix, headers=_headers(other.id)).json()["total"] == 0
    assert (
        client.get(
            f"/api/v1/workspaces/{other_workspace.id}/conversations/{cid}",
            headers=_headers(other.id),
        ).status_code
        == 404
    )
    message = client.post(
        f"{prefix}/{cid}/messages",
        headers={**_headers(owner.id), "Idempotency-Key": "cancel-me"},
        json={"body": "hello"},
    )
    assert message.status_code == 202, message.text
    tid = message.json()["id"]
    assert (
        client.post(f"{prefix}/{cid}/turns/{tid}/cancel", headers=_headers(owner.id)).json()[
            "status"
        ]
        == "cancelled"
    )
    _advance(client, session)
    assert session.scalar(select(ConversationExecution.id)) is None
    accepted = client.post(
        f"{prefix}/{cid}/messages",
        headers={**_headers(owner.id), "Idempotency-Key": "follow-up"},
        json={"body": "What happened?"},
    )
    assert accepted.status_code == 202
    _advance(client, session)
    session.commit()
    task = session.scalar(
        select(Task)
        .join(ConversationExecution, ConversationExecution.task_id == Task.id)
        .where(ConversationExecution.turn_id == UUID(accepted.json()["id"]))
    )
    assert task.description == "What happened?"


def test_conversation_rechecks_revoked_membership_before_dispatch() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    profile = AgentProfile(
        workspace_id=workspace.id, name="Helper", role="assistant", instructions="Help"
    )
    session.add(profile)
    session.commit()
    prefix = f"/api/v1/workspaces/{workspace.id}/conversations"
    created = client.post(
        prefix,
        headers=_headers(owner.id),
        json={
            "mode": "agent",
            "agent_profile_id": str(profile.id),
        },
    )
    cid = created.json()["id"]
    accepted = client.post(
        f"{prefix}/{cid}/messages",
        headers={**_headers(owner.id), "Idempotency-Key": "revoke"},
        json={"body": "hello"},
    )
    member = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id, WorkspaceMember.user_id == owner.id
        )
    )
    member.status = "inactive"
    session.commit()
    assert _advance(client, session)
    session.commit()
    turn = session.get(ConversationTurn, UUID(accepted.json()["id"]))
    assert turn.status == "failed"
    assert turn.error_code == "conversation_admission_denied"
    assert session.scalar(select(ConversationExecution.id)) is None
    member.status = "active"
    session.commit()
    retry_url = f"{prefix}/{cid}/turns/{turn.id}/retry"
    retried = client.post(retry_url, headers=_headers(owner.id))
    assert retried.status_code == 200, retried.text
    assert _advance(client, session)
    session.commit()
    task = session.scalar(
        select(Task)
        .join(ConversationExecution, ConversationExecution.task_id == Task.id)
        .where(ConversationExecution.turn_id == turn.id)
    )
    run = session.scalar(select(AgentRun).where(AgentRun.task_id == task.id))
    task.status = "failed"
    run.status = "failed"
    session.commit()
    _advance(client, session)
    session.commit()
    retried = client.post(retry_url, headers=_headers(owner.id))
    assert retried.status_code == 200, retried.text
    session.expire_all()
    assert session.get(ConversationTurn, turn.id).status == "running"
    assert len(session.scalars(select(AgentRun).where(AgentRun.task_id == task.id)).all()) == 2


def test_operator_manager_cannot_discover_or_delegate_ungranted_expert() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    operator = User(email="operator@test.com", display_name="Operator")
    session.add(operator)
    session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=operator.id, role="operator"))
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="Manager",
        role="manager",
        instructions="Help",
        tool_policy={
            "allowed_tools": ["discover_conversation_targets", "delegate_conversation_task"]
        },
    )
    secret = AgentProfile(
        workspace_id=workspace.id, name="Private expert", role="expert", instructions="Private"
    )
    session.add_all([manager, secret])
    session.flush()
    for profile in (manager, secret):
        session.add(
            SecuredResource(
                workspace_id=workspace.id,
                resource_kind="agent",
                resource_id=profile.id,
                owner_user_id=owner.id,
            )
        )
    for action in ("read", "invoke"):
        session.add(
            ResourceGrant(
                workspace_id=workspace.id,
                resource_kind="agent",
                resource_id=manager.id,
                user_id=operator.id,
                action=action,
            )
        )
    session.commit()
    prefix = f"/api/v1/workspaces/{workspace.id}/conversations"
    response = client.post(
        prefix,
        headers=_headers(operator.id),
        json={"mode": "auto", "agent_profile_id": str(manager.id)},
    )
    assert response.status_code == 201, response.text
    cid = response.json()["id"]
    message = client.post(
        f"{prefix}/{cid}/messages",
        headers={**_headers(operator.id), "Idempotency-Key": "private"},
        json={"body": "ask the private expert"},
    )
    assert message.status_code == 202, message.text
    _advance(client, session)
    session.commit()
    turn = session.get(ConversationTurn, UUID(message.json()["id"]))
    assert turn.status == "running", turn.error_code
    link = session.scalar(
        select(ConversationExecution).where(ConversationExecution.turn_id == turn.id)
    )
    run = session.scalar(select(AgentRun).where(AgentRun.task_id == link.task_id))
    run.status = "running"
    session.commit()
    context = ToolContext(
        workspace_id=workspace.id,
        task_id=link.task_id,
        agent_run_id=run.id,
        allowed_tools=frozenset({"discover_conversation_targets", "delegate_conversation_task"}),
    )
    gateway = ConversationProductTools(session)
    assert gateway.discover(context)["items"] == []
    with pytest.raises(ResourceAccessDenied):
        gateway.delegate(
            context, kind="agent", target_id=secret.id, body="private", request_key="x"
        )


@pytest.mark.parametrize("target", ["auto", "agent", "team", "workflow"])
def test_conversation_runs_configured_expert_without_platform_prompts(target: str) -> None:
    from opsmesh.orchestration.definitions.commands import OrchestrationDefinitionCreate
    from opsmesh.orchestration.definitions.contracts import WorkflowNode
    from opsmesh.orchestration.definitions.service import OrchestrationDefinitionService
    from opsmesh.orchestration.tasks.models import TaskStep

    client, session = _client()
    owner, workspace = _seed_workspace(session)
    expert = AgentProfile(
        workspace_id=workspace.id,
        name="Custom expert",
        role="custom",
        instructions="Follow the user's requested process.",
    )
    session.add(expert)
    session.flush()
    payload = {"mode": target, "agent_profile_id": str(expert.id)}
    if target in {"team", "workflow"}:
        team = AgentTeam(
            workspace_id=workspace.id, name="Custom team", manager_agent_profile_id=expert.id
        )
        session.add(team)
        session.flush()
        session.add(
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=expert.id,
                team_role="custom",
            )
        )
        payload = {"mode": "team", "agent_team_id": str(team.id)}
        if target == "workflow":
            definitions = OrchestrationDefinitionService(session)
            definition = definitions.create_definition(
                workspace.id,
                OrchestrationDefinitionCreate(
                    key="conversation-flow",
                    name="Conversation flow",
                    nodes=[
                        WorkflowNode(
                            package_id="answer", title="Answer", assigned_agent_profile_id=expert.id
                        )
                    ],
                ),
                owner.id,
            )
            definitions.publish_definition(workspace.id, definition.id, owner.id)
            payload.update(orchestration_definition_id=str(definition.id), orchestration_version=1)
    session.commit()
    prefix = f"/api/v1/workspaces/{workspace.id}/conversations"
    created = client.post(prefix, headers=_headers(owner.id), json=payload)
    assert created.status_code == 201, created.text
    accepted = client.post(
        f"{prefix}/{created.json()['id']}/messages",
        headers={**_headers(owner.id), "Idempotency-Key": "configured-entry"},
        json={"body": "Answer my question"},
    )
    assert accepted.status_code == 202, accepted.text
    assert _advance(client, session)
    session.commit()
    turn = session.get(ConversationTurn, UUID(accepted.json()["id"]))
    assert turn.status == "running", turn.error_code
    execution = session.scalar(
        select(ConversationExecution).where(ConversationExecution.turn_id == turn.id)
    )
    run = session.scalar(select(AgentRun).where(AgentRun.task_id == execution.task_id))
    assert run.agent_profile_id == expert.id
    frozen = run.input["authorization_snapshot"]["agent_profile"]
    assert frozen["instructions"] == expert.instructions
    steps = session.scalars(select(TaskStep).where(TaskStep.task_id == execution.task_id)).all()
    assert len(steps) == (1 if target in {"team", "workflow"} else 0)
    assert all(not step.review_policy for step in steps)
    if target == "workflow":
        task = session.get(Task, execution.task_id)
        assert task.orchestration_definition_id == definition.id
        assert task.orchestration_version == 1


def test_conversation_reuses_sdk_history_across_turns_and_isolates_delegates() -> None:
    import asyncio

    from agents import Agent, Model, RunConfig, Runner, function_tool
    from agents.items import ModelResponse
    from agents.usage import Usage
    from openai.types.responses import (
        ResponseFunctionToolCall,
        ResponseOutputMessage,
        ResponseOutputText,
    )
    from sqlalchemy.orm import sessionmaker

    from opsmesh.agents.sessions.gateway import AuthorizedSDKSession
    from opsmesh.shared.concurrency import BlockingIO
    from opsmesh.shared.db.operations import DatabaseOperations

    client, session = _client()
    owner, workspace = _seed_workspace(session)
    profile = AgentProfile(
        workspace_id=workspace.id, name="Manager", role="manager", instructions="Help"
    )
    session.add(profile)
    session.commit()
    base = f"/api/v1/workspaces/{workspace.id}/conversations"
    created = client.post(
        base,
        headers=_headers(owner.id),
        json={"mode": "agent", "agent_profile_id": str(profile.id)},
    )
    assert created.status_code == 201
    cid = created.json()["id"]
    url = f"{base}/{cid}/messages"
    first = client.post(
        url,
        headers={**_headers(owner.id), "Idempotency-Key": "first"},
        json={"body": "Lookup the incident"},
    )
    second = client.post(
        url,
        headers={**_headers(owner.id), "Idempotency-Key": "second"},
        json={"body": "Explain that result"},
    )
    assert first.status_code == second.status_code == 202
    assert _advance(client, session)
    session.commit()
    first_turn = session.get(ConversationTurn, UUID(first.json()["id"]))
    first_link = session.scalar(
        select(ConversationExecution).where(ConversationExecution.turn_id == first_turn.id)
    )
    first_task = session.get(Task, first_link.task_id)
    first_run = session.scalar(select(AgentRun).where(AgentRun.task_id == first_task.id))
    assert first_task.description == first_turn.body
    assert session.get(ConversationTurn, UUID(second.json()["id"])).status == "queued"
    control_url = f"/api/v1/workspaces/{workspace.id}/tasks/{first_task.id}/control"
    for removed in (
        {"action": "add_instruction", "instruction": "Removed action"},
        {"action": "pause", "delivery_mode": "live"},
    ):
        rejected = client.post(control_url, headers=_headers(owner.id), json=removed)
        assert rejected.status_code == 422

    seen = []

    class OfflineModel(Model):
        async def get_response(self, *args, **kwargs):
            seen.append(kwargs["input"])
            output = (
                [
                    ResponseFunctionToolCall(
                        id="tool-1",
                        type="function_call",
                        name="lookup",
                        arguments="{}",
                        call_id="incident-1",
                    )
                ]
                if len(seen) == 1
                else [
                    ResponseOutputMessage(
                        id=f"message-{len(seen)}",
                        type="message",
                        role="assistant",
                        status="completed",
                        content=[
                            ResponseOutputText(
                                type="output_text", text="Found incident", annotations=[]
                            )
                        ],
                    )
                ]
            )
            return ModelResponse(output=output, usage=Usage(), response_id=f"offline-{len(seen)}")

        async def stream_response(self, *args, **kwargs):
            raise AssertionError("Streaming is not used in this history test")
            yield

    @function_tool
    def lookup() -> str:
        return "incident-code-42"

    sdk_agent = Agent(name="Offline", model=OfflineModel(), tools=[lookup])
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)

    def sdk_storage(run, task):
        service = RunRequestSessionService(session)
        ref = service.persistent_session_ref_for_run(run, task, profile)
        storage = service.persistent_session_for_run(
            run, task, profile, ref, sdk_provider="openai_agents"
        )
        session.commit()
        return ref, storage

    first_ref, first_storage = sdk_storage(first_run, first_task)

    async def execute(storage, run_id, text):
        with BlockingIO(1, name="conversation-history") as io:
            database = DatabaseOperations(factory, io, lambda: None)
            native = AuthorizedSDKSession(storage, database, run_id)
            try:
                return await Runner.run(
                    sdk_agent, text, session=native, run_config=RunConfig(tracing_disabled=True)
                )
            finally:
                await native.close()

    result = asyncio.run(execute(first_storage, first_run.id, first_task.description))
    first_run.status = first_task.status = "completed"
    first_task.final_output = {"final_output": result.final_output}
    session.commit()
    assert _advance(client, session)
    session.commit()
    second_link = session.scalar(
        select(ConversationExecution).where(
            ConversationExecution.turn_id == UUID(second.json()["id"])
        )
    )
    second_task = session.get(Task, second_link.task_id)
    second_run = session.scalar(select(AgentRun).where(AgentRun.task_id == second_task.id))
    assert second_task.id != first_task.id
    assert second_task.description == "Explain that result"
    second_ref, second_storage = sdk_storage(second_run, second_task)
    assert second_ref == first_ref
    assert second_ref.scope_type == "conversation_agent"
    asyncio.run(execute(second_storage, second_run.id, second_task.description))
    assert [item["content"] for item in seen[-1] if item.get("role") == "user"] == [
        "Lookup the incident",
        "Explain that result",
    ]
    assert any(item.get("output") == "incident-code-42" for item in seen[-1])
    assert any(item.get("type") == "function_call" for item in seen[-1])
    from opsmesh.orchestration.tasks.service import TaskCreateCommand, WorkspaceTaskService

    expert = AgentProfile(workspace_id=workspace.id, name="Expert", role="expert")
    session.add(expert)
    session.flush()
    delegated = WorkspaceTaskService(session).create_conversation_task(
        workspace_id=workspace.id,
        user_id=owner.id,
        execution_identity=second_task.execution_identity,
        command=TaskCreateCommand(
            title="Expert review",
            description="Independent review",
            agent_profile_id=expert.id,
        ),
        parent_run_id=second_run.id,
    )
    delegate_run = session.scalar(select(AgentRun).where(AgentRun.task_id == delegated.id))
    delegate_storage = RunRequestSessionService(session).persistent_session_for_run(
        delegate_run, delegated, expert, sdk_provider="openai_agents"
    )
    session.commit()
    assert delegate_storage.session_id != first_storage.session_id
    assert delegate_storage.session_id != second_storage.session_id
    delegated.status = delegate_run.status = "completed"
    delegated.final_output = {"final_output": "reviewed"}

    other = client.post(
        base,
        headers=_headers(owner.id),
        json={"mode": "agent", "agent_profile_id": str(profile.id)},
    ).json()
    other_message = client.post(
        f"{base}/{other['id']}/messages",
        headers={**_headers(owner.id), "Idempotency-Key": "other"},
        json={"body": "Independent question"},
    )
    assert other_message.status_code == 202
    # Finish the active turn so task notification advances the independent conversation.
    second_run.status = second_task.status = "completed"
    second_task.final_output = {"final_output": "done"}
    session.commit()
    for _ in range(3):
        _advance(client, session)
        session.commit()
    other_link = session.scalar(
        select(ConversationExecution).where(
            ConversationExecution.turn_id == UUID(other_message.json()["id"])
        )
    )
    other_task = session.get(Task, other_link.task_id)
    other_run = session.scalar(select(AgentRun).where(AgentRun.task_id == other_task.id))
    other_ref, other_storage = sdk_storage(other_run, other_task)
    assert other_ref.session_key != first_ref.session_key
    assert other_storage.session_id != first_storage.session_id

    async def verify_isolation():
        with BlockingIO(1, name="history-isolation") as io:
            database = DatabaseOperations(factory, io, lambda: None)
            native = AuthorizedSDKSession(other_storage, database, other_run.id)
            forged = AuthorizedSDKSession(first_storage, database, other_run.id)
            try:
                assert await native.get_items() == []
                with pytest.raises(ResourceAccessDenied):
                    await forged.get_items()
                with pytest.raises(ResourceAccessDenied):
                    await forged.add_items([{"role": "user", "content": "unauthorized"}])
            finally:
                await native.close()
                await forged.close()

    asyncio.run(verify_isolation())
    other_run.session_key = first_ref.session_key
    with pytest.raises(ResourceAccessDenied):
        RunRequestSessionService(session).persistent_session_ref_for_run(
            other_run, other_task, profile
        )
    session.rollback()
