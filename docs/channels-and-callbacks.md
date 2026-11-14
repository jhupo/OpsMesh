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

API 使用 FastAPI/Starlette 作为传输实现，但业务请求统一遵循 MessageRequest 协议。认证与入口权限检查完成后，channels 构造框架 Event、Message、可信 UserContext 和附件引用，再进入 Rule、Permission、Matcher、Dependent 链路。命令由框架的 `on_command` 处理，普通消息由 `on_message` 处理。FastAPI 类型不进入业务插件。

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

channels 自己只维护消息入口、健康检查和传输生命周期。目标架构中业务插件不使用 `@channels.api.get/post/...` 声明会话、知识库或 cron 路由；它们声明 `on_message/on_command` Matcher，并由统一消息入口按插件归属开放。撤销一个业务插件只撤销它的消息入口，不关闭其他消费者共享的 API。

## 统一消息与身份

统一消息入口使用下面的请求、回复和续接契约。chat/sandbox 示例和客户端已采用该入口；通道内部的非消息管理路由仍可单独声明。

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

统一 HTTP 消息入口固定为 `POST /plugins/{plugin_id}/messages`。请求体为 `MessageRequest`：`message` 可以是纯文本，也可以是有序文本/图片/文件/音频/视频消息段；附件必须引用 channels 已校验的 `attachment_id`。调用者必须遵守这个协议，不能在正文传入用户、组、角色、Matcher ID、Python 函数名或任意本地路径。未知字段、空文本、非法附件和超限请求在进入框架前拒绝。

业务插件不为每个操作定义 HTTP body 或业务 REST 路由。`/new`、`/sessions`、`/use`、`/clear`、知识库查询、计划管理等都以命令或普通消息进入 Matcher；参数通过命令参数和消息段提供，业务插件负责校验资源归属。HTTP、QQ、钉钉的外部请求都转换成相同的 Event/Message/Context，避免每个通道维护不同的业务入口。

标准消息入口通过 `channels.api.message` 绑定同插件已有的 Matcher：

```python
from opsmesh import on_command, on_message
from opsmesh.adapters import Event
from opsmesh.matchers import Matcher
from opsmesh.plugins import require

channels = require("channels")
help_command = on_command("help", priority=1, block=True)
chat_message = on_message(priority=10, block=True)


@help_command.handle()
async def help_reply(matcher: Matcher) -> None:
    await matcher.finish("Send a message to start a conversation.")


@chat_message.handle()
async def chat_reply(matcher: Matcher, event: Event) -> None:
    await matcher.finish("Received: " + event.get_plaintext())


channels.api.message(handlers=(help_command, chat_message))
```

这里省略 path 是目标统一消息入口的接口；当前源码仍要求显式 path，实施时需要同步修改 channels、chat/sandbox 示例和客户端，不保留每个业务的旧 REST 路由兼容分支。

消息请求是 `{"message": "hello"}` 或有序消息段数组。插件不再通过 `body` 子类扩展业务 HTTP 字段；需要业务参数时使用命令参数、消息段或插件自己的数据库对象。`APIMessageEvent` 的类型固定为 message，包括纯附件输入；`payload` 保留 channels 的验证模型。EventMessage、EventPlainText 和 CommandArg 使用同一份 Message。

入口显式绑定同插件的持久、阻断式 Matcher，优先级互不相同；运行时还允许这些 Matcher 派生且通过作用域校验的临时续接 Matcher。不能仅按绑定对象的身份过滤，也不能放行同插件的所有临时 Matcher。框架全局事件钩子继续承担策略和日志。

`Matcher.send/finish` 通过当前事件绑定的 Channel 工作，handler 的普通返回值被忽略。`send()` 可调用多次；`finish(message)` 发送可选消息并结束当前 Matcher，不额外发送“最终消息”标记，也不直接关闭 HTTP。`pause/reject` 改变下一条事件的处理位置，不把当前请求一直挂起等待用户。

### 通道对话标识与多轮续接

