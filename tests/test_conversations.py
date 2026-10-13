import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.orchestration.conversations.models import ConversationExecution, ConversationTurn
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task
from opsmesh.teams.management.models import AgentTeam, AgentTeamMember
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.model_provider import seed_default_model_provider
from tests.test_agent_management_api import _headers, _seed_workspace

pytest_plugins = ["tests.fixtures.agent_api"]
pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required"
)


def _advance(session, workspace_id):
    from sqlalchemy.orm import sessionmaker

    from opsmesh.orchestration.conversations.activities import ConversationActivities
    from opsmesh.orchestration.conversations.models import Conversation
    from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest

    ids = session.scalars(
        select(Conversation.id).where(Conversation.workspace_id == workspace_id)
    ).all()
    factory = sessionmaker(session.get_bind(), expire_on_commit=False)
    for conversation_id in ids:
        ConversationActivities(factory).advance(
            ExecutionRequest(
                workspace_id=workspace_id,
                resource_id=conversation_id,
                kind=ExecutionKind.CONVERSATION,
            )
        )
    session.expire_all()
    return bool(ids)


def test_conversation_failure_exposes_redacted_run_evidence_only_to_owner(agent_api) -> None:
    client, session = agent_api
    owner, workspace = _seed_workspace(session)
    seed_default_model_provider(session, workspace_id=workspace.id, user_id=owner.id)
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
    assert _advance(session, workspace.id)
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
    assert _advance(session, workspace.id)
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


def test_conversation_persists_expert_reply_without_creating_a_summary_task(agent_api) -> None:
    client, session = agent_api
    owner, workspace = _seed_workspace(session)
    seed_default_model_provider(session, workspace_id=workspace.id, user_id=owner.id)
    router = AgentProfile(
        workspace_id=workspace.id, name="Router", role="router", instructions="Route"
    )
    session.add(router)
    session.commit()
    prefix = f"/api/v1/workspaces/{workspace.id}/conversations"
    created = client.post(
        prefix,
        headers=_headers(owner.id),
        json={"mode": "auto", "agent_profile_id": str(router.id)},
    )
    assert created.status_code == 201, created.text
    url = f"{prefix}/{created.json()['id']}"
    headers = {**_headers(owner.id), "Idempotency-Key": "first"}
    accepted = client.post(f"{url}/messages", headers=headers, json={"body": "Analyze logs"})
    assert accepted.status_code == 202
    assert (
        client.post(f"{url}/messages", headers=headers, json={"body": "Analyze logs"}).json()["id"]
        == accepted.json()["id"]
    )
    assert (
        client.post(f"{url}/messages", headers=headers, json={"body": "Different"}).status_code
        == 409
    )
    assert _advance(session, workspace.id)
    session.commit()
    turn = session.get(ConversationTurn, UUID(accepted.json()["id"]))
    link = session.scalar(
        select(ConversationExecution).where(ConversationExecution.turn_id == turn.id)
    )
    task = session.get(Task, link.task_id)
    final_agent = {"name": "Expert", "profile_id": str(uuid4()), "role": "expert"}
    task.status = "completed"
    task.final_output = {"final_output": "Full expert answer", "final_agent": final_agent}
    session.commit()
    assert _advance(session, workspace.id)
    session.commit()
    assert turn.reply == "Full expert answer" and turn.final_agent == final_agent
    assert (
        len(
            session.scalars(
                select(ConversationExecution).where(ConversationExecution.turn_id == turn.id)
            ).all()
        )
        == 1
    )
    response = client.get(f"{url}/messages", headers=_headers(owner.id)).json()["items"][0]
    assert response["final_agent"] == final_agent and "round" not in response
    events = client.get(f"{url}/events", headers=_headers(owner.id)).json()
    assert [event["status"] for event in events] == ["queued", "running", "completed"]


def test_conversation_is_private_and_cancelled_messages_are_not_dispatched(agent_api) -> None:
    client, session = agent_api
    owner, workspace = _seed_workspace(session)
    seed_default_model_provider(session, workspace_id=workspace.id, user_id=owner.id)
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
    _advance(session, workspace.id)
    assert session.scalar(select(ConversationExecution.id)) is None
    accepted = client.post(
        f"{prefix}/{cid}/messages",
        headers={**_headers(owner.id), "Idempotency-Key": "follow-up"},
        json={"body": "What happened?"},
    )
    assert accepted.status_code == 202
    _advance(session, workspace.id)
    session.commit()
    task = session.scalar(
        select(Task)
        .join(ConversationExecution, ConversationExecution.task_id == Task.id)
        .where(ConversationExecution.turn_id == UUID(accepted.json()["id"]))
    )
    assert task.description == "What happened?"


def test_conversation_rechecks_revoked_membership_before_dispatch(agent_api) -> None:
    client, session = agent_api
    owner, workspace = _seed_workspace(session)
    seed_default_model_provider(session, workspace_id=workspace.id, user_id=owner.id)
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
    assert _advance(session, workspace.id)
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
    assert _advance(session, workspace.id)
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
    _advance(session, workspace.id)
    session.commit()
    retried = client.post(retry_url, headers=_headers(owner.id))
    assert retried.status_code == 200, retried.text
    _advance(session, workspace.id)
    session.expire_all()
    assert session.get(ConversationTurn, turn.id).status == "running"
    assert len(session.scalars(select(AgentRun).where(AgentRun.task_id == task.id)).all()) == 2


@pytest.mark.parametrize("target", ["auto", "agent", "team", "workflow"])
def test_conversation_runs_configured_expert_without_platform_prompts(
    agent_api, target: str
) -> None:
    from opsmesh.orchestration.definitions.commands import OrchestrationDefinitionCreateCommand
    from opsmesh.orchestration.definitions.contracts import WorkflowNode
    from opsmesh.orchestration.definitions.service import OrchestrationDefinitionService
    from opsmesh.orchestration.tasks.models import TaskStep

    client, session = agent_api
    owner, workspace = _seed_workspace(session)
    seed_default_model_provider(session, workspace_id=workspace.id, user_id=owner.id)
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
                OrchestrationDefinitionCreateCommand(
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
    assert _advance(session, workspace.id)
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
