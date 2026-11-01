"""Persistent schedule management; all entrypoints share service authorization."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.scheduling.api import (
    PersistentSchedule,
    ScheduleOccurrence,
    SchedulePreview,
)

from ..security import CurrentUser, get_application

router = APIRouter(prefix="/schedules", tags=["scheduler"])


class ScheduleEnabledRequest(OpsMeshContract):
    enabled: bool
    expected_revision: int | None = Field(default=None, ge=1)


class ScheduleUpdateRequest(OpsMeshContract):
    schedule: PersistentSchedule
    expected_revision: int = Field(ge=1)


class ScheduleRunNowRequest(OpsMeshContract):
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


class SchedulePreviewRequest(OpsMeshContract):
    expected_revision: int = Field(ge=1)
    count: int = Field(default=10, ge=1, le=100)
    after: datetime | None = None


@router.post("", status_code=201)
async def create(
    body: PersistentSchedule, request: Request, user: CurrentUser
) -> PersistentSchedule:
    return await get_application(request).services.scheduler.create(body, actor_id=user.id)


@router.get("")
async def schedules(request: Request, user: CurrentUser) -> list[PersistentSchedule]:
    return await get_application(request).services.scheduler.list_schedules(actor_id=user.id)


@router.get("/{schedule_id}")
async def get(schedule_id: str, request: Request, user: CurrentUser) -> PersistentSchedule:
    return await get_application(request).services.scheduler.get(schedule_id, actor_id=user.id)


@router.put("/{schedule_id}")
async def update(
    schedule_id: str, body: ScheduleUpdateRequest, request: Request, user: CurrentUser
) -> PersistentSchedule:
    return await get_application(request).services.scheduler.update(
        schedule_id, body.schedule, body.expected_revision, actor_id=user.id
    )


@router.delete("/{schedule_id}", status_code=204)
async def delete(
    schedule_id: str,
    request: Request,
    user: CurrentUser,
    expected_revision: Annotated[int, Query(ge=1)],
) -> Response:
    await get_application(request).services.scheduler.delete(
        schedule_id, expected_revision, actor_id=user.id
    )
    return Response(status_code=204)


@router.put("/{schedule_id}/enabled")
async def enabled(
    schedule_id: str, body: ScheduleEnabledRequest, request: Request, user: CurrentUser
) -> PersistentSchedule:
    return await get_application(request).services.scheduler.set_enabled(
        schedule_id, body.enabled, actor_id=user.id, expected_revision=body.expected_revision
    )


@router.post("/{schedule_id}/run-now")
async def run_now(
    schedule_id: str, body: ScheduleRunNowRequest, request: Request, user: CurrentUser
) -> ScheduleOccurrence:
    return await get_application(request).services.scheduler.run_now(
        schedule_id,
        body.expected_revision,
        actor_id=user.id,
        idempotency_key=body.idempotency_key,
    )


@router.post("/{schedule_id}/preview")
async def preview(
    schedule_id: str, body: SchedulePreviewRequest, request: Request, user: CurrentUser
) -> SchedulePreview:
    return await get_application(request).services.scheduler.preview(
        schedule_id, body.expected_revision, actor_id=user.id, count=body.count, after=body.after
    )


@router.get("/{schedule_id}/occurrences")
async def occurrences(
    schedule_id: str,
    request: Request,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ScheduleOccurrence]:
    return await get_application(request).services.scheduler.list_occurrences(
        schedule_id, actor_id=user.id, limit=limit, offset=offset
    )
