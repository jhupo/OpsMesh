"""Administrative API; transport ingress is accepted only from authenticated adapters."""

import asyncio
from dataclasses import asdict
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from opsmesh.gateway.api.dependencies import gateway, managed
from opsmesh.gateway.api.management import events_router
from opsmesh.gateway.api.management import router as management_router
from opsmesh.gateway.api.schemas import (
    AccountBind,
    BindingCreate,
    ChannelCreate,
    ChannelPatch,
)
from opsmesh.gateway.channels.bindings import Bindings
from opsmesh.gateway.contracts import (
    ChannelDefinition,
    DeliveryResolution,
    PushCreate,
)
from opsmesh.gateway.identity.conversations import Conversations
from opsmesh.gateway.identity.identities import Identities
from opsmesh.gateway.messaging import push
from opsmesh.gateway.messaging.media import GatewayMedia
from opsmesh.gateway.messaging.recovery import resolve_delivery, retry_inbox
from opsmesh.gateway.storage.models import (
    GatewayDelivery,
    GatewayIdentity,
    GatewayInbox,
    GatewayLease,
)
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.users.access import require_platform_admin
from opsmesh.users.auth.dependencies import CurrentUserDep
from opsmesh.users.service import load_identity

router = APIRouter(prefix="/gateway", tags=["gateway"])
router.include_router(management_router)
router.include_router(events_router)


@router.post("/bindings/qq", status_code=201)
async def create_binding(
    body: BindingCreate, request: Request, user: CurrentUserDep
) -> dict[str, Any]:
    require_platform_admin(user)
    return await asyncio.to_thread(Bindings(gateway(request).store).create, user.user_id, body.name)


@router.post("/bindings/qq/{binding_id}/poll")
async def poll_binding(binding_id: UUID, request: Request, user: CurrentUserDep) -> dict[str, Any]:
    require_platform_admin(user)
    return await asyncio.to_thread(Bindings(gateway(request).store).poll, binding_id, user.user_id)


@router.delete("/bindings/qq/{binding_id}")
async def cancel_binding(
    binding_id: UUID, request: Request, user: CurrentUserDep
) -> dict[str, Any]:
    require_platform_admin(user)
    return await asyncio.to_thread(
        Bindings(gateway(request).store).poll, binding_id, user.user_id, cancel=True
    )


@router.post("/channels/{channel_id}/push", status_code=202)
async def push_messages(
    channel_id: UUID, body: PushCreate, request: Request, user: CurrentUserDep
) -> dict[str, Any]:
    value = await managed(request, user, channel_id)
    definition = await asyncio.to_thread(value.store.get, channel_id)
    info = value.registry.infos.get(definition.kind)
    if info is None or not info.capabilities.proactive:
        raise InvalidArgumentError("This channel does not support proactive delivery")
    turns = await asyncio.to_thread(push.recipients, value.store, channel_id, body)
    for turn in turns:
        await value.execution.authorize(turn.user_id, turn.target)
    try:
        content = await GatewayMedia(value.media_root / str(channel_id)).prepare(
            body.content,
            preserve_image_url=definition.kind == "qq"
            and any(turn.message.reply_address.get("kind") in {"channel", "dm"} for turn in turns),
        )
    except ValueError as error:
        raise InvalidArgumentError("Invalid push media source") from error
    identifiers = await asyncio.to_thread(
        push.enqueue, value.store, channel_id, user.user_id, body, turns, content
    )
    return {"request_id": str(body.request_id), "deliveries": [str(item) for item in identifiers]}


def view(definition: ChannelDefinition) -> dict[str, Any]:
    return {
        **definition.model_dump(mode="json", exclude={"config"}),
        "configured_fields": sorted(definition.config),
    }


@router.get("/kinds")
def kinds(request: Request, user: CurrentUserDep) -> list[dict[str, Any]]:
    return [asdict(item) for item in gateway(request).registry.list()]


