# 通道与执行回调

职责总览见 [插件架构](plugin-architecture.md)。channels 将外部协议接入 Framework；execution 将原生 SDK 生命周期接入插件回调。二者不互相依赖。

## 一个 channels 插件

安装包名称是 `opsmesh-channels`，导入模块是 `opsmesh.plugins.channels`。插件通过标准 entry point 发布短名，业务插件使用 `require("channels")`；宿主的入口和配置条目使用完整模块路径，无默认值覆盖的依赖不必重复列出。`api`、`qq`、`dingtalk` 是同一个插件内的协议模块。

```text
packages/channels/
├── pyproject.toml              # 独立包与 channels entry point
├── requirements.txt
└── src/                       # 构建时映射到 opsmesh.plugins.channels
    ├── __init__.py            # 元数据与懒加载协议入口
    ├── config.py              # api/qq/dingtalk 独立配置分区
    ├── context.py            # UserContext
    ├── dependencies.py       # Connection(handle)
    ├── server.py             # Uvicorn 启停；不解析业务或持有适配器
    ├── requests.py           # 原生请求正文大小限制
    ├── lifecycle/            # 仅编排生命周期，不创建协议连接
    ├── api/                  # FastAPI、认证、路由到框架事件的转换
    │   ├── channel.py        # 自己持有 FastAPI 和监听器
    │   ├── auth.py           # FastAPI Depends 使用的可信身份校验
    │   ├── routing.py        # 插件路由装饰器
    │   ├── routes.py         # 插件归属、Matcher、输入输出契约
    │   ├── events.py         # HTTPEvent
    │   ├── streaming.py      # 原生流式响应的依赖关闭作用域
    │   └── config/messages/responses/errors/adapter.py
    ├── qq/                   # 自己持有 FastAPI Webhook、签名和回复客户端
    └── dingtalk/             # 自己持有官方 Stream SDK 连接和回复客户端
```

删除原先 `http/` 中自建的 Request/Response、ASGI 编解码、路径匹配和 WebSocket 包装。使用 FastAPI/Starlette 原生类型。API 的 `Depends` 先完成认证与权限检查，再转换为框架 Event、Message、UserContext，进入既有 Rule、Permission、Matcher、Dependent 链路。命令仍由框架的 `on_command` 处理，FastAPI 不建立第二套命令系统。

连接由所属模块管理：API 自己读取 `config.api` 并创建监听；QQ 自己读取 `config.qq` 并创建 Webhook 监听；钉钉自己建立 Stream 长连接。API 和 QQ 不共享外部注入的服务器，也不依赖彼此的身份配置，只复用 Uvicorn 启停逻辑。API 默认端口 8080，QQ 默认端口 8081，部署时可分别配置。仅使用钉钉时不创建 FastAPI 实例或本地监听。

```json
{
  "module": "opsmesh.plugins.channels",
  "required": true,
  "config": {
    "api": {
      "host": "127.0.0.1",
      "port": 8080,
      "identities": [{"id": "operator", "token_ref": "OPSMESH_API_TOKEN"}]
    },
    "qq": {"host": "127.0.0.1", "port": 8081}
  }
}
```

API 配置存在即准备入口；省略时不启用，有业务路由却没有身份配置时启动失败。`enabled=false` 表示不绑定本地端口，可供嵌入式 ASGI 与测试使用；不会退回匿名认证。未声明 QQ 连接时不会启动 QQ 监听；机器人账号仍由使用它的业务插件声明，建立、重连和关闭由协议模块完成。`config.http` 已删除，不提供兼容解析。

### 钉钉 Stream 配置

钉钉开发者控制台中，将机器人的消息接收模式切换为 **Stream 模式**，发布相应应用配置。保留消费插件提供的 `client_id` 和 `client_secret_ref`；凭据属于该机器人连接，不属于 API 身份配置。服务器需能出站访问钉钉 HTTPS/WSS 地址，无需对公网开放回调端口。

```python
from opsmesh.plugins import require

channels = require("channels")
dingtalk = channels.dingtalk
robot = dingtalk.declare_connection(
    dingtalk.DingTalkConnectionSettings(
        client_id="configured-client-id",
        client_secret_ref="DINGTALK_CLIENT_SECRET",
    ),
    name="support",
)
```

channels 的 `config.dingtalk` 只放公共限制，例如 `request_timeout`、`reconnect_delay`、`max_pending_events`。删除原有 `path_prefix`、`max_clock_skew` 配置；旧 HTTP 回调和签名接口不再保留。所有消费者就绪后启动 Stream，连接成功才加入 Driver 的在线索引；断线撤出并自动重连。关闭时先停止输入，再排空已接受消息，最后关闭回复客户端。

