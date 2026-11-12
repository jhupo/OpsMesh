"""Store loaded plugin identities, dependencies, and resource ownership.

PluginManager discovers and imports modules. This module holds their shared
runtime records so dependencies, event handlers, and cleanup resolve the same
owner. It does not perform a second discovery or import step.
"""

from collections.abc import Callable
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from functools import wraps
from itertools import chain
from types import ModuleType
from typing import TYPE_CHECKING, Any

from opsmesh.dependencies import Dependent

from .model import Plugin

if TYPE_CHECKING:
    from .manager import PluginManager

_plugins: dict[str, "Plugin"] = {}
_managers: list["PluginManager"] = []
_current_plugin: ContextVar["Plugin | None"] = ContextVar(
    "_current_plugin", default=None
)


def _module_name_to_plugin_name(module_name: str) -> str:
    return module_name.rsplit(".", 1)[-1]


def _controlled_modules() -> dict[str, str]:
    return {
        plugin_id: module_name
        for manager in _managers
        for plugin_id, module_name in manager.controlled_modules.items()
    }


def _find_parent_plugin_id(
    module_name: str, controlled_modules: dict[str, str] | None = None
) -> str | None:
    if controlled_modules is None:
        controlled_modules = _controlled_modules()
    available = {
        module_name: plugin_id
        for plugin_id, module_name in controlled_modules.items()
    }
    while "." in module_name:
        module_name, _ = module_name.rsplit(".", 1)
        if module_name in available:
            return available[module_name]


def _module_name_to_plugin_id(
    module_name: str, controlled_modules: dict[str, str] | None = None
) -> str:
    plugin_name = _module_name_to_plugin_name(module_name)
    if parent_plugin_id := _find_parent_plugin_id(
        module_name, controlled_modules
    ):
        return f"{parent_plugin_id}:{plugin_name}"
    return plugin_name


def _new_plugin(
    module_name: str, module: ModuleType, manager: "PluginManager"
) -> "Plugin":
    plugin_id = _module_name_to_plugin_id(module_name)
    if plugin_id in _plugins:
        raise RuntimeError(
            f"Plugin {plugin_id} already exists! Check your plugin name."
        )

    parent_plugin_id = _find_parent_plugin_id(module_name)
    if parent_plugin_id is not None and parent_plugin_id not in _plugins:
        raise RuntimeError(
            f"Parent plugin {parent_plugin_id} must "
            f"be loaded before loading {plugin_id}."
        )
    parent_plugin = (
        _plugins[parent_plugin_id] if parent_plugin_id is not None else None
    )

    plugin = Plugin(
        name=_module_name_to_plugin_name(module_name),
        module=module,
        module_name=module_name,
        manager=manager,
        parent_plugin=parent_plugin,
        config_namespace=manager.config_namespace,
        config_values=deepcopy(manager.config_values),
        required=bool(getattr(manager, "required", False)),
    )
    if parent_plugin:
        parent_plugin.sub_plugins.add(plugin)

    _plugins[plugin_id] = plugin
    return plugin


def revoke_plugin(plugin: "Plugin") -> None:
    """Remove owned registrations after failure or after host drain.

    This does not undo source side effects or unload Python modules. A failed
    or changed executable installation must be replaced on a fresh host.
    """
    if _plugins.get(plugin.id_) is not plugin:
        return
    errors = []
    for child in reversed(tuple(_plugins.values())):
        if child.parent_plugin is plugin:
            try:
                revoke_plugin(child)
            except Exception as error:
                errors.append(error)
    while plugin.cleanups:
        try:
            plugin.cleanups.pop()()
        except Exception as error:
            errors.append(error)
    for matcher in tuple(plugin.matcher):
        try:
            matcher.destroy()
        except ValueError:
            pass
    plugin.matcher.clear()
    del _plugins[plugin.id_]
    if parent_plugin := plugin.parent_plugin:
        parent_plugin.sub_plugins.discard(plugin)
    if errors:
        raise ExceptionGroup("Plugin registration cleanup failed", errors)


