"""Exercise native trigger semantics and owned framework timer dispatch."""

import asyncio
import importlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError


@pytest.fixture
def cron(monkeypatch):
    from opsmesh.config import Config, Env
    from opsmesh.drivers import Driver, base
    from opsmesh.matchers import matchers
    from opsmesh.plugins import registry
    from opsmesh.plugins.model import Plugin, PluginStatus

    saved_matchers = dict(matchers.items())
    monkeypatch.setattr(base, "_driver", Driver(Env(), Config()))
    database = importlib.import_module("opsmesh.plugins.database")
    database_plugin = Plugin("database", database, database.__name__, None)
    monkeypatch.setattr(
        registry, "_plugins", {**registry._plugins, "database": database_plugin}
    )
    module = importlib.import_module("opsmesh.plugins.cron")
    declaration_module = importlib.import_module(
        "opsmesh.plugins.cron.declarations"
    )
    monkeypatch.setattr(declaration_module, "declarations", {})
    scheduler = importlib.import_module("opsmesh.plugins.cron.scheduler")
    monkeypatch.setattr(
        scheduler, "declarations", declaration_module.declarations
    )
    operations = importlib.import_module("opsmesh.plugins.cron.operations")
    monkeypatch.setattr(
        operations, "declarations", declaration_module.declarations
    )
    consumer = Plugin(
        "timer_consumer", ModuleType("timer_consumer"), "timer_consumer", None
    )
    consumer.status = PluginStatus.READY
    monkeypatch.setattr(
        registry, "_plugins", {**registry._plugins, consumer.id_: consumer}
    )
    with registry.plugin_scope(consumer):
        yield SimpleNamespace(
            api=module,
            declarations=declaration_module.declarations,
            declaration_module=declaration_module,
            scheduler=scheduler,
            owner=consumer,
        )
    matchers.clear()
    matchers.update(saved_matchers)


def test_cron_uses_native_weekday_and_timezone(cron):
    trigger = cron.api.CronDefinition(
        expression="0 9 * * mon", timezone="Asia/Shanghai"
    ).build()
    next_fire = trigger.get_next_fire_time(
        None, datetime(2026, 11, 13, tzinfo=UTC)
    )
    assert next_fire.isoformat() == "2026-11-16T09:00:00+08:00"


def test_interval_restart_preserves_anchor(cron):
    anchor = datetime(2026, 11, 13, tzinfo=UTC)
    trigger = cron.api.IntervalDefinition(seconds=60, start_at=anchor).build()
    assert trigger.get_next_fire_time(
        None, anchor + timedelta(seconds=95)
    ) == anchor + timedelta(seconds=120)


def test_naive_date_is_rejected(cron):
    with pytest.raises(ValidationError):
        cron.api.DateDefinition(run_at=datetime(2026, 11, 13))


def test_invalid_trigger_does_not_register_handler(cron):
    with pytest.raises(ValueError):
        cron.api.on_cron("not a cron", name="invalid")
    assert not cron.declarations


def test_typed_payload_is_validated_before_scheduling(cron):
    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")
        count: int

    cron.api.on_schedule("typed", payload=Payload)
    declaration = cron.declarations[(cron.owner.id_, "typed")]
    assert declaration.validate_payload({"count": 2}) == {"count": 2}
    with pytest.raises(ValidationError):
        declaration.validate_payload({"count": 2, "owner": "other"})


def test_duplicate_declaration_is_rejected(cron):
    cron.api.on_schedule("digest")
    with pytest.raises(cron.api.ScheduleError, match="already declared"):
        cron.api.on_schedule("digest")


def test_code_defaults_use_plugin_timezone_and_limits(cron, monkeypatch):
    settings = cron.api.Settings(
        timezone="Asia/Shanghai",
        timeout_seconds=25,
        misfire_grace_seconds=7,
    )
    monkeypatch.setattr(
        cron.declaration_module, "get_plugin_config", lambda model: settings
    )
    cron.api.on_cron("0 9 * * mon", name="digest")
    definition = cron.declarations[(cron.owner.id_, "digest")].default
    assert definition.trigger.timezone == "Asia/Shanghai"
    assert definition.timeout_seconds == 25
    assert definition.misfire_grace_seconds == 7


def test_dynamic_defaults_use_the_same_plugin_limits(cron):
    definition = cron.api.ScheduleDefinition(
        name="digest",
        handler="digest",
        trigger=cron.api.IntervalDefinition(
            seconds=60, start_at=datetime.now(UTC)
        ),
    )
    settings = cron.api.Settings(
        timeout_seconds=25, misfire_grace_seconds=7
    )
    resolved = cron.scheduler.resolve_schedule_definition(
        definition, settings
    )
    assert resolved.timeout_seconds == 25
    assert resolved.misfire_grace_seconds == 7


def test_invalid_handler_name_is_rejected(cron):
    with pytest.raises(ValidationError):
        cron.api.on_schedule("arbitrary/python:path")
    assert not cron.declarations


