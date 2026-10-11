"""HTTP streaming boundary for queued, running, approval-paused and terminal turns."""

import asyncio
from collections.abc import AsyncIterator
from functools import partial
from time import monotonic
from typing import TYPE_CHECKING, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.errors import AuthorizationError
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.orchestration.conversations.service import TERMINAL
from opsmesh.orchestration.conversations.stream import turn_stream_snapshot
from opsmesh.orchestration.tasks.async_events import AsyncRedisTaskEventBus
from opsmesh.runtime.queues.dependencies import get_worker_queue
from opsmesh.runtime.queues.service import RedisQueue
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.errors import DomainError, ForbiddenError
from opsmesh.shared.http.sse import sse_event

router = APIRouter(prefix="/workspaces/{workspace_id}/conversations", tags=["conversations"])

if TYPE_CHECKING:
    StreamRedis = Redis[str]
else:
    StreamRedis = Redis


def get_stream_redis(request: Request) -> StreamRedis:
    return cast(StreamRedis, request.app.state.stream_redis_client)


@router.get(
    "/{conversation_id}/turns/{turn_id}/events/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def stream_turn_events(
    conversation_id: UUID,
    turn_id: UUID,
    request: Request,
    event_cursor: str = Query(default="0-0", pattern=r"^\d+-\d+$", max_length=64),
    last_event_id: str | None = Header(default=None, pattern=r"^\d+-\d+$", max_length=64),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    redis: StreamRedis = Depends(get_stream_redis),
    queue: RedisQueue = Depends(get_worker_queue),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    workspace_id = context.workspace.id
    read_snapshot = partial(
        turn_stream_snapshot,
        sessionmaker(session.get_bind(), expire_on_commit=False),
        context.user,
        workspace_id,
        conversation_id,
        turn_id,
    )
    # Release the authentication dependency transaction before opening a long stream.
    session.rollback()
    try:
        initial = await asyncio.to_thread(read_snapshot)
    except AuthorizationError as exc:
        raise ForbiddenError("Conversation stream unavailable") from exc
    bus = AsyncRedisTaskEventBus(redis, settings.redis_key_prefix)

    async def frames() -> AsyncIterator[str]:
        cursor = last_event_id or event_cursor
        previous: dict[str, object] | None = None
        snapshot = initial
        last_heartbeat = monotonic()
        async with redis.pubsub() as wakeups:
            await wakeups.subscribe(queue.keys.queue(queue.queue_name) + ":ready")
            while not await request.is_disconnected():
                try:
                    snapshot = await asyncio.to_thread(read_snapshot)
                except (DomainError, AuthorizationError):
                    yield sse_event("stream.revoked", {})
                    return
                state: dict[str, object] = {
                    "turn": snapshot.turn,
                    "task_id": str(snapshot.task_id) if snapshot.task_id else None,
                }
                if previous != state:
                    yield sse_event("turn.snapshot", state)
                    previous = state
                terminal = snapshot.turn["status"] in TERMINAL
                try:
                    if snapshot.task_id is not None:
                        if cursor != "0-0" and not await bus.contains_cursor(
                            workspace_id, snapshot.task_id, cursor
                        ):
                            cursor = "0-0"
                            yield sse_event(
                                "stream.reset", {"reason": "cursor_not_retained", **state}
                            )
                        events = await bus.read(
                            workspace_id=workspace_id,
                            task_id=snapshot.task_id,
                            after_id=cursor,
                            block_ms=0 if terminal else 1000,
                        )
                        if events:
                            # Refresh access after waiting and before emitting any private data.
                            try:
                                await asyncio.to_thread(read_snapshot)
                            except (DomainError, AuthorizationError):
                                yield sse_event("stream.revoked", {})
                                return
                        for item in events:
                            cursor = item.id
                            yield sse_event("task.event", item.as_dict(), event_id=cursor)
                        if len(events) == 100:
                            continue  # Drain retained events before announcing completion.
                    elif not terminal:
                        await wakeups.get_message(ignore_subscribe_messages=True, timeout=1)
                except RedisError:
                    yield sse_event("stream.unavailable", {"reason": "event_transport_unavailable"})
                    return
                if terminal:
                    yield sse_event("stream.completed", state)
                    return
                if monotonic() - last_heartbeat >= 15:
                    yield ": heartbeat\n\n"
                    last_heartbeat = monotonic()

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
