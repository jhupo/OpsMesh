"""Verify schedule ownership, recovery, and session locks on PostgreSQL."""

import importlib
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        not os.environ.get("OPSMESH_TEST_DSN"),
        reason="An isolated PostgreSQL OPSMESH_TEST_DSN is required",
    ),
]


@pytest.fixture
async def postgres_cron(monkeypatch):
    from opsmesh.config import Config, Env
    from opsmesh.drivers import Driver, base
    from opsmesh.matchers import matchers
    from opsmesh.plugins import registry
    from opsmesh.plugins.model import Plugin, PluginStatus

    saved_matchers = dict(matchers.items())
    monkeypatch.setattr(base, "_driver", Driver(Env(), Config()))
    database_api = importlib.import_module("opsmesh.plugins.database")
    database_plugin = Plugin(
        "database", database_api, database_api.__name__, None
    )
    monkeypatch.setattr(
        registry, "_plugins", {**registry._plugins, "database": database_plugin}
    )
    api = importlib.import_module("opsmesh.plugins.cron")
    declaration_module = importlib.import_module(
        "opsmesh.plugins.cron.declarations"
    )
    scheduler_module = importlib.import_module("opsmesh.plugins.cron.scheduler")
    models = importlib.import_module("opsmesh.plugins.cron.models")
    monkeypatch.setattr(declaration_module, "declarations", {})
    monkeypatch.setattr(
        scheduler_module, "declarations", declaration_module.declarations
    )
    consumer = Plugin(
        "cron_probe", ModuleType("cron_probe"), "cron_probe", None
    )
    consumer.status = PluginStatus.READY
    monkeypatch.setattr(
        registry, "_plugins", {**registry._plugins, consumer.id_: consumer}
    )
    database = database_api.Database(
        database_api.Settings(dsn=os.environ["OPSMESH_TEST_DSN"])
    )
    await database.startup()
    engine = database.engine
    schema = "cron_test_" + uuid4().hex
    runtimes = []
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        isolated_engine = engine.execution_options(
            schema_translate_map={None: schema}
        )
        database._engine = isolated_engine
        database._sessionmaker = async_sessionmaker(
            isolated_engine, expire_on_commit=False
        )
        async with isolated_engine.begin() as connection:
            await connection.run_sync(models.ScheduleRecord.__table__.create)

        def scheduler(namespace=None):
            runtime = scheduler_module.Scheduler(
                database, api.Settings(namespace=namespace or schema)
            )
            runtimes.append(runtime)
            return runtime

        with registry.plugin_scope(consumer):
            yield SimpleNamespace(
                api=api,
                scheduler=scheduler,
                declarations=declaration_module.declarations,
                owner=consumer.id_,
                namespace=schema,
            )
    finally:
        for runtime in reversed(runtimes):
            await runtime.shutdown()
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await database.shutdown()
        matchers.clear()
        matchers.update(saved_matchers)


async def test_namespace_lock_is_exclusive_and_released(postgres_cron):
    cron = postgres_cron
    first = cron.scheduler()
    second = cron.scheduler()
    different = cron.scheduler(cron.namespace + "_other")
    await first.startup()
    with pytest.raises(cron.api.SchedulerUnavailable, match="active scheduler"):
        await second.startup()
    await different.startup()
    await first.shutdown()
    await second.startup()
    assert second.available
    assert different.available


async def test_code_overrides_survive_restart(postgres_cron):
    cron = postgres_cron
    cron.api.on_interval(3600, name="digest")
    first = cron.scheduler()
    await first.startup()
    await first.ready()
    record = (await first.schedules.list(cron.owner))[0]
    definition = cron.api.ScheduleDefinition.model_validate(record.definition)
    modified = definition.model_copy(
        update={
            "trigger": cron.api.IntervalDefinition(
                seconds=7200, start_at=definition.trigger.start_at
            )
        }
    )
    await first.schedules.update(cron.owner, record.id, modified)
    await first.schedules.set_enabled(cron.owner, record.id, False)
    declaration = cron.declarations[(cron.owner, "digest")]
    cron.declarations[(cron.owner, "digest")] = replace(
        declaration,
        default=modified.model_copy(update={"enabled": True}),
    )
    await first.shutdown()

    restored = cron.scheduler()
    await restored.startup()
    await restored.ready()
    records = await restored.schedules.list(cron.owner)
    view = restored.view(records[0])
    assert len(records) == 1
    assert view.definition.trigger.seconds == 7200
    assert not view.definition.enabled
    assert view.blocked_reason == "disabled"
    with pytest.raises(cron.api.ScheduleError, match="fixed"):
        await restored.schedules.update(
            cron.owner,
            record.id,
            modified.model_copy(update={"name": "renamed"}),
        )


async def test_expired_date_is_not_replayed(postgres_cron):
    cron = postgres_cron
    cron.api.on_date(datetime.now(UTC) - timedelta(hours=1), name="expired")
    runtime = cron.scheduler()
    await runtime.startup()
    await runtime.ready()
    record = (await runtime.schedules.list(cron.owner))[0]
    assert record.expired
    assert runtime.native.get_job(record.id) is None


async def test_records_are_scoped_and_stale_revision_cannot_fire(postgres_cron):
    cron = postgres_cron
    calls = []
    matcher = cron.api.on_schedule("digest")

    @matcher.handle()
    async def digest():
        calls.append(True)

    runtime = cron.scheduler()
    await runtime.startup()
    record = await runtime.schedules.add(
        cron.owner,
        cron.api.ScheduleDefinition(
            name="online",
            handler="digest",
            trigger=cron.api.IntervalDefinition(
                seconds=3600, start_at=datetime.now(UTC)
            ),
        ),
    )
    with pytest.raises(cron.api.ScheduleNotFound):
        await runtime.schedules.get("different_plugin", record.id)
    with pytest.raises(cron.api.ScheduleNotFound):
        await runtime.schedules.set_enabled("different_plugin", record.id, True)
    await runtime.reconcile()
    job = runtime.native.get_job(record.id)
    await runtime.schedules.set_enabled(cron.owner, record.id, False)
    await job.func(*job.args)
    assert not calls
