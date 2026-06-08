from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agent_messages.models import AgentMessage, AgentMessageThread
from backend.app.agent_messages.service import AgentMailboxService
from backend.app.agents.models import AgentProfile
from backend.app.api.schemas.agent_messages import (
    AgentMessageCreateRequest,
    AgentMessageThreadCreateRequest,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


def test_agent_message_thread_and_messages_round_trip() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    team = _add_team(session, workspace, name="Delivery")
    task = _add_task(session, workspace, owner, team=team)
    sender = _add_agent(session, workspace, name="Planner", role="planner")
    recipient = _add_agent(session, workspace, name="Builder", role="builder")

    thread_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads",
        headers=_headers(owner.id),
        json={"task_id": str(task.id), "subject": "Implementation handoff"},
    )
    assert thread_response.status_code == 201
    thread_id = thread_response.json()["id"]
    assert thread_response.json()["agent_team_id"] == str(team.id)

    first_message = client.post(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads/{thread_id}/messages",
        headers=_headers(owner.id),
        json={
            "sender_agent_profile_id": str(sender.id),
            "recipient_agent_profile_id": str(recipient.id),
            "message_type": "handoff",
            "body": "Please implement the persistence layer.",
            "payload": {"api_key": "sk-hidden", "scope": "backend"},
        },
    )
    assert first_message.status_code == 201
    first_body = first_message.json()
    assert first_body["task_id"] == str(task.id)
    assert first_body["agent_team_id"] == str(team.id)
    assert first_body["body"] == "Please implement the persistence layer."
    assert first_body["payload"] == {"api_key": "[redacted]", "scope": "backend"}

    stored = session.scalar(select(AgentMessage).where(AgentMessage.id == UUID(first_body["id"])))
    assert stored is not None
    assert stored.payload == {"api_key": "sk-hidden", "scope": "backend"}

    read_at = datetime(2026, 6, 6, 1, 0, tzinfo=UTC).isoformat()
    reply = client.post(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads/{thread_id}/messages",
        headers=_headers(owner.id),
        json={
            "sender_agent_profile_id": str(recipient.id),
            "recipient_agent_profile_id": str(sender.id),
            "reply_to_message_id": first_body["id"],
            "message_type": "reply",
            "body": "On it.",
            "status": "read",
            "read_at": read_at,
        },
    )
    messages = client.get(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads/{thread_id}/messages",
        headers=_headers(owner.id),
    )
    agent_threads = client.get(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads"
        f"?agent_profile_id={recipient.id}",
        headers=_headers(owner.id),
    )

    assert reply.status_code == 201
    assert reply.json()["reply_to_message_id"] == first_body["id"]
    assert messages.status_code == 200
    assert messages.json()["total"] == 2
    assert [item["message_type"] for item in messages.json()["items"]] == ["handoff", "reply"]
    assert agent_threads.status_code == 200
    assert agent_threads.json()["total"] == 1
    assert agent_threads.json()["items"][0]["id"] == thread_id
    assert agent_threads.json()["items"][0]["agent_team_id"] == str(team.id)


def test_agent_message_body_is_redacted_without_mutating_storage() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    task = _add_task(session, workspace, owner)
    sender = _add_agent(session, workspace, name="Planner", role="planner")
    recipient = _add_agent(session, workspace, name="Builder", role="builder")
    thread_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads",
        headers=_headers(owner.id),
        json={"task_id": str(task.id), "subject": "Secret handoff"},
    )
    assert thread_response.status_code == 201
    thread_id = thread_response.json()["id"]
    sensitive_body = "Use token sk-message-body-secret and Authorization Bearer hidden."
    sensitive_payload_note = "Forward Authorization: Bearer payload-hidden"

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads/{thread_id}/messages",
        headers=_headers(owner.id),
        json={
            "sender_agent_profile_id": str(sender.id),
            "recipient_agent_profile_id": str(recipient.id),
            "message_type": "handoff",
            "body": sensitive_body,
            "payload": {
                "visible": "ok",
                "note": sensitive_payload_note,
                "nested": {"content": "Use sk-message-payload-secret"},
            },
        },
    )
    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads/{thread_id}/messages",
        headers=_headers(owner.id),
    )
    inbox = client.get(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads/agents/{recipient.id}/inbox",
        headers=_headers(owner.id),
    )

    assert created.status_code == 201
    assert created.json()["body"] == "[redacted]"
    assert created.json()["payload"] == {
        "visible": "ok",
        "note": "[redacted]",
        "nested": {"content": "[redacted]"},
    }
    assert listed.status_code == 200
    assert listed.json()["items"][0]["body"] == "[redacted]"
    assert listed.json()["items"][0]["payload"]["note"] == "[redacted]"
    assert inbox.status_code == 200
    assert inbox.json()["latest_messages"][0]["body"] == "[redacted]"
    assert inbox.json()["latest_messages"][0]["payload"]["nested"] == {
        "content": "[redacted]",
    }
    stored = session.scalar(
        select(AgentMessage).where(AgentMessage.id == UUID(created.json()["id"]))
    )
    assert stored is not None
    assert stored.body == sensitive_body
    assert stored.payload == {
        "visible": "ok",
        "note": sensitive_payload_note,
        "nested": {"content": "Use sk-message-payload-secret"},
    }
    serialized = f"{created.text} {listed.text} {inbox.text}"
    assert "sk-message-body-secret" not in serialized
    assert "sk-message-payload-secret" not in serialized
    assert "payload-hidden" not in serialized
    assert "Bearer hidden" not in serialized


