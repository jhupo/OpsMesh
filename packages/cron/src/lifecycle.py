"""Own scheduler resources using the framework plugin lifecycle."""

from opsmesh import (
    get_plugin_config,
    on_quiescing,
    on_shutdown,
    on_started,
    on_startup,
)
from opsmesh.plugins.database import get_database

from .config import Settings
from .errors import SchedulerUnavailable
from .scheduler import Scheduler

_scheduler: Scheduler | None = None


def get_scheduler() -> Scheduler:
    """Return the ready timer owner without constructing another scheduler."""
    if _scheduler is None or not _scheduler.available:
        raise SchedulerUnavailable("The cron plugin is not accepting work")
    return _scheduler


@on_startup
async def startup() -> None:
    """Acquire the namespace lock and prepare paused native timers."""
    global _scheduler
    if _scheduler is not None:
        raise SchedulerUnavailable(
            "The previous scheduler has unfinished cleanup"
        )
    scheduler = Scheduler(get_database(), get_plugin_config(Settings))
    _scheduler = scheduler
    await scheduler.startup()


@on_started
async def started() -> None:
    """Install timers after dependent handlers are ready."""
    await get_scheduler().ready()


@on_quiescing
def quiescing() -> None:
    """Stop new firings before framework call draining."""
    if _scheduler is not None:
        _scheduler.stop_accepting()


@on_shutdown
async def shutdown() -> None:
    """Close timers and release the dedicated lock connection."""
    global _scheduler
    if _scheduler is not None:
        await _scheduler.shutdown()
        _scheduler = None
