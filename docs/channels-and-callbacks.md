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
    │   ├── events.py         # HTTPEvent / APIMessageEvent
    │   ├── streaming.py      # 原生流式响应的依赖关闭作用域
    │   └── config/messages/responses/errors/adapter.py
    ├── qq/                   # 自己持有 FastAPI Webhook、签名和回复客户端
    └── dingtalk/             # 自己持有官方 Stream SDK 连接和回复客户端
```

API 使用 FastAPI/Starlette 原生类型。FastAPI `Depends` 完成认证与入口权限检查后，进入框架 Event、Rule、Permission、Matcher、Dependent 链路。标准消息入口额外构造 Message，命令由框架的 `on_command` 处理。

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

API 配置存在即准备入口；省略时不启用，有业务路由却没有身份配置时启动失败。`enabled=false` 表示不绑定本地端口，可供嵌入式 ASGI 与测试使用；认证仍生效。未声明 QQ 连接时不会启动 QQ 监听；机器人账号由使用它的功能插件声明，建立、重连和关闭由协议模块完成。

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

channels 的 `config.dingtalk` 只放公共限制，例如 `request_timeout`、`reconnect_delay`、`max_pending_events`。所有消费者就绪后启动 Stream，连接成功才加入 Driver 的在线索引；断线撤出并自动重连。关闭时先停止输入，再排空已接受消息，最后关闭回复客户端。

固定使用 [官方 dingtalk-stream 0.24.3](https://github.com/open-dingtalk/dingtalk-stream-sdk-python)。消息解析、回调及 ACK 使用 SDK 原生实现。该版本原生启动循环会吞掉取消，握手和回复请求使用无超时的同步 HTTP；因此 `stream.py` 只接管异步握手、连接任务和关闭，复用 SDK 协议逻辑。日志不输出握手 ticket、secret 或完整协议帧。回复继续使用钉钉返回的 session webhook（这是出站回复地址，不是本地 Webhook 接收端点）。

QQ 使用签名 Webhook 接入与异步回复客户端，协议参照[官方 botpy](https://github.com/tencent-connect/botpy)。

业务插件使用 `@channels.api.get/post/...` 声明路由，框架按所属插件 ready 状态接单；没有手动 register/activate 协议。撤销一个业务插件不会关闭其他消费者共享的 API。

## 统一消息与身份

```text
FastAPI 请求 / 已验证 QQ 回调 / 钉钉 Stream 消息
→ 通道认证与协议解析
→ Event + UserContext，消息事件另有 Message
→ dispatch_event
→ Permission / Rule / Matcher / Dependent
→ 插件处理函数
→ Channel 回复
```

`UserContext(id, name, groups, roles)` 只携带可信用户信息，字段冻结，组与角色是 frozenset。`Context()` 注入原对象，使用 `user.id` 访问。HTTP 正文中的身份字段不能覆盖认证结果。SDK Session/执行 ID 由调用插件管理；Framework `permissions.User` 是权限匹配器。

普通 HTTP 路由使用 `@channels.api.get/post/...` 声明。`body=Model` 验证输入，`HTTPEvent.payload` 保留模型实例；未指定模型时保留解析后的 JSON。字段名和内容不决定事件类型，普通请求的 `get_message()` 会明确报错。处理函数通过 Event 和 Context/Depends 读取请求及身份，返回注解决定响应契约，也可返回允许的原生 Response。

路由装饰器会创建框架 Matcher，并将处理函数交给 Dependent；它不是绕过框架的 FastAPI 业务处理器。普通查询不需要伪造文本命令。若同一个操作同时提供 REST 与消息命令入口，两者调用相同业务操作，分别解析路径参数或 CommandArg，并在入口转换响应和异常。`example_chat` 的 `/sessions/{id}` 与 `/session` 就共用会话存储；`/chat` 则直接绑定已有命令及消息 Matcher。

标准消息入口通过 `channels.api.message` 绑定同插件已有的 Matcher：

```python
from opsmesh import on_command, on_message
from opsmesh.adapters import Channel, Event
from opsmesh.plugins import require

channels = require("channels")
help_command = on_command("help", priority=1, block=True)
chat_message = on_message(priority=10, block=True)


@help_command.handle()
async def help_reply(channel: Channel, event: Event) -> None:
    await channel.send(event, "Send a message to start a conversation.")


@chat_message.handle()
async def chat_reply(channel: Channel, event: Event) -> None:
    await channel.send(event, "Received: " + event.get_plaintext())