def test_agent_inbox_mark_read_and_thread_status() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    task = _add_task(session, workspace, owner)
    sender = _add_agent(session, workspace, name="Planner", role="planner")
    recipient = _add_agent(session, workspace, name="Builder", role="builder")
    thread = client.post(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads",
        headers=_headers(owner.id),
        json={"task_id": str(task.id), "subject": "Inbox flow"},
    )
    assert thread.status_code == 201
    message = client.post(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads/"
        f"{thread.json()['id']}/messages",
        headers=_headers(owner.id),
        json={
            "sender_agent_profile_id": str(sender.id),
            "recipient_agent_profile_id": str(recipient.id),
            "message_type": "handoff",
            "status": "pending",
            "body": "Please pick this up.",
        },
    )
    assert message.status_code == 201

    inbox = client.get(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads/agents/{recipient.id}/inbox",
        headers=_headers(owner.id),
    )
    unread = client.get(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads/agents/"
        f"{recipient.id}/inbox?unread_only=true",
        headers=_headers(owner.id),
    )
    read = client.post(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads/messages/"
        f"{message.json()['id']}/read",
        headers=_headers(owner.id),
        json={},
    )
    closed = client.post(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads/"
        f"{thread.json()['id']}/status",
        headers=_headers(owner.id),
        json={"status": "closed"},
    )
    inbox_after_read = client.get(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads/agents/"
        f"{recipient.id}/inbox?unread_only=true",
        headers=_headers(owner.id),
    )

    assert inbox.status_code == 200
    assert inbox.json()["agent_profile_id"] == str(recipient.id)
    assert inbox.json()["message_count"] == 1
    assert inbox.json()["unread_count"] == 1
    assert inbox.json()["pending_count"] == 1
    assert inbox.json()["latest_messages"][0]["body"] == "Please pick this up."
    assert unread.status_code == 200
    assert len(unread.json()["latest_messages"]) == 1
    assert read.status_code == 200
    assert read.json()["status"] == "read"
    assert read.json()["read_at"] is not None
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    assert inbox_after_read.status_code == 200
    assert inbox_after_read.json()["unread_count"] == 0
    assert inbox_after_read.json()["latest_messages"] == []


def test_agent_mailbox_service_writes_remain_in_caller_transaction() -> None:
    _, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    task = _add_task(session, workspace, owner)
    sender = _add_agent(session, workspace, name="Planner", role="planner")
    recipient = _add_agent(session, workspace, name="Builder", role="builder")

    service = AgentMailboxService(session)
    thread = service.create_thread(
        workspace_id=workspace.id,
        data=AgentMessageThreadCreateRequest(
            task_id=task.id,
            subject="Transactional thread",
        ),
    )
    message = service.create_message(
        workspace_id=workspace.id,
        thread_id=thread.id,
        data=AgentMessageCreateRequest(
            sender_agent_profile_id=sender.id,
            recipient_agent_profile_id=recipient.id,
            body="Rollback should remove this.",
        ),
    )
    service.mark_message_read(workspace_id=workspace.id, message_id=message.id)
    service.set_thread_status(
        workspace_id=workspace.id,
        thread_id=thread.id,
        status="closed",
    )

    assert session.get(AgentMessageThread, thread.id) is not None
    assert session.get(AgentMessage, message.id) is not None

    session.rollback()

    assert session.get(AgentMessageThread, thread.id) is None
    assert session.get(AgentMessage, message.id) is None


