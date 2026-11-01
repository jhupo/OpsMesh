"""A third-party bot protocol uses platform behavior without a provider-specific branch."""

import json
from contextlib import asynccontextmanager

import httpx
import pytest
from pydantic import Field, ValidationError

from opsmesh.framework.adapters import (
    BotAdapterConnectionStatus,
    BotAdapterDescriptor,
    BotAdapterRegistry,
    BotInboundEvent,
    BotMessageEvent,
    BotRef,
    BotWebhookRequest,
    BotWebhookResponse,
    BotWebhookResult,
    OpsMeshBot,
    OpsMeshBotAdapter,
    OpsMeshMessage,
)
from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.message import DispatchResult
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.accounts.api import UserPrincipal
from opsmesh.modules.bots.api import BotAccount
from opsmesh.modules.bots.internal.service import BotService


class CustomProtocolSettings(OpsMeshContract):
    label: str = Field(min_length=1)


class CustomProtocolBot(OpsMeshBot):
    def __init__(self, adapter, configuration):
        super().__init__(adapter, configuration.self_id)
        self.configuration = configuration

    @property
    def ref(self):
        return BotRef(adapter_id=self.adapter.name, bot_id=self.configuration.id)

    async def send(self, target, message):
        self.adapter.deliveries.append((target, message))
        return f"custom-delivery-{len(self.adapter.deliveries)}"

    async def call_api(self, api, **data):
        raise OpsMeshError("UNSUPPORTED_FEATURE", "No extra API in the test protocol")


class CustomProtocolAdapter(OpsMeshBotAdapter):
    name = "custom_test"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configured = []
        self.deliveries = []

    def describe(self):
        return BotAdapterDescriptor(
            name=self.name,
            display_name="Third-party test protocol",
            capabilities=frozenset({"send_text", "signed_events"}),
            settings_schema=CustomProtocolSettings.model_json_schema(),
            credential_names=frozenset({"signing_key", "secondary_key"}),
        )

    def validate_configuration(self, configuration):
        try:
            CustomProtocolSettings.model_validate(configuration.settings)
        except ValidationError as error:
            raise OpsMeshError("CONFIG_INVALID", "Invalid custom protocol settings") from error
        if configuration.adapter != self.name or set(configuration.credentials) != {
            "signing_key",
            "secondary_key",
        }:
            raise OpsMeshError("CONFIG_INVALID", "Invalid custom protocol credential references")

    def get_bot(self, configuration):
        current = self.bots.get(configuration.id)
        if current is None:
            current = CustomProtocolBot(self, configuration)
            self.connect(current)
        return current

    def configure(self, configuration):
        self.validate_configuration(configuration)
        self.configured.append(configuration)
        if configuration.enabled:
            self.get_bot(configuration)

    async def disconnect_account(self, account_id):
        self.bots.pop(account_id, None)

    def connection_status(self, configuration):
        return BotAdapterConnectionStatus(transport="custom_http", state="configured")

    async def receive_http(self, configuration, request):
        expected = self.resolve_secret(configuration.credentials["signing_key"])
        if request.header("X-Test-Signature") != expected:
            raise OpsMeshError("UNAUTHORIZED", "Invalid custom protocol signature")
        payload = json.loads(request.body)
        event = BotMessageEvent(
            id=f"custom:{configuration.id}:{payload['message_id']}",
            source=f"custom:{configuration.id}",
            message=OpsMeshMessage(payload["text"]),
            message_id=payload["message_id"],
            user_id=payload["user_id"],
            channel_id=payload["channel_id"],
            session_id=f"custom:{configuration.id}:{payload['channel_id']}",
        )
        return BotWebhookResult(
            incoming=BotInboundEvent(event=event, payload=payload),
            response=BotWebhookResponse(body={"accepted": True}),
        )

    async def close(self):
        self.bots.clear()


def account(**changes):
    return BotAccount(
        **{
            "id": "support",
            "adapter": "custom_test",
            "self_id": "remote-bot",
            "settings": {"label": "first"},
            "credentials": {
                "signing_key": "env:FIRST_SECRET",
                "secondary_key": "env:SECOND_SECRET",
            },
            **changes,
        }
    )


def inbound():
    return BotWebhookRequest(
        json.dumps(
            {
                "message_id": "message-1",
                "user_id": "external-user",
                "channel_id": "room:1",
                "text": "hello",
            }
        ).encode(),
        {"X-Test-Signature": "synthetic-secret"},
    )


@pytest.fixture
async def registry_store(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'registry.sqlite').as_posix()}")
    store = VersionedRecordStore(database, "test_registry_bots")
    await database.initialize()
    try:
        yield store
    finally:
        await database.close()


