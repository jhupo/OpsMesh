"""Protocol-independent bot management and adapter-authenticated callbacks."""

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from opsmesh.framework.adapters import BotAdapterDescriptor, BotWebhookRequest, OpsMeshMessage
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.bots.api import (
    BotAccount,
    BotConnectionStatus,
    BotIdentity,
    BotRoute,
    DeliveryReceipt,
    FileDeliveryReceipt,
    InboundReceipt,
)

from ..security import CurrentUser, get_application

router = APIRouter(tags=["bots"])


class BotEnabledRequest(OpsMeshContract):
    enabled: bool


class UpdateBotRequest(OpsMeshContract):
    account: BotAccount
    expected_revision: int = Field(ge=1)


class BindIdentityRequest(OpsMeshContract):
    external_user_id: str
    principal_id: str


class BindRouteRequest(OpsMeshContract):
    channel_id: str = Field(min_length=1, max_length=256)
    agent_revision: str = Field(min_length=1, max_length=256)


class SendMessageRequest(OpsMeshContract):
    channel_id: str
    message: OpsMeshMessage
    idempotency_key: str = Field(min_length=1, max_length=200)
    message_id: str | None = None
    delivery_sequence: int = Field(default=1, ge=1)


@router.post("/bots", status_code=201)
async def create_bot(body: BotAccount, request: Request, user: CurrentUser) -> BotAccount:
    return await get_application(request).services.bots.create_account(body, actor_id=user.id)


@router.get("/bot-adapters")
async def adapters(request: Request, user: CurrentUser) -> tuple[BotAdapterDescriptor, ...]:
    return await get_application(request).services.bots.list_adapters(actor_id=user.id)


@router.get("/bots")
async def bots(request: Request, user: CurrentUser) -> list[BotAccount]:
    return await get_application(request).services.bots.list_accounts(actor_id=user.id)


@router.get("/bots/{bot_id}")
async def bot(bot_id: str, request: Request, user: CurrentUser) -> BotAccount:
    return await get_application(request).services.bots.get_account(bot_id, actor_id=user.id)


@router.put("/bots/{bot_id}")
async def update_bot(
    bot_id: str, body: UpdateBotRequest, request: Request, user: CurrentUser
) -> BotAccount:
    from opsmesh.framework.errors import OpsMeshError

    if body.account.id != bot_id:
        raise OpsMeshError("INVALID_REQUEST", "Bot account ID cannot change")
    return await get_application(request).services.bots.update_account(
        body.account, expected_revision=body.expected_revision, actor_id=user.id
    )


@router.get("/bots/{bot_id}/connection")
async def connection(bot_id: str, request: Request, user: CurrentUser) -> BotConnectionStatus:
    return await get_application(request).services.bots.connection_status(bot_id, actor_id=user.id)


@router.put("/bots/{bot_id}/enabled")
async def enabled(
    bot_id: str, body: BotEnabledRequest, request: Request, user: CurrentUser
) -> BotAccount:
    return await get_application(request).services.bots.set_enabled(
        bot_id, body.enabled, actor_id=user.id
    )


@router.put("/bots/{bot_id}/identities")
async def identity(
    bot_id: str, body: BindIdentityRequest, request: Request, user: CurrentUser
) -> BotIdentity:
    app = get_application(request)
    return await app.services.bots.bind_identity(
        bot_id, body.external_user_id, body.principal_id, actor_id=user.id
    )


@router.get("/bots/{bot_id}/identities")
async def identities(bot_id: str, request: Request, user: CurrentUser) -> list[BotIdentity]:
    return await get_application(request).services.bots.list_identities(bot_id, actor_id=user.id)


@router.get("/bots/{bot_id}/identities/{external_user_id}")
async def get_identity(
    bot_id: str, external_user_id: str, request: Request, user: CurrentUser
) -> BotIdentity:
    return await get_application(request).services.bots.get_identity(
        bot_id, external_user_id, actor_id=user.id
    )