@router.get("/channels")
async def channels(request: Request, user: CurrentUserDep) -> list[dict[str, Any]]:
    require_platform_admin(user)
    return [view(item) for item in await asyncio.to_thread(gateway(request).store.list_channels)]


@router.post("/channels", status_code=201)
async def create(body: ChannelCreate, request: Request, user: CurrentUserDep) -> dict[str, Any]:
    require_platform_admin(user)
    try:
        definition = ChannelDefinition(id=uuid4(), owner_id=user.user_id, **body.model_dump())
        return view(await gateway(request).save(definition, user.user_id))
    except ValueError as error:
        raise InvalidArgumentError("Invalid gateway channel configuration") from error


@router.get("/channels/{channel_id}")
async def detail(channel_id: UUID, request: Request, user: CurrentUserDep) -> dict[str, Any]:
    value = await managed(request, user, channel_id)
    return view(await asyncio.to_thread(value.store.get, channel_id))


@router.patch("/channels/{channel_id}")
async def update(
    channel_id: UUID, body: ChannelPatch, request: Request, user: CurrentUserDep
) -> dict[str, Any]:
    value = await managed(request, user, channel_id)
    previous = await asyncio.to_thread(value.store.get, channel_id)
    patch = body.model_dump(exclude_unset=True)
    if any(item is None for key, item in patch.items() if key != "target"):
        raise InvalidArgumentError("Channel fields cannot be null")
    if previous.revision != body.revision:
        raise ConflictError("Channel revision changed")
    if "config" in patch:
        merged = {**previous.config, **patch["config"]}
        patch["config"] = {key: item for key, item in merged.items() if item is not None}
    try:
        definition = ChannelDefinition.model_validate({**previous.model_dump(), **patch})
        return view(await value.save(definition, user.user_id))
    except ValueError as error:
        raise InvalidArgumentError("Invalid gateway configuration") from error


@router.delete("/channels/{channel_id}", status_code=204)
async def delete(channel_id: UUID, request: Request, user: CurrentUserDep) -> None:
    value = await managed(request, user, channel_id)
    await value.delete(channel_id, user.user_id)


@router.get("/channels/{channel_id}/status")
async def status(channel_id: UUID, request: Request, user: CurrentUserDep) -> dict[str, Any]:
    value = await managed(request, user, channel_id)

    def read() -> dict[str, Any]:
        with value.store.sessions() as session:
            lease = session.scalar(
                select(GatewayLease).where(
                    GatewayLease.channel_id == channel_id, GatewayLease.expires_at > func.now()
                )
            )
            return {
                "lease_held": lease is not None,
                "lease_expires_at": lease.expires_at if lease else None,
            }

    return await asyncio.to_thread(read)


@router.post("/channels/{channel_id}/probe")
async def probe(channel_id: UUID, request: Request, user: CurrentUserDep) -> dict[str, object]:
    value = await managed(request, user, channel_id)
    return await value.probe(channel_id)


@router.post("/channels/{channel_id}/inbox/{inbox_id}/retry", status_code=204)
async def retry_event(
    channel_id: UUID,
    inbox_id: UUID,
    request: Request,
    user: CurrentUserDep,
) -> None:
    value = await managed(request, user, channel_id)
    await asyncio.to_thread(retry_inbox, value.store, channel_id, inbox_id, user.user_id)


@router.post("/channels/{channel_id}/deliveries/{delivery_id}/resolve", status_code=204)
async def reconcile_delivery(
    channel_id: UUID,
    delivery_id: UUID,
    body: DeliveryResolution,
    request: Request,
    user: CurrentUserDep,
) -> None:
    value = await managed(request, user, channel_id)
    await asyncio.to_thread(
        resolve_delivery,
        value.store,
        channel_id,
        delivery_id,
        user.user_id,
        body,
    )


@router.get("/channels/{channel_id}/identities")
async def identities(
    channel_id: UUID, request: Request, user: CurrentUserDep
) -> list[dict[str, object]]:
    value = await managed(request, user, channel_id)
    return await asyncio.to_thread(Identities(value.store).list, channel_id)