def test_agent_messages_reject_cross_workspace_agents_and_tasks() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other-owner@example.com",
        slug="other-space",
    )
    task = _add_task(session, workspace, owner)
    second_task = _add_task(session, workspace, owner)
    other_task = _add_task(session, other_workspace, other_owner)
    sender = _add_agent(session, workspace, name="Planner", role="planner")
    recipient = _add_agent(session, workspace, name="Builder", role="builder")
    other_agent = _add_agent(session, other_workspace, name="Foreign", role="builder")

    foreign_task_thread = client.post(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads",
        headers=_headers(owner.id),
        json={"task_id": str(other_task.id), "subject": "Bad task"},
    )
    thread = client.post(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads",
        headers=_headers(owner.id),
        json={"task_id": str(task.id), "subject": "Valid thread"},
    )
    assert thread.status_code == 201
    thread_id = thread.json()["id"]

    foreign_agent_message = client.post(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads/{thread_id}/messages",
        headers=_headers(owner.id),
        json={
            "sender_agent_profile_id": str(sender.id),
            "recipient_agent_profile_id": str(other_agent.id),
            "body": "This should fail.",
        },
    )
    mismatched_task_message = client.post(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads/{thread_id}/messages",
        headers=_headers(owner.id),
        json={
            "task_id": str(second_task.id),
            "sender_agent_profile_id": str(sender.id),
            "recipient_agent_profile_id": str(recipient.id),
            "body": "This should also fail.",
        },
    )
    forbidden_list = client.get(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads",
        headers=_headers(other_owner.id),
    )

    assert foreign_task_thread.status_code == 404
    assert foreign_agent_message.status_code == 404
    assert mismatched_task_message.status_code == 409
    assert forbidden_list.status_code == 403


def test_agent_messages_reject_team_mailbox_on_non_team_task() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    task = _add_task(session, workspace, owner)
    team = _add_team(session, workspace, name="Delivery")
    sender = _add_agent(session, workspace, name="Planner", role="planner")
    recipient = _add_agent(session, workspace, name="Builder", role="builder")

    mismatched_thread = client.post(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads",
        headers=_headers(owner.id),
        json={
            "task_id": str(task.id),
            "agent_team_id": str(team.id),
            "subject": "Invalid team binding",
        },
    )
    thread = client.post(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads",
        headers=_headers(owner.id),
        json={"task_id": str(task.id), "subject": "Task thread"},
    )
    assert thread.status_code == 201
    mismatched_message = client.post(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads/"
        f"{thread.json()['id']}/messages",
        headers=_headers(owner.id),
        json={
            "agent_team_id": str(team.id),
            "sender_agent_profile_id": str(sender.id),
            "recipient_agent_profile_id": str(recipient.id),
            "body": "Invalid team-bound message.",
        },
    )

    assert mismatched_thread.status_code == 409
    assert mismatched_message.status_code == 409


def test_agent_mailbox_summary_is_workspace_scoped_and_redacted() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="foreign-owner@example.com",
        slug="foreign-space",
    )
    team = _add_team(session, workspace, name="Delivery")
    other_team = _add_team(session, other_workspace, name="Foreign")
    first_task = _add_task(session, workspace, owner, team=team)
    second_task = _add_task(session, workspace, owner, team=team)
    other_task = _add_task(session, other_workspace, other_owner, team=other_team)
    sender = _add_agent(session, workspace, name="Planner", role="planner")
    recipient = _add_agent(session, workspace, name="Builder", role="builder")
    other_sender = _add_agent(session, other_workspace, name="Foreign", role="planner")
    other_recipient = _add_agent(session, other_workspace, name="Hidden", role="builder")

    now = datetime(2026, 6, 6, 1, 0, tzinfo=UTC)
    first_thread = _add_thread(session, workspace, first_task, subject="First")
    second_thread = _add_thread(session, workspace, second_task, subject="Second")
    other_thread = _add_thread(session, other_workspace, other_task, subject="Foreign")
    _add_message(
        session,
        workspace=workspace,
        thread=first_thread,
        task=first_task,
        sender=sender,
        recipient=recipient,
        body="Initial handoff",
        status="sent",
        created_at=now,
        payload={"api_key": "sk-hidden", "visible": "initial"},
    )
    _add_message(
        session,
        workspace=workspace,
        thread=first_thread,
        task=first_task,
        sender=recipient,
        recipient=sender,
        body="Waiting on answer",
        status="pending",
        created_at=now + timedelta(minutes=1),
        payload={"token": "secret-token", "visible": "latest-task"},
    )
    _add_message(
        session,
        workspace=workspace,
        thread=second_thread,
        task=second_task,
        sender=sender,
        recipient=recipient,
        body="Second task is read",
        status="read",
        read_at=now + timedelta(minutes=2),
        created_at=now + timedelta(minutes=2),
        payload={"visible": "team-latest"},
    )
    _add_message(
        session,
        workspace=other_workspace,
        thread=other_thread,
        task=other_task,
        sender=other_sender,
        recipient=other_recipient,
        body="Foreign secret must not leak",
        status="pending",
        created_at=now + timedelta(minutes=3),
        payload={"token": "foreign-token"},
    )

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads/summary",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["thread_count"] == 2
    assert body["message_count"] == 3
    assert body["unread_count"] == 2
    assert body["pending_count"] == 1
    assert body["thread_status_counts"] == {"active": 2}
    assert body["message_status_counts"] == {"pending": 1, "read": 1, "sent": 1}
    assert [item["task_id"] for item in body["latest_messages_by_task"]] == [
        str(second_task.id),
        str(first_task.id),
    ]
    assert body["latest_messages_by_task"][1]["message"]["payload"] == {
        "token": "[redacted]",
        "visible": "latest-task",
    }
    assert body["latest_messages_by_team"] == [
        {
            "team_id": str(team.id),
            "message": body["latest_messages_by_task"][0]["message"],
        }
    ]
    assert "Foreign secret must not leak" not in response.text
    assert "foreign-token" not in response.text


