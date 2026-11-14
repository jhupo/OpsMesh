"""Adapt APScheduler timers to owned framework event invocations."""

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from opsmesh import get_driver, get_plugin, plugin_call
from opsmesh.dispatch import dispatch_event
from opsmesh.plugins.database import Database
from opsmesh.plugins.invocation import PluginUnavailable
from opsmesh.plugins.model import Plugin

from .config import Settings
from .declarations import ScheduleDeclaration, declarations
from .errors import ScheduleError, SchedulerUnavailable
from .events import ScheduleAdapter, ScheduleChannel, ScheduledEvent
from .models import ScheduleRecord
from .schedules import Schedules, namespace_lock
from .schemas import (
    DateDefinition,
    ScheduleDefinition,
    ScheduleView,
    resolve_schedule_definition,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _TimerBinding:
    """Bind a persisted revision to one loaded plugin activation."""

    schedule_id: str
    revision: str
    plugin: Plugin
    generation: int

    @property
    def current(self) -> bool:
        """Whether the declaring plugin activation still accepts calls."""
        return (
            get_plugin(self.plugin.id_) is self.plugin
            and self.plugin.ready
            and self.plugin.generation == self.generation
        )


class Scheduler:
    """Own one locked namespace, native timers, and bounded invocations."""

    def __init__(self, database: Database, settings: Settings) -> None:
        """Create in-memory timers without taking database resources."""
        self.database = database
        self.settings = settings
        self.schedules = Schedules(database, settings)
        self.native = AsyncIOScheduler(timezone=settings.timezone)
        self._lock: AsyncConnection | None = None
        self._backend_pid: int | None = None
        self._healthy = False
        self._closing = False
        self._bindings: dict[str, _TimerBinding] = {}
        self._blocked: dict[str, str] = {}
        self._active: set[asyncio.Task] = set()
        self._running_ids: dict[str, int] = {}
        self._initialized: set[tuple[str, str]] = set()
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._sync_lock = asyncio.Lock()
        self.channel: ScheduleChannel | None = None

    @property
    def available(self) -> bool:
        """Whether this instance still owns scheduling authority."""
        return self._healthy and not self._closing

    async def startup(self) -> None:
        """Reserve a dedicated connection and exclusive namespace lock."""
        connection = await self.database.engine.connect()
        self._lock = connection
        try:
            acquired = await connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": namespace_lock(self.settings.namespace)},
            )
            await connection.commit()
            if not acquired:
                raise SchedulerUnavailable(
                    "This cron namespace already has an active scheduler"
                )
            self._backend_pid = await connection.scalar(
                text("SELECT pg_backend_pid()")
            )
            await connection.commit()
            driver = get_driver()
            try:
                adapter = driver.get_adapter(ScheduleAdapter)
            except KeyError:
                adapter = driver.register_adapter(ScheduleAdapter)
            self.channel = ScheduleChannel(
                adapter, f"cron:{self.settings.namespace}"
            )
            self.native.start(paused=True)
            self._healthy = True
        except BaseException:
            await connection.invalidate()
            await connection.close()
            self._lock = None
            raise

    async def ready(self) -> None:
        """Rebuild future timers and start reconciliation."""
        await self.reconcile()
        self.native.add_job(
            self.reconcile,
            "interval",
            seconds=self.settings.reconcile_seconds,
            id="_reconcile",
            max_instances=1,
            coalesce=True,
        )
        self.native.resume()

    def stop_accepting(self) -> None:
        """Pause native timers before draining admitted invocations."""
        self._closing = True
        if self.native.running:
            self.native.pause()

    async def shutdown(self) -> None:
        """Wait for owned work before releasing timers and the lock."""
        self.stop_accepting()
        if self._active:
            await asyncio.gather(*tuple(self._active), return_exceptions=True)
        if self.native.running:
            self.native.shutdown(wait=True)
            await asyncio.sleep(0)
        async with self._sync_lock:
            self._healthy = False
            if self._lock is not None:
                try:
                    if not self._lock.invalidated:
                        await self._lock.execute(
                            text("SELECT pg_advisory_unlock(:key)"),
                            {"key": namespace_lock(self.settings.namespace)},
                        )
                        await self._lock.commit()
                finally:
                    # A lost session lock must never leak back into the pool.
                    await self._lock.invalidate()
                    await self._lock.close()
                    self._lock = None

    def _declaration(
        self, record: ScheduleRecord
    ) -> ScheduleDeclaration | None:
        definition = ScheduleDefinition.model_validate(record.definition)
        declaration = declarations.get(
            (record.owner_plugin, definition.handler)
        )
        plugin = get_plugin(record.owner_plugin)
        if declaration is None or plugin is None or not plugin.ready:
            return None
        return declaration

    async def _check_lock(self) -> None:
        """Verify the same PostgreSQL session still owns the lock."""
        try:
            if self._lock is None:
                raise SchedulerUnavailable("The scheduler lock is unavailable")
            backend_pid = await self._lock.scalar(
                text("SELECT pg_backend_pid()")
            )
            await self._lock.commit()
            if backend_pid != self._backend_pid:
                raise SchedulerUnavailable("The scheduler lock session changed")
        except Exception:
            self._healthy = False
            self.native.pause()
            logger.exception(
                "Cron lock connection was lost; scheduling stopped"
            )
            raise

    async def reconcile(self) -> None:
        """Restore persisted intent and reject work after losing the lock."""
        if not self.available:
            return
        async with self._sync_lock:
            if not self.available:
                return
            await self._check_lock()
            for declaration in tuple(declarations.values()):
                key = (declaration.owner, declaration.name)
                if key not in self._initialized:
                    await self.schedules.initialize(declaration)
                    self._initialized.add(key)
            records = await self.schedules.all()
            live = {record.id for record in records}
            for schedule_id in self._bindings.keys() | self._blocked.keys():
                if schedule_id not in live:
                    self._remove(schedule_id)
            for record in records:
                try:
                    await self._synchronize_record(record)
                except (ValueError, ScheduleError):
                    self._remove(record.id)
                    self._blocked[record.id] = "invalid definition"
                    logger.exception(
                        "Invalid persisted schedule: %s", record.id
                    )

    async def _synchronize_record(self, record: ScheduleRecord) -> None:
        definition = resolve_schedule_definition(
            ScheduleDefinition.model_validate(record.definition), self.settings
        )
        if (
            isinstance(definition.trigger, DateDefinition)
            and definition.trigger.run_at <= datetime.now(UTC)
            and self.native.get_job(record.id) is None
            and not self._running_ids.get(record.id)
            and not record.completed
            and not record.expired
        ):
            await self.schedules.complete(
                record.id, record.revision, expired=True
            )
            self._remove(record.id)
            self._blocked[record.id] = "expired"
            return
        declaration = self._declaration(record)
        if (
            not definition.enabled
            or record.completed
            or record.expired
            or declaration is None
        ):
            self._remove(record.id)
            self._blocked[record.id] = (
                "disabled"
                if not definition.enabled
                else "finished"
                if record.completed or record.expired
                else "handler unavailable"
            )
            return
        self._blocked.pop(record.id, None)
        plugin = get_plugin(record.owner_plugin)
        binding = _TimerBinding(
            record.id, record.revision, plugin, plugin.generation
        )
        if self._bindings.get(record.id) == binding:
            return
        if isinstance(
            definition.trigger, DateDefinition
        ) and definition.trigger.run_at <= datetime.now(UTC):
            await self.schedules.complete(
                record.id, record.revision, expired=True
            )
            self._remove(record.id)
            self._blocked[record.id] = "expired"
            return
        declaration.validate_payload(definition.payload)
        self.native.add_job(
            self._fire,
            trigger=definition.trigger.build(),
            id=record.id,
            args=(binding,),
            replace_existing=True,
            max_instances=definition.max_instances,
            coalesce=definition.coalesce,
            misfire_grace_time=definition.misfire_grace_seconds,
        )
        self._bindings[record.id] = binding

    def _remove(self, schedule_id: str) -> None:
        if self.native.get_job(schedule_id) is not None:
            self.native.remove_job(schedule_id)
        self._bindings.pop(schedule_id, None)
        self._blocked.pop(schedule_id, None)

    async def _fire(self, binding: _TimerBinding) -> None:
        schedule_id = binding.schedule_id
        owner = binding.plugin.id_
        revision = binding.revision
        task = asyncio.current_task()
        self._active.add(task)
        self._running_ids[schedule_id] = (
            self._running_ids.get(schedule_id, 0) + 1
        )
        try:
            if not self.available or not binding.current:
                return
            async with self._sync_lock:
                await self._check_lock()
            if self._semaphore.locked():
                logger.warning(
                    "Cron concurrency limit reached: %s", schedule_id
                )
                return
            async with self._semaphore:
                record = await self.schedules.get(owner, schedule_id)
                definition = resolve_schedule_definition(
                    ScheduleDefinition.model_validate(record.definition),
                    self.settings,
                )
                declaration = self._declaration(record)
                if (
                    not self.available
                    or not binding.current
                    or record.revision != revision
                    or not definition.enabled
                    or record.completed
                    or record.expired
                    or declaration is None
                ):
                    return
                payload = declaration.validate_payload(definition.payload)
                event = ScheduledEvent(
                    schedule_id=record.id,
                    invocation_id=str(uuid4()),
                    owner_plugin=owner,
                    name=record.name,
                    trigger_type=definition.trigger.type,
                    fired_at=datetime.now(UTC),
                    payload=payload,
                )
                try:
                    async with plugin_call(owner) as admitted_call:
                        async with asyncio.timeout(definition.timeout_seconds):
                            async with AsyncExitStack() as stack:
                                result = await dispatch_event(
                                    self.channel,
                                    event,
                                    stack=stack,
                                    admitted_call=admitted_call,
                                    matcher_filter=lambda matcher: (
                                        matcher is declaration.matcher
                                    ),
                                )
                                result.raise_for_errors()
                finally:
                    if isinstance(definition.trigger, DateDefinition):
                        await self.schedules.complete(record.id, revision)
        except PluginUnavailable:
            logger.info("Scheduled handler is not accepting calls")
        except Exception:
            logger.exception("Scheduled invocation failed: %s", schedule_id)
            raise
        finally:
            self._active.discard(task)
            self._running_ids[schedule_id] -= 1
            if not self._running_ids[schedule_id]:
                del self._running_ids[schedule_id]

    def view(self, record: ScheduleRecord) -> ScheduleView:
        """Combine persisted intent and current timer availability."""
        try:
            definition = resolve_schedule_definition(
                ScheduleDefinition.model_validate(record.definition),
                self.settings,
            )
            definition.trigger.build()
        except ValueError:
            definition = None
        job = self.native.get_job(record.id) if self.native.running else None
        reason = self._blocked.get(record.id)
        binding = self._bindings.get(record.id)
        if definition is None:
            reason = "invalid definition"
        elif not self.available:
            reason = "scheduler unavailable"
        elif (
            binding is None
            or binding.revision != record.revision
            or not binding.current
        ) and reason is None:
            reason = "pending synchronization"
        return ScheduleView(
            id=record.id,
            owner_plugin=record.owner_plugin,
            name=record.name,
            source=record.source,
            definition=definition,
            completed=record.completed,
            expired=record.expired,
            runnable=reason is None and job is not None,
            blocked_reason=reason,
            next_run_at=job.next_run_time if job else None,
        )
