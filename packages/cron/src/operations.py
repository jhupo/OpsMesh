"""Caller-scoped schedule management over the plugin's persistent store."""

import logging

from opsmesh import get_current_plugin, plugin_call

from .declarations import declarations
from .errors import ScheduleError
from .lifecycle import get_scheduler
from .scheduler import Scheduler
from .schemas import (
    ScheduleDefinition,
    ScheduleView,
)

logger = logging.getLogger(__name__)


def _owner() -> str:
    plugin = get_current_plugin()
    if plugin is None:
        raise ScheduleError("Schedule operations require a calling plugin")
    return plugin.id_


def _validate(owner: str, definition: ScheduleDefinition) -> ScheduleDefinition:
    declaration = declarations.get((owner, definition.handler))
    if declaration is None:
        raise ScheduleError(
            "The schedule handler is not declared by this plugin"
        )
    declaration.validate_payload(definition.payload)
    definition.trigger.build()
    return definition


async def add_schedule(definition: ScheduleDefinition) -> ScheduleView:
    """Persist a dynamic schedule referencing this plugin's handler."""
    owner = _owner()
    definition = _validate(owner, definition)
    async with plugin_call(owner):
        scheduler = get_scheduler()
        record = await scheduler.schedules.add(owner, definition)
        await _synchronize(scheduler)
        return scheduler.view(record)


async def list_schedules(
    *, limit: int = 50, offset: int = 0
) -> list[ScheduleView]:
    """List only the calling plugin's schedules."""
    owner = _owner()
    async with plugin_call(owner):
        scheduler = get_scheduler()
        return [
            scheduler.view(record)
            for record in await scheduler.schedules.list(
                owner, limit=limit, offset=offset
            )
        ]


async def get_schedule(schedule_id: str) -> ScheduleView:
    """Read one schedule in the calling plugin's scope."""
    owner = _owner()
    async with plugin_call(owner):
        scheduler = get_scheduler()
        return scheduler.view(await scheduler.schedules.get(owner, schedule_id))


async def update_schedule(
    schedule_id: str, definition: ScheduleDefinition
) -> ScheduleView:
    """Commit a replacement and report pending synchronization explicitly."""
    owner = _owner()
    definition = _validate(owner, definition)
    async with plugin_call(owner):
        scheduler = get_scheduler()
        record = await scheduler.schedules.update(
            owner, schedule_id, definition
        )
        await _synchronize(scheduler)
        return scheduler.view(record)


async def _synchronize(scheduler: Scheduler) -> None:
    try:
        await scheduler.reconcile()
    except Exception:
        logger.exception(
            "Schedule persisted; runtime synchronization is pending"
        )


async def _set_enabled(schedule_id: str, enabled: bool) -> ScheduleView:
    owner = _owner()
    async with plugin_call(owner):
        scheduler = get_scheduler()
        record = await scheduler.schedules.set_enabled(
            owner, schedule_id, enabled
        )
        await _synchronize(scheduler)
        return scheduler.view(record)


async def pause_schedule(schedule_id: str) -> ScheduleView:
    """Persist disabled intent without cancelling an admitted invocation."""
    return await _set_enabled(schedule_id, False)


async def resume_schedule(schedule_id: str) -> ScheduleView:
    """Resume future firings of this plugin's schedule."""
    return await _set_enabled(schedule_id, True)


async def delete_schedule(schedule_id: str) -> None:
    """Delete a dynamic definition; code declarations must be paused."""
    owner = _owner()
    async with plugin_call(owner):
        scheduler = get_scheduler()
        await scheduler.schedules.delete(owner, schedule_id)
        await _synchronize(scheduler)


async def reset_schedule(schedule_id: str) -> ScheduleView:
    """Explicitly restore a code declaration's current defaults."""
    view = await get_schedule(schedule_id)
    declaration = declarations.get((_owner(), view.name))
    if (
        view.source != "code"
        or declaration is None
        or declaration.default is None
    ):
        raise ScheduleError("Only an available code declaration can be reset")
    return await update_schedule(schedule_id, declaration.default)