def track_registration(
    cleanup: Callable[[], None], owner=None
) -> Callable[[], None]:
    """Attach a reversible registration to its current or defining plugin."""
    plugin = _current_plugin.get()
    if plugin is None and owner is not None:
        module_name = (
            owner.__name__
            if isinstance(owner, ModuleType)
            else getattr(owner, "__module__", "")
        )
        plugin = get_plugin_by_module_name(module_name)
    if plugin is not None:
        plugin.cleanups.append(cleanup)

    def release():
        if plugin is not None and cleanup in plugin.cleanups:
            plugin.cleanups.remove(cleanup)

    return release


@dataclass(frozen=True)
class _HookRegistration(Dependent[Any]):
    """Separate ownership even when two plugins register the same callable."""

    registration_id: object = field(default_factory=object)


def register_hook(group, func, parameters):
    """Parse a hook and retain only its own reversible set insertion."""
    dependent = _HookRegistration.parse(call=func, allow_types=parameters)
    group.add(dependent)
    track_registration(lambda: group.discard(dependent), func)
    return func


def register_callback(group, func):
    """Own one asynchronous channel callback without sharing its identity."""

    @wraps(func)
    async def registered(*args, **kwargs):
        return await func(*args, **kwargs)

    group.add(registered)
    track_registration(lambda: group.discard(registered), func)
    return func


def get_plugin(plugin_id: str) -> "Plugin | None":
    """Return a loaded plugin by its identifier.

    Directory-loaded plugins use the file or directory name as their
    identifier.

    Nested plugin identifiers use parent_id:child_name.

    Args:
        plugin_id: The plugin identifier exposed as Plugin.id_.
    """
    return _plugins.get(plugin_id)


def get_plugin_by_module_name(module_name: str) -> "Plugin | None":
    """Return the loaded plugin associated with a module name.

    A plugin submodule resolves to its containing plugin as well.

    Args:
        module_name: The module path exposed as Plugin.module_name.
    """
    loaded = {plugin.module_name: plugin for plugin in _plugins.values()}
    has_parent = True
    while has_parent:
        if module_name in loaded:
            return loaded[module_name]
        module_name, *has_parent = module_name.rsplit(".", 1)


def get_loaded_plugins() -> set["Plugin"]:
    """Return all currently loaded plugins."""
    return set(_plugins.values())


def get_plugins_in_dependency_order() -> list["Plugin"]:
    """Return loaded plugins with dependencies before their dependents.

    Raises:
        RuntimeError: The recorded dependency graph contains a cycle.
    """
    by_id = {plugin.id_: plugin for plugin in _plugins.values()}
    ordered: list[Plugin] = []
    visited: set[str] = set()
    visiting: list[str] = []

    def visit(plugin: Plugin) -> None:
        if plugin.id_ in visited:
            return
        if plugin.id_ in visiting:
            cycle = " -> ".join((*visiting, plugin.id_))
            raise RuntimeError(f"Plugin dependency cycle: {cycle}")
        visiting.append(plugin.id_)
        try:
            for dependency_id in plugin.dependencies:
                dependency = by_id.get(dependency_id)
                if dependency is not None:
                    visit(dependency)
        finally:
            visiting.pop()
        visited.add(plugin.id_)
        ordered.append(plugin)

    for plugin in _plugins.values():
        visit(plugin)
    return ordered


def get_available_plugin_names() -> set[str]:
    """Return available plugin identifiers, including unloaded plugins."""
    return {
        *chain.from_iterable(manager.available_plugins for manager in _managers)
    }


def register_plugin_manager(manager: "PluginManager") -> None:
    """Pre-register a manager without importing any of its modules.

    Hosts use this to install the complete startup snapshot before loading an
    entry, so dependencies resolve to their declared configuration.

    Args:
        manager: The manager whose controlled modules become loadable.
    """
    if manager not in _managers:
        _managers.append(manager)