MessageRequest 增加可选的 `conversation_id`，值为客户端生成的规范 UUID 字符串。例：`{"conversation_id":"d250759b-8d9a-4c3a-a1af-397292fc835d","message":"hello"}`。它只是客户端的对话区分符，不是身份凭据或 SDK Session ID。未传时 channels 为本次请求生成新值并在响应中返回；客户端要继续同一对话必须保留该值，不同窗口默认生成不同值。

API Event 的 `get_session_id()` 返回由协议、连接、目标插件、认证用户及 conversation_id 共同构成的无歧义标识。机器人使用平台验证的账号、对话及用户构造等价范围。正文不能指定这些可信前缀；相同 conversation_id 在不同用户或插件下不共享状态。该标识用于框架续接和调用插件的映射，不进入 UserContext，也不要求 channels 建立会话数据库。

聊天插件按上述范围保存当前 SDK Session 的选择。`/new`、`/use <id>` 修改该范围的映射并回复 session_id；后续普通消息只携带 conversation_id。访问 SDK Session 仍由聊天插件校验，知道通道标识不授予业务访问权。同一范围的 HTTP 请求同时只接纳一个，忙时返回 409，不积压请求；机器人消费也按同一范围串行。跨范围选中同一个 SDK Session 时的执行并发仍由聊天插件管理。

`pause/reject` 派生的临时 Matcher 保留原始 Matcher 的关联；多次暂停继续沿用同一原始关联，不能仅比较模块或源代码行号。入口按该关联过滤，等待范围继续使用 Event.get_session_id() 和现有 Permission。下一次请求重新认证、检查插件准入及权限，再由框架优先匹配该续接。暂停的 state 只保存可跨请求使用的值，不保存 Request、依赖栈、连接或 Agent 流；下一次调用重新解析依赖。不为这条链路另外建立加载代次登记表或等待管理器。

等待复用框架现有过期检查，过期后不能再命中；不新增定时扫描服务。停止时先关闭新调用准入，排空已受理调用后，通过现有注册归属清理该插件的临时续接，保留正常静态声明；这样排空期间产生的等待也会被清理。停止未完成不能报告成功或直接重新启动。撤销继续使用既有注册清理，服务重启不恢复等待；即使只停止再启动资源、未重新导入模块，旧等待也不能复活。每个范围至多保留一个等待，channels 的同范围调用准入避免重复消费。暂停或拒绝后，本次响应根据既有 DispatchResult.suspended 标记 waiting；超时后新输入按新的消息处理，不隐式重放原操作。

### 回复协议

HTTP 的“一次响应”可以承载多条消息，不能沿用当前 HTTPEvent 单次 reply 槽。API Channel 按调用顺序收集已发送消息；QQ/钉钉仍逐条发送。非流式请求在框架分发与清理完成后返回一个 JSON envelope：

```json
{
  "conversation_id": "d250759b-8d9a-4c3a-a1af-397292fc835d",
  "status": "completed",
  "messages": [
    {"content": [{"type": "text", "text": "Session selected."}],
     "data": {"session_id": "session-123"}}
  ],
  "error": null
}
```

status 为 completed、waiting 或 failed；它描述本次消息处理，不代表 Agent 运行或持久任务状态。无回复和无匹配也返回 200、completed、空 messages，不使用 204，确保客户端拿到 conversation_id。已通过认证的处理失败返回安全的 error（code、message）及已收集消息；HTTP 使用对应 4xx/5xx，不将未知异常原文返回。协议校验和认证失败可在进入分发前直接返回通道错误。

结构化结果复用 channels.Message/MessageSegment：拟新增 `MessageSegment.structured(value)`，只接受可 JSON 序列化的对象，并受输出大小限制；同一条 Message 最多含一个 data 段，有 data 时必须同时提供可读内容。HTTP 将 data 段提取为 envelope 中该消息的 data，其余段编码到 content；机器人只发送可读内容，不把 data 拼成文本。业务插件负责数据字段和序列化，不允许直接放 ORM 对象、连接或密钥。data 为出站专用段，HTTP 输入不能利用它注入业务对象。

使用形式为 `await matcher.finish(Message([MessageSegment.text("Session selected."), MessageSegment.structured({"session_id": session_id})]))`。列表等业务结果统一放在对应消息的 data 中；客户端按命令结果契约读取，不解析人类文本。