def record_for(cron, *, trigger=None):
    definition = cron.api.ScheduleDefinition(
        name="online-name",
        handler="digest",
        trigger=trigger
        or cron.api.IntervalDefinition(seconds=60, start_at=datetime.now(UTC)),
    )
    return SimpleNamespace(
        id="schedule",
        owner_plugin=cron.owner.id_,
        name=definition.name,
        source="dynamic",
        definition=definition.model_dump(mode="json"),
        revision="current",
        completed=False,
        expired=False,
    )


def make_scheduler(cron, record):
    settings = cron.api.Settings()
    runtime = cron.scheduler.Scheduler(SimpleNamespace(), settings)
    runtime._healthy = True
    runtime._check_lock = AsyncMock()
    runtime.channel = cron.scheduler.ScheduleChannel(
        cron.scheduler.ScheduleAdapter(
            importlib.import_module("opsmesh.drivers").get_driver()
        ),
        "test-cron",
    )
    runtime.schedules = SimpleNamespace(
        get=AsyncMock(return_value=record), complete=AsyncMock()
    )
    return runtime


@pytest.mark.anyio
async def test_dynamic_name_dispatches_only_referenced_matcher(cron):
    calls = []
    matcher = cron.api.on_schedule("digest")
    other = cron.api.on_schedule("other")

    @matcher.handle()
    async def digest(event: cron.api.ScheduledEvent):
        calls.append(event.name)

    @other.handle()
    async def unrelated():
        raise AssertionError("Unrelated matcher was invoked")

    runtime = make_scheduler(cron, record_for(cron))
    await runtime._fire(
        cron.scheduler._TimerBinding(
            "schedule", "current", cron.owner, cron.owner.generation
        )
    )
    assert calls == ["online-name"]
    assert not runtime._active


@pytest.mark.anyio
async def test_stale_revision_cannot_fire(cron):
    calls = []
    matcher = cron.api.on_schedule("digest")

    @matcher.handle()
    async def digest():
        calls.append(True)

    runtime = make_scheduler(cron, record_for(cron))
    await runtime._fire(
        cron.scheduler._TimerBinding(
            "schedule", "stale", cron.owner, cron.owner.generation
        )
    )
    assert calls == []


@pytest.mark.anyio
async def test_dispatch_failure_is_not_reported_as_success(cron):
    matcher = cron.api.on_schedule("digest")

    @matcher.handle()
    async def digest():
        raise RuntimeError("failure")

    runtime = make_scheduler(cron, record_for(cron))
    with pytest.raises(ExceptionGroup):
        await runtime._fire(
            cron.scheduler._TimerBinding(
                "schedule", "current", cron.owner, cron.owner.generation
            )
        )


@pytest.mark.anyio
async def test_single_fire_completed_even_if_handler_fails(cron):
    matcher = cron.api.on_schedule("digest")

    @matcher.handle()
    async def digest():
        raise RuntimeError("failure")

    record = record_for(
        cron,
        trigger=cron.api.DateDefinition(
            run_at=datetime.now(UTC) + timedelta(seconds=1)
        ),
    )
    runtime = make_scheduler(cron, record)
    with pytest.raises(ExceptionGroup):
        await runtime._fire(
            cron.scheduler._TimerBinding(
                "schedule", "current", cron.owner, cron.owner.generation
            )
        )
    runtime.schedules.complete.assert_awaited_once_with("schedule", "current")


@pytest.mark.anyio
async def test_lock_loss_disables_scheduler(cron):
    runtime = cron.scheduler.Scheduler(SimpleNamespace(), cron.api.Settings())
    runtime._healthy = True
    runtime._lock = SimpleNamespace(
        scalar=AsyncMock(side_effect=OSError("lost"))
    )
    runtime.native.start(paused=True)
    try:
        with pytest.raises(OSError):
            await runtime.reconcile()
        assert not runtime.available
        assert runtime.native.state == 2
    finally:
        runtime.native.shutdown()


@pytest.mark.anyio
async def test_invalid_record_does_not_block_valid_timers(cron):
    cron.api.on_schedule("digest")
    valid = record_for(cron)
    invalid = SimpleNamespace(**vars(valid))
    invalid.id = "invalid"
    invalid.definition = {"name": "invalid"}
    runtime = make_scheduler(cron, valid)
    runtime.schedules.all = AsyncMock(return_value=[invalid, valid])
    runtime.schedules.initialize = AsyncMock()
    runtime.native.start(paused=True)
    try:
        await runtime.reconcile()
        assert runtime._blocked[invalid.id] == "invalid definition"
        assert runtime.native.get_job(valid.id) is not None
    finally:
        runtime.native.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("restart_during_lookup", [False, True])