@router.delete("/bots/{bot_id}/identities/{external_user_id}", status_code=204)
async def unbind_identity(
    bot_id: str, external_user_id: str, request: Request, user: CurrentUser
) -> Response:
    await get_application(request).services.bots.unbind_identity(
        bot_id, external_user_id, actor_id=user.id
    )
    return Response(status_code=204)


@router.put("/bots/{bot_id}/routes")
async def route(
    bot_id: str, body: BindRouteRequest, request: Request, user: CurrentUser
) -> BotRoute:
    app = get_application(request)
    return await app.services.bots.bind_route(
        bot_id, body.channel_id, body.agent_revision, actor_id=user.id
    )


@router.get("/bots/{bot_id}/routes")
async def routes(bot_id: str, request: Request, user: CurrentUser) -> list[BotRoute]:
    return await get_application(request).services.bots.list_routes(bot_id, actor_id=user.id)


@router.get("/bots/{bot_id}/routes/{channel_id}")
async def get_route(bot_id: str, channel_id: str, request: Request, user: CurrentUser) -> BotRoute:
    return await get_application(request).services.bots.get_route(
        bot_id, channel_id, actor_id=user.id
    )


@router.delete("/bots/{bot_id}/routes/{channel_id}", status_code=204)
async def unbind_route(
    bot_id: str, channel_id: str, request: Request, user: CurrentUser
) -> Response:
    await get_application(request).services.bots.unbind_route(bot_id, channel_id, actor_id=user.id)
    return Response(status_code=204)


@router.post("/bots/{bot_id}/messages")
async def send(
    bot_id: str, body: SendMessageRequest, request: Request, user: CurrentUser
) -> DeliveryReceipt:
    service = get_application(request).services.bots
    target = await service.resolve_delivery_target(
        bot_id,
        body.channel_id,
        actor_id=user.id,
        message_id=body.message_id,
        delivery_sequence=body.delivery_sequence,
    )
    return await service.send(
        target, body.message, idempotency_key=body.idempotency_key, actor_id=user.id
    )


class SendFileRequest(OpsMeshContract):
    channel_id: str
    workspace_id: str
    path: str
    name: str
    idempotency_key: str = Field(min_length=1, max_length=200)


@router.post("/bots/{bot_id}/files")
async def send_file(
    bot_id: str, body: SendFileRequest, request: Request, user: CurrentUser
) -> FileDeliveryReceipt:
    app = get_application(request)
    await app.authorize_workspace(user.id, body.workspace_id)
    content = await app.services.workspaces.read(body.workspace_id, body.path)
    target = await app.services.bots.resolve_delivery_target(
        bot_id, body.channel_id, actor_id=user.id
    )
    return await app.services.bots.upload_file(
        target, body.name, content, idempotency_key=body.idempotency_key, actor_id=user.id
    )


@router.post("/callbacks/onebot/{bot_id}", response_model=None)
async def legacy_callback(bot_id: str, request: Request) -> InboundReceipt | Response:
    app = get_application(request)
    address = request.client.host if request.client else "unknown"
    await app.services.rate_limiter.check(f"bot:{bot_id}:{address}", limit=600)
    result = await app.services.bots.receive_webhook(
        bot_id, BotWebhookRequest(await request.body(), dict(request.headers))
    )
    return result.receipt or JSONResponse(
        result.response.body, status_code=result.response.status_code
    )


@router.post("/callbacks/bots/{bot_id}")
async def callback(bot_id: str, request: Request) -> Response:
    app = get_application(request)
    address = request.client.host if request.client else "unknown"
    await app.services.rate_limiter.check(f"bot:{bot_id}:{address}", limit=600)
    result = await app.services.bots.receive_webhook(
        bot_id, BotWebhookRequest(await request.body(), dict(request.headers))
    )
    return JSONResponse(
        result.response.body,
        status_code=result.response.status_code,
        headers=result.response.headers,
    )
