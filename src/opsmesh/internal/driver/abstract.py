import abc
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self, TypeAlias, TypeVar, cast

from anyio import CancelScope, create_task_group
from anyio.abc import TaskGroup

from opsmesh.config import Config, Env
from opsmesh.exception import SkippedException
from opsmesh.log import logger
from opsmesh.typing import T_DependencyCache
from opsmesh.utils import (
    UNSET,
    UnsetType,
    escape_tag,
    flatten_exception_group,
    run_coro_with_catch,
)

from ._lifespan import LIFESPAN_FUNC, Lifespan
from .model import (
    CookieTypes,
    HeaderTypes,
    HTTPServerSetup,
    HTTPVersion,
    QueryTypes,
    Request,
    Response,
    TimeoutTypes,
    WebSocket,
    WebSocketServerSetup,
)

if TYPE_CHECKING:
    from opsmesh.internal.adapter import Adapter, Gateway

A = TypeVar("A", bound="Adapter")


class Driver(abc.ABC):
    """驱动器基类。

    驱动器控制框架的启动和停止，适配器的注册，以及网关实例的登记和注销。

    参数:
        env: 包含环境信息的 Env 对象
        config: 包含配置信息的 Config 对象
    """

    def __init__(self, env: Env, config: Config):
        self.env: str = env.environment
        """环境名称"""
        self.config: Config = config
        """全局配置对象"""
        self._adapters: dict[str, "Adapter"] = {}
        self._gateways: dict[str, "Gateway"] = {}
        self._lifespan = Lifespan()

    def __repr__(self) -> str:
        return (
            f"Driver(type={self.type!r}, "
            f"adapters={len(self._adapters)}, gateways={len(self._gateways)})"
        )

    @property
    def gateways(self) -> dict[str, "Gateway"]:
        """获取当前已登记的网关"""
        return self._gateways

    @property
    def task_group(self) -> TaskGroup:
        return self._lifespan.task_group

    def register_adapter(self, adapter: type[A], **kwargs: Any) -> A:
        """注册一个协议适配器并返回该适配器实例。

        参数:
            adapter: 适配器类
            kwargs: 其他传递给适配器的参数
        """
        name = adapter.get_name()
        if name in self._adapters:
            logger.opt(colors=True).debug(
                f'Adapter "<y>{escape_tag(name)}</y>" already exists'
            )
            return cast(A, self._adapters[name])
        instance = adapter(self, **kwargs)
        self._adapters[name] = instance
        logger.opt(colors=True).debug(
            f'Succeeded to load adapter "<y>{escape_tag(name)}</y>"'
        )
        return instance

    @property
    @abc.abstractmethod
    def type(self) -> str:
        """驱动类型名称"""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def logger(self):
        """驱动专属 logger 日志记录器"""
        raise NotImplementedError

    @abc.abstractmethod
    def run(self, *args, **kwargs):
        """启动驱动框架"""
        logger.opt(colors=True).success(
            f"<g>Loaded adapters: {escape_tag(', '.join(self._adapters))}</g>"
        )

    def on_startup(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """注册一个启动时执行的函数"""
        return self._lifespan.on_startup(func)

    def on_shutdown(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """注册一个停止时执行的函数"""
        return self._lifespan.on_shutdown(func)

    def on_ready(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register a function to run after startup and ready funcs."""
        return self._lifespan.on_ready(func)

    async def wait_started(self) -> None:
        """Wait for startup and ready funcs, raising their failure."""
        await self._lifespan.wait_started()

    def _register_gateway(self, gateway: "Gateway") -> None:
        """登记一个可用的网关实例"""
        from opsmesh.internal.adapter.gateway import Gateway

        if gateway.id in self._gateways:
            raise RuntimeError(f"Duplicate gateway connection with id {gateway.id}")
        self._gateways[gateway.id] = gateway

        if not Gateway._connect_hooks:
            return

        async def _run_hook(gw: "Gateway") -> None:
            dependency_cache: T_DependencyCache = {}
            with CancelScope(shield=True):
                try:
                    async with AsyncExitStack() as stack, create_task_group() as tg:
                        for hook in Gateway._connect_hooks:
                            tg.start_soon(
                                run_coro_with_catch,
                                hook(
                                    gateway=gw,
                                    stack=stack,
                                    dependency_cache=dependency_cache,
                                ),
                                (SkippedException,),
                            )
                except* Exception as exc_group:
                    for exc in flatten_exception_group(exc_group):
                        logger.opt(colors=True, exception=exc).error(
                            "<r><bg #f8bbd0>"
                            "Error when running Gateway connect hook:"
                            "</bg #f8bbd0></r>"
                        )

        self.task_group.start_soon(_run_hook, gateway)

    def _unregister_gateway(self, gateway: "Gateway") -> None:
        """注销一个网关实例"""
        from opsmesh.internal.adapter.gateway import Gateway

        if gateway.id in self._gateways:
            del self._gateways[gateway.id]

        if not Gateway._disconnect_hooks:
            return

        async def _run_hook(gw: "Gateway") -> None:
            dependency_cache: T_DependencyCache = {}
            with CancelScope(shield=True):
                try:
                    async with AsyncExitStack() as stack, create_task_group() as tg:
                        for hook in Gateway._disconnect_hooks:
                            tg.start_soon(
                                run_coro_with_catch,
                                hook(
                                    gateway=gw,
                                    stack=stack,
                                    dependency_cache=dependency_cache,
                                ),
                                (SkippedException,),
                            )
                except* Exception as exc_group:
                    for exc in flatten_exception_group(exc_group):
                        logger.opt(colors=True, exception=exc).error(
                            "<r><bg #f8bbd0>"
                            "Error when running Gateway disconnect hook:"
                            "</bg #f8bbd0></r>"
                        )

        self.task_group.start_soon(_run_hook, gateway)


class Mixin(abc.ABC):
    """可与其他驱动器共用的混入基类。"""

    @property
    @abc.abstractmethod
    def type(self) -> str:
        """混入驱动类型名称"""
        raise NotImplementedError


class ForwardMixin(Mixin):
    """客户端混入基类。"""


class ReverseMixin(Mixin):
    """服务端混入基类。"""


class HTTPClientSession(abc.ABC):
    """HTTP 客户端会话基类。"""

    @abc.abstractmethod
    def __init__(
        self,
        params: QueryTypes = None,
        headers: HeaderTypes = None,
        cookies: CookieTypes = None,
        version: str | HTTPVersion = HTTPVersion.H11,
        timeout: TimeoutTypes | UnsetType = UNSET,
        proxy: str | None = None,
    ):
        raise NotImplementedError

    @abc.abstractmethod
    async def request(self, setup: Request) -> Response:
        """发送一个 HTTP 请求"""
        raise NotImplementedError

    @abc.abstractmethod
    async def stream_request(
        self,
        setup: Request,
        *,
        chunk_size: int = 1024,
    ) -> AsyncGenerator[Response, None]:
        """发送一个 HTTP 流式请求"""
        raise NotImplementedError
        yield  # used for static type checking's generator detection

    @abc.abstractmethod
    async def setup(self) -> None:
        """初始化会话"""
        raise NotImplementedError

    @abc.abstractmethod
    async def close(self) -> None:
        """关闭会话"""
        raise NotImplementedError

    async def __aenter__(self) -> Self:
        await self.setup()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()


class HTTPClientMixin(ForwardMixin):
    """HTTP 客户端混入基类。"""

    @abc.abstractmethod
    async def request(self, setup: Request) -> Response:
        """发送一个 HTTP 请求"""
        raise NotImplementedError

    @abc.abstractmethod
    async def stream_request(
        self,
        setup: Request,
        *,
        chunk_size: int = 1024,
    ) -> AsyncGenerator[Response, None]:
        """发送一个 HTTP 流式请求"""
        raise NotImplementedError
        yield  # used for static type checking's generator detection

    @abc.abstractmethod
    def get_session(
        self,
        params: QueryTypes = None,
        headers: HeaderTypes = None,
        cookies: CookieTypes = None,
        version: str | HTTPVersion = HTTPVersion.H11,
        timeout: TimeoutTypes | UnsetType = UNSET,
        proxy: str | None = None,
    ) -> HTTPClientSession:
        """获取一个 HTTP 会话"""
        raise NotImplementedError


class WebSocketClientMixin(ForwardMixin):
    """WebSocket 客户端混入基类。"""

    @abc.abstractmethod
    @asynccontextmanager
    async def websocket(self, setup: Request) -> AsyncGenerator[WebSocket, None]:
        """发起一个 WebSocket 连接"""
        raise NotImplementedError
        yield  # used for static type checking's generator detection


class ASGIMixin(ReverseMixin):
    """ASGI 服务端基类。

    将后端框架封装，以满足适配器使用。
    """

    @property
    @abc.abstractmethod
    def server_app(self) -> Any:
        """驱动 APP 对象"""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def asgi(self) -> Any:
        """驱动 ASGI 对象"""
        raise NotImplementedError

    @abc.abstractmethod
    def setup_http_server(self, setup: "HTTPServerSetup") -> None:
        """设置一个 HTTP 服务器路由配置"""
        raise NotImplementedError

    @abc.abstractmethod
    def setup_websocket_server(self, setup: "WebSocketServerSetup") -> None:
        """设置一个 WebSocket 服务器路由配置"""
        raise NotImplementedError


ForwardDriver: TypeAlias = ForwardMixin
ReverseDriver: TypeAlias = ReverseMixin
