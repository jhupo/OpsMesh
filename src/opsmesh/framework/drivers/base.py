"""Driver lifecycle, channel tracking, and transport interfaces."""

import abc
import logging
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from types import TracebackType
from typing import TYPE_CHECKING, Any, ClassVar, Self

from anyio import CancelScope, create_task_group
from anyio.abc import TaskGroup

from opsmesh.framework.config import Config, Env
from opsmesh.framework.dependencies import Dependent
from opsmesh.framework.dependencies.params import (
    ChannelParam,
    DefaultParam,
    DependParam,
)
from opsmesh.framework.exceptions import SkippedException
from opsmesh.framework.lifecycle import LIFESPAN_FUNC, Lifespan
from opsmesh.framework.typing import (
    T_ConnectionHook,
    T_DependencyCache,
    T_DisconnectionHook,
)
from opsmesh.framework.utils import (
    UNSET,
    UnsetType,
    flatten_exception_group,
    run_coro_with_catch,
)

from .models import (
    CookieTypes,
    HeaderTypes,
    HTTPServerSetup,
    HTTPVersion,
    QueryTypes,
    Request,
    Response,
    TimeoutTypes,
    WebSocketServerSetup,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from opsmesh.framework.adapters import Adapter, Channel


CONNECTION_HOOK_PARAMS = [DependParam, ChannelParam, DefaultParam]


class Driver(abc.ABC):
    """Base interface for framework drivers.

    Drivers manage startup, shutdown, adapters, and channel lifecycles.

    Args:
        env: Runtime environment settings.
        config: Framework configuration.
    """

    _connection_hook: ClassVar[set[Dependent[Any]]] = set()
    """Hooks invoked when a channel connects."""
    _disconnection_hook: ClassVar[set[Dependent[Any]]] = set()
    """Hooks invoked when a channel disconnects."""

    def __init__(self, env: Env, config: Config):
        self.env: str = env.environment
        """The runtime environment name."""
        self.config: Config = config.model_copy(deep=True)
        """The driver-owned deployment configuration snapshot."""
        self._adapters: dict[str, Adapter] = {}
        self._channels: dict[str, Channel] = {}
        self._lifespan = Lifespan()

    def __repr__(self) -> str:
        return (
            f"Driver(type={self.type!r}, "
            f"adapters={len(self._adapters)}, channels={len(self._channels)})"
        )

    @property
    def adapters(self) -> dict[str, "Adapter"]:
        """A copy of the adapters owned by this driver instance."""
        return self._adapters.copy()

    @property
    def channels(self) -> dict[str, "Channel"]:
        """A read-only snapshot of the currently connected channels."""
        return self._channels.copy()

    @property
    def task_group(self) -> TaskGroup:
        """The task group active during the driver lifespan."""
        return self._lifespan.task_group

    def register_adapter(self, adapter: type["Adapter"], **kwargs) -> None:
        """Register a protocol adapter.

        Args:
            adapter: The adapter class.
            kwargs: Additional arguments passed to the adapter constructor.
        """
        name = adapter.get_name()
        if name in self._adapters:
            logger.debug(f'Adapter "{name}" already exists')
            return
        self._adapters[name] = adapter(self, **kwargs)
        logger.debug(f'Succeeded to load adapter "{name}"')

    @property
    @abc.abstractmethod
    def type(self) -> str:
        """The driver type name."""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def logger(self):
        """The driver-specific logger."""
        raise NotImplementedError

    @property
    def started(self) -> bool:
        """Whether the driver owns an active lifecycle task group."""
        return self._lifespan.started

    async def startup(self) -> None:
        """Start callbacks and background work in the owning host task."""
        await self._lifespan.startup()

    async def shutdown(self) -> None:
        """Drain driver work before closing host-owned resources."""
        await self._lifespan.shutdown()

    @asynccontextmanager
    async def lifespan(self) -> AsyncGenerator[None, None]:
        """Own startup and shutdown within the same host task."""
        await self.startup()
        try:
            yield
        finally:
            await self.shutdown()

    def on_ready(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register a callback after startup callbacks have completed."""
        return self._lifespan.on_ready(func)

    def on_startup(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register a startup callback."""
        return self._lifespan.on_startup(func)

    def on_shutdown(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register a shutdown callback."""
        return self._lifespan.on_shutdown(func)

    def on_stopping(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        """Register work to drain before plugin resources are closed."""
        return self._lifespan.on_stopping(func)

    @classmethod
    def on_connect(cls, func: T_ConnectionHook) -> T_ConnectionHook:
        """Register a callback for successful channel connections.

        Hook arguments:

        channel: The connected channel instance.
        """
        from opsmesh.framework.plugins.registry import register_hook

        return register_hook(cls._connection_hook, func, CONNECTION_HOOK_PARAMS)

    @classmethod
    def on_disconnect(cls, func: T_DisconnectionHook) -> T_DisconnectionHook:
        """Register a callback for channel disconnections.

        Hook arguments:

        channel: The connected channel instance.
        """
        from opsmesh.framework.plugins.registry import register_hook

        return register_hook(
            cls._disconnection_hook, func, CONNECTION_HOOK_PARAMS
        )

    def connect(self, channel: "Channel") -> None:
        """Register a channel after its connection succeeds."""
        if channel.self_id in self._channels:
            raise RuntimeError(
                f"Duplicate channel connection with id {channel.self_id}"
            )
        self._channels[channel.self_id] = channel

        if not self._connection_hook:
            return

        async def _run_hook(channel: "Channel") -> None:
            dependency_cache: T_DependencyCache = {}
            with CancelScope(shield=True):
                try:
                    async with (
                        AsyncExitStack() as stack,
                        create_task_group() as tg,
                    ):
                        for hook in self._connection_hook:
                            tg.start_soon(
                                run_coro_with_catch,
                                hook(
                                    channel=channel,
                                    stack=stack,
                                    dependency_cache=dependency_cache,
                                ),
                                (SkippedException,),
                            )
                except* Exception as exc_group:
                    for exc in flatten_exception_group(exc_group):
                        logger.error(
                            "Error when running Channel connection hook:",
                            exc_info=(type(exc), exc, exc.__traceback__),
                        )

        self.task_group.start_soon(_run_hook, channel)

    def disconnect(self, channel: "Channel") -> None:
        """Unregister a channel after its connection closes."""
        if channel.self_id in self._channels:
            del self._channels[channel.self_id]

        if not self._disconnection_hook:
            return

        async def _run_hook(channel: "Channel") -> None:
            dependency_cache: T_DependencyCache = {}
            # shield cancellation to ensure channel disconnect hooks are always
            # run
            with CancelScope(shield=True):
                try:
                    async with (
                        AsyncExitStack() as stack,
                        create_task_group() as tg,
                    ):
                        for hook in self._disconnection_hook:
                            tg.start_soon(
                                run_coro_with_catch,
                                hook(
                                    channel=channel,
                                    stack=stack,
                                    dependency_cache=dependency_cache,
                                ),
                                (SkippedException,),
                            )
                except* Exception as exc_group:
                    for exc in flatten_exception_group(exc_group):
                        logger.error(
                            "Error when running Channel disconnection hook:",
                            exc_info=(type(exc), exc, exc.__traceback__),
                        )

        self.task_group.start_soon(_run_hook, channel)


class Mixin(abc.ABC):
    """Base interface for composable driver mixins."""

    @property
    @abc.abstractmethod
    def type(self) -> str:
        """The mixin type name."""
        raise NotImplementedError


class ForwardMixin(Mixin):
    """Base interface for outbound transport mixins."""


class ReverseMixin(Mixin):
    """Base interface for inbound transport mixins."""


class HTTPClientSession(abc.ABC):
    """Base interface for HTTP client sessions."""

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
        """Send an HTTP request."""
        raise NotImplementedError

    @abc.abstractmethod
    async def stream_request(
        self,
        setup: Request,
        *,
        chunk_size: int = 1024,
    ) -> AsyncGenerator[Response, None]:
        """Send an HTTP request with a streamed response."""
        raise NotImplementedError
        yield  # used for static type checking's generator detection

    @abc.abstractmethod
    async def setup(self) -> None:
        """Initialize the client session."""
        raise NotImplementedError

    @abc.abstractmethod
    async def close(self) -> None:
        """Close the client session."""
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
    """Base interface for HTTP client mixins."""

    @abc.abstractmethod
    async def request(self, setup: Request) -> Response:
        """Send an HTTP request."""
        raise NotImplementedError

    @abc.abstractmethod
    async def stream_request(
        self,
        setup: Request,
        *,
        chunk_size: int = 1024,
    ) -> AsyncGenerator[Response, None]:
        """Send an HTTP request with a streamed response."""
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
        """Create an HTTP client session."""
        raise NotImplementedError


class ASGIMixin(ReverseMixin):
    """Base interface for ASGI servers.

    Wrap a backend web framework for use by protocol adapters.
    """

    @property
    @abc.abstractmethod
    def server_app(self) -> Any:
        """The driver application object."""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def asgi(self) -> Any:
        """The driver's ASGI application."""
        raise NotImplementedError

    @abc.abstractmethod
    def setup_http_server(self, setup: "HTTPServerSetup") -> None:
        """Register an HTTP server route."""
        raise NotImplementedError

    def remove_http_server(self, method: str, path: str) -> bool:
        """Remove a route previously registered by setup_http_server.

        Returns:
            Whether a matching route was removed.
        """
        raise NotImplementedError(
            "This driver cannot remove HTTP server routes"
        )

    @abc.abstractmethod
    def setup_websocket_server(self, setup: "WebSocketServerSetup") -> None:
        """Register a WebSocket server route."""
        raise NotImplementedError
