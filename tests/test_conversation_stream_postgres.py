import asyncio
import os
from uuid import uuid4

import fakeredis
import pytest
from fakeredis.aioredis import FakeRedis as FakeAsyncRedis
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from opsmesh.orchestration.conversations.advancement import ConversationAdvanceService
from opsmesh.orchestration.conversations.models import ConversationExecution, ConversationTurn
from opsmesh.orchestration.conversations.service import ConversationService
from opsmesh.orchestration.conversations.stream import turn_stream_snapshot
from opsmesh.orchestration.conversations.stream_routes import stream_turn_events
from opsmesh.orchestration.tasks.events import RedisTaskEventBus
from opsmesh.runtime.queues.service import RedisQueue
from opsmesh.shared.config import Settings
from opsmesh.shared.db.base import Base
from opsmesh.shared.errors import NotFoundError
from opsmesh.shared.redis.keys import RedisKeyBuilder
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.test_conversations_postgres import _context, _seed_conversation
from tests.test_postgres_scheduler_concurrency import _temporary_postgres_schema

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL URL required"
)


async def _receive():
    await asyncio.Event().wait()
    return {}


def test_stream_queued_text_replay_final_and_no_open_database_transaction() -> None:
    with _temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        wid, uid, mid, cid = _seed_conversation(factory)
        server = fakeredis.FakeServer()
        redis = fakeredis.FakeRedis(server=server, decode_responses=True)
        queue = RedisQueue(
            redis, RedisKeyBuilder("stream-test"), "jobs", blocking_timeout_seconds=0
        )
        with factory() as session:
            context = _context(session, wid, uid, mid)
            turn_id = (
                ConversationService(session).send(context, cid, "hello", "once", queue=queue).id
            )

        async def exercise():
            async with FakeAsyncRedis(server=server, decode_responses=True) as async_redis:
                request = Request(
                    {"type": "http", "method": "GET", "path": "/stream", "headers": []},
                    receive=_receive,
                )
                with factory() as dependency:
                    response = await stream_turn_events(
                        cid,
                        turn_id,
                        request,
                        "0-0",
                        None,
                        context,
                        dependency,
                        async_redis,
                        queue,
                        Settings(redis_key_prefix="stream-test"),
                    )
                    iterator = response.body_iterator
                    first = await anext(iterator)
                    assert '"status": "queued"' in first
                    assert not dependency.in_transaction()
                    assert engine.pool.checkedout() == 0
                    with factory() as session:
                        ConversationAdvanceService(session).advance(wid, cid)
                        task_id = session.scalar(
                            select(ConversationExecution.task_id).where(
                                ConversationExecution.turn_id == turn_id
                            )
                        )
                        session.commit()
                    state = await anext(iterator)
                    assert '"status": "running"' in state and str(task_id) in state
                    bus = RedisTaskEventBus(redis, "stream-test")
                    cursors = []
                    for index in range(105):
                        cursors.append(
                            bus.publish(
                                workspace_id=wid,
                                task_id=task_id,
                                event_type="run.live",
                                payload={
                                    "kind": "output.text",
                                    "data": {"text": f"preview {index}"},
                                },
                            )
                        )
                    frame = await anext(iterator)
                    assert "event: task.event" in frame and "preview 0" in frame
                    assert engine.pool.checkedout() == 0
                    await iterator.aclose()
                    with factory() as session:
                        turn = session.get(ConversationTurn, turn_id)
                        turn.status = "completed"
                        turn.reply = "final answer"
                        session.commit()
                    response = await stream_turn_events(
                        cid,
                        turn_id,
                        request,
                        "0-0",
                        cursors[0],
                        context,
                        dependency,
                        async_redis,
                        queue,
                        Settings(redis_key_prefix="stream-test"),
                    )
                    frames = [item async for item in response.body_iterator]
                    assert sum("event: task.event" in item for item in frames) == 104
                    assert "event: stream.completed" in frames[-1] and "final answer" in frames[-1]
                    # An absent retained cursor is explicit, never silent loss.
                    response = await stream_turn_events(
                        cid,
                        turn_id,
                        request,
                        "1-0",
                        None,
                        context,
                        dependency,
                        async_redis,
                        queue,
                        Settings(redis_key_prefix="stream-test"),
                    )
                    frames = [item async for item in response.body_iterator]
                    assert any("event: stream.reset" in item for item in frames)

        asyncio.run(exercise())


def test_stream_rejects_other_owner_and_revokes_membership_midstream() -> None:
    with _temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        wid, uid, mid, cid = _seed_conversation(factory)
        server = fakeredis.FakeServer()
        redis = fakeredis.FakeRedis(server=server, decode_responses=True)
        queue = RedisQueue(
            redis, RedisKeyBuilder("stream-test"), "jobs", blocking_timeout_seconds=0
        )
        with factory() as session:
            context = _context(session, wid, uid, mid)
            turn_id = (
                ConversationService(session).send(context, cid, "hello", "once", queue=queue).id
            )
        with pytest.raises(NotFoundError):
            turn_stream_snapshot(factory, context.user, wid, uuid4(), turn_id)
        other_wid, other_uid, other_mid, other_cid = _seed_conversation(factory)
        with factory() as session:
            other = _context(session, other_wid, other_uid, other_mid)
        with pytest.raises(NotFoundError):
            turn_stream_snapshot(factory, other.user, other_wid, cid, turn_id)
        with factory() as session:
            session.add(WorkspaceMember(workspace_id=wid, user_id=other_uid, role="owner"))
            session.commit()
        with pytest.raises(NotFoundError):
            turn_stream_snapshot(factory, other.user, wid, cid, turn_id)

        async def exercise():
            async with FakeAsyncRedis(server=server, decode_responses=True) as async_redis:
                request = Request(
                    {"type": "http", "method": "GET", "path": "/stream", "headers": []},
                    receive=_receive,
                )
                with factory() as dependency:
                    response = await stream_turn_events(
                        cid,
                        turn_id,
                        request,
                        "0-0",
                        None,
                        context,
                        dependency,
                        async_redis,
                        queue,
                        Settings(redis_key_prefix="stream-test"),
                    )
                    iterator = response.body_iterator
                    assert "event: turn.snapshot" in await anext(iterator)
                    with factory() as session:
                        session.get(WorkspaceMember, mid).status = "inactive"
                        session.commit()
                    assert "event: stream.revoked" in await anext(iterator)
                    with pytest.raises(StopAsyncIteration):
                        await anext(iterator)

        asyncio.run(exercise())
