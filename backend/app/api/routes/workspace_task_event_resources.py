import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams, PageResponse, pagination_params
from backend.app.api.routes.workspace_task_streaming import (
    _message_sequence,
    _read_task_bus_events,
    _snapshot_messages,
    _sse_event,
    _task_event_stream_payload,
    _task_stream_complete,
    get_task_event_bus,
)
from backend.app.api.schemas.tasks import (
    TaskLiveStatusResponse,
    TaskMessageResponse,
    TaskPlanningAttemptResponse,
)
from backend.app.api.services.workspace_reads import WorkspaceReadService
from backend.app.auth.context import WorkspaceContext
from backend.app.auth.dependencies import workspace_dependency
from backend.app.auth.permissions import WorkspaceAction
from backend.app.core.trace_context import current_trace_metadata
from backend.app.db.session import get_db_session
from backend.app.tasks.events import TaskEventBus
from backend.app.tasks.live_status import TaskLiveStatusService

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])


@router.get("/tasks/{task_id}/messages", response_model=PageResponse[TaskMessageResponse])
async def list_task_messages(
    task_id: UUID,
    page: PageParams = Depends(pagination_params),
    message_type: str | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[TaskMessageResponse]:
    try:
        items, total = WorkspaceReadService(session).list_task_messages(
            workspace_id=context.workspace.id,
            task_id=task_id,
            page=page,
            message_type=message_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/tasks/{task_id}/live-status", response_model=TaskLiveStatusResponse)
async def get_task_live_status(
    task_id: UUID,
    after_sequence: int = Query(default=0, ge=0),
    message_limit: int = Query(default=50, ge=1, le=200),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> TaskLiveStatusResponse:
    status_snapshot = TaskLiveStatusService(session).get_status(
        workspace_id=context.workspace.id,
        task_id=task_id,
        after_sequence=after_sequence,
        message_limit=message_limit,
    )
    if status_snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskLiveStatusResponse.model_validate(status_snapshot)


@router.get("/tasks/{task_id}/events/stream")
async def stream_task_events(
    task_id: UUID,
    after_sequence: int = Query(default=0, ge=0),
    event_cursor: str = Query(default="$", min_length=1, max_length=64),
    message_limit: int = Query(default=50, ge=1, le=200),
    poll_seconds: float = Query(default=1.0, ge=0.25, le=10.0),
    heartbeat_seconds: float = Query(default=15.0, ge=1.0, le=60.0),
    once: bool = Query(default=False),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    task_event_bus: TaskEventBus = Depends(get_task_event_bus),
) -> StreamingResponse:
    service = TaskLiveStatusService(session)
    initial = service.get_status(
        workspace_id=context.workspace.id,
        task_id=task_id,
        after_sequence=after_sequence,
        message_limit=message_limit,
    )
    if initial is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    stream_trace_metadata = current_trace_metadata()

    async def event_stream() -> AsyncIterator[str]:
        cursor = after_sequence
        bus_cursor = event_cursor
        last_emit_at = asyncio.get_running_loop().time()
        snapshot = initial
        while True:
            messages = _snapshot_messages(snapshot)
            if messages:
                cursor = max(_message_sequence(message, cursor) for message in messages)
            yield _sse_event(
                "task.snapshot",
                {
                    **snapshot,
                    "stream": {
                        "cursor": cursor,
                        "event_cursor": bus_cursor,
                        "message_count": len(messages),
                        "complete": _task_stream_complete(snapshot),
                    },
                },
            )
            last_emit_at = asyncio.get_running_loop().time()

            complete = _task_stream_complete(snapshot)
            stop_after_bus_drain = once or complete

            bus_events = await _read_task_bus_events(
                task_event_bus,
                workspace_id=context.workspace.id,
                task_id=task_id,
                after_id=bus_cursor,
                wait_seconds=0 if stop_after_bus_drain else poll_seconds,
            )
            for task_event in bus_events:
                bus_cursor = task_event.id
                yield _sse_event("task.event", _task_event_stream_payload(task_event, cursor))
                last_emit_at = asyncio.get_running_loop().time()

            if stop_after_bus_drain:
                break

            session.expire_all()
            next_snapshot = service.get_status(
                workspace_id=context.workspace.id,
                task_id=task_id,
                after_sequence=cursor,
                message_limit=message_limit,
            )
            if next_snapshot is None:
                yield _sse_event("task.missing", {"task_id": str(task_id), "cursor": cursor})
                break
            snapshot = next_snapshot
            if not _snapshot_messages(snapshot):
                now = asyncio.get_running_loop().time()
                if now - last_emit_at >= heartbeat_seconds:
                    yield _sse_event(
                        "heartbeat",
                        {
                            "workspace_id": str(context.workspace.id),
                            "task_id": str(task_id),
                            "cursor": cursor,
                            "event_cursor": bus_cursor,
                            **stream_trace_metadata,
                        },
                    )
                    last_emit_at = now

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.get(
    "/tasks/{task_id}/planning-attempts",
    response_model=PageResponse[TaskPlanningAttemptResponse],
)
async def list_task_planning_attempts(
    task_id: UUID,
    page: PageParams = Depends(pagination_params),
    status_filter: str | None = Query(default=None, alias="status"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[TaskPlanningAttemptResponse]:
    try:
        items, total = WorkspaceReadService(session).list_task_planning_attempts(
            workspace_id=context.workspace.id,
            task_id=task_id,
            page=page,
            status=status_filter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageResponse(
        items=[TaskPlanningAttemptResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


