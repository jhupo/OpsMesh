"""PostgreSQL event replay and authorization at the Chat SSE boundary."""

import asyncio
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.identity.authorization.context import AuthenticatedUser, WorkspaceContext
from opsmesh.identity.users.models import User
from opsmesh.orchestration.conversations.models import (
    Conversation,
    ConversationExecution,
    ConversationTurn,
)
from opsmesh.orchestration.conversations.stream import turn_stream_snapshot
from opsmesh.orchestration.conversations.stream_routes import stream_turn_events
from opsmesh.orchestration.tasks.async_events import AsyncPostgresTaskEventReader
from opsmesh.orchestration.tasks.models import Task, TaskEventRecord
from opsmesh.orchestration.tasks.notifications import TaskEventNotifications
from opsmesh.shared.db.base import Base
from opsmesh.shared.errors import NotFoundError
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL URL required"
)


@contextmanager
def _postgres_schema():
    schema = f"stream_test_{uuid4().hex}"
    database_url = os.environ["OPSMESH_TEST_POSTGRES_URL"]
    admin = create_engine(database_url)
    engine = None
    try:
        with admin.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            database_url,
            pool_size=4,
            max_overflow=0,
            connect_args={"options": f"-csearch_path={schema},public"},
        )
        Base.metadata.create_all(engine)
        yield engine, schema
    finally:
        if engine is not None:
            engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def _seed(factory: sessionmaker[Session]):
    with factory.begin() as session:
        user = User(email=f"{uuid4()}@example.com", display_name="Owner")
        workspace = Workspace(owner=user, name="Stream", slug=uuid4().hex, settings={})
        member = WorkspaceMember(workspace=workspace, user=user, role="owner")
        session.add_all([user, workspace, member])
        session.flush()
        agent = AgentProfile(workspace_id=workspace.id, name="Assistant", role="assistant")
        session.add(agent)
        session.flush()
        conversation = Conversation(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            title="Replay",
            mode="agent",
            agent_profile_id=agent.id,
        )
        task = Task(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            owner_agent_profile_id=agent.id,
            title="Replay",
            description="Stream fixture",
            status="running",
        )
        session.add_all([conversation, task])
        session.flush()
        turn = ConversationTurn(
            workspace_id=workspace.id,
            conversation_id=conversation.id,
            sequence=1,
            idempotency_key="stream",
            body="hello",
            status="running",
            execution_identity={},
        )
        session.add(turn)
        session.flush()
        session.add(
            ConversationExecution(workspace_id=workspace.id, turn_id=turn.id, task_id=task.id)
        )
        return workspace.id, user.id, member.id, conversation.id, turn.id, task.id


def _context(factory, workspace_id, user_id, member_id):
    with factory() as session:
        return WorkspaceContext(
            AuthenticatedUser.from_model(session.get(User, user_id)),
            session.get(Workspace, workspace_id),
            session.get(WorkspaceMember, member_id),
        )


def _append_events(factory, workspace_id, task_id, count):
    event_ids = []
    anchor = datetime.now(UTC)
    with factory.begin() as session:
        for index in range(count):
            identifier = uuid4()
            event_ids.append(str(identifier))
            session.add(
                TaskEventRecord(
                    workspace_id=workspace_id,
                    task_id=task_id,
                    event_id=identifier,
                    sequence=index + 1,
                    event_type="agent.stream.output.text.delta",
                    payload={
                        "runtime_stream_event": {
                            "event_type": "output.text.delta",
                            "payload": {},
                            "delta": f"preview {index}",
                            "provider_sequence": index + 1,
                            "is_terminal": False,
                        }
                    },
                    created_at=anchor + timedelta(microseconds=index),
                )
            )
    return event_ids


def _async_reader(engine: Engine, schema: str):
    async_engine = create_async_engine(
        make_url(engine.url).set(drivername="postgresql+psycopg"),
        pool_size=4,
        max_overflow=0,
        connect_args={"options": f"-csearch_path={schema},public"},
    )
    return async_engine, AsyncPostgresTaskEventReader(
        async_sessionmaker(async_engine, expire_on_commit=False), TaskEventNotifications()
    )


