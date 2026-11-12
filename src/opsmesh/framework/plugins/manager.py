"""Plugin discovery and import-hook integration.

See https://docs.python.org/3/reference/import.html#import-hooks.
"""

import importlib
import importlib.util
import logging
import pkgutil
import sys
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from importlib.abc import Loader, MetaPathFinder
from itertools import chain
from pathlib import Path
from types import ModuleType
from typing import Any

from opsmesh.framework.utils import path_to_module_name

from .admission import check_plugin_load
from .errors import PluginReloadError
from .model import Plugin, PluginMetadata
from .registry import (
    _current_plugin,
    _managers,
    _module_name_to_plugin_id,
    _new_plugin,
    get_plugin,
    revoke_plugin,
)

logger = logging.getLogger(__name__)


def _module_origin(module_name: str) -> str | None:
    """Resolve a module's source origin without importing it."""
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, AttributeError, ValueError):
        return None
    return None if spec is None else spec.origin


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
        namespace: str | None = None,
        required: bool = False,
    ):
        if (config is None) != (namespace is None):
            raise ValueError(
                "Plugin configuration and namespace are required together"
            )
        if namespace is not None and not namespace.strip():
            raise ValueError("Plugin configuration namespace cannot be empty")
        self.config_values = (
            deepcopy(dict(config)) if config is not None else None
        )
        self.config_namespace = namespace
        self.required = bool(required)
        """Whether a startup failure of this plugin must abort the host."""
        # simple plugin not in search path
        self.plugins: set[str] = set(plugins or [])
        self.search_path: set[str] = set(search_path or [])

        # cache plugins
        self._third_party_plugin_ids: dict[str, str] = {}
        self._searched_plugin_ids: dict[str, str] = {}
        self._prepare_plugins()

    def __repr__(self) -> str:
        return f"PluginManager(available_plugins={self.controlled_modules})"

    @property
    def third_party_plugins(self) -> set[str]:
        """The explicitly configured plugin identifiers."""
        return set(self._third_party_plugin_ids.keys())

    @property
    def searched_plugins(self) -> set[str]:
        """The discovered plugin identifiers."""
        return set(self._searched_plugin_ids.keys())

    @property
    def available_plugins(self) -> set[str]:
        """All plugin identifiers available through this manager."""
        return self.third_party_plugins | self.searched_plugins

    @property
    def controlled_modules(self) -> dict[str, str]:
        """The mapping of managed plugin identifiers to module paths."""
        return dict(
            chain(
                self._third_party_plugin_ids.items(),
                self._searched_plugin_ids.items(),
            )
        )

    def _previous_controlled_modules(self) -> dict[str, str]:
        _pre_managers: list[PluginManager]
        if self in _managers:
            _pre_managers = _managers[: _managers.index(self)]
        else:
            _pre_managers = _managers[:]

        return {
            plugin_id: module_name
            for manager in _pre_managers
            for plugin_id, module_name in manager.controlled_modules.items()
        }

    def _prepare_plugins(self) -> set[str]:
        """Discover plugins and cache their identifiers."""
        # get all previous ready to load plugins
        previous_plugin_ids = self._previous_controlled_modules()

        # if self not in global managers, merge self's controlled modules
        def get_controlled_modules():
            return (
                previous_plugin_ids
                if self in _managers
                else {**previous_plugin_ids, **self.controlled_modules}
            )

        # check third party plugins
        for plugin in self.plugins:
            plugin_id = _module_name_to_plugin_id(
                plugin, get_controlled_modules()
            )
            if (
                plugin_id in self._third_party_plugin_ids
                or plugin_id in previous_plugin_ids
            ):
                raise RuntimeError(
                    f"Plugin already exists: "
                    f"{plugin_id}! Check your plugin name"
                )
            self._third_party_plugin_ids[plugin_id] = plugin

        # check plugins in search path
        for module_info in pkgutil.iter_modules(self.search_path):
            # ignore if startswith "_"
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

            # get module name from path, pkgutil does not return the actual
            # module name
            module_path = Path(module_spec.origin).resolve()
            module_name = path_to_module_name(module_path)
            plugin_id = _module_name_to_plugin_id(
                module_name, get_controlled_modules()
            )

            if (
                plugin_id in previous_plugin_ids
                or plugin_id in self._third_party_plugin_ids
                or plugin_id in self._searched_plugin_ids
            ):
                raise RuntimeError(
                    f"Plugin already exists: "
                    f"{plugin_id}! Check your plugin name"
                )

            self._searched_plugin_ids[plugin_id] = module_name

        return self.available_plugins

    def _resolve_module_name(self, name: str) -> str:
        """Resolve a plugin id or module name owned by this manager."""
        if name in self._third_party_plugin_ids:
            return self._third_party_plugin_ids[name]
        if name in self._searched_plugin_ids:
            return self._searched_plugin_ids[name]
        if (
            name in self._third_party_plugin_ids.values()
            or name in self._searched_plugin_ids.values()
        ):
            return name
        raise RuntimeError(f"Plugin not found: {name}! Check your plugin name")

    def load_plugin(self, name: str) -> Plugin | None:
        """Load a plugin managed by this instance.

        Accept either a complete module path or a plugin identifier.

        Args:
            name: The plugin module name or identifier.
        """
        try:
            module_name = self._resolve_module_name(name)
        except RuntimeError as error:
            logger.error("%s", error)
            return None

        plugin_id = _module_name_to_plugin_id(module_name)
        check_plugin_load(plugin_id, module_name, _module_origin(module_name))

        try:
            module = importlib.import_module(module_name)
        except Exception as error:
            logger.error(
                'Failed to import plugin "%s"',
                plugin_id,
                exc_info=(type(error), error, error.__traceback__),
            )
            return None

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
                (self.load_plugin(name) for name in self.available_plugins),
            )
        )


