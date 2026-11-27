# 协议适配器（Adapter）开发指南

在 OpsMesh 中，外部系统的协议差异完全收敛在 **Adapter 层**。编写一个新适配器通常需要实现一组五元组模型：**`MessageSegment`**、**`Message`**、**`Event`**、**`Gateway`** 与 **`Adapter`**。

---

## 一、五元组职责与抽象契约

### 1. `MessageSegment` 与 `Message`（富内容载体）

位于 `opsmesh.adapters`（定义于 `opsmesh.internal.adapter.message`），原生支持 Pydantic v2 `TypeAdapter` 校验与 `MessageTemplate` 模板渲染：

- `MessageSegment`（`@dataclass`）：表示单个内容片段（`type: str`、`data: dict[str, Any]`），必须实现：
  - `get_message_class(cls) -> type[TM]`：返回所属 `Message` 子类；
  - `__str__(self) -> str`：序列化为字符串（用于命令/正则匹配）；
  - `is_text(self) -> bool`：标记是否为纯文本段（供 `extract_plain_text()` 与 `TrieRule` 识别）。
- `Message`（继承 `list[TMS]`）：表示有序消息段序列，必须实现：
  - `get_segment_class(cls) -> type[TMS]`：返回元素 `MessageSegment` 子类；
  - `_construct(msg: str) -> Iterable[TMS]`：将纯字符串构造成消息段迭代器。

### 2. `Event`（内部流转标准事件）

继承自 `abc.ABC, pydantic.BaseModel`（`extra="allow"`），内置 UTC 时间字段 `time: datetime` 与 `get_time()`：

| 方法 | 必须实现 | 说明 |
| :--- | :---: | :--- |
| `get_type() -> str` | 是 | 事件大类（如 `"message"`、`"notice"`、`"request"`、`"meta_event"` 或自定义如 `"webhook"`） |
| `get_event_name() -> str` | 是 | 细粒度事件名（如 `"webhook.deploy"`），用于日志展示 |
| `get_event_description() -> str` | 是 | 人类可读事件摘要，默认拼接至 `get_log_string()` |
| `get_user_id() -> str` | 是 | 触发主体标识（配合 `SUPERUSER` 形成 `"{adapter}:{user_id}"`） |
| `get_session_id() -> str` | 是 | 会话上下文标识（用于多轮会话续接、`USER` 权限与 `single_session` 互斥） |
| `get_message() -> Message` | 是 | 返回事件消息体；无消息体的通知/元事件可抛出 `ValueError` |
| `is_tome() -> bool` | 是 | 标记事件是否与当前网关实例直接有关（供 `to_me()` 规则与 `EventToMe()` 依赖使用） |
| `get_log_string() -> str` | 否 | 自定义日志文本；抛出 `NoLogException` 可静默隐藏该事件日志（如高频心跳） |
| `get_plaintext() -> str` | 否 | 默认调用 `self.get_message().extract_plain_text()` |

### 3. `Gateway`（协议连接/路由实例）

代表一个具体的可交互网关实例（例如一个 Webhook 通道、一个租户连接或一个客户端会话）：

- 构造参数：`Gateway(adapter: Adapter, id: str)`（`id` 在当前 `Driver` 中必须唯一）。
- 必须实现：`async def send(self, event: Event, message: str | Message | MessageSegment | MessageTemplate, **kwargs: Any) -> Any`。
- 内置方法：
  - `gateway.connect()` / `gateway.disconnect()`：向所属 `Adapter` 与 `Driver` 登记/注销实例并触发 `@Gateway.on_connect` / `@Gateway.on_disconnect`；
  - `await gateway.invoke(action, **params)` 或 `await gateway.<action>(**params)`：执行出站动作，自动经过 `@Gateway.before_invoke` 与 `@Gateway.after_invoke` 钩子。

### 4. `Adapter`（协议编解码与传输宿主）

必须实现：
- `@classmethod def get_name(cls) -> str`：返回适配器唯一名称（如 `"webhook"`、`"slack"`）。
- `async def _invoke(self, gateway: Gateway, action: str, **params: Any) -> Any`：真实执行出站动作；不支持的动作建议抛出 `ActionNotAvailable(self.get_name(), action)`。

`Adapter` 内置的传输委托方法（自动校验 `Driver` 是否混入对应 `Mixin`）：

| 方法 | 依赖 Mixin | 用途 |
| :--- | :--- | :--- |
| `setup_http_server(HTTPServerSetup)` | `ASGIMixin` | 挂载 HTTP Webhook / API 服务端路由 |
| `setup_websocket_server(WebSocketServerSetup)` | `ASGIMixin` | 挂载 WebSocket 服务端路由 |
| `await request(Request) -> Response` | `HTTPClientMixin` | 发起单次异步 HTTP 请求 |
| `async for chunk in stream_request(Request, chunk_size=1024)` | `HTTPClientMixin` | 发起流式 HTTP / SSE 请求 |
| `get_session(...) -> HTTPClientSession` | `HTTPClientMixin` | 获取复用连接池与 Cookie 的 HTTP 会话 |
| `async with websocket(Request) as ws:` | `WebSocketClientMixin` | 建立主动出站 WebSocket 长连接（支持 `ping_interval` 与 `Timeout.ping`） |
| `on_ready(func)` | 无（所有 `Driver`） | 注册驱动就绪后执行的启动回调 |

---

