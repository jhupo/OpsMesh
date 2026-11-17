# Channels 结构与行为评审

评审对象为当前工作区，包括尚未提交的 SDK 替换及结构精简。基准提交：
`7650db2deae04f399c648b46a828a7606cb370d8`。以下保留初次评审依据；修复结果见文末。

## 初次评审结论

公共连接状态和消息准备已经收拢，但连接声明、事件准入、启停、HTTP 请求执行仍有重复。
部分重复已造成实际行为分叉。当前不能认定通道结构已经完善，建议修复以下问题后再发布。

检查覆盖 QQ、钉钉、HTTP、附件、消息、流式输出、交付记录和生命周期，并对照框架的
Channel、Matcher、dispatch_event、Dependent、PluginCall 使用方式。
当前 Python 源码共 72 个文件、9034 行（含空行和注释）：API 2880 行、钉钉 2107 行、
QQ 1796 行，公共层与其他目录共 2251 行。规模本身不是缺陷，重复职责和行为分叉才是。

## 初次确认的问题

### P1：停止接收或短暂断线会丢弃已准入的机器人事件

`qq/adapter.py:179` 先停止 Stream，再排空队列；`qq/stream.py:91` 在正常停止时也
调用 stream_disconnected，使 channel.ready 变为 False。`_work.py:62` 因此直接跳过
已经入队的事件。`qq/adapter.py:398` 同时拒绝仍在执行的处理器发送回复。

同一个队列判断也影响 QQ、钉钉短暂断线：入队和 ACK 已完成的事件可能被丢弃，
去重记录仍在，不一定能靠重发恢复。

无网络探针使用真实 QQStream.run 的取消清理和 dispatch_queued，得到：
`ready_after_intake_stop=False, admitted_event_dispatched=False`。

修复方向：区分接收连接就绪和已准入工作/出站能力。停止接收后先排空或明确取消，
最后关闭出站资源；保留框架 PluginCall 与 generation 检查，不能绕过插件卸载隔离。

### P2：HTTP 请求体读取不受请求超时约束

普通路由在 `api/channel.py:298` 读 body，超时从第 309 行分发才开始；标准消息在
`api/exchange.py:59` 读 body，超时从第 98 行才开始。附件上传也只有大小限制。
`api/requests.py:7` 的 read_body 没有时间限制。

配置 0.01 秒超时的只读探针，在 receive 延迟 0.06 秒时仍成功读取，耗时约 0.063 秒。
已认证的慢请求可以长时间占用插件准入和请求资源。

修复方向：在读取请求体前建立统一的读取/请求截止时间，覆盖普通路由、消息和上传。
分发超时、SSE 断开清理仍需保留各自语义。

### P2：普通 HTTP 与标准消息的异常映射不一致

`api/channel.py:277` 将 PermissionError 当作未知异常返回 500；
`api/exchange.py:216` 返回 403。只读探针对同一个拒绝异常确认了 500/403 差异。

修复方向：在 api/errors.py 统一异常分类和 ExceptionGroup 解包；JSON 与 SSE 只负责
各自响应编码，不分别定义业务错误语义。

### P2：QQ 普通群消息也被标记为发给机器人

`qq/events.py:147` 的 QQMessageEvent.is_tome 无条件返回 True。
解析器已经接受 GROUP_MESSAGE_CREATE，却未保留它与 GROUP_AT_MESSAGE_CREATE 的区别。
只读探针对两类事件均得到 True。这会让使用框架 to_me 规则的插件响应未 @ 机器人的群消息。

修复方向：解析时保留是否明确指向机器人的语义；私聊、明确 @ 和普通群消息应分别处理。
持续监听由调用插件的 Matcher/Rule 决定，不能通过一律标记 to_me 实现。

## 初次识别的重复职责

