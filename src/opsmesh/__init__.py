"""本模块提供 OpsMesh 初始化、启动和网关查询接口。

## 快捷导入

为方便使用，本模块从子模块导入了部分内容，以下内容可以直接通过本模块导入:

- `on` => {ref}``on` <opsmesh.plugin.on.on>`
- `on_metaevent` => {ref}``on_metaevent` <opsmesh.plugin.on.on_metaevent>`
- `on_message` => {ref}``on_message` <opsmesh.plugin.on.on_message>`
- `on_notice` => {ref}``on_notice` <opsmesh.plugin.on.on_notice>`
- `on_request` => {ref}``on_request` <opsmesh.plugin.on.on_request>`
- `on_startswith` => {ref}``on_startswith` <opsmesh.plugin.on.on_startswith>`
- `on_endswith` => {ref}``on_endswith` <opsmesh.plugin.on.on_endswith>`
- `on_fullmatch` => {ref}``on_fullmatch` <opsmesh.plugin.on.on_fullmatch>`
- `on_keyword` => {ref}``on_keyword` <opsmesh.plugin.on.on_keyword>`
- `on_command` => {ref}``on_command` <opsmesh.plugin.on.on_command>`
- `on_shell_command` => {ref}``on_shell_command` <opsmesh.plugin.on.on_shell_command>`
- `on_regex` => {ref}``on_regex` <opsmesh.plugin.on.on_regex>`
- `on_type` => {ref}``on_type` <opsmesh.plugin.on.on_type>`
- `CommandGroup` => {ref}``CommandGroup` <opsmesh.plugin.on.CommandGroup>`
- `Matchergroup` => {ref}``MatcherGroup` <opsmesh.plugin.on.MatcherGroup>`
- `load_plugin` => {ref}``load_plugin` <opsmesh.plugin.load.load_plugin>`
- `load_plugins` => {ref}``load_plugins` <opsmesh.plugin.load.load_plugins>`
- `load_all_plugins` => {ref}``load_all_plugins` <opsmesh.plugin.load.load_all_plugins>`
- `load_from_json` => {ref}``load_from_json` <opsmesh.plugin.load.load_from_json>`
- `load_from_toml` => {ref}``load_from_toml` <opsmesh.plugin.load.load_from_toml>`
- `load_builtin_plugin` =>
  {ref}``load_builtin_plugin` <opsmesh.plugin.load.load_builtin_plugin>`
- `load_builtin_plugins` =>
  {ref}``load_builtin_plugins` <opsmesh.plugin.load.load_builtin_plugins>`
- `get_plugin` => {ref}``get_plugin` <opsmesh.plugin.get_plugin>`
- `get_plugin_by_module_name` =>
  {ref}``get_plugin_by_module_name` <opsmesh.plugin.get_plugin_by_module_name>`
- `get_loaded_plugins` =>
  {ref}``get_loaded_plugins` <opsmesh.plugin.get_loaded_plugins>`
- `get_available_plugin_names` =>
  {ref}``get_available_plugin_names` <opsmesh.plugin.get_available_plugin_names>`
- `get_plugin_config` => {ref}``get_plugin_config` <opsmesh.plugin.get_plugin_config>`
- `require` => {ref}``require` <opsmesh.plugin.load.require>`

FrontMatter:
    mdx:
        format: md
    sidebar_position: 0
    description: opsmesh 模块
"""

from importlib.metadata import version
import os
from typing import Any, TypeVar, overload

import loguru

from opsmesh.adapters import Adapter, Gateway
from opsmesh.compat import model_dump
from opsmesh.config import DOTENV_TYPE, Config, Env
from opsmesh.drivers import ASGIMixin, Driver, combine_driver
from opsmesh.log import logger as logger
from opsmesh.utils import escape_tag, resolve_dot_notation

try:
    __version__ = version("opsmesh")
except Exception:  # pragma: no cover
    __version__ = None

A = TypeVar("A", bound=Adapter)

_driver: Driver | None = None


def get_driver() -> Driver:
    """获取全局 {ref}`opsmesh.drivers.Driver` 实例。

    可用于在计划任务的回调等情形中获取当前 {ref}`opsmesh.drivers.Driver` 实例。

    返回:
        全局 {ref}`opsmesh.drivers.Driver` 对象

    异常:
        ValueError: 全局 {ref}`opsmesh.drivers.Driver` 对象尚未初始化
            ({ref}`opsmesh.init <opsmesh.init>` 尚未调用)

    用法:
        ```python
        driver = opsmesh.get_driver()
        ```
    """
    if _driver is None:
        raise ValueError("OpsMesh has not been initialized.")
    return _driver


@overload
def get_adapter(name: str) -> Adapter:
    """
    参数:
        name: 适配器名称

    返回:
        指定名称的 {ref}`opsmesh.adapters.Adapter` 对象
    """


