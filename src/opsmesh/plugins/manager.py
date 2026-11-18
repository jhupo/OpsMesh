"""Discover plugin sources and import them in a host-owned environment."""

from __future__ import annotations

import importlib
import logging
import pkgutil
import sys
from collections.abc import (
    Awaitable,
    Callable,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
from pathlib import Path
from threading import Lock
from types import ModuleType
from typing import Any

from opsmesh.utils import path_to_module_name

from .admission import PluginAdmission, PluginLoadRequest, check_plugin_load
from .errors import PluginLoadDenied, PluginReloadError
from .model import Plugin, PluginMetadata
from .registry import (
    _new_plugin,
    _registration_scope,
    _revoke_plugin,
    get_plugin,
    plugin_scope,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PluginSource:
    plugin_id: str
    module_name: str
    parent_id: str | None
    manager: PluginManager

    def request(self, origin: str | None) -> PluginLoadRequest:
        return PluginLoadRequest(self.plugin_id, self.module_name, origin)


def _parent_modules(module_name: str) -> Iterator[str]:
    while "." in module_name:
        module_name, _ = module_name.rsplit(".", 1)
        yield module_name


class _PluginSources:
    """One source index shared by explicit loads and Python's import hook."""

    def __init__(self, admission: PluginAdmission | None) -> None:
        self.admission = admission
        self.by_id: dict[str, _PluginSource] = {}
        self.by_module: dict[str, _PluginSource] = {}
        self._parent_modules: set[str] = set()

    def resolve(self, name: str) -> _PluginSource | None:
        return self.by_module.get(name) or self.by_id.get(name)

    def register(self, manager: PluginManager) -> None:
        pending: dict[str, _PluginSource] = {}
        ids: set[str] = set()
        for module in sorted(manager._module_names):
            if existing := self.by_module.get(module):
                if existing.manager is manager:
                    continue
                raise RuntimeError(
                    f"Plugin module already registered: {module}"
                )
            if module in self._parent_modules:
                raise RuntimeError(
                    f"Register parent plugin before its children: {module}"
                )
            parent = next(
                (
                    source
                    for name in _parent_modules(module)
                    if (source := pending.get(name) or self.by_module.get(name))
                ),
                None,
            )
            plugin_id = module.rsplit(".", 1)[-1]
            if parent is not None:
                plugin_id = f"{parent.plugin_id}:{plugin_id}"
            if plugin_id in self.by_id or plugin_id in ids:
                raise RuntimeError(
                    f"Plugin already exists: {plugin_id}! "
                    "Check your plugin name"
                )
            pending[module] = _PluginSource(
                plugin_id, module, parent.plugin_id if parent else None, manager
            )
            ids.add(plugin_id)
        for module, source in pending.items():
            self.by_id[source.plugin_id] = source
            self.by_module[module] = source
            self._parent_modules.update(_parent_modules(module))


_sources: _PluginSources | None = None
_environment_claim = Lock()
_approved_import: ContextVar[tuple[_PluginSource, str | None] | None] = (
    ContextVar("_approved_import", default=None)
)


@contextmanager
def plugin_environment(
    *, admission: PluginAdmission | None = None
) -> Iterator[None]:
    """Own plugin sources, import interception and loaded declarations.

    Only one environment can own a Python process at a time. Shut down all
    plugin resources before leaving it. Exiting does not unload Python modules.
    """
    global _sources
    if not _environment_claim.acquire(blocking=False):
        raise RuntimeError("A plugin environment already owns this process")
    try:
        _sources = _PluginSources(admission)
        finder = PluginFinder(_sources)
        sys.meta_path.insert(0, finder)
        try:
            with _registration_scope():
                yield
        finally:
            sys.meta_path.remove(finder)
    finally:
        _sources = None
        _environment_claim.release()


def _active_sources() -> _PluginSources:
    if _sources is None:
        raise RuntimeError(
            "Plugin loading requires an active plugin_environment()"
        )
    return _sources


def register_plugin_manager(manager: PluginManager) -> None:
    """Register sources without importing them, parents before children."""
    _active_sources().register(manager)


def get_plugin_manager(name: str) -> PluginManager | None:
    """Find the manager for an exact module name or plugin identifier."""
    source = _sources.resolve(name) if _sources is not None else None
    return source.manager if source is not None else None


def get_available_plugin_names() -> set[str]:
    """Return registered source identifiers, including unloaded plugins."""
    return set(_sources.by_id) if _sources is not None else set()


def _find_module_spec(
    fullname: str, path: Sequence[str] | None
) -> ModuleSpec | None:
    for finder in tuple(sys.meta_path):
        if not isinstance(finder, PluginFinder):
            spec = finder.find_spec(fullname, path)
            if spec is not None:
                return spec
    return None


def _module_origin(module_name: str) -> str | None:
    """Resolve the proposed origin without executing unimported parents."""
    path = None
    for name in (*reversed(tuple(_parent_modules(module_name))), module_name):
        if name in sys.modules:
            module = sys.modules[name]
            spec = getattr(module, "__spec__", None)
            path = getattr(module, "__path__", None)
        else:
            try:
                spec = _find_module_spec(name, path)
            except KeyError as error:
                # Some namespace finders need their parent to be imported.
                if error.args != (name.rpartition(".")[0],):
                    raise
                return None
            path = spec.submodule_search_locations if spec else None
        if name == module_name:
            return spec.origin if spec else None
        if path is None:
            return None
    return None


class PluginManager:
    """A manager for explicit and directory-discovered plugins.

    Args:
        plugins: Explicit plugin module names.
        search_path: Plugin directories relative to the current working
            directory.
    """

    def __init__(
        self,
        plugins: Iterable[str] | None = None,
        search_path: Iterable[str] | None = None,
        *,
        config: Mapping[str, Any] | None = None,
        dependencies: Mapping[str, Mapping[str, Any]] | None = None,
        required: bool = False,
    ):
        self.config_values = (
            deepcopy(dict(config)) if config is not None else None
        )
        self.dependency_config_values = deepcopy(dict(dependencies or {}))
        self.required = bool(required)
        """Whether a startup failure of this plugin must abort the host."""
        self.enabled = True
        self.prepare_config: Callable[[Plugin], Awaitable[None]] | None = None
        """Optional host callback preparing configuration before startup."""
        self._module_names = frozenset(plugins or ()) | frozenset(
            self._discover_modules(search_path or ())
        )

    @property
    def available_plugins(self) -> set[str]:
        """All plugin identifiers available through this manager."""
        sources = _active_sources()
        return {
            source.plugin_id
            for module in self._module_names
            if (source := sources.by_module.get(module))
            and source.manager is self
        }

    @staticmethod
    def _discover_modules(search_path: Iterable[str]) -> Iterator[str]:
        for module_info in pkgutil.iter_modules(search_path):
            if module_info.name.startswith("_"):
                continue

            if not (
                module_spec := module_info.module_finder.find_spec(
                    module_info.name, None
                )
            ):
                continue

            if not module_spec.origin:
                continue

            yield path_to_module_name(Path(module_spec.origin).resolve())

    def load_plugin(self, name: str) -> Plugin | None:
        """Load a plugin managed by this instance.

        Accept either a complete module path or a plugin identifier.

        Args:
            name: The plugin module name or identifier.
        """
        sources = _active_sources()
        source = sources.resolve(name)
        if source is None or source.manager is not self:
            logger.error("Plugin is not registered with this manager: %s", name)
            return None
        module_name = source.module_name
        plugin_id = source.plugin_id
        origin = _module_origin(module_name)
        check_plugin_load(sources.admission, source.request(origin))
        token = _approved_import.set((source, origin))
        try:
            module = importlib.import_module(module_name)
        except (PluginLoadDenied, PluginReloadError):
            raise
        except Exception as error:
            logger.error(
                'Failed to import plugin "%s"',
                plugin_id,
                exc_info=(type(error), error, error.__traceback__),
            )
            return None
        finally:
            _approved_import.reset(token)

        plugin = getattr(module, "__plugin__", None)
        if not isinstance(plugin, Plugin):
            logger.error(
                'Module "%s" is not loaded as a plugin! '
                "Make sure not to import it before loading.",
                module.__name__,
            )
            return None
        if get_plugin(plugin.id_) is not plugin:
            raise PluginReloadError(
                f'Plugin "{plugin.id_}" was revoked; restart the host to '
                "load it again"
            )

        logger.info(
            f'Succeeded to load plugin "{plugin.id_}"'
            + (
                f' from "{plugin.module_name}"'
                if plugin.module_name != plugin.id_
                else ""
            )
        )
        return plugin

    def load_all_plugins(self) -> set[Plugin]:
        """Load every available plugin."""
        return set(
            filter(
                None,
                (
                    self.load_plugin(name)
                    for name in sorted(self.available_plugins)
                ),
            )
        )


class PluginFinder(MetaPathFinder):
    """Add plugin ownership to source, bytecode, and frozen imports."""

    def __init__(self, sources: _PluginSources) -> None:
        self.sources = sources

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ):
        """Resolve a managed plugin without intercepting unrelated imports."""
        source = self.sources.by_module.get(fullname)
        if source is None:
            return None
        if target is not None:
            raise PluginReloadError(
                f'Plugin "{source.plugin_id}" requires a fresh process'
            )
        spec = _find_module_spec(fullname, path)
        if spec is not None and spec.loader is not None:
            spec.loader = PluginLoader(
                source, self.sources.admission, spec.loader
            )
        return spec


class PluginLoader(Loader):
    """Execute plugin modules within their registration context."""

    def __init__(
        self,
        source: _PluginSource,
        admission: PluginAdmission | None,
        delegate: Loader,
    ) -> None:
        self.source = source
        self.admission = admission
        self.delegate = delegate

    def __getattr__(self, name: str) -> Any:
        """Preserve the underlying loader's resource and inspection APIs."""
        return getattr(self.delegate, name)

    def create_module(self, spec) -> ModuleType | None:
        """Apply admission before an underlying loader can execute code."""
        if _approved_import.get() != (self.source, spec.origin):
            check_plugin_load(self.admission, self.source.request(spec.origin))
        return self.delegate.create_module(spec)

    def exec_module(self, module: ModuleType) -> None:
        """Execute plugin source and restore the prior plugin context."""
        source = self.source
        plugin = _new_plugin(
            source.plugin_id, source.parent_id, module, source.manager
        )
        module.__plugin__ = plugin

        try:
            with plugin_scope(plugin):
                self.delegate.exec_module(module)
                if getattr(module, "__plugin__", None) is not plugin:
                    raise TypeError(
                        'Plugin code must not overwrite "__plugin__"; '
                        'declare "__plugin_meta__" instead'
                    )
                metadata = getattr(module, "__plugin_meta__", None)
                if metadata is not None and not isinstance(
                    metadata, PluginMetadata
                ):
                    raise TypeError("Plugin metadata must be PluginMetadata")
                plugin.metadata = metadata
                from .hooks import bind_plugin_hooks, notify_plugin_loaded

                bind_plugin_hooks(plugin)
                notify_plugin_loaded(plugin)
        except BaseException:
            _revoke_plugin(plugin)
            raise