async def test_old_timer_cannot_enter_a_restarted_plugin(
    cron, restart_during_lookup
):
    calls = []
    matcher = cron.api.on_schedule("digest")

    @matcher.handle()
    async def digest():
        calls.append(cron.owner.generation)

    record = record_for(cron)
    runtime = make_scheduler(cron, record)
    runtime.schedules.all = AsyncMock(return_value=[record])
    runtime.schedules.initialize = AsyncMock()
    runtime.native.start(paused=True)
    try:
        await runtime.reconcile()
        old_job = runtime.native.get_job(record.id)
        if restart_during_lookup:

            async def lookup(owner, identifier):
                cron.owner.generation += 1
                return record

            runtime.schedules.get.side_effect = lookup
        else:
            cron.owner.generation += 1
        await old_job.func(*old_job.args)
        assert calls == []
        assert not runtime.view(record).runnable
        runtime.schedules.get.side_effect = None
        await runtime.reconcile()
        new_job = runtime.native.get_job(record.id)
        await new_job.func(*new_job.args)
        assert calls == [cron.owner.generation]
    finally:
        await runtime.shutdown()


@pytest.mark.anyio
async def test_old_timer_cannot_enter_a_reloaded_plugin(cron, monkeypatch):
    from opsmesh.plugins import registry
    from opsmesh.plugins.model import Plugin, PluginStatus

    cron.api.on_schedule("digest")
    record = record_for(cron)
    runtime = make_scheduler(cron, record)
    runtime.schedules.all = AsyncMock(return_value=[record])
    runtime.schedules.initialize = AsyncMock()
    runtime.native.start(paused=True)
    try:
        await runtime.reconcile()
        old_job = runtime.native.get_job(record.id)
        replacement = Plugin(
            cron.owner.name, cron.owner.module, cron.owner.module_name, None
        )
        replacement.status = PluginStatus.READY
        replacement.generation = cron.owner.generation
        monkeypatch.setitem(registry._plugins, cron.owner.id_, replacement)
        await old_job.func(*old_job.args)
        runtime.schedules.get.assert_not_awaited()
        assert not runtime.view(record).runnable
    finally:
        await runtime.shutdown()


@pytest.mark.anyio
async def test_invalid_definition_remains_queryable(cron, monkeypatch):
    lifecycle = importlib.import_module("opsmesh.plugins.cron.lifecycle")
    cron.api.on_schedule("digest")
    valid = record_for(cron)
    invalid = SimpleNamespace(
        **{**vars(valid), "id": "invalid", "definition": {"name": "old"}}
    )
    records = {record.id: record for record in (invalid, valid)}
    runtime = make_scheduler(cron, valid)
    runtime.schedules.get.side_effect = lambda owner, key: records[key]
    runtime.schedules.list = AsyncMock(return_value=list(records.values()))
    runtime.schedules.all = AsyncMock(return_value=list(records.values()))
    runtime.schedules.initialize = AsyncMock()
    monkeypatch.setattr(lifecycle, "_scheduler", runtime)
    runtime.native.start(paused=True)
    try:
        await runtime.reconcile()
        views = await cron.api.list_schedules()
        assert len(views) == 2
        blocked = await cron.api.get_schedule(invalid.id)
        assert blocked.id == invalid.id and blocked.name == invalid.name
        assert blocked.definition is None
        assert blocked.blocked_reason == "invalid definition"
        assert not blocked.runnable
        assert views[1].definition is not None and views[1].runnable
    finally:
        await runtime.shutdown()


@pytest.mark.anyio
async def test_invalid_code_definition_can_be_reset_by_its_fixed_name(
    cron, monkeypatch
):
    lifecycle = importlib.import_module("opsmesh.plugins.cron.lifecycle")
    cron.api.on_interval(60, name="digest")
    record = record_for(cron)
    record.name = "digest"
    record.source = "code"
    record.definition = {"name": "obsolete"}
    runtime = make_scheduler(cron, record)
    default = cron.declarations[(cron.owner.id_, "digest")].default

    @asynccontextmanager
    async def session():
        yield SimpleNamespace(scalar=AsyncMock(return_value=record))

    store = importlib.import_module("opsmesh.plugins.cron.schedules")
    runtime.schedules = store.Schedules(
        SimpleNamespace(session=session, transaction=session), runtime.settings
    )
    runtime.reconcile = AsyncMock()
    monkeypatch.setattr(lifecycle, "_scheduler", runtime)
    result = await cron.api.reset_schedule(record.id)
    assert result.definition == default


@pytest.mark.anyio
async def test_shutdown_waits_for_lock_connection_use(cron):
    runtime = make_scheduler(cron, record_for(cron))
    connection = SimpleNamespace(
        invalidated=False,
        execute=AsyncMock(),
        commit=AsyncMock(),
        invalidate=AsyncMock(),
        close=AsyncMock(),
    )
    runtime._lock = connection
    async with runtime._sync_lock:
        closing = asyncio.create_task(runtime.shutdown())
        await asyncio.sleep(0)
        assert not closing.done()
        connection.close.assert_not_awaited()
    await closing
    connection.close.assert_awaited_once()
    assert runtime._lock is None


def test_scheduled_event_has_no_fabricated_user_or_reply(cron):
    event = cron.api.ScheduledEvent(
        schedule_id="id",
        invocation_id="call",
        owner_plugin=cron.owner.id_,
        name="digest",
        trigger_type="cron",
        fired_at=datetime.now(UTC),
        payload={},
    )
    with pytest.raises(ValueError, match="human user"):
        event.get_user_id()
    with pytest.raises(ValueError, match="conversation"):
        event.get_session_id()