@router.put("/channels/{channel_id}/identities/{identity_id}/binding")
async def bind(
    channel_id: UUID, identity_id: UUID, body: AccountBind, request: Request, user: CurrentUserDep
) -> dict[str, str]:
    value = await managed(request, user, channel_id)

    def apply() -> None:
        with value.store.sessions() as session:
            actor = load_identity(session, body.user_id)
            if actor is None or actor.status != "active":
                raise NotFoundError("Active platform account does not exist")
        Identities(value.store).bind(channel_id, identity_id, body.user_id, user.user_id)

    await asyncio.to_thread(apply)
    return {"status": "bound"}


@router.delete("/channels/{channel_id}/identities/{identity_id}/binding", status_code=204)
async def unbind(
    channel_id: UUID, identity_id: UUID, request: Request, user: CurrentUserDep
) -> None:
    value = await managed(request, user, channel_id)

    def apply() -> None:
        with value.store.sessions() as session:
            row = session.get(GatewayIdentity, identity_id)
            if row is None or row.channel_id != channel_id:
                raise NotFoundError("External identity does not exist")
        Identities(value.store).unbind(identity_id, user.user_id)

    await asyncio.to_thread(apply)


@router.get("/channels/{channel_id}/conversations")
async def conversations(
    channel_id: UUID, request: Request, user: CurrentUserDep
) -> list[dict[str, object]]:
    value = await managed(request, user, channel_id)
    return await asyncio.to_thread(Conversations(value.store).list, channel_id)


@router.get("/channels/{channel_id}/subjects")
async def subjects(
    channel_id: UUID, request: Request, user: CurrentUserDep
) -> list[dict[str, object]]:
    value = await managed(request, user, channel_id)
    turns = await asyncio.to_thread(push.known_subjects, value.store, channel_id)
    return [
        {
            "receipt_id": str(turn.inbox_id),
            "identity_id": str(turn.identity_id),
            "user_id": str(turn.user_id),
            "conversation": turn.message.conversation.model_dump(mode="json"),
        }
        for turn in turns
    ]


@router.get("/channels/{channel_id}/conversations/{key}/members")
async def members(
    channel_id: UUID, key: str, request: Request, user: CurrentUserDep
) -> list[dict[str, object]]:
    value = await managed(request, user, channel_id)
    return await asyncio.to_thread(Conversations(value.store).members, channel_id, key)


@router.get("/channels/{channel_id}/deliveries")
async def deliveries(
    channel_id: UUID, request: Request, user: CurrentUserDep
) -> list[dict[str, Any]]:
    value = await managed(request, user, channel_id)

    def read() -> list[dict[str, Any]]:
        with value.store.sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "status": row.status,
                    "attempts": row.attempts,
                    "remote_id": row.remote_id,
                    "error_code": row.error_code,
                }
                for row in session.scalars(
                    select(GatewayDelivery)
                    .where(GatewayDelivery.channel_id == channel_id)
                    .order_by(GatewayDelivery.created_at.desc())
                    .limit(100)
                )
            ]

    return await asyncio.to_thread(read)


@router.get("/channels/{channel_id}/inbox")
async def inbox(channel_id: UUID, request: Request, user: CurrentUserDep) -> list[dict[str, Any]]:
    value = await managed(request, user, channel_id)

    def read() -> list[dict[str, Any]]:
        with value.store.sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "identity_id": str(row.identity_id),
                    "run_id": str(row.run_id) if row.run_id else None,
                    "status": row.status,
                    "error_code": row.error_code,
                }
                for row in session.scalars(
                    select(GatewayInbox)
                    .where(GatewayInbox.channel_id == channel_id)
                    .order_by(GatewayInbox.created_at.desc())
                    .limit(100)
                )
            ]

    return await asyncio.to_thread(read)
