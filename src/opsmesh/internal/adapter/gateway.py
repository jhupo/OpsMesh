import abc
from functools import partial
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

import anyio

from opsmesh.config import Config
from opsmesh.dependencies import Dependent
from opsmesh.exception import MockInvokeResult
from opsmesh.internal.params import DefaultParam, DependParam, GatewayParam
from opsmesh.log import logger
from opsmesh.typing import (
    T_AfterInvokeHook,
    T_BeforeInvokeHook,
    T_ConnectHook,
    T_DisconnectHook,
)
from opsmesh.utils import flatten_exception_group

if TYPE_CHECKING:
    from .adapter import Adapter
    from .event import Event
    from .message import Message, MessageSegment
    from .template import MessageTemplate

    class _ActionCall(Protocol):
        async def __call__(self, **params: Any) -> Any: ...


CONNECTION_HOOK_PARAMS = (DependParam, GatewayParam, DefaultParam)


class Gateway(abc.ABC):
    """适配器管理的网关实例。事件回复和出站动作调用由该实例交给适配器处理。

    参数:
        adapter: 协议适配器实例
        id: 在当前 Driver 中唯一的网关实例标识
    """

    _connect_hooks: ClassVar[set[Dependent[Any]]] = set()
    """网关登记时执行的钩子函数"""
    _disconnect_hooks: ClassVar[set[Dependent[Any]]] = set()
    """网关注销时执行的钩子函数"""
    _before_invoke_hooks: ClassVar[set[T_BeforeInvokeHook]] = set()
    """`invoke` 前执行的钩子函数"""
    _after_invoke_hooks: ClassVar[set[T_AfterInvokeHook]] = set()
    """`invoke` 后执行的钩子函数"""

    def __init__(self, adapter: "Adapter", id: str):
        self.adapter: "Adapter" = adapter
        """协议适配器实例"""
        self.id: str = id
        """网关实例标识"""

    def __repr__(self) -> str:
        return f"Gateway(type={self.type!r}, id={self.id!r})"

    def __getattr__(self, action: str) -> "_ActionCall":
        if action.startswith("__") and action.endswith("__"):
            raise AttributeError(
                f"'{self.__class__.__name__}' object has no attribute '{action}'"
            )
        return partial(self.invoke, action)

    @property
    def type(self) -> str:
        """协议适配器名称"""
        return self.adapter.get_name()

    @property
    def config(self) -> Config:
        """全局 OpsMesh 配置"""
        return self.adapter.config

    def connect(self) -> None:
        """将当前网关实例登记到所属适配器与驱动器。"""
        self.adapter.connect(self)

    def disconnect(self) -> None:
        """将当前网关实例从所属适配器与驱动器注销。"""
        self.adapter.disconnect(self)

    async def invoke(self, action: str, **params: Any) -> Any:
        """调用适配器提供的出站动作，可直接调用本方法或通过网关同名属性调用。

        参数:
            action: 动作名称
            params: 动作参数

        用法:
            ```python
            await gateway.invoke("publish", topic="sys/ready")
            await gateway.publish(topic="sys/ready")
            ```
        """
        result: Any = None
        skip_invoke: bool = False
        exception: Exception | None = None

        if self._before_invoke_hooks:
            logger.debug("Running before_invoke hooks...")
            try:
                async with anyio.create_task_group() as tg:
                    for hook in self._before_invoke_hooks:
                        tg.start_soon(hook, self, action, params)
            except* MockInvokeResult as exc_group:
                excs = list(flatten_exception_group(exc_group))
                if len(excs) > 1:
                    logger.warning(
                        "Multiple hooks want to mock invoke result. Use the first one."
                    )
                skip_invoke = True
                result = excs[0].result
                logger.debug(
                    f"Invoking action {action} is cancelled. Return {result!r} instead."
                )
            except* Exception as exc_group:
                for exc in flatten_exception_group(exc_group):
                    logger.opt(colors=True, exception=exc).error(
                        "<r><bg #f8bbd0>Error when running before_invoke hook. "
                        "Running cancelled!</bg #f8bbd0></r>"
                    )

        if not skip_invoke:
            try:
                result = await self.adapter._invoke(self, action, **params)
            except Exception as e:
                exception = e

        if self._after_invoke_hooks:
            logger.debug("Running after_invoke hooks...")
            try:
                async with anyio.create_task_group() as tg:
                    for hook in self._after_invoke_hooks:
                        tg.start_soon(hook, self, exception, action, params, result)
            except* MockInvokeResult as exc_group:
                excs = list(flatten_exception_group(exc_group))
                if len(excs) > 1:
                    logger.warning(
                        "Multiple hooks want to mock invoke result. Use the first one."
                    )
                result = excs[0].result
                exception = None
                logger.debug(
                    f"Invoking action {action} result is mocked. "
                    f"Return {result!r} instead."
                )
            except* Exception as exc_group:
                for exc in flatten_exception_group(exc_group):
                    logger.opt(colors=True, exception=exc).error(
                        "<r><bg #f8bbd0>Error when running after_invoke hook. "
                        "Running cancelled!</bg #f8bbd0></r>"
                    )

        if exception:
            raise exception
        return result

    @abc.abstractmethod
    async def send(
        self,
        event: "Event",
        message: "str | Message | MessageSegment | MessageTemplate",
        **kwargs: Any,
    ) -> Any:
        """通过当前网关向指定事件发送响应消息。"""
        raise NotImplementedError

    @classmethod
    def on_connect(cls, func: T_ConnectHook) -> T_ConnectHook:
        """注册网关登记时执行的钩子函数。"""
        cls._connect_hooks.add(
            Dependent[Any].parse(call=func, allow_types=CONNECTION_HOOK_PARAMS)
        )
        return func

    @classmethod
    def on_disconnect(cls, func: T_DisconnectHook) -> T_DisconnectHook:
        """注册网关注销时执行的钩子函数。"""
        cls._disconnect_hooks.add(
            Dependent[Any].parse(call=func, allow_types=CONNECTION_HOOK_PARAMS)
        )
        return func

    @classmethod
    def before_invoke(cls, func: T_BeforeInvokeHook) -> T_BeforeInvokeHook:
        """注册动作调用前置钩子（参数: gateway, action, params）。"""
        cls._before_invoke_hooks.add(func)
        return func

    @classmethod
    def after_invoke(cls, func: T_AfterInvokeHook) -> T_AfterInvokeHook:
        """注册动作调用后置钩子（参数: gateway, exception, action, params, result）。"""
        cls._after_invoke_hooks.add(func)
        return func
