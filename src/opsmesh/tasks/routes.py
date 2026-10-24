import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent
from opsmesh_sdk import UserIdentity
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.agents.service import usable
from opsmesh.infra.database import now
from opsmesh.shared.errors import ConflictError, DomainError, NotFoundError
from opsmesh.shared.http import get_application, get_current_user, get_db_session, serialize_record
from opsmesh.users.service import require_owner

from .interactions import respond
from .models import Task, TaskAttempt, TaskInteraction, TaskSchedule
from .schedules import next_time
from .schemas import (
    CreateScheduleRequest,
    CreateTaskRequest,
    InteractionReceiptView,
    InteractionView,
    InvokePublishedAgentRequest,
    RespondToInteractionRequest,
    ScheduleView,
    TaskAttemptView,
    TaskDetailsView,
    TaskView,
    UpdateScheduleRequest,
)
from .service import cancel, owned, resume, submit, submit_publication
from .streaming import EventSubscription

router = APIRouter(tags=["tasks"])


def event_subscription(
    task_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None),
    authorization: str = Header(default=""),
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> EventSubscription:
    task = owned(db, user, task_id)
    if last_event_id is not None:
        if not last_event_id.isascii() or not last_event_id.isdecimal() or len(last_event_id) > 12:
            raise ValueError("Invalid Last-Event-ID")
        after = int(last_event_id)
    if after > task.event_sequence:
        raise ValueError("Event cursor is ahead of the task")
    app = get_application(request)
    return EventSubscription(app.sessions, app.cipher, authorization[7:], task_id, after)


@router.get("/tasks/{task_id}/events", response_class=EventSourceResponse)
async def task_events(
    request: Request,
    subscription: EventSubscription = Depends(event_subscription, scope="function"),
) -> AsyncIterator[ServerSentEvent]:
    initial = True
    while not await request.is_disconnected():
        try:
            page = await asyncio.to_thread(subscription.read)
        except DomainError as exc:
            yield ServerSentEvent(event="stream.error", data={"code": exc.code})
            return
        if initial:
            yield ServerSentEvent(event="task.snapshot", data=page.snapshot, retry=2000)
            initial = False
        for item in page.events:
            subscription.after = item["sequence"]
            yield ServerSentEvent(
                id=str(subscription.after), event=item["event"], data=item["data"]
            )
        if page.stopped and subscription.after >= page.latest_sequence:
            yield ServerSentEvent(event="stream.end", data={"status": page.snapshot["status"]})
            return
        if not page.events:
            await asyncio.sleep(0.5)


@router.post("/catalog/{publication_id}/tasks", status_code=201, response_model=TaskView)
def invoke_published_agent(
    publication_id: str,
    body: InvokePublishedAgentRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    return task_view(
        submit_publication(
            db,
            get_application(request).cipher,
            user,
            publication_id,
            body.input,
            metadata=body.metadata,
            idempotency_key=body.idempotency_key,
            timeout=body.timeout_seconds,
            session_id=body.session_id,
        )
    )


def task_view(row: Task) -> dict[str, Any]:
    return serialize_record(row, exclude=("snapshot",))


@router.post("/tasks", status_code=201, response_model=TaskView)
def create_task(
    body: CreateTaskRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    row = submit(
        db,
        get_application(request).cipher,
        user,
        body.agent_id,
        body.input,
        metadata=body.metadata,
        idempotency_key=body.idempotency_key,
        timeout=body.timeout_seconds,
        session_id=body.session_id,
    )
    return task_view(row)


@router.get("/tasks", response_model=list[TaskView])
def list_tasks(
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
    offset: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if offset < 0 or not 1 <= limit <= 200:
        raise ValueError("Invalid pagination")
    return [
        task_view(row)
        for row in db.scalars(
            select(Task)
            .where(Task.owner_id == user.user_id)
            .order_by(Task.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ]


@router.get("/tasks/{task_id}", response_model=TaskDetailsView)
def get_task(
    task_id: str,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    row = owned(db, user, task_id)
    result = task_view(row)
    if row.encrypted_result is not None:
        result["result"] = get_application(request).cipher.decrypt(row.encrypted_result)
    return result


@router.get("/tasks/{task_id}/attempts", response_model=list[TaskAttemptView])
def list_attempts(
    task_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> list[dict[str, Any]]:
    owned(db, user, task_id)
    return [
        serialize_record(row)
        for row in db.scalars(
            select(TaskAttempt).where(TaskAttempt.task_id == task_id).order_by(TaskAttempt.number)
        )
    ]


@router.post("/tasks/{task_id}/cancel", response_model=TaskView)
def cancel_task(
    task_id: str,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    return task_view(cancel(db, get_application(request).cipher, user, task_id))


@router.post("/tasks/{task_id}/resume", response_model=TaskView)
def resume_task(
    task_id: str,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    return task_view(resume(db, get_application(request).cipher, user, task_id))


@router.get("/interactions", response_model=list[InteractionView])
def list_interactions(
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> list[dict[str, Any]]:
    result = []
    for row in db.scalars(
        select(TaskInteraction)
        .where(
            TaskInteraction.recipient_id == user.user_id,
            TaskInteraction.status == "pending",
            TaskInteraction.expires_at > now(),
        )
        .order_by(TaskInteraction.created_at)
        .limit(100)
    ):
        result.append(
            serialize_record(row) | get_application(request).cipher.decrypt(row.encrypted_prompt)
        )
    return result


@router.post("/interactions/{request_id}/respond", response_model=InteractionReceiptView)
def answer_interaction(
    request_id: str,
    body: RespondToInteractionRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    return serialize_record(
        respond(db, get_application(request).cipher, user, request_id, body.value)
    )


@router.post("/schedules", status_code=201, response_model=ScheduleView)
def create_schedule(
    body: CreateScheduleRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    usable(db, user, body.agent_id)
    if body.first_at <= now():
        raise ValueError("First occurrence must be in the future")
    row = TaskSchedule(
        owner_id=user.user_id,
        agent_id=body.agent_id,
        name=body.name,
        kind=body.kind,
        expression=body.expression,
        interval_seconds=body.interval_seconds,
        timezone=body.timezone,
        next_at=body.first_at,
        encrypted_input=get_application(request).cipher.encrypt(body.input),
    )
    next_time(row, row.next_at)
    if row.kind == "cron":
        first = next_time(row, row.next_at - timedelta(microseconds=1))
        assert first is not None
        row.next_at = first
    db.add(row)
    db.flush()
    return serialize_record(row)


@router.get("/schedules", response_model=list[ScheduleView])
def list_schedules(
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> list[dict[str, Any]]:
    return [
        serialize_record(row)
        for row in db.scalars(
            select(TaskSchedule)
            .where(TaskSchedule.owner_id == user.user_id)
            .order_by(TaskSchedule.created_at)
            .limit(200)
        )
    ]


@router.patch("/schedules/{schedule_id}", response_model=ScheduleView)
def update_schedule(
    schedule_id: str,
    body: UpdateScheduleRequest,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    row = db.get(TaskSchedule, schedule_id, with_for_update=True)
    if row is None:
        raise NotFoundError("Schedule not found")
    require_owner(user, row.owner_id)
    if body.enabled and row.next_at <= now():
        following = next_time(row, now())
        if following is None:
            raise ConflictError("An expired one-time schedule cannot be enabled")
        row.next_at = following
    row.enabled = body.enabled
    return serialize_record(row)


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(
    schedule_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> None:
    row = db.get(TaskSchedule, schedule_id, with_for_update=True)
    if row is None:
        raise NotFoundError("Schedule not found")
    require_owner(user, row.owner_id)
    db.delete(row)
