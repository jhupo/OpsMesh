"""Owning plugin policy gates managed protocol use and drains disabled connections."""

from types import ModuleType

import httpx
import pytest

from opsmesh.framework.adapters import (
    BotAdapterConnectionStatus,
    BotAdapterDescriptor,
    BotAdapterRegistry,
    BotConnectionConfiguration,
    BotInboundEvent,
    BotRef,
    BotWebhookRequest,
    OpsMeshBot,
    OpsMeshBotAdapter,
    OpsMeshEvent,
    OpsMeshMessage,
    ReplyTarget,
)
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.message import DispatchResult
from opsmesh.framework.plugin import OpsMeshPlugin, PluginManager, PluginState
from opsmesh.framework.plugin.manager import _current_plugin
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.bots.api import BotAccount
from opsmesh.modules.bots.internal.service import BotService


class PolicyTestAdapter(OpsMeshBotAdapter):
    name = "policy_test"

    def __init__(self, driver, client):
        super().__init__(driver, client, lambda reference: "unused")
        self.close_count = 0
        self.fail_close = False

    def describe(self):
        return BotAdapterDescriptor(
            name=self.name,
            display_name="Test",
            capabilities=frozenset(),
            settings_schema={},
            credential_names=frozenset(),
        )

    def validate_configuration(self, configuration):
        return None

    def get_bot(self, configuration) -> OpsMeshBot:
        raise AssertionError("This policy test must not create a protocol client")

    def configure(self, configuration):
        return None

    async def disconnect_account(self, account_id):
        return None

    def connection_status(self, configuration):
        return BotAdapterConnectionStatus(transport="test", state="configured")

    async def close(self):
        self.close_count += 1
        if self.fail_close:
            raise RuntimeError("Retry cleanup")


def register_owned(registry, adapter):
    owner = OpsMeshPlugin(
        name="custom_protocol",
        module_name="custom_protocol",
        module=ModuleType("custom_protocol"),
        state=PluginState.ENABLED,
    )
    manager = PluginManager()
    manager.plugins[owner.module_name] = owner
    token = _current_plugin.set(owner)
    try:
        registry.register(adapter)
    finally:
        _current_plugin.reset(token)
    return manager, owner


async def test_disabled_plugin_is_hidden_rejected_and_cannot_emit() -> None:
    driver = OpsMeshDriver()
    registry = BotAdapterRegistry(driver)
    consumed = []

    async def consume(configuration, incoming):
        consumed.append(incoming.event.id)

    async with httpx.AsyncClient() as client:
        adapter = PolicyTestAdapter(driver, client)
        manager, owner = register_owned(registry, adapter)
        configuration = BotConnectionConfiguration(id="test", adapter=adapter.name, self_id="test")
        incoming = BotInboundEvent(OpsMeshEvent(type="test", id="one"), {})
        registry.bind_receiver(consume)
        assert registry.get(adapter.name) is adapter
        await adapter.emit(configuration, incoming)
        manager.disable(owner.module_name)
        assert registry.values() == () and registry.describe() == ()
        assert registry.values(include_disabled=True) == (adapter,)
        with pytest.raises(OpsMeshError, match="disabled") as error:
            registry.get(adapter.name)
        assert error.value.code == "PLUGIN_DISABLED"
        with pytest.raises(OpsMeshError) as error:
            await adapter.emit(configuration, incoming)
        assert error.value.code == "PLUGIN_DISABLED" and consumed == ["one"]
        with pytest.raises(OpsMeshError):
            await adapter.dispatch(incoming.event)


async def test_synchronize_drains_once_then_reports_reenabled_adapter() -> None:
    driver = OpsMeshDriver()
    registry = BotAdapterRegistry(driver)
    async with httpx.AsyncClient() as client:
        adapter = PolicyTestAdapter(driver, client)
        manager, owner = register_owned(registry, adapter)
        manager.disable(owner.module_name)
        assert await registry.synchronize() == ()
        assert await registry.synchronize() == ()
        assert adapter.close_count == 1
        manager.enable(owner.module_name)
        assert await registry.synchronize() == (adapter.name,)
        assert await registry.synchronize() == (adapter.name,)
        registry.acknowledge_reconfigured((adapter.name,))
        assert await registry.synchronize() == ()
        assert registry.get(adapter.name) is adapter
        manager.disable(owner.module_name)
        assert await registry.synchronize() == ()
        assert adapter.close_count == 2
        await registry.close()
        assert adapter.close_count == 3


async def test_cleanup_failure_is_retried_and_does_not_reenable_adapter() -> None:
    driver = OpsMeshDriver()
    registry = BotAdapterRegistry(driver)
    async with httpx.AsyncClient() as client:
        adapter = PolicyTestAdapter(driver, client)
        manager, owner = register_owned(registry, adapter)
        manager.disable(owner.module_name)
        adapter.fail_close = True
        with pytest.raises(ExceptionGroup):
            await registry.synchronize()
        assert not adapter.enabled
        adapter.fail_close = False
        assert await registry.synchronize() == ()
        assert adapter.close_count == 2


