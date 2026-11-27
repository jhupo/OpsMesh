"""本模块定义插件加载接口。

FrontMatter:
    mdx:
        format: md
    sidebar_position: 1
    description: opsmesh.plugin.load 模块
"""

from collections.abc import Iterable
import json
from pathlib import Path
import tomllib
from types import ModuleType

from opsmesh.utils import path_to_module_name

from . import _managers, _module_name_to_plugin_id, get_plugin
from .manager import PluginManager
from .model import Plugin


def load_plugin(module_path: str | Path) -> Plugin | None:
    """加载单个插件，可以是本地插件或是通过 `pip` 安装的插件。

    参数:
        module_path: 插件名称 `path.to.your.plugin`
            或插件路径 `pathlib.Path(path/to/your/plugin)`
    """
    module_path = (
        path_to_module_name(module_path)
        if isinstance(module_path, Path)
        else module_path
    )
    manager = PluginManager([module_path])
    _managers.append(manager)
    return manager.load_plugin(module_path)


def load_plugins(*plugin_dir: str) -> set[Plugin]:
    """导入文件夹下多个插件，以 `_` 开头的插件不会被导入!

    参数:
        plugin_dir: 文件夹路径
    """
    manager = PluginManager(search_path=plugin_dir)
    _managers.append(manager)
    return manager.load_all_plugins()


def load_all_plugins(
    module_path: Iterable[str], plugin_dir: Iterable[str]
) -> set[Plugin]:
    """导入指定列表中的插件以及指定目录下多个插件，以 `_` 开头的插件不会被导入!

    参数:
        module_path: 指定插件集合
        plugin_dir: 指定文件夹路径集合
    """
    manager = PluginManager(module_path, plugin_dir)
    _managers.append(manager)
    return manager.load_all_plugins()


def load_from_json(file_path: str | Path, encoding: str = "utf-8") -> set[Plugin]:
    """导入指定 json 文件中的 `plugins` 以及 `plugin_dirs` 下多个插件。
    以 `_` 开头的插件不会被导入!

    参数:
        file_path: 指定 json 文件路径
        encoding: 指定 json 文件编码

    用法:
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
    if not isinstance(plugins, list):
        raise TypeError("plugins must be a list of plugin name")
    if not isinstance(plugin_dirs, list):
        raise TypeError("plugin_dirs must be a list of directories")
    return load_all_plugins(set(plugins), set(plugin_dirs))


def load_from_toml(file_path: str | Path, encoding: str = "utf-8") -> set[Plugin]:
    """导入指定 toml 文件 `[tool.opsmesh]` 中的
    `plugins` 以及 `plugin_dirs` 下多个插件。
    以 `_` 开头的插件不会被导入!

    参数:
        file_path: 指定 toml 文件路径
        encoding: 指定 toml 文件编码

    用法:
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

    opsmesh_data = data.get("tool", {}).get("opsmesh")
    if opsmesh_data is None:
        raise ValueError("Cannot find '[tool.opsmesh]' in given toml file!")
    if not isinstance(opsmesh_data, dict):
        raise TypeError("'[tool.opsmesh]' must be a Table!")
    plugins = opsmesh_data.get("plugins", [])
    plugin_dirs = opsmesh_data.get("plugin_dirs", [])
    if not isinstance(plugins, list):
        raise TypeError("plugins must be a list of plugin name")
    if not isinstance(plugin_dirs, list):
        raise TypeError("plugin_dirs must be a list of directories")
    return load_all_plugins(set(plugins), set(plugin_dirs))


def load_builtin_plugin(name: str) -> Plugin | None:
    """导入 OpsMesh 内置插件。

    参数:
        name: 插件名称
    """
    return load_plugin(f"opsmesh.plugins.{name}")


def load_builtin_plugins(*plugins: str) -> set[Plugin]:
    """导入多个 OpsMesh 内置插件。

    参数:
        plugins: 插件名称列表
    """
    return load_all_plugins([f"opsmesh.plugins.{p}" for p in plugins], [])


def _find_manager_by_name(name: str) -> PluginManager | None:
    for manager in reversed(_managers):
        if (
            name in manager.controlled_modules
            or name in manager.controlled_modules.values()
        ):
            return manager


def require(name: str) -> ModuleType:
    """声明依赖插件。

    参数:
        name: 插件模块名或插件标识符，仅在已声明插件的情况下可使用标识符。

    异常:
        RuntimeError: 插件无法加载
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
    return plugin.module


def inherit_supported_adapters(*names: str) -> set[str] | None:
    """获取已加载插件的适配器支持状态集合。

    如果传入了多个插件名称，返回值会自动取交集。

    参数:
        names: 插件名称列表。

    异常:
        RuntimeError: 插件未加载
        ValueError: 插件缺少元数据
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
            f"opsmesh.adapters.{adapter[1:]}" if adapter.startswith("~") else adapter
            for adapter in raw
        }

        final_supported = (
            support if final_supported is None else (final_supported & support)
        )

    return final_supported