class PluginFinder(MetaPathFinder):
    """Add plugin ownership to source, bytecode, and frozen imports."""

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ):
        """Resolve a managed plugin without intercepting unrelated imports."""
        manager = next(
            (
                item
                for item in reversed(_managers)
                if fullname in item.controlled_modules.values()
            ),
            None,
        )
        if manager is None:
            return None
        for finder in sys.meta_path:
            if isinstance(finder, PluginFinder):
                continue
            module_spec = finder.find_spec(fullname, path, target)
            if module_spec is not None:
                if module_spec.loader is not None:
                    module_spec.loader = PluginLoader(
                        manager, fullname, module_spec.loader
                    )
                return module_spec
        return None


class PluginLoader(Loader):
    """Execute plugin modules within their registration context."""

    def __init__(
        self, manager: PluginManager, fullname: str, delegate: Loader
    ) -> None:
        self.manager = manager
        self.name = fullname
        self.delegate = delegate
        self.loaded = False

    def __getattr__(self, name: str) -> Any:
        """Preserve the underlying loader's resource and inspection APIs."""
        return getattr(self.delegate, name)

    def create_module(self, spec) -> ModuleType | None:
        """Reuse a loaded plugin module when its identity is registered."""
        if self.name in sys.modules:
            self.loaded = True
            return sys.modules[self.name]
        # return None to use default module creation
        return self.delegate.create_module(spec)

    def exec_module(self, module: ModuleType) -> None:
        """Execute plugin source and restore the prior plugin context."""
        if self.loaded:
            return

        # create plugin before executing
        plugin = _new_plugin(self.name, module, self.manager)
        module.__plugin__ = plugin

        # enter plugin context
        _plugin_token = _current_plugin.set(plugin)

        try:
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
            from opsmesh.framework.lifecycle import bind_plugin_lifecycle

            from .hooks import bind_plugin_hooks, notify_plugin_loaded

            bind_plugin_hooks(plugin)
            notify_plugin_loaded(plugin)
            bind_plugin_lifecycle(plugin)
        except BaseException:
            revoke_plugin(plugin)
            raise
        finally:
            # leave plugin context
            _current_plugin.reset(_plugin_token)

        return


sys.meta_path.insert(0, PluginFinder())
