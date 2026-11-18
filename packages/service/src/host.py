"""Bootstrap the host from framework settings and persisted plugin records."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
from importlib.metadata import entry_points
from pathlib import Path
from pkgutil import extend_path
from threading import Lock

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.plugins import (
    PluginLoadDenied,
    PluginLoadRequest,
    PluginManager,
    get_plugin,
    get_plugin_by_module_name,
    get_plugin_manager,
    plugin_environment,
    register_plugin_manager,
)
from opsmesh.plugins.config import PluginConfiguration
from opsmesh.plugins.errors import PluginOperationBlocked
from opsmesh.plugins.model import Plugin
from opsmesh.utils import run_sync, run_sync_ctx_manager

from .catalog import PluginProtectedError, PluginRecord, PluginStorage

logger = logging.getLogger(__name__)
_host_claim = Lock()


def _refresh_plugin_namespace() -> None:
    """Expose external plugin roots through both namespace levels."""
    import opsmesh
    import opsmesh.plugins

    for package in (opsmesh, opsmesh.plugins):
        package.__path__ = extend_path(package.__path__, package.__name__)


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
        """Bind the service catalog to its framework runtime."""
        self.storage = storage
        self.driver = driver
        self.records: dict[str, PluginRecord] = {}
        self._builtin_modules: set[str] = set()

    def _is_builtin(self, module: str) -> bool:
        return any(
            module == root or module.startswith(root + ".")
            for root in self._builtin_modules
        )

    def _protection_reason(self, module: str) -> str | None:
        if self._is_builtin(module):
            return "Built-in plugin"
        root = module
        while root:
            record = self.records.get(root)
            if record is not None and record.protected:
                return "Protected plugin"
            root = root.rpartition(".")[0]
        return None

    def _check_protected(self, module: str) -> None:
        if reason := self._protection_reason(module):
            raise PluginProtectedError(f"{reason}: {module}")

    def _check_online_start(
        self, plugin: Plugin, *, restart: bool = False
    ) -> None:
        """Reject online activation of protected plugins in the start graph."""
        if plugin.ready and not restart:
            return
        pending = [plugin]
        visited: set[Plugin] = set()
        while pending:
            candidate = pending.pop()
            if candidate in visited:
                continue
            visited.add(candidate)
            self._check_protected(candidate.module_name)
            for name in candidate.dependencies:
                provider = get_plugin(name)
                if provider is not None and not provider.ready:
                    pending.append(provider)

    @staticmethod
    def _loaded_plugin(module: str) -> Plugin | None:
        plugin = get_plugin_by_module_name(module)
        if plugin is not None and plugin.module_name == module:
            return plugin
        return None

    @asynccontextmanager
    async def _change(self, module: str) -> AsyncIterator[None]:
        async with self.driver.plugin_change():
            if self.driver.started and self._is_builtin(module):
                raise PluginProtectedError(f"Built-in plugin: {module}")
            yield

    async def prepare(self) -> None:
        """Discover installed entry points before importing any plugin."""
        records = await run_sync(self.storage.list)()
        self.records = {record.module: record for record in records}
        for record in records:
            self._add_import_directory(record)
        self._builtin_modules = {
            entry.module
            for entry in entry_points(group="opsmesh.builtin_plugins")
        }
        for entry in entry_points(group="opsmesh.plugins"):
            if entry.module not in self.records:
                record = PluginRecord(module=entry.module, enabled=True)
                await run_sync(self.storage.save)(record)
                self.records[record.module] = record
        for module in sorted(
            self.records, key=lambda name: (name.count("."), name)
        ):
            self._register_source(self.records[module])

    def _register_source(self, record: PluginRecord) -> None:
        manager = PluginManager(
            [record.module],
            config=record.config,
            dependencies=record.dependencies,
            required=record.required,
        )
        manager.enabled = record.enabled
        manager.prepare_config = self.initialize_config
        register_plugin_manager(manager)

    @staticmethod
    def _manager(module: str) -> PluginManager:
        manager = get_plugin_manager(module)
        if manager is None:
            raise RuntimeError(f"Plugin source is not registered: {module}")
        return manager

    @staticmethod
    def _add_import_directory(record: PluginRecord) -> None:
        if record.directory is not None and record.directory not in sys.path:
            sys.path.insert(0, record.directory)

    def admit(self, request: PluginLoadRequest) -> bool:
        """Allow registered declaration imports without starting resources."""
        record = self.records.get(request.module_name)
        if record is None:
            return False
        if (
            self.driver.started
            and self._is_builtin(request.module_name)
            and self._loaded_plugin(request.module_name) is None
        ):
            raise PluginLoadDenied(
                f"Built-in plugin must be loaded at host startup: "
                f"{request.module_name}"
            )
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
        plugin = self._manager(module).load_plugin(module)
        if plugin is None:
            raise RuntimeError(f"Cannot load plugin: {module}")
        plugin.enabled = self.records[module].enabled
        return plugin

    def plugin_status(self, module: str) -> dict[str, JsonValue]:
        """Read catalog and runtime status without importing a plugin."""
        record = self._known_record(module)
        plugin = self._loaded_plugin(module)
        return {
            "module": module,
            "pid": os.getpid(),
            "builtin": self._is_builtin(module),
            "protected": self._protection_reason(module) is not None,
            "enabled": record.enabled,
            "autoload": record.autoload,
            "state": plugin.status.value if plugin else "not_loaded",
            "manually_stopped": bool(plugin and plugin.manually_stopped),
            "cleanup_complete": plugin is None or plugin.cleanup_complete,
            "in_flight": len(plugin.calls) if plugin else 0,
            "generation": plugin.generation if plugin else None,
        }

    def _known_record(self, module: str) -> PluginRecord:
        try:
            return self.records[module]
        except KeyError:
            raise PluginOperationBlocked(
                f"Plugin is not registered: {module}"
            ) from None

    async def control_plugin(
        self, module: str, action: str
    ) -> dict[str, JsonValue]:
        """Run a resource operation in the current host and return its state."""
        if action not in {"load", "start", "stop", "restart"}:
            raise ValueError("Unsupported resource operation")
        if not self.driver.started:
            raise PluginOperationBlocked("Host lifecycle is not running")
        stopping = action in {"stop", "restart"}
        if stopping:
            self._check_protected(module)
            if current := self._loaded_plugin(module):
                self.driver.check_plugin_stoppable(current)
        async with self._change(module):
            record = self._known_record(module)
            plugin = self._loaded_plugin(module)
            if stopping:
                self._check_protected(module)
            if action in {"start", "restart"} and not record.enabled:
                raise PluginOperationBlocked(f"Plugin is disabled: {module}")
            if action == "load":
                self.load(module)
            elif action == "stop":
                if plugin is None:
                    raise PluginOperationBlocked(
                        f"Plugin is not loaded: {module}"
                    )
                await self.driver.stop_plugin(plugin)
            else:
                restart = action == "restart" and plugin is not None
                plugin = plugin or self.load(module)
                self._check_online_start(plugin, restart=restart)
                if restart:
                    await self.driver.stop_plugin(plugin)
                await self.driver.start_plugin(plugin)
            return self.plugin_status(module)

    async def register(self, record: PluginRecord) -> None:
        """Register an existing plugin without installing or executing code."""
        async with self._change(record.module):
            if record.module in self.records:
                raise ValueError("Plugin is already registered")
            record = PluginRecord.model_validate(record.model_dump())
            if (
                record.directory is not None
                and not Path(record.directory).is_dir()
            ):
                raise ValueError("Plugin import directory does not exist")
            await run_sync(self.storage.save)(record)
            self.records[record.module] = record
            original_path = list(sys.path)
            import opsmesh
            import opsmesh.plugins

            original_namespaces = (
                list(opsmesh.__path__),
                list(opsmesh.plugins.__path__),
            )
            try:
                self._add_import_directory(record)
                _refresh_plugin_namespace()
                self._register_source(record)
            except BaseException:
                sys.path[:] = original_path
                opsmesh.__path__, opsmesh.plugins.__path__ = original_namespaces
                self.records.pop(record.module)
                await run_sync(self.storage.delete)(record.module)
                raise

    def list_plugins(self) -> tuple[PluginRecord, ...]:
        """List registered plugins without loading them."""
        return tuple(self.records.values())

    async def protect(self, module: str) -> None:
        """Protect a registered plugin during exclusive maintenance."""
        async with self._change(module):
            if self.driver.started:
                raise PluginOperationBlocked(
                    "Protection changes require offline maintenance"
                )
            self._known_record(module)
            record = await run_sync(self.storage.set_protected)(module)
            self.records[module] = record

    async def save_config(
        self,
        module: str,
        values: dict[str, JsonValue],
        *,
        dependencies: dict[str, dict[str, JsonValue]] | None = None,
    ) -> None:
        """Validate and persist configuration without restarting a plugin."""
        async with self._change(module):
            plugin = self.load(module)
            dependency_values = (
                self.records[module].dependencies
                if dependencies is None
                else dependencies
            )
            await self._save_configuration(
                plugin,
                PluginConfiguration(
                    config=values, dependencies=dependency_values
                ),
            )

    async def _save_configuration(
        self, plugin: Plugin, configuration: PluginConfiguration
    ) -> PluginRecord:
        """Validate and persist a complete independently owned configuration."""
        from opsmesh.plugins.config import validate_dependency_config

        schema = plugin.metadata.config if plugin.metadata else None
        if schema is None and configuration.config:
            raise ValueError("The plugin has no configuration schema")
        if schema is not None:
            schema.model_validate(deepcopy(configuration.config))
        validate_dependency_config(plugin, configuration.dependencies)
        record = self.records[plugin.module_name].model_copy(
            update={**configuration.model_dump(), "config_initialized": True},
            deep=True,
        )
        await run_sync(self.storage.save)(record)
        self.records[plugin.module_name] = record
        return record

    async def initialize_config(self, plugin: Plugin) -> None:
        """Import initial values before resource startup, once per record."""
        record = self.records[plugin.module_name]
        if record.config_initialized:
            return
        from opsmesh.plugins.config import read_config_file

        configuration = (
            await run_sync(read_config_file)(plugin)
            if plugin.metadata and plugin.metadata.config_file is not None
            else PluginConfiguration(
                config=record.config, dependencies=record.dependencies
            )
        )
        record = await self._save_configuration(plugin, configuration)
        self._use_configuration(plugin, record)

    async def import_config(self, module: str) -> None:
        """Replace saved values from the declared file without restarting."""
        from opsmesh.plugins.config import read_config_file

        async with self._change(module):
            plugin = self.load(module)
            configuration = await run_sync(read_config_file)(plugin)
            await self._save_configuration(plugin, configuration)

    def _use_configuration(self, plugin: Plugin, record: PluginRecord) -> None:
        """Replace runtime values with independently owned saved snapshots."""
        for target in (plugin, plugin.manager):
            snapshot = record.model_copy(deep=True)
            target.config_values = snapshot.config
            target.dependency_config_values = snapshot.dependencies

    async def apply_config(self, module: str) -> None:
        """Apply saved values, restarting only an already running plugin."""
        self._check_protected(module)
        current = self._loaded_plugin(module)
        if current is not None and self.driver.started:
            self.driver.check_plugin_stoppable(current)
        async with self._change(module):
            self._check_protected(module)
            plugin = self.load(module)
            await self.initialize_config(plugin)
            record = self.records[module]
            if plugin.metadata and plugin.metadata.config:
                plugin.metadata.config.model_validate(record.config)
            from opsmesh.plugins.config import validate_dependency_config

            validate_dependency_config(plugin, record.dependencies)
            was_running = plugin.ready
            if was_running:
                self._check_online_start(plugin, restart=True)
                await self.driver.stop_plugin(plugin)
            elif not plugin.cleanup_complete:
                raise RuntimeError("Plugin cleanup has not completed")
            record = self.records[module]
            self._use_configuration(plugin, record)
            if was_running:
                await self.driver.start_plugin(plugin)

    async def enable(self, module: str) -> None:
        """Persist startup intent and start resources on a running host."""
        async with self._change(module):
            record = self.records[module].model_copy(
                update={"enabled": True, "autoload": True}
            )
            await run_sync(self.storage.save)(record)
            self.records[module] = record
            self._manager(module).enabled = True
            plugin = self.load(module)
            if not plugin.ready and plugin.cleanup_complete:
                self._use_configuration(plugin, record)
            if self.driver.started:
                self._check_online_start(plugin)
                await self.driver.start_plugin(plugin)

    async def disable(self, module: str) -> None:
        """Persist disabled intent after checking active dependencies."""
        self._check_protected(module)
        current = self._loaded_plugin(module)
        if current is not None and self.driver.started:
            self.driver.check_plugin_stoppable(current)
        async with self._change(module):
            self._check_protected(module)
            plugin = self._loaded_plugin(module)
            if plugin is not None and self.driver.started:
                self.driver.check_plugin_stoppable(plugin)
            record = self.records[module].model_copy(update={"enabled": False})
            await run_sync(self.storage.save)(record)
            self.records[module] = record
            self._manager(module).enabled = False
            if plugin is not None:
                plugin.enabled = False
                if self.driver.started:
                    await self.driver.stop_plugin(plugin)


@contextmanager
def _plugin_import_paths() -> Iterator[None]:
    """Restore the import roots added by this host's catalog."""
    original_path = list(sys.path)
    import opsmesh
    import opsmesh.plugins as plugin_package

    original_root_namespace = list(opsmesh.__path__)
    original_namespace = list(plugin_package.__path__)
    try:
        yield
    finally:
        sys.path[:] = original_path
        opsmesh.__path__ = original_root_namespace
        plugin_package.__path__ = original_namespace


@asynccontextmanager
async def load_host(
    manifest: HostManifest, *, load_entries: bool = True
) -> AsyncIterator[Host]:
    """Assemble a single process host without opening network resources."""
    if not _host_claim.acquire(blocking=False):
        raise RuntimeError("A host already owns this process")
    try:
        root = manifest.data_dir
        async with (
            run_sync_ctx_manager(_data_lock(root)),
            run_sync_ctx_manager(PluginStorage(root)) as storage,
        ):
            driver = Driver(
                Env(environment=manifest.environment), manifest.framework
            )
            host = Host(storage, driver)
            with (
                _plugin_import_paths(),
                plugin_environment(admission=host.admit),
            ):
                await host.prepare()
                _refresh_plugin_namespace()
                if load_entries:
                    for record in host.list_plugins():
                        if not (record.enabled and record.autoload):
                            continue
                        try:
                            host.load(record.module)
                        except Exception:
                            if record.required:
                                raise
                            logger.error(
                                "Plugin could not load: %s", record.module
                            )
                yield host
    finally:
        _host_claim.release()