| 位置 | 重复内容 | 收敛方向 |
| --- | --- | --- |
| qq/adapter.py、dingtalk/adapter.py 的 declare | 归属解析、命名校验、声明保存、撤销及生命周期闭包 | 单一机器人连接声明流程，厂商负责创建具体连接 |
| 两个 _resolve_owner、lifecycle/transports.py | 三处解析插件归属，ModuleType 处理还有差异 | 统一归属解析，复用框架插件上下文 |
| 两个 connections.py | 获取/创建 adapter、凭据及账号独占校验、transport 注册 | 与声明流程明确分工，删除纯转发步骤 |
| 两个 adapter 的回调接收 | 去重、准入、入队、失败释放、撤销去重 | 一份公共准入流程；解析和 ACK 编码留给协议 |
| 两个 adapter 的启停与断线处理 | 连接集合、状态迁移、停止及排空 | 优先统一生命周期不变量，避免再出现丢弃已准入工作 |
| APIChannel 与 MessageExchange | 准入、依赖栈、分发、异常及响应资源归属 | 共享请求执行作用域；普通 Response 和消息输出保留不同策略 |
| sending.prepare 与 api.attachments.prepare_message | 遍历消息和收集附件引用 | 仅共享内容校验/解析；HTTP 导出 URL 与机器人原生上传保持分离 |
| operations.operation_response 与 DingTalkClient.invoke | 错误码过滤、状态分类 | 共用平台错误构造；httpx 与 Tea 异常解码仍各自负责 |

不要为这些重复新增万能 registry、通用协议执行引擎或多层基类。建议先收拢声明、
准入和请求作用域三个实际流程，而不是继续把相同名称的文件机械合并。

## 应保留的边界

- 框架负责 Matcher、依赖注入、权限/规则、事件分发与插件调用作用域。
- 网络回调队列提供容量和会话串行，最终仍调用 dispatch_event，不是另一套分发框架。
- FastAPI/Starlette 承担 HTTP 监听、认证入口及 ASGI 响应；Bearer 身份验证不能由 Matcher 替代。
- APIRouter 登记 URL 与插件归属，底层确实复用原生 Matcher、Dependent 和 dispatch_event。
- channels/streaming.py 校验逻辑消息流，api/streaming.py 处理 ASGI 背压和断开，
  dingtalk/cards.py 处理原生卡片更新。这三者不是同一种职责。
- AttachmentCollection 是单次事件的懒加载文件集合，API 上传缓存跨 HTTP 请求存活；生命周期不同。
- Delivery 是每次回复的可选交付观察，不含数据库、重试调度或持久 Outbox，不宜简单删除。
- QQ 与钉钉事件中的 ID、群/私聊、ACK 和媒体格式存在真实协议差异，不应强制使用同一个原始模型。
- 钉钉两个官方 SDK 分别负责 Stream 和服务端 API，双依赖不是冗余实现。

## 初次评审的验证与限制

GitNexus 已以 --pdg --index-only 更新索引；局部变更识别 85 个符号，公共 RobotChannel
及 prepare 的直接依赖与源码相符。索引提示部分动态调用和执行流被截断，因此没有把图中
缺少路径当成无调用或安全证明。附件下载的 taint 查询没有发现记录，也不代表完整 SSRF 防护。

本次执行了 QQ is_tome、QQ 停止接收/队列执行、HTTP 读取超时、HTTP 错误分类的只读探针。
未运行全量测试，未发送真实机器人消息，未修改或部署生产代码。之前通过的单元测试未覆盖
上述全部组合路径，不能作为本轮发现问题不存在的依据。


## 首轮修复后的结构与行为（后续精简见文末）

四个确认问题均已修复，改动限于 channels 及对应测试、文档，未修改框架核心。

| 职责 | 实现位置与结果 |
| --- | --- |
| 连接声明和生命周期 | `adapter.py` 的 `RobotAdapter` 复用框架 Driver，统一声明、归属、启动、停止接收、排空、关闭；QQ 和钉钉只提供具体 SDK 连接和账号资源处理 |
| 连接状态 | `channel.py` 区分接收连接 `ready` 与回复资源 `replies_available`；断线只撤下接收状态，已准入消息继续执行，真正关闭后不再发送 |
| 事件准入 | `_work.py` 的 `enqueue` 共用去重、PluginCall 获取、入队及拒绝清理；失败允许平台重试，成功项保留原插件代次 |
| 插件归属 | `_validation.py` 统一当前插件和显式模块的归属解析；声明的生命周期回调注册在实际拥有者的框架作用域 |
| 协议连接入口 | 两个 `connections.py` 只负责对应账号配置、凭据和账号独占校验；适配器获取/创建复用 Driver，没有新注册表 |
| 请求执行作用域 | `api/requests.py` 的 `RequestScope` 共享准入、截止时间和跨 ASGI 任务的插件作用域；不跨任务传递 AnyIO CancelScope |
| HTTP 超时 | 普通路由、JSON 消息和 SSE 消息的读取与分发共享一个时间预算；附件上传读取也受请求超时约束 |
| HTTP 错误 | `api/errors.py` 统一单叶异常组、权限拒绝、超时、不可用和校验失败；取消/客户端断开继续传播，JSON/SSE 保留各自编码 |
| QQ 消息目标 | 私聊和 `GROUP_AT_MESSAGE_CREATE` 的 `to_me=True`；普通 `GROUP_MESSAGE_CREATE=False`，不伪造 @ 语义 |
| 平台操作错误 | `operations.py` 统一安全错误码、请求 ID 和状态分类；HTTP 与 Tea SDK 各自解码原始响应 |
| 附件与消息 | 继续共用 `normalize`、支持类型检查、`prepare_attachment`；HTTP 的上传缓存与下载 URL、机器人原生媒体发送保留协议职责 |

