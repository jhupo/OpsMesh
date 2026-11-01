"""Persistent delivery outcomes preserve definite rejection without replaying uncertainty."""

from contextlib import asynccontextmanager

import httpx
import pytest

from opsmesh.framework.adapters import (
    BotAdapterRegistry,
    BotRef,
    OpsMeshMessage,
    OpsMeshMessageSegment,
    ReplyTarget,
)
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.message import DispatchResult
from opsmesh.infrastructure.bots.dingtalk import DingTalkAdapter
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.bots.api import BotAccount
from opsmesh.modules.bots.internal.service import BotService


@pytest.fixture
async def delivery_store(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'outcomes.sqlite').as_posix()}")
    store = VersionedRecordStore(database, "test_delivery_outcomes")
    await database.initialize()
    try:
        yield store
    finally:
        await database.close()


@asynccontextmanager
async def delivery_platform(store, handler, *, create=True):
    async def authorize(actor, action, resource):
        assert actor == "operator"

    async def dispatch(context):
        return DispatchResult(context.event.id)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        driver = OpsMeshDriver()
        registry = BotAdapterRegistry(driver)
        adapter = DingTalkAdapter(driver, client, lambda ref: "synthetic-secret", autostart=False)
        registry.register(adapter)
        service = BotService(
            store, lambda ref: "synthetic-secret", authorize, dispatch, adapters=registry
        )
        if create:
            await service.create_account(
                BotAccount(
                    id="support",
                    adapter="dingtalk",
                    self_id="ding-app",
                    credentials={"client_secret": "env:TEST_DINGTALK_SECRET"},
                ),
                actor_id="operator",
            )
        try:
            yield service, adapter
        finally:
            await service.close()


def target():
    return ReplyTarget(bot=BotRef(adapter_id="dingtalk", bot_id="support"), channel_id="group:room")


async def send(service, *, key="attempt", message=None):
    return await service.send(
        target(), message or OpsMeshMessage("hello"), idempotency_key=key, actor_id="operator"
    )


async def delivery_record(store, kind="delivery"):
    return next(record for record in await store.list() if record.data.get("kind") == kind)


@pytest.mark.parametrize("operation", ["message", "file"])
async def test_local_unsupported_delivery_replays_rejection_across_restart(
    delivery_store, operation
):
    requests = []

    def handler(request):
        requests.append(request)
        raise AssertionError("Unsupported content must not make a network request")

    async def perform(service, *, changed=False):
        if operation == "file":
            return await service.upload_file(
                target(),
                "report.txt",
                b"changed" if changed else b"report",
                idempotency_key="attempt",
                actor_id="operator",
            )
        return await send(
            service,
            message=OpsMeshMessage(
                OpsMeshMessageSegment.image("resource:other" if changed else "resource:one")
            ),
        )

    async with delivery_platform(delivery_store, handler) as (service, _):
        with pytest.raises(OpsMeshError) as first:
            await perform(service)
        assert first.value.code == "UNSUPPORTED_FEATURE"
        record = await delivery_record(
            delivery_store, "file_delivery" if operation == "file" else "delivery"
        )
        assert record.data["state"] == "not_attempted"
        assert record.data["error_code"] == "UNSUPPORTED_FEATURE"
    async with delivery_platform(delivery_store, handler, create=False) as (restarted, _):
        with pytest.raises(OpsMeshError) as repeated:
            await perform(restarted)
        assert repeated.value.code == "UNSUPPORTED_FEATURE" and not repeated.value.retryable
        with pytest.raises(OpsMeshError) as conflict:
            await perform(restarted, changed=True)
        assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    assert not requests


