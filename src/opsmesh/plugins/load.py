"""Public interfaces for discovering and loading plugins."""

import json
import tomllib
from collections.abc import Iterable, Mapping
from itertools import chain
from pathlib import Path
from types import ModuleType
from typing import Any

from opsmesh.utils import path_to_module_name

from .manager import (
    PluginManager,
    _active_sources,
    get_plugin_manager,
    register_plugin_manager,
)
from .model import Plugin
from .registry import (
    _current_plugin,
    get_plugin,
    get_plugin_by_module_name,
)


def load_plugin(
    module_path: str | Path,
    *,
    config: Mapping[str, Any] | None = None,
    dependencies: Mapping[str, Mapping[str, Any]] | None = None,
) -> Plugin | None:
    """Load a local or installed plugin.

    Args:
        module_path: A dotted plugin module name
            or a pathlib.Path identifying the plugin source.
        config: Host-selected installation settings copied before import.
        dependencies: Settings supplied to dependencies declared by the plugin.
    """
    module_path = (
        path_to_module_name(module_path)
        if isinstance(module_path, Path)
        else module_path
    )
    source = _active_sources().by_module.get(module_path)
    if source is None:
        manager = PluginManager(
            [module_path], config=config, dependencies=dependencies
        )
        register_plugin_manager(manager)
    elif config is not None or dependencies is not None:
        raise RuntimeError(
            f"Configuration is already registered for {module_path}"
        )
    else:
        manager = source.manager
    return manager.load_plugin(module_path)


def load_plugins(*plugin_dir: str) -> set[Plugin]:
    """Load plugins from directories, excluding underscore-prefixed names.

    Args:
        plugin_dir: Directories to search.
    """
    manager = PluginManager(search_path=plugin_dir)
    register_plugin_manager(manager)
    return manager.load_all_plugins()


def load_all_plugins(
    module_path: Iterable[str], plugin_dir: Iterable[str]
) -> set[Plugin]:
    """Load explicit modules and directory plugins, excluding private names.

    Args:
        module_path: The set of explicit plugin modules.
        plugin_dir: The set of directories to search.
    """
    manager = PluginManager(module_path, plugin_dir)
    register_plugin_manager(manager)
    return manager.load_all_plugins()


def load_from_json(file_path: str, encoding: str = "utf-8") -> set[Plugin]:
    """Load plugins and plugin_dirs declared in a JSON file.

    Names starting with an underscore are excluded from directory discovery.

    Args:
        file_path: The JSON configuration path.
        encoding: The JSON file encoding.

    Examples:
        ```json title=plugins.json
        {
            "plugins": ["some_plugin"],
            "plugin_dirs": ["some_dir"]
        }
        ```

        ```python
        opsmesh.load_from_json("plugins.json")
        ```
    """
    with open(file_path, encoding=encoding) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise TypeError("json file must contains a dict!")
    plugins = data.get("plugins")
    plugin_dirs = data.get("plugin_dirs")
    assert isinstance(plugins, list), "plugins must be a list of plugin name"
    assert isinstance(plugin_dirs, list), (
        "plugin_dirs must be a list of directories"
    )
    return load_all_plugins(set(plugins), set(plugin_dirs))


def load_from_toml(file_path: str, encoding: str = "utf-8") -> set[Plugin]:
    """Load plugin declarations from a TOML tool.opsmesh table.

    Both explicit plugins and plugin_dirs are supported.
    Names starting with an underscore are excluded from directory discovery.

    Args:
        file_path: The TOML configuration path.
        encoding: The TOML file encoding.

    Examples:
        Current format:

        ```toml title=pyproject.toml
        [tool.opsmesh]
        plugin_dirs = ["some_dir"]

        [tool.opsmesh.plugins]
        some-store-plugin = ["some_store_plugin"]
        "@local" = ["some_local_plugin"]
        ```

        Legacy format:

        ```toml title=pyproject.toml
        [tool.opsmesh]
        plugins = ["some_plugin"]
        plugin_dirs = ["some_dir"]
        ```

        ```python
        opsmesh.load_from_toml("pyproject.toml")
        ```
    """
    with open(file_path, encoding=encoding) as f:
        data = tomllib.loads(f.read())

    framework_data = data.get("tool", {}).get("opsmesh")
    if framework_data is None:
        raise ValueError("Cannot find '[tool.opsmesh]' in given toml file!")
    if not isinstance(framework_data, dict):
        raise TypeError("'[tool.opsmesh]' must be a Table!")
    plugins = framework_data.get("plugins", {})
    plugin_dirs = framework_data.get("plugin_dirs", [])
    assert isinstance(plugins, (list, dict)), (
        "plugins must be a list or a dict of plugin name"
    )
    assert isinstance(plugin_dirs, list), (
        "plugin_dirs must be a list of directories"
    )
    return load_all_plugins(
        set(
            chain.from_iterable(plugins.values())
            if isinstance(plugins, dict)
            else plugins
        ),
        plugin_dirs,
    )


def _get_loaded_plugin(name: str) -> Plugin | None:
    plugin = get_plugin_by_module_name(name)
    if plugin is not None and plugin.module_name == name:
        return plugin
    return get_plugin(name)


def require(name: str) -> ModuleType:
    """Declare and load a plugin dependency.

    Args:
        name: A module name, or an identifier for an already declared plugin.

    Raises:
        RuntimeError: The plugin could not be loaded.
    """
    plugin = _get_loaded_plugin(name)

    owner = _current_plugin.get()
    if (
        owner is not None
        and owner.ready
        and (plugin is None or plugin.id_ not in owner.dependencies)
    ):
        raise RuntimeError("Declare plugin dependencies before starting")

    # if plugin not loaded
    if plugin is None:
        # plugin already declared, module name / plugin id
        if manager := get_plugin_manager(name):
            if not manager.enabled:
                raise RuntimeError(f'Plugin dependency "{name}" is disabled')
            plugin = manager.load_plugin(name)

        # plugin not declared, try to declare and load it
        else:
            from importlib.metadata import entry_points

            candidates = entry_points(group="opsmesh.plugins", name=name)
            if len(candidates) > 1:
                raise RuntimeError(
                    f"Multiple installed plugins use the name {name!r}"
                )
            module_name = next(iter(candidates)).module if candidates else name
            manager = get_plugin_manager(module_name)
            if manager is not None and not manager.enabled:
                raise RuntimeError(f'Plugin dependency "{name}" is disabled')
            plugin = load_plugin(module_name)

    if plugin is None:
        raise RuntimeError(f'Cannot load plugin "{name}"!')

    if not plugin.enabled:
        raise RuntimeError(f'Plugin dependency "{name}" is disabled')
    if owner is not None and owner is not plugin:
        from .model import PluginStatus

        if (
            owner.status is PluginStatus.READY
            and plugin.id_ not in owner.dependencies
        ):
            raise RuntimeError("Declare plugin dependencies before starting")
        if plugin.id_ not in owner.dependencies:
            owner.dependencies.append(plugin.id_)
    return plugin.module
