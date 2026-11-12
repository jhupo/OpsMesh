"""Public interfaces for discovering and loading plugins."""

import json
import tomllib
from collections.abc import Iterable, Mapping
from itertools import chain
from pathlib import Path
from types import ModuleType
from typing import Any

from opsmesh.framework.utils import path_to_module_name

from .manager import PluginManager
from .model import Plugin
from .registry import (
    _current_plugin,
    _managers,
    _module_name_to_plugin_id,
    get_plugin,
)


def load_plugin(
    module_path: str | Path,
    *,
    config: Mapping[str, Any] | None = None,
    namespace: str | None = None,
) -> Plugin | None:
    """Load a local or installed plugin.

    Args:
        module_path: A dotted plugin module name
            or a pathlib.Path identifying the plugin source.
        config: Host-selected installation settings copied before import.
        namespace: Namespace used by the plugin's get_plugin_config calls.
    """
    module_path = (
        path_to_module_name(module_path)
        if isinstance(module_path, Path)
        else module_path
    )
    manager = PluginManager([module_path], config=config, namespace=namespace)
    _managers.append(manager)
    return manager.load_plugin(module_path)


def load_plugins(*plugin_dir: str) -> set[Plugin]:
    """Load plugins from directories, excluding underscore-prefixed names.

    Args:
        plugin_dir: Directories to search.
    """
    manager = PluginManager(search_path=plugin_dir)
    _managers.append(manager)
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
    _managers.append(manager)
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
        opsmesh.framework.load_from_json("plugins.json")
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
    """Load plugin declarations from a TOML tool.opsmesh.framework table.

    Both explicit plugins and plugin_dirs are supported.
    Names starting with an underscore are excluded from directory discovery.

    Args:
        file_path: The TOML configuration path.
        encoding: The TOML file encoding.

    Examples:
        Current format:

        ```toml title=pyproject.toml
        [tool.opsmesh.framework]
        plugin_dirs = ["some_dir"]

        [tool.opsmesh.framework.plugins]
        some-store-plugin = ["some_store_plugin"]
        "@local" = ["some_local_plugin"]
        ```

        Legacy format:

        ```toml title=pyproject.toml
        [tool.opsmesh.framework]
        plugins = ["some_plugin"]
        plugin_dirs = ["some_dir"]
        ```

        ```python
        opsmesh.framework.load_from_toml("pyproject.toml")
        ```
    """
    with open(file_path, encoding=encoding) as f:
        data = tomllib.loads(f.read())

    framework_data = data.get("tool", {}).get("opsmesh", {}).get("framework")
    if framework_data is None:
        raise ValueError(
            "Cannot find '[tool.opsmesh.framework]' in given toml file!"
        )
    if not isinstance(framework_data, dict):
        raise TypeError("'[tool.opsmesh.framework]' must be a Table!")
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


def _find_manager_by_name(name: str) -> PluginManager | None:
    for manager in reversed(_managers):
        if (
            name in manager.controlled_modules
            or name in manager.controlled_modules.values()
        ):
            return manager


def require(name: str) -> ModuleType:
    """Declare and load a plugin dependency.

    Args:
        name: A module name, or an identifier for an already declared plugin.

    Raises:
        RuntimeError: The plugin could not be loaded.
    """
    if "." in name:
        # name is a module name
        plugin = get_plugin(_module_name_to_plugin_id(name))
    else:
        # name is a plugin id or simple module name (equals to plugin id)
        plugin = get_plugin(name)

    # if plugin not loaded
    if plugin is None:
        # plugin already declared, module name / plugin id
        if manager := _find_manager_by_name(name):
            plugin = manager.load_plugin(name)

        # plugin not declared, try to declare and load it
        else:
            plugin = load_plugin(name)

    if plugin is None:
        raise RuntimeError(f'Cannot load plugin "{name}"!')

    owner = _current_plugin.get()
    if owner is not None and owner is not plugin:
        if plugin.id_ not in owner.dependencies:
            owner.dependencies.append(plugin.id_)
    return plugin.module


def inherit_supported_adapters(*names: str) -> set[str] | None:
    """Return the adapter compatibility shared by loaded plugins.

    When multiple plugin names are supplied, intersect their supported
    adapters.

    Args:
        names: Plugin names.

    Raises:
        RuntimeError: A requested plugin is not loaded.
        ValueError: A requested plugin has no metadata.
    """
    final_supported: set[str] | None = None

    for name in names:
        plugin = get_plugin(_module_name_to_plugin_id(name))
        if plugin is None:
            raise RuntimeError(
                f'Plugin "{name}" is not loaded! You should require it first.'
            )
        meta = plugin.metadata
        if meta is None:
            raise ValueError(f'Plugin "{name}" has no metadata!')

        if (raw := meta.supported_adapters) is None:
            continue

        support = {
            f"opsmesh.framework.adapters.{adapter[1:]}"
            if adapter.startswith("~")
            else adapter
            for adapter in raw
        }

        final_supported = (
            support if final_supported is None else (final_supported & support)
        )

    return final_supported