async def test_client_configuration_failure_is_persisted_without_rebuilding_on_repeat(
    delivery_store, monkeypatch
):
    requests, factory_calls = [], []

    def handler(request):
        requests.append(request)
        raise AssertionError("Invalid client configuration must not send")

    async with delivery_platform(delivery_store, handler) as (service, adapter):
        original = adapter.get_bot

        def invalid(configuration):
            factory_calls.append(configuration.id)
            raise OpsMeshError("CONFIG_INVALID", "Adapter client cannot be constructed")

        monkeypatch.setattr(adapter, "get_bot", invalid)
        with pytest.raises(OpsMeshError) as first:
            await send(service)
        assert first.value.code == "CONFIG_INVALID"
        assert (await delivery_record(delivery_store)).data["state"] == "not_attempted"
        monkeypatch.setattr(adapter, "get_bot", original)
        with pytest.raises(OpsMeshError) as repeated:
            await send(service)
        assert repeated.value.code == "CONFIG_INVALID"
        assert factory_calls == ["support"] and not requests


@pytest.mark.parametrize(
    "status, expected_code", [(400, "BOT_REQUEST_REJECTED"), (401, "BOT_AUTHENTICATION_FAILED")]
)
async def test_provider_rejection_is_recorded_and_repeated_without_another_send(
    delivery_store, status, expected_code
):
    deliveries = []

    def handler(request):
        if request.url.path.endswith("accessToken"):
            return httpx.Response(200, json={"accessToken": "TOKEN", "expireIn": 7200})
        deliveries.append(request)
        return httpx.Response(status, json={"message": "synthetic-provider-rejection"})

    async with delivery_platform(delivery_store, handler) as (service, _):
        for _ in range(2):
            with pytest.raises(OpsMeshError) as rejected:
                await send(service)
            assert rejected.value.code == expected_code and not rejected.value.retryable
        record = await delivery_record(delivery_store)
        assert record.data["state"] == "rejected" and record.data["error_code"] == expected_code
        assert len(deliveries) == 1


@pytest.mark.parametrize("failure", ["connect", "rate_limit"])
async def test_safe_transient_failures_can_retry_the_same_payload(delivery_store, failure):
    deliveries = []

    def handler(request):
        if request.url.path.endswith("accessToken"):
            return httpx.Response(200, json={"accessToken": "TOKEN", "expireIn": 7200})
        deliveries.append(request)
        if len(deliveries) == 1:
            if failure == "connect":
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"processQueryKey": "confirmed"})

    async with delivery_platform(delivery_store, handler) as (service, _):
        with pytest.raises(OpsMeshError) as first:
            await send(service)
        assert first.value.retryable
        record = await delivery_record(delivery_store)
        assert (
            record.data["state"] == "retryable_failed"
            and record.data["error_code"] == first.value.code
        )
        with pytest.raises(OpsMeshError) as changed:
            await send(service, message=OpsMeshMessage("different"))
        assert changed.value.code == "IDEMPOTENCY_CONFLICT" and len(deliveries) == 1
        assert (await send(service)).message_id == "confirmed"
        assert (await send(service)).duplicate and len(deliveries) == 2


async def test_read_timeout_and_unknown_retryable_error_do_not_replay(delivery_store, monkeypatch):
    deliveries = []

    def handler(request):
        if request.url.path.endswith("accessToken"):
            return httpx.Response(200, json={"accessToken": "TOKEN", "expireIn": 7200})
        deliveries.append(request)
        raise httpx.ReadTimeout("receipt lost", request=request)

    async with delivery_platform(delivery_store, handler) as (service, adapter):
        for _ in range(2):
            with pytest.raises(OpsMeshError) as uncertain:
                await send(service)
            assert uncertain.value.code == "DELIVERY_UNKNOWN" and not uncertain.value.retryable
        assert (await delivery_record(delivery_store)).data["state"] == "unknown"
        assert len(deliveries) == 1
        bot = adapter.get_bot(await service.get_account("support", actor_id="operator"))
        opaque_calls = []

        async def opaque_failure(target, message):
            opaque_calls.append(message)
            raise OpsMeshError(
                "OPAQUE_PROVIDER_FAILURE", "Effect may have happened", retryable=True
            )

        monkeypatch.setattr(bot, "send", opaque_failure)
        with pytest.raises(OpsMeshError):
            await send(service, key="opaque")
        with pytest.raises(OpsMeshError) as opaque_repeat:
            await send(service, key="opaque")
        assert opaque_repeat.value.code == "DELIVERY_UNKNOWN" and len(opaque_calls) == 1
