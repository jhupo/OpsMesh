"""本模块实现插件加载流程。

参考: [import hooks](https://docs.python.org/3/reference/import.html#import-hooks), [PEP302](https://www.python.org/dev/peps/pep-0302/)

FrontMatter:
    mdx:
        format: md
    sidebar_position: 5
    description: opsmesh.plugin.manager 模块
"""

from collections.abc import Iterable, Sequence
import importlib
from importlib.abc import MetaPathFinder
from importlib.machinery import PathFinder, SourceFileLoader
from itertools import chain
from pathlib import Path
import pkgutil
import sys
from types import ModuleType

from opsmesh.log import logger
from opsmesh.utils import escape_tag, path_to_module_name

from . import (
    _current_plugin,
    _managers,
    _module_name_to_plugin_id,
    _new_plugin,
    _revert_plugin,
    get_loaded_plugins,
    get_plugin,
)
from .model import Plugin, PluginMetadata


class PluginManager:
    """插件管理器。

    参数:
        plugins: 独立插件模块名集合。
        search_path: 插件搜索路径（文件夹），相对于当前工作目录。
    """

    def __init__(
        self,
        plugins: Iterable[str] | None = None,
        search_path: Iterable[str] | None = None,
    ):
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
        """返回所有独立插件标识符。"""
        return set(self._third_party_plugin_ids.keys())

    @property
    def searched_plugins(self) -> set[str]:
        """返回已搜索到的插件标识符。"""
        return set(self._searched_plugin_ids.keys())

    @property
    def available_plugins(self) -> set[str]:
        """返回当前插件管理器中可用的插件标识符。"""
        return self.third_party_plugins | self.searched_plugins

    @property
    def controlled_modules(self) -> dict[str, str]:
        """返回当前插件管理器中控制的插件标识符与模块路径映射字典。"""
        return dict(
            chain(
                self._third_party_plugin_ids.items(), self._searched_plugin_ids.items()
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
        """搜索插件并缓存插件名称。"""
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
            plugin_id = _module_name_to_plugin_id(plugin, get_controlled_modules())
            if (
                plugin_id in self._third_party_plugin_ids
                or plugin_id in previous_plugin_ids
            ):
                raise RuntimeError(
                    f"Plugin already exists: {plugin_id}! Check your plugin name"
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

            # get module name from path, pkgutil does not return the actual module name
            module_path = Path(module_spec.origin).resolve()
            module_name = path_to_module_name(module_path)
            plugin_id = _module_name_to_plugin_id(module_name, get_controlled_modules())

            if (
                plugin_id in previous_plugin_ids
                or plugin_id in self._third_party_plugin_ids
                or plugin_id in self._searched_plugin_ids
            ):
                raise RuntimeError(
                    f"Plugin already exists: {plugin_id}! Check your plugin name"
                )

            self._searched_plugin_ids[plugin_id] = module_name

        return self.available_plugins

    def load_plugin(self, name: str) -> Plugin | None:
        """加载指定插件。

        可以使用完整插件模块名或者插件标识符加载。

        参数:
            name: 插件名称或插件标识符。
        """

        try:
            # load using plugin id
            if name in self._third_party_plugin_ids:
                module = importlib.import_module(self._third_party_plugin_ids[name])
            elif name in self._searched_plugin_ids:
                module = importlib.import_module(self._searched_plugin_ids[name])
            # load using module name
            elif (
                name in self._third_party_plugin_ids.values()
                or name in self._searched_plugin_ids.values()
            ):
                module = importlib.import_module(name)
            else:
                raise RuntimeError(f"Plugin not found: {name}! Check your plugin name")

            if (
                plugin := getattr(module, "__plugin__", None)
            ) is None or not isinstance(plugin, Plugin):
                raise RuntimeError(
                    f"Module {module.__name__} is not loaded as a plugin! "
                    f"Make sure not to import it before loading."
                )
            logger.opt(colors=True).success(
                f'Succeeded to load plugin "<y>{escape_tag(plugin.id_)}</y>"'
                + (
                    f' from "<m>{escape_tag(plugin.module_name)}</m>"'
                    if plugin.module_name != plugin.id_
                    else ""
                )
            )
            return plugin
        except Exception as e:
            logger.opt(colors=True, exception=e).error(
                f'<r><bg #f8bbd0>Failed to import "{escape_tag(name)}"</bg #f8bbd0></r>'
            )

    def _check_owner(self, plugin: Plugin) -> None:
        if plugin.manager is not self or get_plugin(plugin.id_) is not plugin:
            raise RuntimeError(f"Plugin {plugin.id_} is not loaded by this manager")

    def enable_plugin(self, plugin: Plugin) -> None:
        self._check_owner(plugin)
        plugin.enabled = True

    def disable_plugin(self, plugin: Plugin) -> None:
        self._check_owner(plugin)
        if plugin.metadata and plugin.metadata.protected:
            raise RuntimeError(f"Protected plugin: {plugin.id_}")
        plugin.enabled = False

    def unload_plugin(self, plugin: Plugin) -> None:
        """解除已停用插件的 Matcher、模块缓存及加载登记，不删除制品文件。

        调用方负责先结束在途处理、释放外部资源和其他模块持有的引用。
        """
        from opsmesh.matcher import matchers

        self._check_owner(plugin)
        if plugin.metadata and plugin.metadata.protected:
            raise RuntimeError(f"Protected plugin: {plugin.id_}")
        if plugin.enabled:
            raise RuntimeError(f"Disable plugin {plugin.id_} before unloading it")
        name = plugin.module_name
        prefix = name + "."
        if plugin.sub_plugins or any(p.module_name.startswith(prefix) for p in get_loaded_plugins()):
            raise RuntimeError(f"Unload child plugins of {name} first")
        if sys.modules.get(name) is not plugin.module:
            raise RuntimeError(f"Module binding changed for {name}")

        # 会话续接和运行时创建的 Matcher 不一定在 plugin.matcher 中。
        for group in matchers.values():
            for matcher in tuple(group):
                if matcher in plugin.matcher or matcher.plugin is plugin:
                    matcher.destroy()
        plugin.matcher.clear()
        plugin_id = plugin.id_
        _revert_plugin(plugin)
        self.plugins.discard(name)
        self._third_party_plugin_ids.pop(plugin_id, None)
        self._searched_plugin_ids.pop(plugin_id, None)
        if not self.controlled_modules:
            _managers.remove(self)

        # 只清理精确的包命名空间，不能误删名称相似的兄弟插件。
        modules = [key for key in sys.modules if key == name or key.startswith(prefix)]
        for key in sorted(modules, key=len, reverse=True):
            module = sys.modules.pop(key)
            parent_name, _, attr = key.rpartition(".")
            parent = sys.modules.get(parent_name)
            if parent is not None and attr in vars(parent) and vars(parent)[attr] is module:
                delattr(parent, attr)
        importlib.invalidate_caches()

    def load_all_plugins(self) -> set[Plugin]:
        """加载所有可用插件。"""

        return set(
            filter(None, (self.load_plugin(name) for name in self.available_plugins))
        )


class PluginFinder(MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ):
        if _managers:
            module_spec = PathFinder.find_spec(fullname, path, target)
            if not module_spec:
                return
            module_origin = module_spec.origin
            if not module_origin:
                return

            for manager in reversed(_managers):
                if fullname in manager.controlled_modules.values():
                    module_spec.loader = PluginLoader(manager, fullname, module_origin)
                    return module_spec
        return


class PluginLoader(SourceFileLoader):
    def __init__(self, manager: PluginManager, fullname: str, path: str) -> None:
        self.manager = manager
        self.loaded = False
        super().__init__(fullname, path)

    def create_module(self, spec) -> ModuleType | None:
        if self.name in sys.modules:
            self.loaded = True
            return sys.modules[self.name]
        # return None to use default module creation
        return super().create_module(spec)

    def exec_module(self, module: ModuleType) -> None:
        if self.loaded:
            return

        # create plugin before executing
        plugin = _new_plugin(self.name, module, self.manager)
        setattr(module, "__plugin__", plugin)

        # enter plugin context
        _plugin_token = _current_plugin.set(plugin)

        try:
            super().exec_module(module)
        except Exception:
            _revert_plugin(plugin)
            raise
        finally:
            # leave plugin context
            _current_plugin.reset(_plugin_token)

        # get plugin metadata
        metadata: PluginMetadata | None = getattr(module, "__plugin_meta__", None)
        plugin.metadata = metadata

        return


sys.meta_path.insert(0, PluginFinder())