@asynccontextmanager
async def platform(store, *, deny_secret=None):
    contexts, authorized, resolved = [], [], []

    async def authorize(actor, action, resource):
        authorized.append((actor, action, resource))
        if action == "secret:use" and resource == deny_secret:
            raise OpsMeshError("FORBIDDEN", "Secret is not available to this actor")

    def resolve(reference):
        resolved.append(reference)
        return "synthetic-secret"

    async def principal(identifier):
        return UserPrincipal(id=identifier, username=identifier, is_admin=identifier == "admin")

    async def agent(reference):
        assert reference == "assistant@2"
        return AgentSpecification(id="assistant", name="Assistant", revision=2)

    async def dispatch(context):
        contexts.append(context)
        return DispatchResult(context.event.id, matched=1)

    async with httpx.AsyncClient() as client:
        driver = OpsMeshDriver()
        registry = BotAdapterRegistry(driver)
        adapter = CustomProtocolAdapter(driver, client, resolve)
        registry.register(adapter)
        service = BotService(
            store,
            resolve,
            authorize,
            dispatch,
            adapters=registry,
            agent_resolver=agent,
            principal_resolver=principal,
        )
        try:
            yield service, adapter, contexts, authorized, resolved
        finally:
            await service.close()


async def test_third_party_adapter_identity_routing_delivery_and_restart(registry_store):
    async with platform(registry_store) as (service, adapter, contexts, authorized, resolved):
        configuration = await service.create_account(account(), actor_id="admin")
        await service.bind_identity("support", "external-user", "user", actor_id="admin")
        await service.bind_route("support", "room:1", "assistant@2", actor_id="admin")
        response = await service.receive_webhook("support", inbound())
        assert response.receipt.status == "processed" and response.response.body == {
            "accepted": True
        }
        assert contexts[0].values == {
            "principal_id": "user",
            "bot_id": "support",
            "agent_revision": "assistant@2",
            "bot_dispatch_mode": "direct",
        }
        target = await service.resolve_delivery_target(
            "support", "room:1", actor_id="user", message_id="message-1", delivery_sequence=2
        )
        assert target.bot.adapter_id == "custom_test" and target.delivery_sequence == 2
        first = await service.send(
            target, OpsMeshMessage("reply"), idempotency_key="reply-1", actor_id="user"
        )
        assert first.message_id == "custom-delivery-1" and len(adapter.deliveries) == 1
        assert ("user", "bots.receive", "support") in authorized
        assert resolved[:2] == ["env:FIRST_SECRET", "env:SECOND_SECRET"]
        assert (
            await service.connection_status("support", actor_id="admin")
        ).transport == "custom_http"
    async with (
        platform(registry_store) as (restarted, adapter, contexts, _, _),
        restarted.driver.lifespan(),
    ):
        assert adapter.configured == [configuration]
        assert (await restarted.receive_webhook("support", inbound())).receipt.status == "duplicate"
        duplicate = await restarted.send(
            target, OpsMeshMessage("reply"), idempotency_key="reply-1", actor_id="user"
        )
        assert duplicate.duplicate and not adapter.deliveries and not contexts


async def test_unknown_adapter_and_invalid_settings_fail_before_persistence(registry_store):
    async with platform(registry_store) as (service, adapter, _, _, resolved):
        for configuration, code in [
            (account(adapter="uninstalled"), "BOT_ADAPTER_UNAVAILABLE"),
            (account(settings={"label": ""}), "CONFIG_INVALID"),
        ]:
            with pytest.raises(OpsMeshError) as caught:
                await service.create_account(configuration, actor_id="admin")
            assert caught.value.code == code
        assert not resolved and not adapter.configured
        assert await service.list_accounts(actor_id="admin") == []


async def test_all_adapter_credentials_are_authorized_before_any_secret_resolution(registry_store):
    async with platform(registry_store, deny_secret="env:SECOND_SECRET") as (
        service,
        adapter,
        _,
        authorized,
        resolved,
    ):
        with pytest.raises(OpsMeshError) as caught:
            await service.create_account(account(), actor_id="admin")
        assert caught.value.code == "FORBIDDEN" and not resolved and not adapter.configured
        assert [resource for _, action, resource in authorized if action == "secret:use"] == [
            "env:FIRST_SECRET",
            "env:SECOND_SECRET",
        ]
        assert await service.list_accounts(actor_id="admin") == []


@pytest.mark.parametrize("change", [{"adapter": "another"}, {"self_id": "another-bot"}])
async def test_bot_protocol_and_external_identity_cannot_be_replaced(registry_store, change):
    async with platform(registry_store) as (service, _, _, _, _):
        saved = await service.create_account(account(), actor_id="admin")
        with pytest.raises(OpsMeshError) as caught:
            await service.update_account(
                saved.model_copy(update=change), expected_revision=saved.revision, actor_id="admin"
            )
        assert caught.value.code == "INVALID_REQUEST"
        assert await service.get_account(saved.id, actor_id="admin") == saved


async def test_late_worker_events_are_fenced_by_configuration_revision(registry_store):
    async with platform(registry_store) as (service, adapter, contexts, _, _):
        original = await service.create_account(account(), actor_id="admin")
        await service.bind_identity("support", "external-user", "user", actor_id="admin")
        incoming = (await adapter.receive_http(original, inbound())).incoming
        updated = await service.update_account(
            original.model_copy(update={"settings": {"label": "new"}}),
            expected_revision=original.revision,
            actor_id="admin",
        )
        with pytest.raises(OpsMeshError) as caught:
            await service.receive_adapter_event(original, incoming)
        assert caught.value.code == "CONFLICT" and not contexts
        await service.receive_adapter_event(updated, incoming)
        assert len(contexts) == 1 and adapter.configured[-1].revision == updated.revision
