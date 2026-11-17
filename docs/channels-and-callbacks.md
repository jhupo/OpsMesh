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
    ├── config.py              # 依赖方连接配置与机器人公共传输限制
    ├── context.py            # UserContext
    ├── adapter.py             # 连接归属、操作准入与清理
    ├── channel.py             # 连接状态、回复可用性、统一 send
    ├── lifecycle.py           # 按消费插件注册框架生命周期回调
    ├── callbacks/             # 每连接的队列、去重与分发
    ├── api/                   # FastAPI、固定消息协议与框架事件转换
    │   ├── channel.py         # 自己持有 FastAPI 和监听器
    │   ├── exchange.py        # 单次请求、框架分发及响应生命周期
    │   ├── requests.py        # 固定协议解析、总截止时间与依赖清理
    │   ├── server.py          # Uvicorn 监听器启停
    │   ├── streaming.py       # SSE 背压、断开取消与实际写出清理
    │   └── codec/schemas/output/attachments/errors/adapter.py
    ├── qq/                   # 自己持有 Gateway WebSocket、事件和回复客户端
    └── dingtalk/             # 自己持有官方 Stream SDK 连接和回复客户端
```

API 使用 FastAPI/Starlette 作为传输实现，但业务请求统一遵循 MessageRequest 协议。channels 校验请求结构后，把 `user` 资料转换为 UserContext，并构造框架 Event、Message 和附件引用，再进入 Rule、Permission、Matcher、Dependent 链路。命令由框架的 `on_command` 处理，普通消息由 `on_message` 处理。业务 Matcher 接收统一的 Event、Message 和 UserContext；HTTP 认证由消费插件自己的中间件或外部网关完成。

连接由使用方的依赖配置声明，各协议模块创建并管理连接。HTTP 监听、QQ 机器人和钉钉机器人均归属声明它的插件。channels 不保存业务用户目录，不定义 token 策略或管理员权限。

```json
{
  "config": {
    "model": "your-model-name"
  },
  "dependencies": {
    "channels": {
      "api": {"main": {"host": "127.0.0.1", "port": 8080}}
    }
  }
}
```

消费插件只配置 `dependencies.channels.api.<连接名>` 的传输参数，不注册处理器或身份函数。连接名默认 `main`；channels 自动收集该插件的框架 Matcher，并把固定 HTTP 消息请求转换成框架 Event、Message 和 UserContext。

API 调用必须在正文提供 `user` 资料：`id` 必填，`name`、`groups`、`roles`
可选。该资料只是输入声明，不代表认证结果；需要认证时由消费插件在自己的
API 应用上安装中间件，或依赖外部网关，channels 不读取请求头或认证对象来替代正文。
正文中的用户 ID、组和角色均可由请求方伪造，授权必须依据独立验证的身份和权限。
附件缓存仅按连接隔离，不按正文用户隔离；需要用户隔离时由消费插件或网关实施。

每个 HTTP 连接拥有独立监听器及所属插件的消息入口；不同连接使用不同端口，不隐式共享端口。未配置 HTTP 连接时不监听。插件停止会关闭自己的监听器，再启用时按当前配置重建。

QQ 使用官方 Gateway WebSocket，钉钉使用官方 Stream 长连接；两者不依赖 API 监听器。SDK 的 token、签名、握手与 ACK 属于协议认证，继续由通道管理。

### 钉钉 Stream 配置

钉钉开发者控制台中，将机器人的消息接收模式切换为 **Stream 模式**，发布相应应用配置。保留消费插件提供的 `client_id` 和 `client_secret_ref`；凭据属于该机器人连接，不属于 API 身份配置。服务器需能出站访问钉钉 HTTPS/WSS 地址，无需对公网开放回调端口。

使用方的配置文件声明连接，由 channels 在使用方就绪后自动启动：

```json
{
  "config": {"model": "your-model-name"},
  "dependencies": {
    "channels": {
      "dingtalk": {
        "main": {
          "client_id": "configured-client-id",
          "client_secret_ref": "DINGTALK_CLIENT_SECRET"
        }
      }
    }
  }
}
```

QQ 使用 `dependencies.channels.qq.<连接名称>`，账号字段为 `app_id`、`secret_ref`。
使用方只需 `require("channels")`，机器人连接参数直接放在该插件的依赖配置中；
通道自动将消息交给该插件的 `on_message`、`on_command` 和其他框架 Matcher。
连接归属当前使用方；停止时关闭并移除，重新启用按当前配置重建。不同插件声明同一
机器人账号会报出已有归属，不隐式共享或创建第二条连接。

连接限制只来自消费插件的依赖配置，不再有 channels 自身的协议配置。所有消费者就绪后启动 Stream，连接成功才加入 Driver 的在线索引；断线撤出并自动重连。关闭时先停止输入，再排空已接受消息，最后关闭回复客户端。

固定使用 [官方 dingtalk-stream 0.24.3](https://github.com/open-dingtalk/dingtalk-stream-sdk-python)。消息解析、回调及 ACK 使用 SDK 原生实现。该版本原生启动循环会吞掉取消，握手和回复请求使用无超时的同步 HTTP；因此 `stream.py` 只接管异步握手、连接任务和关闭，复用 SDK 协议逻辑。日志不输出握手 ticket、secret 或完整协议帧。回复继续使用钉钉返回的 session webhook（这是出站回复地址，不是本地 Webhook 接收端点）。

QQ 固定使用 [qqbot-agent-sdk 1.2.2](https://github.com/tencent-connect/qqbot-agent-sdk/tree/v1.2.2)。QQApiClient 管理 Token、消息及原生媒体上传；QQWebSocket 管理握手、心跳、重连和 Resume。OpsMesh 负责插件生命周期、框架事件转换、有界队列及 SDK 尚未封装的群管理接口；群管理通过 SDK 的 request() 调用。

SDK 的端点来自进程环境变量，在首次导入前设置。推荐在服务环境中配置 QQ_API_BASE=https://api.bot.qq.com 和 QQ_TOKEN_URL=https://api.bot.qq.com/app/getAppAccessToken。未配置时使用 SDK 原生默认地址。端点不再放入 config.qq；api_base、sandbox_api_base、token_url、reconnect_delay 已移除，账号配置中的 sandbox 也已移除。机器人可用范围由 QQ 开放平台管理，重连退避由 SDK 管理。

SDK 1.2.2 没有完整通知回调及可配置 intents；gateway.py 补这两个入口、原始事件 ID/序列保留及任务清理，并将正常断线返回转为 SDK 重连，避免原生监听空转。业务事件由框架在入队时去重，避免 SDK 仅按消息 ID 去重而丢失连续消息。原生 SDK 在宿主事件循环中运行，不额外启动网关线程。该版本 SDK 的内部钩子已纳入契约测试，升级依赖时必须验证。原生 EventParser 负责消息身份解析，GROUP_MESSAGE_CREATE 按相同的群消息结构补充解析。群消息使用 `GROUP_AT_MESSAGE_CREATE` 或 `GROUP_MESSAGE_CREATE` 的 `group_openid`，C2C 消息使用 `C2C_MESSAGE_CREATE` 的 `user_openid`；三者都先转换成 Framework Event，再进入有界队列和 Matcher 分发。被动回复按官方限制执行：单聊最多 4 次，群聊最多 5 次。

channels 自己只维护消息入口、健康检查和传输生命周期。业务插件通过框架处理会话、知识库或 cron 操作；它们声明 `on_message/on_command` Matcher，并由统一消息入口按插件归属开放。停止一个业务插件会关闭它自己的 HTTP 连接，不影响其他插件的监听器。

### 原生 HTTP 接口

确需额外 HTTP 接口时，调用插件在 `on_ready` 中通过 `channels.api.get_app(connection="main")` 获取已准备但尚未监听的 FastAPI 应用，使用原生 FastAPI 路由、中间件或挂载独立子应用。监听在 `on_started` 阶段由 channels 完成，此后不再支持修改中间件。业务对话和命令不使用这些自定义路由，统一走固定消息入口。

一个命名连接自动归属声明它的消费插件，并收集该插件的框架 Matcher；端点固定为 `/plugins/{plugin_id}/messages`。插件启用、停止和撤销通过框架生命周期生效，不动态生成业务 URL 路由。

## 统一消息与身份

统一消息入口使用下面的请求、回复和续接契约。chat/sandbox 示例和客户端采用该入口。channels 只提供固定消息、附件及健康检查端点。

```text
FastAPI 请求 / QQ Gateway Dispatch / 钉钉 Stream 消息
→ 协议解析、SDK 认证或 HTTP 正文 user 资料转换
→ Event + UserContext，消息事件另有 Message
→ dispatch_event
→ Permission / Rule / Matcher / Dependent
→ 插件处理函数
→ Channel 回复
```

`UserContext(id, name, groups, roles)` 只携带调用方声明的用户资料，字段冻结，组与角色是 frozenset。`Context()` 注入原对象，使用 `user.id` 访问。它的存在不代表身份已认证；消费插件按业务需要检查 Permission、Rule、Depends 或自己的 HTTP 中间件。SDK Session/执行 ID 由调用插件管理；Framework `permissions.User` 是权限匹配器。

统一 HTTP 消息入口固定为 `POST /plugins/{plugin_id}/messages`。请求体为 `MessageRequest`：`user` 必填，`message` 可以是纯文本，也可以是有序文本/图片/文件/音频/视频消息段；附件必须引用 channels 已校验的 `attachment_id`。调用者不能传入 Matcher ID、Python 函数名或任意本地路径。未知字段、空文本、非法附件和超限请求在进入框架前拒绝。

业务插件不为每个操作定义 HTTP body 或业务 REST 路由。`/new`、`/sessions`、`/use`、`/clear`、知识库查询、计划管理等都以命令或普通消息进入 Matcher；参数通过命令参数和消息段提供，业务插件负责校验资源归属。HTTP、QQ、钉钉的外部请求都转换成相同的 Event/Message/Context，避免每个通道维护不同的业务入口。

HTTP 请求必须在正文提供 `user`，其中 `id` 必填，name、groups 和 roles 可选。channels 只负责把资料转换成冻结的 `UserContext`，不实现业务权限；认证由消费插件自己的中间件或外部网关完成。QQ、钉钉从官方 SDK 事件提取平台用户信息。

业务权限在调用插件的 Rule、Permission、Depends 或处理函数中检查。QQ、钉钉只从 SDK 事件提取平台用户 ID 和显示名，不将聊天群 ID 当成业务用户组；群路由使用事件的群信息。角色、业务组及资源访问权限由调用插件决定。

标准消息入口不需要显式绑定函数。消费插件只声明 `dependencies.channels.api.<name>`，照常使用框架的 `on_command`、`on_message` 和其他 Matcher；channels 自动按插件归属收集它们：

```python
from opsmesh import on_message
from opsmesh.matchers import Matcher

