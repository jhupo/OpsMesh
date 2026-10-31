"""Operational policy changes are scoped, versioned and reconciled across instances."""

import asyncio
from types import ModuleType

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.plugin.manager import PluginManager
from opsmesh.framework.plugin.model import OpsMeshPlugin, PluginState
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.accounts.internal.service import LocalAccountService
from opsmesh.modules.audit.internal.journal import PersistentAuditJournal
from opsmesh.modules.operations.outbox import OutboxManagement
from opsmesh.modules.operations.plugins import PluginDeploymentManagement


def plugin_manager(*names):
    manager = PluginManager()
    for name in names:
        manager.plugins[name] = OpsMeshPlugin(
            name=name, module_name=name, module=ModuleType(name), state=PluginState.ENABLED
        )
    return manager


@pytest.fixture
async def platform(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'operations.db').as_posix()}")
    accounts = LocalAccountService(database)
    audit = PersistentAuditJournal(database)
    first_manager = plugin_manager("base", "child")
    first_manager.plugins["child"].dependencies.add("base")
    second_manager = plugin_manager("base", "child", "remote")
    second_manager.plugins["child"].dependencies.add("base")
    first = PluginDeploymentManagement(database, accounts, audit, first_manager)
    second = PluginDeploymentManagement(database, accounts, audit, second_manager)
    outbox = OutboxManagement(database, accounts, audit)
    source = VersionedRecordStore(database, "event_source")
    await database.initialize()
    admin = await accounts.create_user("admin", "admin-password-123", is_admin=True)
    ordinary = await accounts.create_user("ordinary", "ordinary-password")
    try:
        yield first, second, outbox, source, admin, ordinary
    finally:
        await database.close()


async def test_plugin_policy_permissions_cascade_and_cross_instance_preservation(platform):
    first, second, _outbox, _source, admin, ordinary = platform
    for operation in (
        first.policy(actor_id=ordinary.id),
        first.inventory(actor_id=ordinary.id),
        first.instance_status(actor_id=ordinary.id),
        first.set_enabled("base", False, actor_id=ordinary.id, expected_revision=0),
    ):
        with pytest.raises(OpsMeshError) as denied:
            await operation
        assert denied.value.code == "FORBIDDEN"
    with pytest.raises(OpsMeshError):
        await first.set_enabled("base", False, actor_id=admin.id, expected_revision=0)
    saved = await second.set_enabled("remote", False, actor_id=admin.id, expected_revision=0)
    saved = await first.set_enabled(
        "base", False, actor_id=admin.id, expected_revision=saved.revision, cascade=True
    )
    assert saved.enabled == {"base": False, "child": False, "remote": False}
    await asyncio.gather(second.synchronize(force=True), second.synchronize(force=True))
    assert not any(plugin.enabled for plugin in second.manager.plugins.values())
    with pytest.raises(OpsMeshError) as stale:
        await second.set_enabled("base", True, actor_id=admin.id, expected_revision=1)
    assert stale.value.code == "CONFLICT"
    saved = await first.set_enabled(
        "base", True, actor_id=admin.id, expected_revision=saved.revision
    )
    await first.set_enabled("child", True, actor_id=admin.id, expected_revision=saved.revision)
    await second.synchronize(force=True)
    assert second.manager.plugins["base"].enabled and second.manager.plugins["child"].enabled
    assert not second.manager.plugins["remote"].enabled


async def test_missing_plugin_dependency_fails_closed_and_publishes_instance_error(platform):
    first, _second, _outbox, _source, admin, _ordinary = platform
    first.manager.plugins["child"].dependencies.add("not-deployed")
    await first.synchronize(force=True)
    assert not first.manager.plugins["child"].enabled
    status = (await first.instance_status(actor_id=admin.id))[0]
    assert status.errors["child"] == "DEPENDENCY_NOT_DEPLOYED"


async def test_outbox_inspection_redaction_and_single_explicit_requeue(platform):
    _first, _second, outbox, source, admin, ordinary = platform
    await source.create(
        "resource",
        {},
        events=[
            {"id": "recover-me", "type": "demo.failed", "payload": {"secret": "do-not-display"}}
        ],
    )
    events = await source.database.claim_events("worker", limit=100)
    for event in events:
        if event["id"] == "recover-me":
            await source.database.reject_event(event["id"], owner="worker", max_attempts=1)
        else:
            await source.database.acknowledge_event(event["id"], owner="worker")
    with pytest.raises(OpsMeshError):
        await outbox.list_events(actor_id=ordinary.id)
    view = await outbox.list_events(actor_id=admin.id)
    assert view[0]["id"] == "recover-me" and "do-not-display" not in str(view)
    with pytest.raises(OpsMeshError):
        await outbox.requeue("recover-me", actor_id=admin.id, reason=" ")
    results = await asyncio.gather(
        outbox.requeue("recover-me", actor_id=admin.id, reason="Reviewed delivery"),
        outbox.requeue("recover-me", actor_id=admin.id, reason="Reviewed delivery"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, OpsMeshError) for result in results) == 1
    assert await outbox.list_events(actor_id=admin.id) == []
    assert (await outbox.list_events(actor_id=admin.id, status="pending"))[0]["id"] == "recover-me"