async def test_failed_plugin_rollback_removes_registration_and_owner() -> None:
    driver = OpsMeshDriver()
    registry = BotAdapterRegistry(driver)
    async with httpx.AsyncClient() as client:
        adapter = PolicyTestAdapter(driver, client)
        _, owner = register_owned(registry, adapter)
        owner.state = PluginState.FAILED
        owner.rollback()
        assert driver.adapters == {}
        assert adapter._plugin_owner is None and not adapter.enabled
        with pytest.raises(OpsMeshError) as error:
            registry.get(adapter.name)
        assert error.value.code == "BOT_ADAPTER_UNAVAILABLE"
        with pytest.raises(OpsMeshError):
            adapter.ensure_enabled()


async def test_receiver_binding_includes_initially_disabled_adapter() -> None:
    driver = OpsMeshDriver()
    registry = BotAdapterRegistry(driver)
    consumed = []

    async def consume(configuration, incoming):
        consumed.append(incoming.event.id)

    async with httpx.AsyncClient() as client:
        adapter = PolicyTestAdapter(driver, client)
        manager, owner = register_owned(registry, adapter)
        manager.disable(owner.module_name)
        registry.bind_receiver(consume)
        await registry.synchronize()
        manager.enable(owner.module_name)
        assert await registry.synchronize() == (adapter.name,)
        await adapter.emit(
            BotConnectionConfiguration(id="test", adapter=adapter.name, self_id="test"),
            BotInboundEvent(OpsMeshEvent(type="test", id="reenabled"), {}),
        )
        assert consumed == ["reenabled"]


async def test_platform_rejects_disabled_provider_before_send_ingress_or_account_creation(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'policy.sqlite').as_posix()}")
    store = VersionedRecordStore(database, "policy_bot_records")
    await database.initialize()
    dispatched = []

    async def authorize(*args):
        return None

    async def dispatch(context):
        dispatched.append(context.event.id)
        return DispatchResult(context.event.id)

    async with httpx.AsyncClient() as client:
        driver = OpsMeshDriver()
        registry = BotAdapterRegistry(driver)
        adapter = PolicyTestAdapter(driver, client)
        manager, owner = register_owned(registry, adapter)
        service = BotService(
            store, lambda reference: "unused", authorize, dispatch, adapters=registry
        )
        try:
            account = BotAccount(id="test", adapter=adapter.name, self_id="test")
            await service.create_account(account, actor_id="admin")
            manager.disable(owner.module_name)
            with pytest.raises(OpsMeshError) as error:
                await service.send(
                    ReplyTarget(
                        bot=BotRef(adapter_id=adapter.name, bot_id="test"), channel_id="user"
                    ),
                    OpsMeshMessage("blocked"),
                    idempotency_key="attempt",
                    actor_id="admin",
                )
            assert error.value.code == "PLUGIN_DISABLED"
            with pytest.raises(OpsMeshError) as error:
                await service.receive_webhook("test", BotWebhookRequest(b"{}"))
            assert error.value.code == "PLUGIN_DISABLED"
            with pytest.raises(OpsMeshError) as error:
                await service.create_account(
                    account.model_copy(update={"id": "other"}), actor_id="admin"
                )
            assert error.value.code == "PLUGIN_DISABLED"
            assert dispatched == []
            records = await store.list()
            assert len([record for record in records if record.data["kind"] == "account"]) == 1
            delivery = [record for record in records if record.data["kind"] == "delivery"]
            assert all(record.data["state"] == "not_attempted" for record in delivery)
        finally:
            await service.close()
            await database.close()


async def test_reconfiguration_survives_other_adapter_cleanup_failure_and_missing_ack() -> None:
    driver = OpsMeshDriver()
    registry = BotAdapterRegistry(driver)
    async with httpx.AsyncClient() as client:
        first = PolicyTestAdapter(driver, client)
        second = PolicyTestAdapter(driver, client)
        second.name = "other_policy"
        first_manager, first_owner = register_owned(registry, first)
        second_manager, second_owner = register_owned(registry, second)
        first_manager.disable(first_owner.module_name)
        await registry.synchronize()
        first_manager.enable(first_owner.module_name)
        second_manager.disable(second_owner.module_name)
        second.fail_close = True
        with pytest.raises(ExceptionGroup):
            await registry.synchronize()
        second.fail_close = False
        assert await registry.synchronize() == (first.name,)
        assert await registry.synchronize() == (first.name,)
        registry.acknowledge_reconfigured((first.name,))
        assert await registry.synchronize() == ()


async def test_disabling_during_pending_reconfiguration_drains_again() -> None:
    driver = OpsMeshDriver()
    registry = BotAdapterRegistry(driver)
    async with httpx.AsyncClient() as client:
        adapter = PolicyTestAdapter(driver, client)
        manager, owner = register_owned(registry, adapter)
        manager.disable(owner.module_name)
        await registry.synchronize()
        manager.enable(owner.module_name)
        assert await registry.synchronize() == (adapter.name,)
        # Platform partially configured the adapter, then its policy changed before ACK.
        manager.disable(owner.module_name)
        registry.acknowledge_reconfigured((adapter.name,))
        assert await registry.synchronize() == ()
        assert adapter.close_count == 2