@overload
def get_adapter(name: type[A]) -> A:
    """
    参数:
        name: 适配器类型

    返回:
        指定类型的 {ref}`opsmesh.adapters.Adapter` 对象
    """


def get_adapter(name: str | type[Adapter]) -> Adapter:
    """获取已注册的 {ref}`opsmesh.adapters.Adapter` 实例。

    异常:
        ValueError: 指定的 {ref}`opsmesh.adapters.Adapter` 未注册
        ValueError: 全局 {ref}`opsmesh.drivers.Driver` 对象尚未初始化
            ({ref}`opsmesh.init <opsmesh.init>` 尚未调用)

    用法:
        ```python
        from opsmesh.adapters.console import Adapter
        adapter = opsmesh.get_adapter(Adapter)
        ```
    """
    adapters = get_adapters()
    target = name if isinstance(name, str) else name.get_name()
    if target not in adapters:
        raise ValueError(f"Adapter {target} not registered.")
    return adapters[target]


def get_adapters() -> dict[str, Adapter]:
    """获取所有已注册的 {ref}`opsmesh.adapters.Adapter` 实例。

    返回:
        所有 {ref}`opsmesh.adapters.Adapter` 实例字典

    异常:
        ValueError: 全局 {ref}`opsmesh.drivers.Driver` 对象尚未初始化
            ({ref}`opsmesh.init <opsmesh.init>` 尚未调用)

    用法:
        ```python
        adapters = opsmesh.get_adapters()
        ```
    """
    return get_driver()._adapters.copy()


def get_app() -> Any:
    """获取全局 {ref}`opsmesh.drivers.ASGIMixin` 对应的 Server App 对象。

    返回:
        Server App 对象

    异常:
        AssertionError: 全局 Driver 对象不是 {ref}`opsmesh.drivers.ASGIMixin` 类型
        ValueError: 全局 {ref}`opsmesh.drivers.Driver` 对象尚未初始化
            ({ref}`opsmesh.init <opsmesh.init>` 尚未调用)

    用法:
        ```python
        app = opsmesh.get_app()
        ```
    """
    driver = get_driver()
    assert isinstance(driver, ASGIMixin), "app object is only available for asgi driver"
    return driver.server_app


def get_asgi() -> Any:
    """获取全局 {ref}`opsmesh.drivers.ASGIMixin` 对应的
    [ASGI](https://asgi.readthedocs.io/) 对象。

    返回:
        ASGI 对象

    异常:
        AssertionError: 全局 Driver 对象不是 {ref}`opsmesh.drivers.ASGIMixin` 类型
        ValueError: 全局 {ref}`opsmesh.drivers.Driver` 对象尚未初始化
            ({ref}`opsmesh.init <opsmesh.init>` 尚未调用)

    用法:
        ```python
        asgi = opsmesh.get_asgi()
        ```
    """
    driver = get_driver()
    assert isinstance(driver, ASGIMixin), (
        "asgi object is only available for asgi driver"
    )
    return driver.asgi


def get_gateway(id: str | None = None) -> Gateway:
    """获取一个已登记的 {ref}`opsmesh.adapters.Gateway` 对象。

    当提供 `id` 时，此函数是 `get_gateways()[id]` 的简写；
    当不提供时，返回一个 {ref}`opsmesh.adapters.Gateway`。

    参数:
        id: 用来识别 {ref}`opsmesh.adapters.Gateway` 的
            {ref}`opsmesh.adapters.Gateway.id` 属性

    返回:
        {ref}`opsmesh.adapters.Gateway` 对象

    异常:
        KeyError: 对应 id 的 Gateway 不存在
        ValueError: 没有传入 id 且没有 Gateway 可用
        ValueError: 全局 {ref}`opsmesh.drivers.Driver` 对象尚未初始化
            ({ref}`opsmesh.init <opsmesh.init>` 尚未调用)

    用法:
        ```python
        assert opsmesh.get_gateway("12345") == opsmesh.get_gateways()["12345"]

        another_unspecified_gateway = opsmesh.get_gateway()
        ```
    """
    gateways = get_gateways()
    if id is not None:
        return gateways[id]

    for gateway in gateways.values():
        return gateway

    raise ValueError("There are no gateways to get.")


def get_gateways() -> dict[str, Gateway]:
    """获取所有已登记的 {ref}`opsmesh.adapters.Gateway` 对象。

    返回:
        一个以 {ref}`opsmesh.adapters.Gateway.id` 为键
        {ref}`opsmesh.adapters.Gateway` 对象为值的字典

    异常:
        ValueError: 全局 {ref}`opsmesh.drivers.Driver` 对象尚未初始化
            ({ref}`opsmesh.init <opsmesh.init>` 尚未调用)

    用法:
        ```python
        gateways = opsmesh.get_gateways()
        ```
    """
    return get_driver().gateways


