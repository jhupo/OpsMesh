import abc
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from opsmesh.config import Config
from opsmesh.internal.driver import (
    ASGIMixin,
    Driver,
    HTTPClientMixin,
    HTTPServerSetup,
    Request,
    Response,
    WebSocket,
    WebSocketClientMixin,
    WebSocketServerSetup,
)
from opsmesh.internal.driver._lifespan import LIFESPAN_FUNC

from .gateway import Gateway


class Adapter(abc.ABC):
    """协议适配器基类。

    通常，在 Adapter 中编写协议通信相关代码，如: 建立通信连接、处理接收与发送 data 等。

    参数:
        driver: {ref}`opsmesh.drivers.Driver` 实例
        kwargs: 其他由 {ref}`opsmesh.drivers.Driver.register_adapter` 传入的额外参数
    """

    def __init__(self, driver: Driver, **kwargs: Any):
        self.driver: Driver = driver
        """{ref}`opsmesh.drivers.Driver` 实例"""
        self.gateways: dict[str, Gateway] = {}
        """本适配器已登记的网关，按实例标识索引。"""

    def __repr__(self) -> str:
        return f"Adapter(name={self.get_name()!r})"

    @classmethod
    @abc.abstractmethod
    def get_name(cls) -> str:
        """当前协议适配器的名称"""
        raise NotImplementedError

    @property
    def config(self) -> Config:
        """全局 OpsMesh 配置"""
        return self.driver.config

    def connect(self, gateway: Gateway) -> None:
        """登记已就绪的网关并触发回调，不建立网络连接。"""
        self.driver._register_gateway(gateway)
        self.gateways[gateway.id] = gateway

    def disconnect(self, gateway: Gateway) -> None:
        """注销不再可用的网关并触发回调；传输资源由适配器关闭。"""
        if self.gateways.pop(gateway.id, None) is None:
            raise RuntimeError(f"{gateway} not found in adapter {self.get_name()}")
        self.driver._unregister_gateway(gateway)

    def setup_http_server(self, setup: HTTPServerSetup):
        """设置一个 HTTP 服务器路由配置"""
        if not isinstance(self.driver, ASGIMixin):
            raise TypeError("Current driver does not support http server")
        self.driver.setup_http_server(setup)

    def setup_websocket_server(self, setup: WebSocketServerSetup):
        """设置一个 WebSocket 服务器路由配置"""
        if not isinstance(self.driver, ASGIMixin):
            raise TypeError("Current driver does not support websocket server")
        self.driver.setup_websocket_server(setup)

    async def request(self, setup: Request) -> Response:
        """进行一个 HTTP 客户端请求"""
        if not isinstance(self.driver, HTTPClientMixin):
            raise TypeError("Current driver does not support http client")
        return await self.driver.request(setup)

    @asynccontextmanager
    async def websocket(self, setup: Request) -> AsyncGenerator[WebSocket, None]:
        """建立一个 WebSocket 客户端连接请求"""
        if not isinstance(self.driver, WebSocketClientMixin):
            raise TypeError("Current driver does not support websocket client")
        async with self.driver.websocket(setup) as ws:
            yield ws

    def on_ready(self, func: LIFESPAN_FUNC) -> LIFESPAN_FUNC:
        return self.driver._lifespan.on_ready(func)

    @abc.abstractmethod
    async def _call_api(self, gateway: Gateway, api: str, **data: Any) -> Any:
        """`Adapter` 实际调用 api 的逻辑实现函数，实现该方法以调用 api。

        参数:
            api: API 名称
            data: API 数据
        """
        raise NotImplementedError


__autodoc__ = {"Adapter._call_api": True}
