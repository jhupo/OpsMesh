"""Keep incompatible persisted schedules from blocking timer recovery."""

import importlib
from datetime import UTC, datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def cron_runtime(monkeypatch):
    from opsmesh.config import Config, Env
    from opsmesh.drivers import Driver, base
    from opsmesh.matchers import matchers
    from opsmesh.plugins import registry
    from opsmesh.plugins.model import Plugin, PluginStatus

    saved_matchers = dict(matchers.items())
    monkeypatch.setattr(base, "_driver", Driver(Env(), Config()))
    database_module = importlib.import_module("opsmesh.plugins.database")
    database = Plugin(
        "database", database_module, database_module.__name__, None
    )
    monkeypatch.setattr(
        registry, "_plugins", {**registry._plugins, database.id_: database}
    )
    cron = importlib.import_module("opsmesh.plugins.cron")
    declaration_module = importlib.import_module(
        "opsmesh.plugins.cron.declarations"
    )
    scheduler = importlib.import_module("opsmesh.plugins.cron.scheduler")
    monkeypatch.setattr(declaration_module, "declarations", {})
    monkeypatch.setattr(
        scheduler, "declarations", declaration_module.declarations
    )
    owner = Plugin("recovery", ModuleType("recovery"), "recovery", None)
    owner.status = PluginStatus.READY
    monkeypatch.setattr(
        registry, "_plugins", {**registry._plugins, owner.id_: owner}
    )
    runtime = scheduler.Scheduler(SimpleNamespace(), cron.Settings())
    runtime._healthy = True
    runtime._check_lock = AsyncMock()
    runtime.schedules = SimpleNamespace(initialize=AsyncMock(), all=AsyncMock())
    try:
        with registry.plugin_scope(owner):
            yield cron, owner, runtime
    finally:
        matchers.clear()
        matchers.update(saved_matchers)


@pytest.mark.anyio
async def test_removed_payload_type_does_not_block_other_persisted_schedules(
    cron_runtime,
):
    cron, owner, runtime = cron_runtime
    cron.on_schedule("digest")
    definition = cron.ScheduleDefinition(
        name="digest",
        handler="digest",
        trigger=cron.IntervalDefinition(seconds=60, start_at=datetime.now(UTC)),
    )
    valid = SimpleNamespace(
        id="valid",
        owner_plugin=owner.id_,
        name="digest",
        definition=definition.model_dump(mode="json"),
        revision="current",
        completed=False,
        expired=False,
    )
    old_payload = SimpleNamespace(
        **{
            **vars(valid),
            "id": "old-payload",
            "definition": {**valid.definition, "payload": {"count": 2}},
        }
    )
    runtime.schedules.all.return_value = [old_payload, valid]
    runtime.native.start(paused=True)
    try:
        await runtime.reconcile()
        assert runtime._blocked[old_payload.id] == "invalid definition"
        assert runtime.native.get_job(old_payload.id) is None
        assert runtime.native.get_job(valid.id) is not None
    finally:
        await runtime.shutdown()
