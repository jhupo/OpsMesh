from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from backend.app.agents.profiles.models import AgentProfile
from backend.app.capabilities.tools.contracts import ToolContext
from backend.app.capabilities.tools.conversations import ConversationProductTools
from backend.app.identity.authorization.models import ResourceGrant, SecuredResource
from backend.app.identity.authorization.resources import ResourceAccessDenied
from backend.app.identity.users.models import User
from backend.app.orchestration.conversations.maintenance import ConversationMaintenanceService
from backend.app.orchestration.conversations.models import ConversationExecution, ConversationTurn
from backend.app.orchestration.requests.builder import RunRequestBuilder
from backend.app.orchestration.requests.sessions import RunRequestSessionService
from backend.app.orchestration.runs.authorization.policy import RunRuntimeAuthorizationError
from backend.app.orchestration.runs.models import AgentRun
from backend.app.orchestration.tasks.models import Task
from backend.app.runtime.queues.contracts import JobPayload, JobType
from backend.app.runtime.queues.dependencies import get_worker_queue
from backend.app.runtime.recovery.rehydration import QueueRehydrationService
from backend.app.teams.management.models import AgentTeam, AgentTeamMember
from backend.app.workspaces.members.models import WorkspaceMember
from backend.tests.test_agent_management_api import _client, _headers, _seed_workspace


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
    maintenance = ConversationMaintenanceService(session)
    assert maintenance.process_one()
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
    maintenance.process_one()
    session.commit()
    assert session.get(ConversationTurn, UUID(queued.json()["id"])).status == "queued"
    queue = client.app.dependency_overrides[get_worker_queue]()
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
    maintenance.process_one()
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
    maintenance.process_one()
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
    maintenance.process_one()
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
    assert not ConversationMaintenanceService(session).process_one()
    accepted = client.post(
        f"{prefix}/{cid}/messages",
        headers={**_headers(owner.id), "Idempotency-Key": "follow-up"},
        json={"body": "What happened?"},
    )
    assert accepted.status_code == 202
    ConversationMaintenanceService(session).process_one()
    session.commit()
    task = session.scalar(
        select(Task)
        .join(ConversationExecution, ConversationExecution.task_id == Task.id)
        .where(ConversationExecution.turn_id == UUID(accepted.json()["id"]))
    )
    assert '"status": "cancelled"' in task.description


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
    assert ConversationMaintenanceService(session).process_one()
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
    assert ConversationMaintenanceService(session).process_one()
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
    ConversationMaintenanceService(session).process_one()
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
    ConversationMaintenanceService(session).process_one()
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
    from backend.app.orchestration.definitions.commands import OrchestrationDefinitionCreate
    from backend.app.orchestration.definitions.contracts import WorkflowNode
    from backend.app.orchestration.definitions.service import OrchestrationDefinitionService
    from backend.app.orchestration.tasks.models import TaskStep

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
    assert ConversationMaintenanceService(session).process_one()
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
