"""Bootstrap the host from framework settings and persisted plugin records."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, contextmanager
from importlib.metadata import entry_points
from pathlib import Path
from pkgutil import extend_path

import anyio
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from opsmesh.config import Config, Env
from opsmesh.drivers import Driver, is_initialized, set_driver
from opsmesh.plugins import (
    PluginLoadDenied,
    PluginManager,
    clear_plugin_admission,
    get_loaded_plugins,
    get_plugin_by_module_name,
    register_plugin_manager,
    set_plugin_admission,
)
from opsmesh.plugins.admission import PluginLoadRequest
from opsmesh.plugins.model import Plugin
from opsmesh.plugins.storage import PluginRecord, PluginStorage

logger = logging.getLogger(__name__)


@contextmanager
def _data_lock(root: Path):
    """Prevent two hosts from managing the same resource directory."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / "host.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            lock.write(b"\0")
            lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


class HostManifest(BaseModel):
    """Framework and process settings, with no plugin-specific values."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", hide_input_in_errors=True
    )
    environment: str = Field(default="development", pattern=r"^[a-zA-Z0-9_-]+$")
    framework: Config = Field(default_factory=Config)
    data_dir: Path = Path("./data")


def load_manifest(path: str | Path) -> HostManifest:
    """Resolve the host data directory relative to its configuration file."""
    path = Path(path).resolve()
    manifest = HostManifest.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    root = manifest.data_dir
    if not root.is_absolute():
        root = path.parent / root
    return manifest.model_copy(update={"data_dir": root.resolve()})


class Host:
    """Connect persisted settings to existing plugin lifecycle APIs."""

    def __init__(self, storage: PluginStorage, driver: Driver) -> None:
        self.storage = storage
        self.driver = driver
        self.records: dict[str, PluginRecord] = {}
        self.managers: dict[str, PluginManager] = {}
        self.lock = anyio.Lock()

    @asynccontextmanager
    async def _change(self) -> AsyncIterator[None]:
        from opsmesh.lifecycle import _managing

        if _managing.get() is asyncio.current_task():
            raise RuntimeError(
                "Lifecycle callbacks cannot change plugin settings"
            )
        async with self.lock, self.driver._lifespan.change():
            yield

    async def prepare(self) -> None:
        """Discover installed entry points before importing any plugin."""
        records = await anyio.to_thread.run_sync(self.storage.list)
        self.records = {record.module: record for record in records}
        for record in records:
            self._manager(record)
        for entry in entry_points(group="opsmesh.plugins"):
            if entry.module not in self.records:
                record = PluginRecord(module=entry.module, enabled=True)
                await anyio.to_thread.run_sync(self.storage.save, record)
                self.records[record.module] = record
        for record in self.records.values():
            self._manager(record)

    def _manager(self, record: PluginRecord) -> PluginManager:
        manager = self.managers.get(record.module)
        if manager is not None:
            return manager
        if record.directory is not None:
            path = Path(record.directory).resolve()
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
        manager = PluginManager(
            [record.module],
            config=record.config,
            required=record.required,
            protected=record.protected,
        )
        manager.enabled = record.enabled
        register_plugin_manager(manager)
        self.managers[record.module] = manager
        return manager

    def admit(self, request: PluginLoadRequest) -> bool:
        """Allow registered declaration imports without starting resources."""
        record = self.records.get(request.module_name)
        if record is None:
            return False
        if record.directory is not None:
            if request.origin is None:
                raise PluginLoadDenied("The plugin has no verifiable source")
            root = Path(record.directory).resolve()
            origin = Path(request.origin).resolve()
            if not origin.is_relative_to(root):
                raise PluginLoadDenied(
                    "Plugin source differs from its registered directory"
                )
        return True

    def load(self, module: str) -> Plugin:
        """Load declarations, including those of a disabled plugin."""
        record = self.records[module]
        if record.directory is not None and not Path(record.directory).is_dir():
            raise ValueError(
                f"Plugin import directory does not exist: {module}"
            )
        plugin = self.managers[module].load_plugin(module)
        if plugin is None:
            raise RuntimeError(f"Cannot load plugin: {module}")
        plugin.enabled = self.records[module].enabled
        plugin.protected = self.records[module].protected
        return plugin

    async def register(self, record: PluginRecord) -> None:
        """Register an existing plugin without installing or executing code."""
        async with self._change():
            if record.module in self.records:
                raise ValueError("Plugin is already registered")
            record = PluginRecord.model_validate(record.model_dump())
            if (
                record.directory is not None
                and not Path(record.directory).is_dir()
            ):
                raise ValueError("Plugin import directory does not exist")
            await anyio.to_thread.run_sync(self.storage.save, record)
            self.records[record.module] = record
            original_path = list(sys.path)
            try:
                self._manager(record)
                import opsmesh.plugins as plugin_package

                plugin_package.__path__ = extend_path(
                    plugin_package.__path__, plugin_package.__name__
                )
            except BaseException:
                sys.path[:] = original_path
                self.records.pop(record.module)
                await anyio.to_thread.run_sync(
                    self.storage.delete, record.module
                )
                raise

    async def save_config(
        self, module: str, values: dict[str, JsonValue]
    ) -> None:
        """Validate and persist configuration without restarting a plugin."""
        async with self._change():
            plugin = self.load(module)
            schema = plugin.metadata.config if plugin.metadata else None
            if schema is None and values:
                raise ValueError("The plugin has no configuration schema")
            if schema is not None:
                schema.model_validate(values)
            record = self.records[module].model_copy(
                update={"config": values}, deep=True
            )
            await anyio.to_thread.run_sync(self.storage.save, record)
            self.records[module] = record

    async def apply_config(self, module: str) -> None:
        """Apply saved values, restarting only an already running plugin."""
        current = get_plugin_by_module_name(module)
        if current is not None and self.driver.started:
            self.driver._lifespan.check_stoppable(current)
        async with self._change():
            plugin = self.load(module)
            record = self.records[module]
            if plugin.metadata and plugin.metadata.config:
                plugin.metadata.config.model_validate(record.config)
            was_running = plugin.ready
            if plugin.protected:
                from opsmesh.plugins.errors import PluginProtectedError

                raise PluginProtectedError(
                    "Protected plugin configuration requires host restart"
                )
            if was_running:
                await self.managers[module].stop_plugin(module)
            elif not plugin.cleanup_complete:
                raise RuntimeError("Plugin cleanup has not completed")
            record = self.records[module]
            plugin.config_values = record.model_copy(deep=True).config
            self.managers[module].config_values = plugin.config_values.copy()
            if was_running:
                await self.managers[module].start_plugin(module)

    async def enable(self, module: str) -> None:
        """Persist startup intent and start resources on a running host."""
        async with self._change():
            record = self.records[module].model_copy(
                update={"enabled": True, "autoload": True}
            )
            await anyio.to_thread.run_sync(self.storage.save, record)
            self.records[module] = record
            self.managers[module].enabled = True
            plugin = self.load(module)
            if not plugin.ready and plugin.cleanup_complete:
                plugin.config_values = record.model_copy(deep=True).config
                self.managers[module].config_values = record.model_copy(
                    deep=True
                ).config
            if self.driver.started:
                await self.managers[module].start_plugin(module)

    async def disable(self, module: str) -> None:
        """Persist disabled intent after checking active dependencies."""
        current = get_plugin_by_module_name(module)
        if current is not None and self.driver.started:
            self.driver._lifespan.check_stoppable(current)
        async with self._change():
            plugin = get_plugin_by_module_name(module)
            if self.records[module].protected:
                from opsmesh.plugins.errors import PluginProtectedError

                raise PluginProtectedError(
                    "Protected plugins cannot be disabled"
                )
            if plugin is not None and self.driver.started:
                self.driver._lifespan.check_stoppable(plugin)
            record = self.records[module].model_copy(update={"enabled": False})
            await anyio.to_thread.run_sync(self.storage.save, record)
            self.records[module] = record
            self.managers[module].enabled = False
            if plugin is not None:
                plugin.enabled = False
                if self.driver.started:
                    await self.managers[module].stop_plugin(module)


_host: Host | None = None


def get_host() -> Host:
    """Expose host operations to an authorized management plugin."""
    if _host is None:
        raise RuntimeError("The host has not been initialized")
    return _host


@asynccontextmanager
async def load_host(
    manifest: HostManifest,
    *,
    manifest_dir: str | Path | None = None,
    load_entries: bool = True,
) -> AsyncIterator[Driver]:
    """Prepare recorded plugins without opening network resources."""
    global _host
    if is_initialized():
        raise RuntimeError("Framework is already initialized")
    root = manifest.data_dir
    if not root.is_absolute():
        root = Path(manifest_dir or Path.cwd()) / root
    storage = PluginStorage(root)
    ownership = _data_lock(root)
    await anyio.to_thread.run_sync(ownership.__enter__)
    try:
        await anyio.to_thread.run_sync(storage.__enter__)
    except BaseException:
        ownership.__exit__(None, None, None)
        raise
    driver = Driver(Env(environment=manifest.environment), manifest.framework)
    set_driver(driver)
    host = Host(storage, driver)
    _host = host
    existing = get_loaded_plugins()
    original_path = list(sys.path)
    import opsmesh.plugins as plugin_package

    original_namespace = list(plugin_package.__path__)
    from opsmesh.plugins.registry import _managers

    original_managers = list(_managers)
    try:
        await host.prepare()
        plugin_package.__path__ = extend_path(
            plugin_package.__path__, plugin_package.__name__
        )
        set_plugin_admission(host.admit)
        if load_entries:
            for record in tuple(host.records.values()):
                if record.enabled and record.autoload:
                    try:
                        host.load(record.module)
                    except Exception:
                        if record.required:
                            raise
                        logger.error("Plugin could not load: %s", record.module)
        yield driver
    finally:
        with anyio.CancelScope(shield=True):
            try:
                for plugin in reversed(tuple(get_loaded_plugins())):
                    if plugin not in existing:
                        from opsmesh.plugins.registry import _revoke_plugin

                        _revoke_plugin(plugin)
            finally:
                clear_plugin_admission()
                sys.path[:] = original_path
                plugin_package.__path__ = original_namespace
                _managers[:] = original_managers
                _host = None
                await anyio.to_thread.run_sync(storage.__exit__)
                ownership.__exit__(None, None, None)
                from opsmesh.drivers import base

                base._driver = None


async def run_host(
    manifest: HostManifest, *, manifest_dir=None, stop_event=None
) -> None:
    """Run plugin entrances until cancellation or an explicit signal."""
    async with load_host(manifest, manifest_dir=manifest_dir) as driver:
        async with driver.lifespan():
            if stop_event is None:
                await anyio.sleep_forever()
            else:
                await stop_event.wait()


def main(argv: list[str] | None = None) -> int:
    """Run the installed executable entry point."""
    from .cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
