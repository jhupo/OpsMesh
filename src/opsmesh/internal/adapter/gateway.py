import abc
from functools import partial
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

import anyio
from exceptiongroup import BaseExceptionGroup, catch

from opsmesh.config import Config
from opsmesh.exception import MockApiException
from opsmesh.log import logger
from opsmesh.typing import T_CalledHook, T_CallingHook
from opsmesh.utils import flatten_exception_group

if TYPE_CHECKING:
    from .adapter import Adapter
    from .event import Event
    from .message import Message, MessageSegment

    class _ApiCall(Protocol):
        async def __call__(self, **kwargs: Any) -> Any: ...


class Gateway(abc.ABC):
    """适配器管理的事件来源实例。事件回复和协议 API 调用由该实例交给适配器处理。

    参数:
        adapter: 协议适配器实例
        id: 在当前 Driver 中唯一的网关实例标识，不是单次事件的 ID
        owner_plugin_id: 专属网关的使用方插件 ID；None 表示共享网关
    """

    _calling_hooks: ClassVar[set[T_CallingHook]] = set()
    """call_api 时执行的函数"""
    _called_hooks: ClassVar[set[T_CalledHook]] = set()
    """call_api 后执行的函数"""

    def __init__(
        self, adapter: "Adapter", id: str, *, owner_plugin_id: str | None = None
    ):
        self.adapter: "Adapter" = adapter
        """协议适配器实例"""
        self.id: str = id
        """网关实例标识"""
        self.owner_plugin_id = owner_plugin_id

    def __repr__(self) -> str:
        return f"Gateway(type={self.type!r}, id={self.id!r})"

    def __getattr__(self, name: str) -> "_ApiCall":
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(
                f"'{self.__class__.__name__}' object has no attribute '{name}'"
            )
        return partial(self.call_api, name)

    @property
    def type(self) -> str:
        """协议适配器名称"""
        return self.adapter.get_name()

    @property
    def config(self) -> Config:
        """全局 OpsMesh 配置"""
        return self.adapter.config

    def _allows_callback(self, callback: Any) -> bool:
        if self.owner_plugin_id is None:
            return True

        from opsmesh.plugin import get_plugin_by_module_name

        while isinstance(callback, partial):
            callback = callback.func
        plugin = get_plugin_by_module_name(getattr(callback, "__module__", ""))
        return bool(
            plugin and plugin.enabled and plugin.id_ == self.owner_plugin_id
        )

    async def call_api(self, api: str, **data: Any) -> Any:
        """调用适配器提供的协议 API，可直接调用本方法或使用对应的网关属性。

        参数:
            api: API 名称
            data: API 数据

        用法:
            ```python
            await gateway.call_api("send_msg", message="hello world")
            await gateway.send_msg(message="hello world")
            ```
        """

        result: Any = None
        skip_call: bool = False
        exception: Exception | None = None

        if self._calling_hooks:
            logger.debug("Running on_calling hooks...")

            def _handle_mock_api_exception(
                exc_group: BaseExceptionGroup[MockApiException],
            ) -> None:
                nonlocal skip_call, result

                excs = [
                    exc
                    for exc in flatten_exception_group(exc_group)
                    if isinstance(exc, MockApiException)
                ]
                if not excs:
                    return
                elif len(excs) > 1:
                    logger.warning(
                        "Multiple hooks want to mock API result. Use the first one."
                    )

                skip_call = True
                result = excs[0].result

                logger.debug(
                    f"Calling API {api} is cancelled. Return {result!r} instead."
                )

            def _handle_exception(exc_group: BaseExceptionGroup[Exception]) -> None:
                for exc in flatten_exception_group(exc_group):
                    logger.opt(colors=True, exception=exc).error(
                        "<r><bg #f8bbd0>Error when running on_calling hook. "
                        "Running cancelled!</bg #f8bbd0></r>"
                    )

            with catch(
                {
                    MockApiException: _handle_mock_api_exception,
                    Exception: _handle_exception,
                }
            ):
                async with anyio.create_task_group() as tg:
                    for hook in self._calling_hooks:
                        if self._allows_callback(hook):
                            tg.start_soon(hook, self, api, data)

        if not skip_call:
            try:
                result = await self.adapter._call_api(self, api, **data)
            except Exception as e:
                exception = e

        if self._called_hooks:
            logger.debug("Running on_called hooks...")

            def _handle_mock_api_exception(
                exc_group: BaseExceptionGroup[MockApiException],
            ) -> None:
                nonlocal result, exception

                excs = [
                    exc
                    for exc in flatten_exception_group(exc_group)
                    if isinstance(exc, MockApiException)
                ]
                if not excs:
                    return
                elif len(excs) > 1:
                    logger.warning(
                        "Multiple hooks want to mock API result. Use the first one."
                    )

                result = excs[0].result
                exception = None
                logger.debug(
                    f"Calling API {api} result is mocked. Return {result} instead."
                )

            def _handle_exception(exc_group: BaseExceptionGroup[Exception]) -> None:
                for exc in flatten_exception_group(exc_group):
                    logger.opt(colors=True, exception=exc).error(
                        "<r><bg #f8bbd0>Error when running on_called hook. "
                        "Running cancelled!</bg #f8bbd0></r>"
                    )

            with catch(
                {
                    MockApiException: _handle_mock_api_exception,
                    Exception: _handle_exception,
                }
            ):
                async with anyio.create_task_group() as tg:
                    for hook in self._called_hooks:
                        if self._allows_callback(hook):
                            tg.start_soon(
                                hook, self, exception, api, data, result
                            )

        if exception:
            raise exception
        return result

    @abc.abstractmethod
    async def send(
        self,
        event: "Event",
        message: "str | Message | MessageSegment",
        **kwargs: Any,
    ) -> Any:
        """通过当前网关向指定事件发送消息。

        参数:
            event: 上报事件
            message: 要发送的消息
            kwargs: 任意额外参数
        """
        raise NotImplementedError

    @classmethod
    def on_calling(cls, func: T_CallingHook) -> T_CallingHook:
        """调用 api 预处理。

        钩子函数参数:

        - gateway: 当前 gateway 对象
        - api: 调用的 api 名称
        - data: api 调用的参数字典
        """
        cls._calling_hooks.add(func)
        return func

    @classmethod
    def on_called(cls, func: T_CalledHook) -> T_CalledHook:
        """调用 api 后处理。

        钩子函数参数:

        - gateway: 当前 gateway 对象
        - exception: 调用 api 时发生的错误
        - api: 调用的 api 名称
        - data: api 调用的参数字典
        - result: api 调用的返回
        """
        cls._called_hooks.add(func)
        return func
