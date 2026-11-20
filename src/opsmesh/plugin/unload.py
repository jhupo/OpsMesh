"""插件卸载入口。"""

from pathlib import Path

from opsmesh.utils import path_to_module_name

from . import get_plugin, get_plugin_by_module_name
from .model import Plugin


def unload_plugin(plugin: Plugin | str | Path) -> None:
    """按插件对象、插件标识符、模块名或路径卸载已停用的插件。"""
    if not isinstance(plugin, Plugin):
        name = path_to_module_name(plugin) if isinstance(plugin, Path) else plugin
        found = get_plugin(name) or get_plugin_by_module_name(name)
        if found is None or name not in (found.id_, found.module_name):
            raise RuntimeError(f"Plugin not loaded: {name}")
        plugin = found
    plugin.manager.unload_plugin(plugin)