chat_message = on_message()


@chat_message.handle()
async def chat_reply(matcher: Matcher) -> None:
    await matcher.finish("Received.")
```

消息入口不提供业务 path，固定使用 `POST /plugins/{plugin_id}/messages`。
调用方按统一消息协议提交内容；请求随后转换为框架
Event、Message 或命令并进入统一分发，不保留每个业务的 REST 路由。

消息请求是含 `user` 的纯文本或有序消息段数组，可声明 `conversation_type`；群消息还需提供 `group_id`，例如
`{"user": {"id": "alice"}, "conversation_type": "private", "message": "hello"}`。未声明时按 private 处理。插件不再通过 `body` 子类扩展
业务 HTTP 字段；需要业务参数时使用命令参数、消息段或插件自己的数据库对象。
`APIMessageEvent` 的类型固定为 message，包括纯附件输入；`payload` 保留 channels
的验证模型。EventMessage、EventPlainText 和 CommandArg 使用同一份 Message。

入口按插件归属接收该插件的全部 Matcher，包括运行时派生且通过作用域校验的临时续接 Matcher；不要求 `block=True`，也不过早过滤临时 Matcher。框架全局事件钩子继续承担策略和日志。

`Matcher.send/finish` 通过当前事件绑定的 Channel 工作，handler 的普通返回值被忽略。`send()` 可调用多次；`finish(message)` 发送可选消息并结束当前 Matcher，不额外发送“最终消息”标记，也不直接关闭 HTTP。`pause/reject` 改变下一条事件的处理位置，不把当前请求一直挂起等待用户。

### 通道对话标识与多轮续接

MessageRequest 接受可选的 `conversation_id`，值为规范 UUID 字符串。例：`{"user":{"id":"alice"},"conversation_id":"d250759b-8d9a-4c3a-a1af-397292fc835d","message":"hello"}`。它只是客户端的对话区分符，不是身份凭据或 SDK Session ID。私聊未传时生成新 UUID；群聊未传时根据 group_id 生成稳定 UUID。响应返回该值，客户端可保存并在后续请求中传回。

API Event 的 `get_session_id()` 返回由连接、对话类型、conversation_id 及用户 ID 共同构成的无歧义标识；连接标识本身包含协议、所属插件和连接名。机器人使用平台账号、对话及用户构造等价范围。相同 conversation_id 在不同用户、连接或插件下不共享状态。该标识用于框架续接和调用插件的映射，不进入 UserContext，也不要求 channels 建立会话数据库。

聊天插件按上述范围保存当前 SDK Session 的选择。`/new`、`/use <id>` 修改该范围的映射并回复 session_id；后续普通消息只携带 conversation_id。访问 SDK Session 仍由聊天插件校验，知道通道标识不授予业务访问权。同一范围的 HTTP 请求同时只接纳一个，忙时返回 409，不积压请求；机器人消费也按同一范围串行。跨范围选中同一个 SDK Session 时的执行并发仍由聊天插件管理。

`pause/reject` 派生的临时 Matcher 保留原始 Matcher 的关联；多次暂停继续沿用同一原始关联，不能仅比较模块或源代码行号。入口按该关联过滤，等待范围继续使用 Event.get_session_id() 和现有 Permission。下一次请求重新认证、检查插件准入及权限，再由框架优先匹配该续接。暂停的 state 只保存可跨请求使用的值，不保存 Request、依赖栈、连接或 Agent 流；下一次调用重新解析依赖。不为这条链路另外建立加载代次登记表或等待管理器。

等待复用框架现有过期检查，过期后不能再命中；不新增定时扫描服务。停止时先关闭新调用准入，排空已受理调用后，通过现有注册归属清理该插件的临时续接，保留正常静态声明；这样排空期间产生的等待也会被清理。停止未完成不能报告成功或直接重新启动。撤销继续使用既有注册清理，服务重启不恢复等待；即使只停止再启动资源、未重新导入模块，旧等待也不能复活。每个范围至多保留一个等待，channels 的同范围调用准入避免重复消费。暂停或拒绝后，本次响应根据既有 DispatchResult.suspended 标记 waiting；超时后新输入按新的消息处理，不隐式重放原操作。

### 回复协议

HTTP 的一次响应可以承载多条消息。API Channel 按调用顺序收集已发送消息；QQ/钉钉仍逐条发送。非流式请求在框架分发与清理完成后返回一个 JSON envelope：

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

status 为 completed、unhandled、waiting 或 failed；它描述本次消息处理，不代表 Agent 运行或持久任务状态。没有处理器实际处理，或事件被忽略且无错误时返回 200 和 unhandled、空 messages；命中处理器但没有回复时为 completed、空 messages。两者都不使用 204，确保客户端拿到 conversation_id。已通过认证的处理失败返回安全的 error（code、message）及已收集消息；HTTP 使用对应 4xx/5xx，不将未知异常原文返回。协议校验和认证失败可在进入分发前直接返回通道错误。

结构化结果复用 channels.Message/MessageSegment：拟新增 `MessageSegment.structured(value)`，只接受可 JSON 序列化的对象，并受输出大小限制；同一条 Message 最多含一个 data 段，有 data 时必须同时提供可读内容。HTTP 将 data 段提取为 envelope 中该消息的 data，其余段编码到 content；机器人只发送可读内容，不把 data 拼成文本。业务插件负责数据字段和序列化，不允许直接放 ORM 对象、连接或密钥。data 为出站专用段，HTTP 输入不能利用它注入业务对象。

使用形式为 `await matcher.finish(Message([MessageSegment.text("Session selected."), MessageSegment.structured({"session_id": session_id})]))`。列表等业务结果统一放在对应消息的 data 中；客户端按命令结果契约读取，不解析人类文本。

HTTP 客户端通过 `Accept: text/event-stream` 选择流式传输。普通 Matcher.send 始终发送一条完整消息，每次对应独立的 message 事件，不能把相邻文本消息自动拼成一条回答。仅选择 SSE 不会使 send 变成模型增量接口。

模型增量由 channels 提供独立的通道输出操作，沿用 Channel.call 扩展点，不新增 Matcher 方法或 SDK 事件解析器。该操作在调用者作用域内消费完整 Message 或 StreamMessageStart/StreamTextDelta/StreamMessageEnd；调用插件在 execution.stream 的上下文中选择原生 SDK 的公开输出并转换成这些通道数据，然后等待消费结束。不能返回一个依赖已经关闭的 SDK 流交给通道后台读取。结构化结果完成校验后转换为完整 Message；附件和表格可在文字块结束后作为完整 Message 穿插发送。

每个流式回答由 channels 分配一个 message_id：SSE 发出 message_start，随后发送带相同 ID 和单调 sequence 的 message_delta，最后发送 message_end。客户端仅在同一 message_id 下追加 delta；message_end 只结束这一条回答，不代表整个请求成功。普通 message 事件也带独立 message_id。非流式 HTTP 将这些增量有界聚合成一个完整消息项。QQ/钉钉默认同样聚合成完整回答再按平台长度限制发送，不逐 token 调用机器人 API；只有已验证的平台消息更新能力才可启用限频更新，并保证结束时提交完整内容。

出站 Markdown 使用 `channels.MessageSegment.markdown(text, title="Answer")`，普通字符串仍是纯文本，不检测 Markdown 标记。流式入口为 `channel.call("stream", event=event, source=source)`；source 产出类型化事件或完整 Message，在 StreamMessageStart 中设置格式，默认 text。禁止传旧字符串流或整次调用的 format 参数。SSE 的 `message_start.format` 标识整条消息的格式，JSON/完整 SSE 消息保留 `type: markdown`。输入请求协议仍使用文本或附件，不要求客户端把用户输入识别为 Markdown。

钉钉连接可配置 `card: {template_id, content_key, update_interval}`；模板须为已发布的 AI 卡片，内容字段绑定流式 Markdown 组件，并使用原生 `flowStatus`。只有 Markdown 流使用此卡片，每个 Markdown 文字块创建一张卡片，以完整快照限频更新；结束后提交最终状态。异常或取消时有界尝试标记同一张卡片失败，随后传播原异常。没有卡片配置则聚合为完整 Markdown 回复；已配置但发送失败时不自动重发另一条回复。QQ 目前保留可读 Markdown 源文本，不声明支持原生 Markdown 渲染。详见 [channels 配置](../packages/channels/README.md#explicit-markdown-and-dingtalk-ai-cards)。

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
    # Only use this profile for authorization after independently verifying it.
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

QQ 频道接口使用 guild ID，支持频道信息、成员、角色查询以及成员和全员禁言。普通 QQ 群接口使用 group openid，支持群信息、机器人状态、成员查询、成员禁言和入群申请查询/审批；普通群踢人、黑名单和自动审批策略未暴露。钉钉支持 Stream 消息、原生事件、群基础信息、互通群成员和群名操作，仍受应用权限和群类型限制。

平台通知保留原生事件类型、ID 和 payload，通过 on_notice/on_type 处理。未知身份不伪造，群业务策略由调用插件决定。准确操作名、接口及官方依据见 [channels 包说明](../packages/channels/README.md#platform-group-operations)。


## 在插件代码中选择群组与智能体

`channels.in_group(*groups, connection=None, protocol=None)` 返回框架原生 `Rule`。
群 ID 使用 API 请求的 `group_id`、QQ 的 `group_openid` 或钉钉的 `conversationId`，不使用群名称。
Rule 同时校验当前插件归属、事件的连接标识和 `conversation_type == "group"`；API、QQ、钉钉都可以命中。
两个协议存在同名连接时，指定 `protocol="api"`、`protocol="qq"` 或 `protocol="dingtalk"`。

```python
channels = require("channels")
sales = on_message(rule=channels.in_group("group-a", connection="main"))
technical = on_message(rule=channels.in_group("group-b", connection="main"))
```

处理器分别把自己的原生 Agent 交给 execution，并用 Matcher 回复。相同 Rule 可用于
`on_command`。一条机器人连接服务多个处理器；没有匹配处理器的消息不会调用模型。
自定义 Rule 的重叠遵循框架原有优先级与阻断语义，不另建路由系统。机器人能收到哪些
消息仍受平台权限影响，Rule 不扩大平台推送权限。完整示例见
[同插件多智能体](../examples/groups/README.md)。


## 内容与传输边界

智能体插件负责解析 SDK 输出、选择文件、持久化产物和决定展示格式。
`channel.send_types` 声明完整消息类型，`channel.stream_formats` 声明当前配置支持的增量格式。
插件通过框架 `Matcher.send()` / `Matcher.finish()` 回复，流通过当前 channel 的 stream 操作发送。
QQ 不支持增量格式及 Markdown 消息段；钉钉配置卡片后支持 Markdown 增量。
不支持的消息明确拒绝，插件自行转换或缓冲，不由通道猜测或静默降级。
示例共享的 files.py 保存产物选择，outputs.py 决定输出格式与缓冲，deliveries.py 处理持久化。
channels 只管理事件附件访问、协议转换、临时下载引用和实际传输结果。
HTTP 内容预算不含 JSON/SSE 外层编码，文件字节使用附件限额；处理期限同时适用于 JSON 与 SSE。


钉钉服务端接口使用 `alibabacloud_dingtalk==2.2.67` 原生异步客户端：
OAuth 令牌、IM 群操作、机器人消息、附件下载地址及卡片创建和更新。
请求模型、响应模型和协议序列化由 SDK 提供；channels 保留消息转换、令牌缓存、
超时、取消和交付记录。SDK 自动重试关闭，避免重复发送。
会话 Webhook、旧版素材上传以及 Stream 异步握手保留独立 HTTP 调用；
前两项在当前服务端 SDK 中未发现对应封装，Stream SDK 的握手仍为同步调用。
附件接口仅返回下载地址，实际字节读取仍受大小、超时及地址检查限制。


## 接收、排空与回复资源

QQ、钉钉的每一条连接由 channels 内部的 `ChannelAdapter` 管理归属与操作准入；由 `Channel` 持有本连接的去重、队列、SDK client 和连接任务。
框架仍负责 Driver、插件生命周期、Matcher、Dependent、PluginCall 和事件分发。
协议适配器负责解析事件、调用各自 SDK 和编码平台 ACK。连接资源按连接独立，不存在跨消费者共享的 client 或队列。

`ready` 表示接收连接已就绪；`replies_available` 表示出站资源仍可使用。
长连接断开或停止接收不会撤销已经受理的事件。停机先拒绝新事件，排空或取消已有工作，
再关闭出站客户端。旧插件代次的事件不能进入重新启用后的处理器。

QQ 私聊和明确 @ 机器人的群消息满足 `to_me`；普通群消息不满足。
持续监听群消息应由调用插件的 Matcher/Rule 选择，不通过改变 `to_me` 实现。

普通 HTTP 路由及标准消息入口的 `request_timeout_seconds` 从读取请求体前开始计算，
读取与处理共用截止时间；附件上传读取也受该超时约束。SSE 的响应任务使用剩余预算，
客户端断开会取消处理并释放依赖。权限拒绝统一为 403、插件不可用为 503、超时为 504；
SSE 已开始时通过 error 帧表达，不能再改变 HTTP 状态码。