async def _receive():
    await asyncio.Event().wait()
    return {}


def test_postgres_stream_replays_pages_and_final_result_without_open_transaction():
    with _postgres_schema() as (engine, schema):
        factory = sessionmaker(engine, expire_on_commit=False)
        wid, uid, mid, cid, turn_id, task_id = _seed(factory)
        context = _context(factory, wid, uid, mid)
        cursors = _append_events(factory, wid, task_id, 105)

        async def exercise():
            async_engine, reader = _async_reader(engine, schema)
            try:
                assert not await reader.contains_cursor(uuid4(), task_id, cursors[0])
                request = Request(
                    {"type": "http", "method": "GET", "path": "/stream", "headers": []},
                    receive=_receive,
                )
                with factory() as dependency:
                    response = await stream_turn_events(
                        cid, turn_id, request, "0", None, context, dependency, reader
                    )
                    iterator = response.body_iterator
                    assert "event: turn.snapshot" in await anext(iterator)
                    assert not dependency.in_transaction()
                    assert engine.pool.checkedout() == 0
                    frame = await anext(iterator)
                    assert "event: task.event" in frame and "preview 0" in frame
                    assert engine.pool.checkedout() == 0
                    await iterator.aclose()
                    with factory.begin() as session:
                        turn = session.get(ConversationTurn, turn_id)
                        turn.status = "completed"
                        turn.reply = "final answer"
                    response = await stream_turn_events(
                        cid, turn_id, request, "0", cursors[0], context, dependency, reader
                    )
                    frames = [item async for item in response.body_iterator]
                    assert sum("event: task.event" in item for item in frames) == 104
                    assert "event: stream.completed" in frames[-1]
                    assert "final answer" in frames[-1]
                    response = await stream_turn_events(
                        cid, turn_id, request, str(uuid4()), None, context, dependency, reader
                    )
                    frames = [item async for item in response.body_iterator]
                    assert any("event: stream.reset" in item for item in frames)
            finally:
                await async_engine.dispose()

        asyncio.run(exercise())


def test_postgres_stream_rejects_other_owner_and_revokes_membership_midstream():
    with _postgres_schema() as (engine, schema):
        factory = sessionmaker(engine, expire_on_commit=False)
        wid, uid, mid, cid, turn_id, task_id = _seed(factory)
        context = _context(factory, wid, uid, mid)
        other_wid, other_uid, other_mid, _, _, _ = _seed(factory)
        other = _context(factory, other_wid, other_uid, other_mid)
        with pytest.raises(NotFoundError):
            turn_stream_snapshot(factory, context.user, wid, uuid4(), turn_id)
        with pytest.raises(NotFoundError):
            turn_stream_snapshot(factory, other.user, other_wid, cid, turn_id)
        with factory.begin() as session:
            session.add(WorkspaceMember(workspace_id=wid, user_id=other_uid, role="owner"))
        with pytest.raises(NotFoundError):
            turn_stream_snapshot(factory, other.user, wid, cid, turn_id)

        async def exercise():
            async_engine, reader = _async_reader(engine, schema)
            try:
                request = Request(
                    {"type": "http", "method": "GET", "path": "/stream", "headers": []},
                    receive=_receive,
                )
                with factory() as dependency:
                    response = await stream_turn_events(
                        cid, turn_id, request, "0", None, context, dependency, reader
                    )
                    iterator = response.body_iterator
                    assert "event: turn.snapshot" in await anext(iterator)
                    with factory.begin() as session:
                        session.get(WorkspaceMember, mid).status = "inactive"
                    assert "event: stream.revoked" in await anext(iterator)
                    with pytest.raises(StopAsyncIteration):
                        await anext(iterator)
            finally:
                await async_engine.dispose()

        asyncio.run(exercise())