## 二、接入模式 A：ASGI 服务端（HTTP Webhook / WS Server）

适合由外部系统主动推送事件到 OpsMesh 的场景（如 GitHub/Alertmanager Webhook、飞书/钉钉回调、内部 HTTP 网关）。完整可运行代码见 [examples/webhook_ops/adapter.py](../examples/webhook_ops/adapter.py)，核心结构如下：

```python
from typing import Any
from typing_extensions import override

from opsmesh.adapters import Adapter, Gateway
from opsmesh.drivers import (
    URL,
    ASGIMixin,
    Driver,
    HTTPServerSetup,
    Request,
    Response,
)
from opsmesh.message import handle_event


class MyWebhookAdapter(Adapter):
    @override
    def __init__(self, driver: Driver, **kwargs: Any):
        super().__init__(driver, **kwargs)
        self.gateway = MyGateway(self, "webhook:default")
        if not isinstance(self.driver, ASGIMixin):
            raise TypeError("MyWebhookAdapter requires an ASGI driver")

        self.setup_http_server(
            HTTPServerSetup(
                path=URL("/webhook/events"),
                method="POST",
                name="my_webhook",
                handle_func=self._handle_http,
            )
        )
        self.driver.on_startup(self._startup)
        self.driver.on_shutdown(self._shutdown)

    @classmethod
    @override
    def get_name(cls) -> str:
        return "my_webhook"

    async def _startup(self) -> None:
        self.gateway.connect()

    async def _shutdown(self) -> None:
        if self.gateway.id in self.gateways:
            self.gateway.disconnect()

    async def _handle_http(self, request: Request) -> Response:
        event = MyEvent.model_validate_json(request.content or b"{}")
        # 异步入队快速返回 202，避免长耗时插件阻塞上游 Webhook 超时
        self.driver.task_group.start_soon(handle_event, self.gateway, event)
        return Response(202, content=b'{"accepted": true}')

    @override
    async def _invoke(self, gateway: Gateway, action: str, **params: Any) -> Any:
        # 可进一步通过 self.request(...) 调用下游 API
        return {"gateway": gateway.id, "action": action, "params": params}
```

---

## 三、接入模式 B：主动客户端长连接（WebSocket / 轮询）

适合由 OpsMesh 主动向远端平台发起 WebSocket 长连接或 HTTP 流式订阅的场景（需配置支持客户端 Mixin 的驱动，如 `DRIVER=~none+~websockets+~httpx` 或 `~fastapi+~aiohttp`）：

```python
import anyio
from typing import Any
from typing_extensions import override

from opsmesh.adapters import Adapter, Gateway
from opsmesh.drivers import (
    Driver,
    Request,
    Timeout,
    WebSocketClientMixin,
)
from opsmesh.exception import WebSocketClosed
from opsmesh.log import logger
from opsmesh.message import handle_event


class StreamClientAdapter(Adapter):
    @override
    def __init__(self, driver: Driver, endpoint: str = "wss://ops.internal/ws"):
        super().__init__(driver)
        self.endpoint = endpoint
        if not isinstance(self.driver, WebSocketClientMixin):
            raise TypeError("StreamClientAdapter requires WebSocketClientMixin")
        self.on_ready(self._start_loop)

    @classmethod
    @override
    def get_name(cls) -> str:
        return "stream"

    async def _start_loop(self) -> None:
        self.driver.task_group.start_soon(self._connect_forever)

    async def _connect_forever(self) -> None:
        req = Request(
            "GET",
            self.endpoint,
            timeout=Timeout(connect=10.0, ping=20.0, close=5.0),
            ping_interval=30.0,
        )
        while True:
            gw = StreamGateway(self, "stream:primary")
            try:
                async with self.websocket(req) as ws:
                    gw.bind_ws(ws)
                    gw.connect()
                    while True:
                        raw = await ws.receive_text()
                        event = StreamEvent.model_validate_json(raw)
                        self.driver.task_group.start_soon(handle_event, gw, event)
            except WebSocketClosed as exc:
                logger.warning(f"WebSocket closed ({exc.code}), reconnecting in 3s...")
            finally:
                if gw.id in self.gateways:
                    gw.disconnect()
            await anyio.sleep(3)

    @override
    async def _invoke(self, gateway: Gateway, action: str, **params: Any) -> Any:
        ...
```

---

## 四、第三方适配器打包与测试规范

1. **命名空间挂载**：`opsmesh.adapters` 已启用 `pkgutil.extend_path`。独立发布的适配器包（如 `opsmesh-adapter-slack`）可将源码置于 `opsmesh/adapters/slack/` 下，用户即可通过 `from opsmesh.adapters.slack import Adapter` 引入，并在 `PluginMetadata.supported_adapters` 中使用简写 `{"~slack"}`。
2. **统一日志前缀**：使用 `opsmesh.utils.logger_wrapper("MyAdapter")` 生成带彩色适配器标签的日志函数，保持全框架日志风格一致。
3. **进程内测试（零端口依赖）**：
   - ASGI 适配器可使用 `httpx.AsyncClient(transport=httpx.ASGITransport(app=driver.asgi), base_url="http://testserver")` 配合 `async with driver._lifespan_manager(driver.server_app):` 完成完整端到端测试；
   - 出站调用可通过 `@Gateway.before_invoke` 抛出 `MockInvokeResult(...)` 进行零网络 Mock 验证。
