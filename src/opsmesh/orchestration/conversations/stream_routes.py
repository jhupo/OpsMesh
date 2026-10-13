"""HTTP streaming boundary for queued, running, approval-paused and terminal turns."""

import asyncio
from collections.abc import AsyncIterator
from functools import partial
from time import monotonic
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.errors import AuthorizationError
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.orchestration.conversations.service import TERMINAL
from opsmesh.orchestration.conversations.stream import turn_stream_snapshot
from opsmesh.orchestration.tasks.async_events import (
    AsyncPostgresTaskEventReader,
    TaskEventStreamUnavailableError,
)
from opsmesh.orchestration.tasks.routes.streaming import get_task_event_reader
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.errors import DomainError, ForbiddenError
from opsmesh.shared.http.sse import sse_event

router = APIRouter(prefix="/workspaces/{workspace_id}/conversations", tags=["conversations"])


@router.get(
    "/{conversation_id}/turns/{turn_id}/events/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def stream_turn_events(
    conversation_id: UUID,
    turn_id: UUID,
    request: Request,
    event_cursor: str = Query(default="0", min_length=1, max_length=36),
    last_event_id: str | None = Header(default=None, min_length=1, max_length=36),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    task_event_reader: AsyncPostgresTaskEventReader = Depends(get_task_event_reader),
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

    async def frames() -> AsyncIterator[str]:
        cursor = last_event_id or event_cursor
        previous: dict[str, object] | None = None
        snapshot = initial
        last_heartbeat = monotonic()
        cursor_checked = False
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
                    if not cursor_checked:
                        cursor_checked = True
                        if cursor != "0" and not await task_event_reader.contains_cursor(
                            workspace_id,
                            snapshot.task_id,
                            cursor,
                        ):
                            cursor = "0"
                            yield sse_event(
                                "stream.reset", {"reason": "cursor_not_retained", **state}
                            )
                    events = await task_event_reader.read(
                        workspace_id=workspace_id,
                        task_id=snapshot.task_id,
                        after_id=cursor,
                        count=100,
                        wait_seconds=0 if terminal else 15,
                    )
                    # Access can change while the subscription waits without any Task event.
                    try:
                        await asyncio.to_thread(read_snapshot)
                    except (DomainError, AuthorizationError):
                        yield sse_event("stream.revoked", {})
                        return
                    for item in events:
                        cursor = item.id
                        yield sse_event("task.event", item.as_dict(), event_id=cursor)
                    if len(events) == 100:
                        continue
                elif not terminal:
                    await asyncio.sleep(1)
            except TaskEventStreamUnavailableError:
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
