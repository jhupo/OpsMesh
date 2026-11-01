"""Explicit external event ingestion and signed, delivery-idempotent webhooks."""

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from pydantic import Field

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.events import OpsMeshEvent
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.triggers.api import TriggerDefinition, TriggerDelivery

from ..security import CurrentUser, get_application

router = APIRouter(prefix="/triggers", tags=["triggers"])


class TriggerUpdateRequest(OpsMeshContract):
    definition: TriggerDefinition
    expected_revision: int = Field(ge=1)


class TriggerEnabledRequest(OpsMeshContract):
    enabled: bool
    expected_revision: int = Field(ge=1)


@router.post("", status_code=201)
async def create(body: TriggerDefinition, request: Request, user: CurrentUser) -> TriggerDefinition:
    return await get_application(request).services.triggers.create(body, actor_id=user.id)


@router.get("")
async def triggers(request: Request, user: CurrentUser) -> list[TriggerDefinition]:
    return await get_application(request).services.triggers.list_triggers(actor_id=user.id)


@router.post("/events", status_code=202)
async def ingest_event(
    body: OpsMeshEvent, request: Request, user: CurrentUser
) -> list[TriggerDelivery]:
    return await get_application(request).services.triggers.dispatch_event(body, actor_id=user.id)


@router.get("/{trigger_id}")
async def get(trigger_id: str, request: Request, user: CurrentUser) -> TriggerDefinition:
    return await get_application(request).services.triggers.get(trigger_id, actor_id=user.id)


@router.put("/{trigger_id}")
async def update(
    trigger_id: str, body: TriggerUpdateRequest, request: Request, user: CurrentUser
) -> TriggerDefinition:
    return await get_application(request).services.triggers.update(
        trigger_id, body.definition, body.expected_revision, actor_id=user.id
    )


@router.put("/{trigger_id}/enabled")
async def enabled(
    trigger_id: str, body: TriggerEnabledRequest, request: Request, user: CurrentUser
) -> TriggerDefinition:
    return await get_application(request).services.triggers.set_enabled(
        trigger_id, body.enabled, body.expected_revision, actor_id=user.id
    )


@router.delete("/{trigger_id}", status_code=204)
async def delete(
    trigger_id: str,
    request: Request,
    user: CurrentUser,
    expected_revision: Annotated[int, Query(ge=1)],
) -> Response:
    await get_application(request).services.triggers.delete(
        trigger_id, expected_revision, actor_id=user.id
    )
    return Response(status_code=204)


@router.get("/{trigger_id}/deliveries")
async def deliveries(
    trigger_id: str,
    request: Request,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TriggerDelivery]:
    return await get_application(request).services.triggers.list_deliveries(
        trigger_id, actor_id=user.id, limit=limit, offset=offset
    )


@router.post("/{trigger_id}/webhook", status_code=202)
async def webhook(trigger_id: str, request: Request) -> TriggerDelivery:
    timestamp = request.headers.get("x-opsmesh-timestamp", "")
    delivery_id = request.headers.get("x-opsmesh-delivery", "")
    signature = request.headers.get("x-opsmesh-signature", "")
    if not timestamp or not delivery_id or not signature:
        raise OpsMeshError("UNAUTHORIZED", "Signed webhook headers are required")
    return await get_application(request).services.triggers.ingest_webhook(
        trigger_id,
        await request.body(),
        timestamp=timestamp,
        delivery_id=delivery_id,
        signature=signature,
    )