固定使用 [官方 dingtalk-stream 0.24.3](https://github.com/open-dingtalk/dingtalk-stream-sdk-python)。消息解析、回调及 ACK 使用 SDK 原生实现。该版本原生启动循环会吞掉取消，握手和回复请求使用无超时的同步 HTTP；因此 `stream.py` 只接管异步握手、连接任务和关闭，复用 SDK 协议逻辑。日志不输出握手 ticket、secret 或完整协议帧。回复继续使用钉钉返回的 session webhook（这是出站回复地址，不是本地 Webhook 接收端点）。

QQ 保留签名 Webhook 接入。[官方 botpy](https://github.com/tencent-connect/botpy) 的接收实现是 WebSocket 客户端，不能直接替换当前 Webhook 入口。本次没有引入 botpy 或切换 QQ 的接收方式。

业务插件使用 `@channels.api.get/post/...` 声明路由，框架按所属插件 ready 状态接单；没有手动 register/activate 协议。撤销一个业务插件不会关闭其他消费者共享的 API。

## 统一消息与身份

```text
FastAPI 请求 / 已验证 QQ 回调 / 钉钉 Stream 消息
→ 通道认证与协议解析
→ Event + Message + UserContext
→ dispatch_event
→ Permission / Rule / Matcher / Dependent
→ 插件处理函数
→ Channel 回复
```

`UserContext(id, name, groups, roles)` 只携带可信用户信息，字段冻结，组与角色是 frozenset。`Context()` 注入原对象，使用 `user.id` 访问，没有旧字典接口和类型别名。HTTP 正文中的身份字段不能覆盖认证结果。SDK Session/执行 ID 不属于用户上下文；Framework `permissions.User` 保持权限匹配器语义。

API 的 JSON 字符串或对象中字符串 `message` 转为 Message 文本段；其他 JSON 作为 data 段。HTTPEvent 保留规范化 payload、路径和查询信息。处理函数使用 Event、EventMessage、EventPlainText、CommandArg、Context 和 Depends，不引入 HTTPBody 参数体系。

路由装饰器的 `body=Model` 只负责前置 JSON 校验。返回注解用于响应契约。业务插件决定返回字段，也可用 Channel.send / Matcher.finish；同一事件只允许一个最终响应，SSE 是这个响应中的流。

HTTP 路由默认只派发对应 Matcher。消息路由可显式选择同插件命令 Matcher，命令和普通消息沿同一次分发处理，无关插件路由不执行。机器人事件选择连接所属插件的 Matcher；Framework 全局事件钩子仍可用于宿主级策略和日志。

## 请求与流生命周期

普通响应完成编码后关闭请求依赖。流式响应通过 Starlette StreamingResponse 子类绑定请求依赖栈；正常耗尽、提前关闭、异常、取消都结束同一作用域。streaming 模块负责源流和依赖各自的关闭期限；源流关闭失败或超时仍必须尝试关闭依赖，保留原异常。

404 表示路由不存在，503 表示通道/插件未就绪，403 表示权限/规则拒绝且没有响应，504 表示取得响应前超时。流开始后的持续时间不等于前置 request timeout。断连取消请求，默认不把工作转成后台任务，也不自动续传。

机器人内存队列用于快速接收与异步消费。队列满/关闭返回失败并撤销该事件去重占位，允许供应商随后重试。队列不是持久任务系统，进程退出可能丢失已确认事件；不会自动重跑可能已产生副作用的模型调用。

停机时 channels 的 `on_quiescing` 先停止 HTTP 接单、等待在途请求，再停止协议入口并排空机器人工作；之后框架运行消费者的 `on_stopping`，最后反向执行 `on_shutdown` 释放资源。模型/MCP/沙箱资源不能在使用者还未排空时提前释放。

## execution 的插件接口

```python
import logging
from typing import Annotated

from opsmesh.params import Context
from opsmesh.plugins import require

channels = require("channels")
execution = require("execution")
logger = logging.getLogger(__name__)


@execution.before_run
async def check_run(
    user: Annotated[channels.UserContext, Context()],
) -> None:
    if "suspended" in user.roles:
        raise PermissionError("The user cannot start a model run")


@execution.on_run_end
async def observe_result(result, error, trace_id) -> None:
    if result is not None:
        logger.info("Trace %s used %s", trace_id, result.context_wrapper.usage)
```

`before_run` 可接收 agent、input、run_config、trace_id，抛错会阻断执行。`on_run_end` 接收原生 result、原异常 error 和 trace_id；成功/失败/取消后的观测异常只记录，不覆盖已有结果。日志使用普通 logging，由宿主配置输出。

SDK 对应装饰器 `on_agent_start/end`、`on_llm_start/end`、`on_tool_start/end`、`on_handoff` 保留原生参数和异常语义。它们不能与不会阻断的结束通知混为一谈。调用者原生 RunHooks、AgentHooks、Guardrails 不被替换或吞错。

回调使用 Framework Dependent 解析 `Context()` 和 `Depends()`。当前 Matcher 的插件与其 require 依赖闭包参与回调，重复依赖不重复执行；无关插件不参与。后台调用传 `plugin_id="stable_plugin_id"`，与当前 Matcher 冲突时拒绝。没有归属时不全局广播。

调用者传 `trace_context=execution.TraceContext(user_id=user.id, session_id=session_id, tags=(...))` 关联 Langfuse。run 返回原生结果，stream 在异步上下文内暴露原生 RunResultStreaming。用量直接读取 SDK Usage；execution 不写 PostgreSQL、维护统计报表或会话列表。

MCP、Session、Agent 和沙箱的构造及长期资源归属由功能插件承担。SDK 工具函数不自动识别 Framework Depends；需要时显式使用 Dependent。普通 Agent 与 SandboxAgent 共用 execution 接口，不新增沙箱执行器。