def test_agent_mailbox_summary_includes_team_level_threads_without_task() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    team = _add_team(session, workspace, name="Runtime Team")
    sender = _add_agent(session, workspace, name="Planner", role="planner")
    recipient = _add_agent(session, workspace, name="Builder", role="builder")
    thread_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads",
        headers=_headers(owner.id),
        json={"agent_team_id": str(team.id), "subject": "Team runtime"},
    )
    assert thread_response.status_code == 201
    assert thread_response.json()["task_id"] is None
    assert thread_response.json()["agent_team_id"] == str(team.id)
    message_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads/"
        f"{thread_response.json()['id']}/messages",
        headers=_headers(owner.id),
        json={
            "sender_agent_profile_id": str(sender.id),
            "recipient_agent_profile_id": str(recipient.id),
            "message_type": "team.runtime.note",
            "body": "Team-level runtime note.",
        },
    )

    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads"
        f"?agent_team_id={team.id}",
        headers=_headers(owner.id),
    )
    summary = client.get(
        f"/api/v1/workspaces/{workspace.id}/agent-message-threads/summary",
        headers=_headers(owner.id),
    )

    assert message_response.status_code == 201
    assert message_response.json()["task_id"] is None
    assert message_response.json()["agent_team_id"] == str(team.id)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["agent_team_id"] == str(team.id)
    assert summary.status_code == 200
    assert summary.json()["latest_messages_by_task"] == []
    assert summary.json()["latest_messages_by_team"] == [
        {
            "team_id": str(team.id),
            "message": message_response.json(),
        }
    ]


def _client() -> tuple[TestClient, Session]:
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
    worker_queue = RedisQueue(
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


def _add_agent(session: Session, workspace: Workspace, *, name: str, role: str) -> AgentProfile:
    agent = AgentProfile(workspace_id=workspace.id, name=name, role=role)
    session.add(agent)
    session.commit()
    return agent


def _add_team(session: Session, workspace: Workspace, *, name: str) -> AgentTeam:
    team = AgentTeam(workspace_id=workspace.id, name=name, team_type="delivery")
    session.add(team)
    session.commit()
    return team


def _add_task(
    session: Session,
    workspace: Workspace,
    owner: User,
    *,
    team: AgentTeam | None = None,
) -> Task:
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id if team is not None else None,
        title="Build mailbox",
    )
    session.add(task)
    session.commit()
    return task


def _add_thread(
    session: Session,
    workspace: Workspace,
    task: Task,
    *,
    subject: str,
) -> AgentMessageThread:
    thread = AgentMessageThread(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_team_id=task.agent_team_id,
        subject=subject,
    )
    session.add(thread)
    session.commit()
    return thread


def _add_message(
    session: Session,
    *,
    workspace: Workspace,
    thread: AgentMessageThread,
    task: Task,
    sender: AgentProfile,
    recipient: AgentProfile,
    body: str,
    status: str,
    created_at: datetime,
    payload: dict[str, object],
    read_at: datetime | None = None,
) -> AgentMessage:
    message = AgentMessage(
        workspace_id=workspace.id,
        thread_id=thread.id,
        task_id=task.id,
        agent_team_id=thread.agent_team_id or task.agent_team_id,
        sender_agent_profile_id=sender.id,
        recipient_agent_profile_id=recipient.id,
        message_type="message",
        body=body,
        payload=payload,
        status=status,
        read_at=read_at,
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(message)
    session.commit()
    return message


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