HTTP 客户端通过 `Accept: text/event-stream` 选择流式传输。普通 Matcher.send 始终发送一条完整消息，每次对应独立的 message 事件，不能把相邻文本消息自动拼成一条回答。仅选择 SSE 不会使 send 变成模型增量接口。

模型增量由 channels 提供独立的通道输出操作，沿用 Channel.call 扩展点，不新增 Matcher 方法或 SDK 事件解析器。该操作在调用者作用域内消费异步文本迭代器；调用插件在 execution.stream 的上下文中将原生 SDK 事件转换成文本增量，并等待通道消费结束。不能返回一个依赖已经关闭的 SDK 流交给通道后台读取。结构化结果和附件继续走完整消息发送。

每个流式回答由 channels 分配一个 message_id：SSE 发出 message_start，随后发送带相同 ID 和单调 sequence 的 message_delta，最后发送 message_end。客户端仅在同一 message_id 下追加 delta；message_end 只结束这一条回答，不代表整个请求成功。普通 message 事件也带独立 message_id。非流式 HTTP 将这些增量有界聚合成一个完整消息项。QQ/钉钉默认同样聚合成完整回答再按平台长度限制发送，不逐 token 调用机器人 API；只有已验证的平台消息更新能力才可启用限频更新，并保证结束时提交完整内容。

分发、原生执行及依赖清理成功后，HTTP 发送唯一 done 事件，包含 conversation_id 和 completed/waiting。增量读取、发送或清理失败则发送安全 error（可包含当前 message_id）后关闭，不发送成功 done，不把已显示的部分回答标成完整。HTTP 状态在响应头发出后不可更改。通道不承诺断线恢复，sequence 仅用于本次流的排序。

SSE 使用有界缓冲和背压，非流式收集限制累计消息数与大小。断连取消当前分发并清理流与依赖，不转后台、不自动重试或续传；已有副作用不回滚。Matcher 结束不提前关闭仍在清理的资源，终止后禁止继续发送。pause/reject 产生等待状态，但不跨请求持有执行资源。

### 命令分派与未知命令

命令前缀、已注册命令及参数继续由框架现有规则解析。聊天 Matcher 增加“不是命令输入”的 Rule；这里按配置的命令前缀判定，不能只排除已匹配的已知命令。已声明命令之后、普通聊天之前设置阻断式命令兜底：未知命令回复安全错误，不进入模型；已知命令权限不通过时给出不泄露命令存在性的拒绝/不可用回复，也不转普通聊天。参数错误由命中的命令处理器回复。兜底与规则由声明入口的插件复用，channels 不建立第二套命令解析器。

前面的简短示例仅演示 Matcher 回复；正式 chat/sandbox 实施时必须同时加入该 Rule 与兜底。有等待续接时，下一条消息先按续接流程处理；需要在等待中接受取消等命令，由该交互显式定义，不让全局兜底抢走续接输入。

## 请求与流生命周期

非流式顺序固定为：处理器产生消息 → 在依赖有效期内校验、序列化并物化发送所需数据 → 关闭执行资源与请求依赖 → 返回响应。清理后不能再惰性读取附件或 ORM 属性。附件若通过后续下载获取，必须在清理前转成 channels 已有的受控、可在响应后使用的附件引用；只保存即将失效的临时句柄不算物化。清理失败按失败响应处理，不能提前发送成功头。

流式响应复用现有响应资源持有机制，作用域覆盖 SDK 流消费、附件读取和通道发送。完成或取消时先关闭源执行，再清理请求依赖，最后才发送成功 done；断连时仅完成清理。源流关闭失败或超时仍必须尝试依赖清理，保留原异常。Matcher.finish 不能绕过上述顺序，也不在依赖 finalizer 中启动新的业务发送。

404 表示入口不存在，503 表示通道/插件未就绪，403 表示入口授权拒绝，409 表示同一通道对话仍有请求执行，504 表示非流式处理超时。消息入口的空结果仍按上面的 envelope 返回。流式响应头发出后的错误通过 error 事件报告；流执行不受“等待首个响应”的超时约束，但保留断连取消和插件排空规则。

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