def _resolve_combine_expr(obj_str: str) -> type[Driver]:
    drivers = obj_str.split("+")
    DriverClass = resolve_dot_notation(
        drivers[0], "Driver", default_prefix="opsmesh.drivers."
    )
    if len(drivers) == 1:
        logger.trace(f"Detected driver {DriverClass} with no mixins.")
        return DriverClass
    mixins = [
        resolve_dot_notation(mixin, "Mixin", default_prefix="opsmesh.drivers.")
        for mixin in drivers[1:]
    ]
    logger.trace(f"Detected driver {DriverClass} with mixins {mixins}.")
    return combine_driver(DriverClass, *mixins)


def _log_patcher(record: "loguru.Record"):
    """使用插件标识优化日志展示"""
    record["name"] = (
        plugin.id_
        if (module_name := record["name"])
        and (plugin := get_plugin_by_module_name(module_name))
        else (module_name and module_name.split(".", maxsplit=1)[0])
    )


def init(*, _env_file: DOTENV_TYPE | None = None, **kwargs: Any) -> None:
    """初始化 OpsMesh 以及 全局 {ref}`opsmesh.drivers.Driver` 对象。

    OpsMesh 将会从 .env 文件中读取环境信息，并使用相应的 env 文件配置。

    也可以传入自定义的 `_env_file` 来指定 OpsMesh 从该文件读取配置。

    参数:
        _env_file: 配置文件名，默认从 `.env.{env_name}` 中读取配置
        kwargs: 任意变量，将会存储到 {ref}`opsmesh.drivers.Driver.config` 对象里

    用法:
        ```python
        opsmesh.init(database=Database(...))
        ```
    """
    global _driver
    if not _driver:
        logger.success("OpsMesh is initializing...")
        env = Env()
        _env_file = _env_file or f".env.{env.environment}"
        config = Config(
            **kwargs,
            _env_file=(
                (".env", _env_file)
                if isinstance(_env_file, (str, os.PathLike))
                else _env_file
            ),
        )

        logger.configure(
            extra={"opsmesh_log_level": config.log_level}, patcher=_log_patcher
        )
        logger.opt(colors=True).info(
            f"Current <y><b>Env: {escape_tag(env.environment)}</b></y>"
        )
        logger.opt(colors=True).debug(
            f"Loaded <y><b>Config</b></y>: {escape_tag(str(model_dump(config)))}"
        )

        DriverClass = _resolve_combine_expr(config.driver)
        _driver = DriverClass(env, config)


def run(*args: Any, **kwargs: Any) -> None:
    """启动 OpsMesh，即运行全局 {ref}`opsmesh.drivers.Driver` 对象。

    参数:
        args: 传入 {ref}`opsmesh.drivers.Driver.run` 的位置参数
        kwargs: 传入 {ref}`opsmesh.drivers.Driver.run` 的命名参数

    用法:
        ```python
        opsmesh.run(host="127.0.0.1", port=8080)
        ```
    """
    from opsmesh.plugin.config import prepare_plugins

    driver = get_driver()
    prepare_plugins(driver, get_loaded_plugins(), get_adapters().keys())
    logger.success("Running OpsMesh...")
    driver.run(*args, **kwargs)


from opsmesh.plugin import CommandGroup as CommandGroup
from opsmesh.plugin import MatcherGroup as MatcherGroup
from opsmesh.plugin import get_available_plugin_names as get_available_plugin_names
from opsmesh.plugin import get_loaded_plugins as get_loaded_plugins
from opsmesh.plugin import get_plugin as get_plugin
from opsmesh.plugin import get_plugin_by_module_name as get_plugin_by_module_name
from opsmesh.plugin import get_plugin_config as get_plugin_config
from opsmesh.plugin import load_all_plugins as load_all_plugins
from opsmesh.plugin import load_builtin_plugin as load_builtin_plugin
from opsmesh.plugin import load_builtin_plugins as load_builtin_plugins
from opsmesh.plugin import load_from_json as load_from_json
from opsmesh.plugin import load_from_toml as load_from_toml
from opsmesh.plugin import load_plugin as load_plugin
from opsmesh.plugin import load_plugins as load_plugins
from opsmesh.plugin import unload_plugin as unload_plugin
from opsmesh.plugin import on as on
from opsmesh.plugin import on_command as on_command
from opsmesh.plugin import on_endswith as on_endswith
from opsmesh.plugin import on_fullmatch as on_fullmatch
from opsmesh.plugin import on_keyword as on_keyword
from opsmesh.plugin import on_message as on_message
from opsmesh.plugin import on_metaevent as on_metaevent
from opsmesh.plugin import on_notice as on_notice
from opsmesh.plugin import on_regex as on_regex
from opsmesh.plugin import on_request as on_request
from opsmesh.plugin import on_shell_command as on_shell_command
from opsmesh.plugin import on_startswith as on_startswith
from opsmesh.plugin import on_type as on_type
from opsmesh.plugin import require as require
