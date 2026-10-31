"""Exercise the real persistence and protocol conversion with a fake HTTP peer."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from opsmesh.framework.adapters import BotRef, OpsMeshMessage, OpsMeshMessageSegment, ReplyTarget
from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.drivers import OpsMeshNetworkDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.events import DispatchContext, OpsMeshEvent
from opsmesh.framework.message import DispatchResult
from opsmesh.infrastructure.bots.onebot.event import OneBotMessageEvent
from opsmesh.infrastructure.bots.onebot.message import decode_message, encode_message
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.accounts.api import UserPrincipal
from opsmesh.modules.bots.api import BotAccount
from opsmesh.modules.bots.internal.record_keys import bot_record_key

from .helpers import create_bot_service as BotService
from .helpers import onebot_adapter, receive_onebot


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[VersionedRecordStore]:
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'bots.sqlite').as_posix()}")
    records = VersionedRecordStore(database, "test_bots")
    await database.initialize()
    try:
        yield records
    finally:
        await database.close()


async def authorize(actor_id: str, action: str, resource: str) -> None:
    if actor_id not in {"admin", "user"}:
        raise OpsMeshError("FORBIDDEN", "Unauthorized test principal")


def secret(reference: str) -> str:
    return {"env:BOT_SECRET": "test-secret", "env:BOT_TOKEN": "test-token"}[reference]


def account() -> BotAccount:
    return BotAccount(
        id="support",
        self_id="100",
        adapter="onebot_v11",
        settings={"api_base_url": "http://onebot.test"},
        credentials={"webhook_secret": "env:BOT_SECRET", "access_token": "env:BOT_TOKEN"},
    )


def target() -> ReplyTarget:
    return ReplyTarget(
        bot=BotRef(adapter_id="onebot_v11", bot_id="support"), channel_id="private:200"
    )


def webhook(**overrides: object) -> tuple[bytes, str]:
    data = {
        "time": 1700000000,
        "post_type": "message",
        "self_id": 100,
        "message_type": "private",
        "message_id": 1,
        "user_id": 200,
        "message": [{"type": "text", "data": {"text": "hello"}}],
        **overrides,
    }
    body = json.dumps(data).encode()
    signature = "sha1=" + hmac.new(b"test-secret", body, hashlib.sha1).hexdigest()
    return body, signature


async def ignore(context: DispatchContext[OpsMeshEvent]) -> DispatchResult:
    return DispatchResult(context.event.id)


async def resolve_principal(principal_id: str) -> UserPrincipal:
    if principal_id not in {"admin", "user"}:
        raise OpsMeshError("NOT_FOUND", "Unknown local principal")
    return UserPrincipal(id=principal_id, username=principal_id)


async def resolve_agent(reference: str) -> AgentSpecification:
    if reference != "assistant@2":
        raise OpsMeshError("NOT_FOUND", "Published agent revision does not exist")
    return AgentSpecification(id="assistant", name="Assistant", revision=2)


async def test_bot_adapter_uses_shared_application_driver(store: VersionedRecordStore) -> None:
    driver = OpsMeshNetworkDriver()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    ) as client:
        service = BotService(store, secret, client, authorize, ignore, driver=driver)
        assert service.driver is driver
        assert onebot_adapter(service) is driver.adapters["onebot_v11"]
        assert onebot_adapter(service).driver is driver
        async with driver.lifespan():
            restarted = BotService(store, secret, client, authorize, ignore, driver=driver)
            assert onebot_adapter(restarted) is onebot_adapter(service)


@pytest.mark.asyncio
async def test_signed_ingress_identity_route_and_duplicate_survive_service_restart(
    store: VersionedRecordStore,
) -> None:
    contexts: list[DispatchContext[OpsMeshEvent]] = []

    async def dispatch(context: DispatchContext[OpsMeshEvent]) -> DispatchResult:
        contexts.append(context)
        return DispatchResult(context.event.id, matched=1)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    ) as client:
        service = BotService(
            store,
            secret,
            client,
            authorize,
            dispatch,
            agent_resolver=resolve_agent,
            principal_resolver=resolve_principal,
        )
        await service.create_account(account(), actor_id="admin")
        await service.bind_identity("support", "200", "user", actor_id="admin")
        await service.bind_route("support", "private:200", "assistant@2", actor_id="admin")
        body, signature = webhook()
        receipt = await receive_onebot(service, "support", body, signature=signature)
        assert receipt.status == "processed" and receipt.matched == 1
        restarted = BotService(store, secret, client, authorize, dispatch)
        duplicate = await receive_onebot(restarted, "support", body, signature=signature)
        assert duplicate.status == "duplicate"
        assert len(contexts) == 1
        context = contexts[0]
        assert context.values == {
            "principal_id": "user",
            "bot_id": "support",
            "agent_revision": "assistant@2",
        }
        assert isinstance(context.event, OneBotMessageEvent)
        assert context.reply_target == target().model_copy(update={"message_id": "1"})
        assert (await restarted.get_account("support", actor_id="admin")).self_id == "100"


@pytest.mark.asyncio
async def test_signature_unknown_user_and_disabled_account_are_rejected(
    store: VersionedRecordStore,
) -> None:
    async with httpx.AsyncClient() as client:
        service = BotService(store, secret, client, authorize, ignore)
        await service.create_account(account(), actor_id="admin")
        body, signature = webhook()
        with pytest.raises(OpsMeshError) as invalid:
            await receive_onebot(service, "support", body, signature="sha1=wrong")
        assert invalid.value.code.upper() == "UNAUTHORIZED"
        with pytest.raises(OpsMeshError) as unknown:
            await receive_onebot(service, "support", body, signature=signature)
        assert unknown.value.code.upper() == "FORBIDDEN"
        await service.set_enabled("support", False, actor_id="admin")
        with pytest.raises(OpsMeshError) as disabled:
            await receive_onebot(service, "support", body, signature=signature)
        assert disabled.value.code.upper() == "BOT_DISABLED"


@pytest.mark.asyncio
async def test_successful_send_is_persistently_deduplicated_and_key_cannot_change_payload(
    store: VersionedRecordStore,
) -> None:
    sent: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/send_msg"
        assert request.headers["Authorization"] == "Bearer test-token"
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"message_id": 77}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = BotService(store, secret, client, authorize, ignore)
        await service.create_account(account(), actor_id="admin")
        receipt = await service.send(
            target(), OpsMeshMessage("hello"), idempotency_key="reply:1", actor_id="user"
        )
        restarted = BotService(store, secret, client, authorize, ignore)
        duplicate = await restarted.send(
            target(), OpsMeshMessage("hello"), idempotency_key="reply:1", actor_id="user"
        )
        assert receipt.message_id == duplicate.message_id == "77" and duplicate.duplicate
        assert len(sent) == 1 and sent[0]["user_id"] == 200
        with pytest.raises(OpsMeshError) as conflict:
            await service.send(
                target(), OpsMeshMessage("different"), idempotency_key="reply:1", actor_id="user"
            )
        assert conflict.value.code.upper() == "IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_failed_connection_can_retry_but_uncertain_delivery_cannot(
    store: VersionedRecordStore,
) -> None:
    attempts = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("connection refused", request=request)
        if attempts == 3:
            raise httpx.ReadTimeout("response lost", request=request)
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"message_id": 2}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = BotService(store, secret, client, authorize, ignore)
        await service.create_account(account(), actor_id="admin")
        with pytest.raises(OpsMeshError) as failed:
            await service.send(
                target(), OpsMeshMessage("hello"), idempotency_key="safe", actor_id="user"
            )
        assert failed.value.retryable
        assert (
            await service.send(
                target(), OpsMeshMessage("hello"), idempotency_key="safe", actor_id="user"
            )
        ).message_id == "2"
        with pytest.raises(OpsMeshError) as uncertain:
            await service.send(
                target(), OpsMeshMessage("hello"), idempotency_key="unknown", actor_id="user"
            )
        assert uncertain.value.code.upper() == "DELIVERY_UNKNOWN" and not uncertain.value.retryable
        with pytest.raises(OpsMeshError):
            await service.send(
                target(), OpsMeshMessage("hello"), idempotency_key="unknown", actor_id="user"
            )
        assert attempts == 3


@pytest.mark.asyncio
async def test_concurrent_delivery_claim_prevents_double_send(store: VersionedRecordStore) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    attempts = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"message_id": 1}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = BotService(store, secret, client, authorize, ignore)
        await service.create_account(account(), actor_id="admin")
        first = asyncio.create_task(
            service.send(target(), OpsMeshMessage("hi"), idempotency_key="same", actor_id="user")
        )
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(OpsMeshError):
            await service.send(
                target(), OpsMeshMessage("hi"), idempotency_key="same", actor_id="user"
            )
        release.set()
        await first
        assert attempts == 1


def test_cq_and_array_segments_are_preserved_without_false_file_support() -> None:
    message = decode_message("hello &amp; [CQ:at,qq=200][CQ:image,file=a&#44;b]")
    assert message.extract_plain_text() == "hello & "
    assert encode_message(message)[1] == {"type": "at", "data": {"qq": "200"}}
    assert encode_message(message)[2]["data"]["file"] == "a,b"
    with pytest.raises(OpsMeshError):
        encode_message(OpsMeshMessage(OpsMeshMessageSegment.file("artifact:1")))


async def test_persisted_legacy_account_can_send_and_upgrade_without_recreation(store) -> None:
    legacy = {
        "id": "support",
        "adapter": "onebot_v11",
        "self_id": "100",
        "transport": "http",
        "api_base_url": "http://onebot.test",
        "websocket_url": None,
        "reconnect_seconds": 1,
        "access_token_ref": "env:BOT_TOKEN",
        "webhook_secret_ref": "env:BOT_SECRET",
        "file_upload": "unsupported",
        "enabled": True,
        "revision": 1,
    }
    key = bot_record_key("account", "support")
    await store.create(key, {"kind": "account", "account": legacy})
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.url.path == "/send_msg"
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"message_id": 91}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = BotService(store, secret, client, authorize, ignore)
        migrated = await service.get_account("support", actor_id="admin")
        assert migrated.settings["api_base_url"] == "http://onebot.test"
        assert migrated.credentials == {
            "access_token": "env:BOT_TOKEN",
            "webhook_secret": "env:BOT_SECRET",
        }
        assert (await store.get(key)).data["account"] == legacy
        receipt = await service.send(
            target(), OpsMeshMessage("hello"), idempotency_key="legacy-reply", actor_id="user"
        )
        assert receipt.message_id == "91" and len(calls) == 1
        upgraded = await service.update_account(migrated, expected_revision=1, actor_id="admin")
        saved = await store.get(key)
        assert upgraded.revision == saved.revision == 2
        assert saved.data["account"]["settings"] == migrated.settings
        assert "access_token_ref" not in saved.data["account"]
        assert "api_base_url" not in saved.data["account"]