channels.api.message("/chat", handlers=(help_command, chat_message))
```

消息请求是 `{"message": "hello"}` 或有序消息段数组。`body` 可选用 `MessageRequest` 子类扩展插件字段，必须保留标准 message 契约。`APIMessageEvent` 的类型固定为 message，包括纯附件输入；`payload` 保留完整验证模型。EventMessage、EventPlainText 和 CommandArg 使用同一份 Message。

普通路由只派发其路由 Matcher；消息入口只派发绑定的持久、阻断式 Matcher，优先级必须互不相同。消息处理器显式使用 Channel.send / Matcher.finish 回复，返回值不自动变成 HTTP 响应。每个请求只有一个最终回复或一个流；没有回复时返回 204。机器人事件选择连接所属插件的 Matcher；框架全局事件钩子仍可用于策略和日志。

## 请求与流生命周期

普通响应完成编码后关闭请求依赖。流式响应通过 Starlette StreamingResponse 子类绑定请求依赖栈；正常耗尽、提前关闭、异常、取消都结束同一作用域。streaming 模块负责源流和依赖各自的关闭期限；源流关闭失败或超时仍必须尝试关闭依赖，保留原异常。

404 表示路由不存在，503 表示通道/插件未就绪，403 表示入口认证授权拒绝，504 表示取得响应前超时。Matcher 没有命中且没有回复时返回 204。流开始后的持续时间不等于前置 request timeout。断连取消请求，不自动转后台或续传。

机器人内存队列用于快速接收与异步消费。队列满/关闭返回失败并撤销该事件去重占位，允许供应商随后重试。队列不是持久任务系统，进程退出可能丢失已确认事件；不会自动重跑可能已产生副作用的模型调用。

停机先关闭新工作准入，保留已受理消息所需的回复连接。框架排空消费者调用后按依赖逆序释放资源；未成功关闭的传输保留引用供重试，不能将失败伪装为已停止。模型、MCP 和沙箱资源在使用者清理完成后释放。

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

`before_run` 可接收 agent、input、run_config、trace_id，抛错会阻断执行。`on_run_end` 接收原生 result、原异常 error 和 trace_id；普通观测异常只记录，不覆盖已有结果，取消继续传播。日志使用普通 logging，由宿主配置输出。

SDK 对应装饰器 `on_agent_start/end`、`on_llm_start/end`、`on_tool_start/end`、`on_handoff` 保留原生参数和异常语义。它们不能与不会阻断的结束通知混为一谈。调用者原生 RunHooks、AgentHooks、Guardrails 不被替换或吞错。

回调使用 Framework Dependent 解析 `Context()` 和 `Depends()`。当前 Matcher 的插件与其 require 依赖闭包参与回调，重复依赖不重复执行；无关插件不参与。每个回调归属拥有独立的依赖栈和缓存，函数及 finalizer 均在声明插件作用域执行。参与集合在执行准入时确定，STOPPING 中的已受理运行仍完成通知。后台调用传 `plugin_id="stable_plugin_id"`，与当前 Matcher 冲突时拒绝；缺少执行归属会报错。

原生子智能体编排使用 SDK Agent.as_tool、handoff 等能力，执行及收尾包含在外层调用中。独立任务再次调用 execution.run/stream 时重新检查准入；create_task 继承上下文不代表继承调用占用。流清理超时后仍在运行的 SDK 任务保持外层调用占用，直到实际结束；排空超时会报告失败并保留提供方，最终强制进程退出由部署服务负责。

调用者传 `trace_context=execution.TraceContext(user_id=user.id, session_id=session_id, tags=(...))` 关联 Langfuse。run 返回原生结果，stream 在异步上下文内暴露原生 RunResultStreaming。用量直接读取 SDK Usage；execution 不写 PostgreSQL、维护统计报表或会话列表。

MCP、Session、Agent 和沙箱的构造及长期资源归属由功能插件承担。SDK 工具函数不自动识别 Framework Depends；需要时显式使用 Dependent。普通 Agent 与 SandboxAgent 共用 execution 接口，不新增沙箱执行器。

## 统一消息与附件

API、QQ、钉钉复用 `channels.Message` 与 `MessageSegment`。
API 的 message 支持字符串或有序内容数组。上传、读取、SDK 输入及回复边界见
[多模态消息与附件](multimodal-messages.md)。

## 群操作与通知

QQ、钉钉通过 Channel.call 暴露已实现的官方操作；连接限制调用插件归属，业务授权由 Permission 或 calling Hook 完成。calling Hook 的拒绝异常阻断供应商请求，普通观测 Hook 失败仅记录日志。

QQ 当前支持频道信息、成员、角色查询以及成员禁言、全员禁言和移除成员；这些接口使用 guild ID，不适用于普通 QQ 群 openid。钉钉支持群基础信息和互通群成员、群名操作，仍受应用权限和群类型限制。

平台通知保留原生事件类型、ID 和 payload，通过 on_notice/on_type 处理。未知身份不伪造，群业务策略由调用插件决定。准确操作名、接口及官方依据见 [channels 包说明](../packages/channels/README.md#platform-group-operations)。
