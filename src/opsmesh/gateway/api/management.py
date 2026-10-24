"""Administrative admission and explicit recovery, with no platform I/O."""

import asyncio
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from opsmesh_sdk.channel_management import ManagementRequest, ManagementResult, OperationReceipt
from pydantic import BaseModel, ConfigDict

from opsmesh.gateway.api.dependencies import managed
from opsmesh.gateway.management.contracts import OperationOrigin
from opsmesh.gateway.management.service import Management
from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.users.auth.dependencies import CurrentUserDep

router = APIRouter(prefix="/channels/{channel_id}/operations", tags=["gateway-management"])
events_router = APIRouter(prefix="/channels/{channel_id}/events", tags=["gateway-management"])


class OperationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    operation: ManagementRequest


@events_router.get("")
async def events(
    channel_id: UUID, request: Request, user: CurrentUserDep
) -> list[dict[str, object]]:
    value = await managed(request, user, channel_id)
    if value.events is None:
        raise InvalidArgumentError("Conversation event management is not assembled")
    return await asyncio.to_thread(value.events.list, channel_id)


@events_router.post("/{event_id}/cancel", status_code=204)
async def cancel_event(
    channel_id: UUID, event_id: UUID, request: Request, user: CurrentUserDep
) -> None:
    value = await managed(request, user, channel_id)
    if value.events is None:
        raise InvalidArgumentError("Conversation event management is not assembled")
    await asyncio.to_thread(value.events.cancel, channel_id, event_id, user.user_id)


class OperationResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["approve", "cancel", "retry", "acknowledge"]
    accept_duplicate_risk: bool = False
    result: ManagementResult | None = None


@router.post("", status_code=202)
async def submit(
    channel_id: UUID, body: OperationCreate, request: Request, user: CurrentUserDep
) -> OperationReceipt:
    value = await managed(request, user, channel_id)
    return await asyncio.to_thread(
        Management(value.store, value.registry.generation).submit,
        channel_id,
        body.request_id,
        OperationOrigin(kind="admin", actor_id=user.user_id),
        body.operation,
    )


@router.get("")
async def operations(
    channel_id: UUID, request: Request, user: CurrentUserDep
) -> list[OperationReceipt]:
    value = await managed(request, user, channel_id)
    return await asyncio.to_thread(
        Management(value.store, value.registry.generation).list, channel_id
    )


@router.get("/{operation_id}")
async def detail(
    channel_id: UUID, operation_id: UUID, request: Request, user: CurrentUserDep
) -> OperationReceipt:
    value = await managed(request, user, channel_id)
    return await asyncio.to_thread(
        Management(value.store, value.registry.generation).get, channel_id, operation_id
    )


@router.post("/{operation_id}/resolve")
async def resolve(
    channel_id: UUID,
    operation_id: UUID,
    body: OperationResolution,
    request: Request,
    user: CurrentUserDep,
) -> OperationReceipt:
    value = await managed(request, user, channel_id)
    return await asyncio.to_thread(
        Management(value.store, value.registry.generation).resolve,
        channel_id,
        operation_id,
        user.user_id,
        body.action,
        accept_duplicate_risk=body.accept_duplicate_risk,
        result=body.result,
    )