请求依赖栈和传输占用仍有意分开：标准消息的依赖可以在处理结束时清理，但插件占用和
会话占用必须保留到实际响应写入结束；普通流式路由由 StreamLease 接管依赖清理。
没有为统一名称而合并这些不同的资源生命周期。

队列仍是有界进程内缓冲，不提供持久交付。进程崩溃或排空超时后的取消不承诺自动补发；
本次修复的是正常停机与接收连接断开时错误跳过已受理消息的问题。

## 修复验证

17 个受影响的通道、API 与多模态测试文件共 369 项通过（35.65 秒）。
Ruff 和差异空白检查通过；未运行全仓库测试。

新增回归覆盖慢请求体、读取与执行共享截止时间、JSON/SSE 权限错误一致性、QQ to_me、
接收断线后的已准入执行、停止接收后的队列排空和回复，以及插件代次变更隔离。
SDK 测试仍使用模拟传输，执行原生序列化和异常处理，不发送真实机器人消息。
本次未提交、打 tag、发布或更新服务器。


## 第二轮职责精简

- 删除 API 通用 REST 路由装饰器、输入输出类型推导、动态路由冲突处理及 HTTPHandler。只保留固定消息入口，使用已有框架 Matcher/Dependent/dispatch_event。
- `api/entrances.py` 保存插件与 Matcher 的绑定；`api/channel.py` 只挂载一条参数化消息路由，停用、启用与撤销不重建监听器。
- APIMessageEvent 直接继承框架 Event。JSON 与 SSE 使用同一 MessageExchange、权限错误分类和请求总截止时间；SSE 直接使用 Starlette StreamingResponse。
- 自定义 HTTP 接口使用 `get_app()` 暴露的原生 FastAPI，由调用插件负责认证、准入和撤销。没有保留旧路由接口或兼容别名。
- 删除 QQ、钉钉的 connections.py 转发层，账号配置与独占校验由对应 Adapter 处理，公共 RobotAdapter 继续复用框架生命周期。
- ChannelTransports 采用一套明确的生命周期协议；同一个插件拥有多个机器人账号时，共享传输只注册一次生命周期回调。
- 保留消息输入/输出、SDK 协议差异、有界队列、附件传输限制和实际写出回执；它们仍有具体调用和不同的生命周期要求。

框架核心未修改。chat/sandbox 示例继续使用已有消息与命令处理器；测试调用已迁移到新入口，旧 REST 测试随已删除功能移除，并补充消息入口的鉴权、总期限、动态撤销及原生 FastAPI 路由归属测试。


### 第二轮验证结果

channels Python 源码（含空行和注释）从本轮开始时的 73 文件 / 8,915 行，收敛到 70 文件 / 8,090 行，净减少 825 行。API 从 2,894 行降至 2,144 行。计数不包含测试、文档和依赖。

19 个受影响测试模块覆盖 321 项。首次运行 320 项通过，1 项 SSE 超时测试依赖 1 ms 与 10 ms 定时器先后顺序出现不稳定结果；将该用例改为等待中的处理器超时取消后，定向复验 API 消息协议 36 项全部通过，其余 285 项此前通过。包括 chat/sandbox 示例、消息续接、权限、附件、JSON/SSE、机器人生命周期和模拟 SDK 传输。Ruff 检查、格式检查、差异空白检查通过。

未运行全仓库测试、未发送真实机器人消息、未更新服务器。
