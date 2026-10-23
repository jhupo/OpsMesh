# Gateway

OpsMesh 自己管理通道、外部身份、账号绑定、路由与可靠投递。内置 QQ 官方机器人和
钉钉应用机器人；仅参考 octop 的协议处理方式，不加载它的网关运行库。
来源与 MIT 许可见 [NOTICE](../../../NOTICE) 和 `adapters/UPSTREAM-LICENSE`。
其他通道使用插件 SDK 的 `ChannelAdapter` 注册。

## 所有权与目录

| 文件或目录 | 职责 |
| --- | --- |
| contracts.py、ports.py | 通道、路由、事件与执行端口 |
| api/ | 管理路由和 HTTP 输入；不启动连接 |
| channels/ | 配置策略、注册、插件适配、租约、生命周期与扫码任务 |
| identity/ | 外部身份、账号绑定、已见会话与成员事实 |
| routing/ | 身份/群路由、提交前授权、原生 Session 映射 |
| messaging/ | 原始收件、过滤插件、合批、输入转换、调度、发件、恢复、推送与附件 |
| storage/ | ORM、加密配置与事务审计 |
| adapters/qq、adapters/dingtalk | 配置校验、官方认证、连接、事件解析和发送 |

```text
gateway/
  __init__.py
  contracts.py
  ports.py
  api/          routes.py, schemas.py
  channels/     manager.py, leases.py, registry.py, plugins.py, policy.py, bindings.py
  identity/     identities.py, conversations.py
  routing/      service.py, sessions.py
  messaging/    inbox.py, filters.py, inputs.py, dispatcher.py, commands.py, outbox.py, recovery.py, push.py, media.py
  storage/      models.py, store.py
  adapters/
    qq/         adapter.py, config.py, client.py, connection.py, events.py, content.py, messages.py, binding.py
    dingtalk/   adapter.py, config.py, client.py, connection.py, events.py, messages.py
```

路由与提交前授权统一在 `routing/service.py`，执行事件统一在 `contracts.py`；
原顶层路径不提供兼容包装。连接、令牌和厂商错误归适配器，投递队列、顺序、限流与恢复
归消息处理；不在两层分别实现一套自动重发。

网关通过 `ExecutionPort` 调用执行层，不导入 Agent、execution、workflows 或
Temporal。`bootstrap/gateway.py` 负责组合。API 进程只提供管理接口；
`opsmesh-server gateway` 启动独立连接进程，Docker 与本机安装共用此入口。
Compose 使用 API 镜像运行 gateway 服务，不额外暴露端口或 Docker socket。

多个网关进程使用每通道数据库租约避免同时消费。配置变更约两秒内协调，
插件启停和版本变更自动重载。持有租约表示进程拥有消费权，不证明厂商连接已成功。
停止通道不删除实例、沙箱、Session 或已经提交的 Run。

## 绑定流程

所有路径相对 `/api/v1`。`GET /gateway/kinds` 要求登录，其他接口要求平台管理员。
凭据与回复地址加密保存；查询连接只返回已配置字段名称。

1. `POST /gateway/channels` 创建禁用通道。
   QQ 配置为 `app_id/app_secret`，可选 `sandbox/intents`；钉钉配置为
   `app_key/app_secret/robot_code/corp_id`。凭据来自厂商开发者平台。
2. `PATCH /gateway/channels/{id}` 提交 `revision` 和 `enabled: true`。
   用户发送消息后，`GET .../identities` 查看实际观察到的身份。
3. `PUT .../identities/{identity_id}/binding` 提交 `{"user_id":"平台用户 UUID"}`。
   管理员必须核实真人与外部身份；不能根据昵称自动绑定。
4. `POST .../routes` 提交 `identity_id/target_kind/target_id`，目标为已有且该用户
   有权使用的执行实例或编排实例。默认 `conversation_id: ""` 只用于私聊。
   群聊必须配置具体 conversation ID 并显式 `allow_group: true`。
5. 未绑定消息进入 blocked。修正绑定或路由后可调用
   `POST .../inbox/{inbox_id}/retry`。`GET .../inbox` 和 `.../deliveries` 返回最近
   100 条状态。运行中每次提交和发送前重新校验当前账号、绑定与路由。

`GET .../conversations` 与 `.../conversations/{key}/members` 只返回已观察事实，
不是全量组织目录。钉钉 `isAdmin` 是外部角色提示，绝不升级为平台管理员。
QQ 的 user_openid、member_openid 和 guild_user_id 分别保存，不能互相推断。
解绑会禁用该身份的个人路由，但保留原用户的 Session 和历史。修改路由要提供当前 revision。
`POST .../probe` 只校验凭据，不启动消息连接；QQ 查询机器人身份，钉钉验证令牌获取。

公开群可另外创建共享群路由：省略 identity_id，提交已观察会话的 conversation_key、
受限平台账号 user_id、target_kind/target_id 和 allow_group: true。
conversation_key 包含通道范围、会话类型和外部 ID，私聊不能命中这个路由。
共享群路由优先于成员个人路由，关闭后拒绝该群，不回退到成员的个人权限。
群友不必逐个绑定账号；平台权限来自明确配置的服务账号，外部 ID 与群角色逐条保留。

Agent Session 按通道、平台账号、目标实例与会话范围隔离。默认同群同账号同 Agent
共享 Session；`session_scope: "user"` 可显式按发送者隔离。私聊始终按身份隔离。
路由 revision 更新本身不丢失历史，切换账号或目标实例会进入不同 Session。
`/help`、`/new`、`/reset`、`/cancel`、`/stop` 支持 Agent 路由；群内非 help 命令还需
匹配 control_roles，默认 owner/admin。未知角色不获得控制权限。
Workflow 输入为 `{"messages":[{"role":"user","content":[...]}],"gateway":{...}}`。
gateway 包含平台用户及角色、外部身份、会话、提及/引用和当前批次；不包含回复凭据或历史拼接。
实例的输入 schema 必须接受该结构，其 Agent 节点继续继承可信调用来源。
网关来源的编排 Agent Session 按用户、会话范围、编排实例和 Agent 绑定隔离，
不会复用其他用户的固定 Session；非网关编排仍使用显式实例绑定。

路由默认 `output_mode: "text"`。Agent 或 Workflow 可显式使用 `output_mode: "message"`，
输出必须为 `{"action":"reply","content":[{"type":"text","text":"..."}]}` 或
`{"action":"ignore","content":[]}`。其他对象不会被当作决策指令，结构错误会阻断该收件。
显式开启目标群的 `allow_recall: true` 后，还可返回
`{"action":"recall","content":[]}`，撤回当前入站消息。目标群和消息 ID 仅从原始收件
回复地址取得，模型不能指定其他目标。撤回仍经过持久发件、发送前授权与适配器能力检查；
QQ 需有厂商授予的群管理权限，钉钉当前不支持。默认不允许撤回。
TypeSafe 只存在于[独立审核插件示例](../../../sdk/python/examples/typesafe-decision/README.md)，
网关不选择决策厂商、不根据外部群角色授权工具、不固定业务分支。

## 消息策略

通道 `policy` 独立于厂商 `config` 加密存储。`private_enabled` 控制私聊激活；
`group` 设置默认群策略，`groups` 按具体会话 ID 覆盖完整群策略。
默认只在提及机器人时激活。`visibility` 描述收到事件的可见性，不负责读取历史。
`activation: "always"` 必须同时显式配置 visibility=all，且该厂商事件确认为全消息可见；
配置不能使 QQ/钉钉获得厂商未授予的事件订阅权限。

消息顺序为：接收入库 → 配置的 message_filter 逐条审核 → 激活检查 → 有界合批 →
目标 Agent/Workflow。审核使用原始消息，禁止把未审核的后续收件先拼进当前消息。
每个过滤器返回 allow/drop/recall；异常与超时进入有界重试，默认 3 次、间隔 1 秒，
耗尽后 blocked，管理员可以修复配置后显式 retry。审核结果加密保存在原始收件。
recall 指向被拒绝的那一条原文，受群授权、适配器能力与厂商权限限制。
配置版本变化时待提交消息重新审核；已经提交的 Run 不重新执行。

过滤插件只审核内容。需要按机器人名字或接续对话判断回复时，由 Agent 配置/业务插件
负责；全消息激活后可由结构化 Agent 返回 reply/ignore。单 Agent 不需要额外编排。
只有原始消息的协议值转换为原生 SDK 输入。历史由原生 Session 读取和保存，
不保留 history_limit/history_ttl_seconds/history_scope/clear_after_reply 旧配置接口，
不从本地收件或平台 recent_messages 构造另一份聊天历史。

公开群聊可由部署策略把该群外部身份路由到受限机器人服务账号，无需群成员各自注册。
此时外部发送者、群角色和会话隔离仍保留；平台权限来自服务账号而非群主或管理员身份。
管理员应限制可接入群、Agent 和工具；网关本身不自动创建用户或绑定未知身份。

`workers` 控制每通道独立任务并发，慢审核不会等待整轮任务结束才调度其他会话。
同一 Session 的普通消息按收件顺序处理，
控制命令可以越过正在运行的请求以执行取消。
入站落库后立即唤醒调度，生成发件后立即唤醒投递；运行中结果每 200 ms 检查，
空闲时保留每秒一次恢复扫描，不依赖定时扫描才发现本进程新收到的消息。
`debounce_seconds` 控制合并前的静默窗口，0 表示关闭；batch_max_messages 默认 20，
最多 100 条，每批约束 1 MB 序列化收件。batch_max_wait_seconds 默认 2 秒、最多 30 秒。
原始收件、每条发送者、消息 ID 与路由快照保留；运行中到达的消息形成下一批。
混合发送者的 InvocationSource 顶层身份/群角色留空，participants 逐条提供事实；
不能把首位管理员的角色继承给整批。执行提交、恢复和发送前重新核对所有成员的路由。

多人群聊始终把每条原文作为独立 user 消息提交，保留发送者、消息 ID、提及和引用；
不把窗口内所有人合成一个发言者，也不把群友标成 assistant。
需要理解未提及机器人的接话时，部署方可配置 visibility=all、activation=always、
session_scope=group 和 output_mode=message，让 Agent 根据共享 Session 返回 reply/ignore。
只激活提及消息时，其他闲聊不会进入模型记忆。称呼、接话与避免重复回复属于 Agent 配置，
不写入网关的通用提示词。滑动窗口只合并尚未提交的新收件，不重复搬运 Session 历史。

`send_limit/send_window_seconds` 使用持久固定窗口计数，
发送失败重试同样占用次数；`send_timeout_seconds` 限制单次发送。
`response_timeout_seconds` 传给 Agent Run，编排总时限由 WorkflowSpec.limits 控制。
`progress_after_seconds` 非零时发送一次占位提示；`show_tool_hints` 只发通用步骤提示。
`tool_hint_throttle_seconds` 限制工具提示频率。`show_thinking` 只转发模型供应商显式提供的
公开 reasoning summary，不读取或展示隐藏推理；供应商不返回摘要时没有相应提示。
终态入队时丢弃从未发送的旧占位和 typing，已尝试且结果不确定的请求仍需人工处理。
`typing_interval_seconds`、`streaming` 需适配器声明对应能力。

## 投递与权限

输入用厂商稳定事件 ID 去重，持久收件成功后才确认厂商消息。执行提交与收件关联
在同一事务落库。最终结果写入发件箱，按分片顺序发送。
断线会重连，进程崩溃后恢复已收件任务。厂商未投递或已经过期的事件不能由本地补回。

发送期间失联或响应无法判定时标记 uncertain，后续分片等待处理，禁止自动重发。
`POST .../deliveries/{id}/resolve` 可用 `action: acknowledge` 和已核实的 `remote_id`
确认发送，或用 `action: retry` 重试。uncertain 重试要求显式
`accept_duplicate_risk: true`。所有配置、绑定、路由与恢复修改同事务写审计。

SDK 接收可信 Principal 与 InvocationSource，工具支持发现过滤和执行前授权。
业务插件负责业务数据范围与返回内容，群回复策略可根据 `reply_scope` 拒绝敏感操作。
身份提示词只是让模型理解调用者；不能替代代码授权，也不保证模型不会复述历史内容。
群路由应使用适合公开回答的 Agent 与工具。

附件按通道存于 `StorageLayout.gateway_media`，使用不透明 key，不接受本地任意路径。
单附件下载和文件读写上限 8 MB；模型输入仍受 execution 的 1 MB 请求上限约束，
过大的模型输入会被拒绝。下载仅允许公共 HTTPS、443 端口，禁止重定向和私网地址。
媒体来源为不透明 key、公共 HTTPS URL 或 base64 三选一；URL 和 base64 在入队前归一化
到通道媒体存储。QQ 频道图片接口需要保留公共 URL，本地文件不能冒充可访问地址。
媒体保留与清理由部署方管理。QQ 发送文本、图片、音视频、文件；钉钉发送文本、图片、音频、
文件，具体取决于机器人接口与权限。当前执行桥输出最终文本，厂商适配器的媒体发送
可通过编排的显式 message 输出和推送接口使用，不从模型任意输出中猜测媒体地址。

QQ 的富媒体标签、语音转写、引用消息与事件附带近期上下文在适配器内解析。
钉钉短文本使用 Markdown 模板，超过 3500 字符使用纯文本模板；Webhook 和主动消息一致。
当前批次附件通过执行桥转换为原生 SDK 多模态消息；模型是否接受音视频与文件
仍取决于所选模型和接口。编排 Agent 节点用 `input_mode: "messages"` 接收原生 messages，
默认 json 模式继续把节点参数作为 JSON 文本。

QQ C2C 的 Agent text 路由可启用 streaming：使用官方 Temporal Agents 集成的
`Runner.run_streamed`、`WorkflowStream` 和原生 `Model.stream_response`，
仅把文本增量写入持久事件日志，再经发件箱生成 replace 帧及结束帧。
未发送的增量会合并，已发送或结果不确定的请求不改写，重连不重新生成模型输出。
替换帧保留平台已接受的前缀，最终文本发生修订时不会删除已经公开的内容。
支持根 Agent、公开 handoff 与根 SandboxAgent；Agent-as-tool 及其后代不公开中间文本，
同时作为工具和 handoff 的 Agent 也按内部角色处理。
图内存在输入/输出守卫或结构化输出时只发最终结果，防止绕过校验。
不转发隐藏 reasoning；Workflow/message 决策路由也不提前转发子节点文本。
QQ群、频道和钉钉当前使用完整消息回复，不承诺逐 Token 送达。

## 主动推送与扫码

`POST /gateway/channels/{id}/push` 提交 request_id、recipients 和 content。
recipients 是本通道已完成或被动收件的 UUID，作为已核实身份与会话的依据；
不接受任意目标地址。一次最多 100 个收件依据、500 条投递，多个依据不能指向同一目标。
可以发送文本或 ContentPart 媒体。`GET .../subjects` 列出仍有有效绑定、路由的已知会话，
返回可用于推送的 receipt_id，不暴露回复凭据。
广播使用 `{"request_id":"UUID","broadcast":true,"content":[...]}`，不能同时提供 recipients。
只枚举本通道已观察且仍可授权的会话，同一实际目的地去重，一次最多 10000 条投递。
群会话只发一次；这不是发现平台上所有用户或所有群。
相同 request_id 和相同请求只返回已有投递；变更内容会冲突。
入队前及实际投递前均检查绑定和目标权限，复用原发件箱、限流、审计和 uncertain 恢复。
厂商仍可因主动消息额度、会话范围或权限拒绝发送。

`POST /gateway/bindings/qq` 提交 name，返回任务 ID、8 分钟有效期和可生成二维码的 URL。
`POST .../{id}/poll` 单次轮询，`DELETE .../{id}` 取消本地任务。
任务归创建管理员所有，一次性解密材料加密存储，终态清除；成功时同事务创建禁用的 QQ
通道，只返回 channel_id，不返回 AppSecret，也不根据扫码人的 OpenID 自动绑定平台账号。
扫码、启用通道、用户核实绑定和配置授权路由是不同操作。

## Octop 能力对照

源码对照基线为 [octop-gateway 224ed07](https://github.com/TencentCloud/octop-gateway/tree/224ed078ce20042fb5fa59240d6e3c7c34bcd67f)，
不代表与上游所有版本或所有参数完全一致。

| 能力 | OpsMesh 当前实现与范围 |
| --- | --- |
| 统一消息、媒体与适配器扩展 | SDK 合同；内置 QQ/钉钉，其他通道走插件 |
| 多通道、工作池与批处理 | 通道租约、受限并发、会话顺序、静默合并、控制命令、配置/插件热重载 |
| 消息、完成、错误与工具事件 | 持久 Run 事件和终态、节流步骤提示、公开推理摘要；错误不静默重跑 |
| 群提及、可见性、激活与会话 | 默认/分群策略、共享群路由、原生 Session；不复制上游短期历史拼接 |
| 已知会话与观察用户 | subjects、conversations、members；上游同样主要依赖已见消息，不等于全量群目录 |
| 流式文本与 typing | QQ C2C，合并 replace 帧、独立索引、已接受前缀、结束帧；守卫/内部 Agent 有公开限制 |
| 超时、占位提示、发送限流 | 已接入；编排总时限由其定义管理 |
| 单发、媒体与广播推送 | 显式收件或 broadcast 已知会话，授权后复用持久发件箱 |
| 凭据探测 | QQ 身份探测、钉钉令牌探测；不代表 Stream 已连接 |
| QQ 扫码绑定 | 创建禁用通道；不自动登录或授权平台用户 |
| 媒体来源 | 公共 URL、base64、不透明存储 key；不接受任意服务器文件路径 |
| 其他平台 | 插件协议支持；按项目范围只内置 QQ、钉钉 |
| 持久性与平台权限 | 加密、审计、幂等、Session 隔离、授权重查和不确定投递恢复 |

这些项目对应上游通用网关和 QQ/钉钉的能力范围；接口参数、安全约束、错误恢复与存储实现
按 OpsMesh 的持久执行架构设计，不提供上游 API 兼容层。代码对照不等于真实通道验收。

## 升级

迁移 `0004_native_gateway` 保留旧配置与旧绑定表，并禁用旧连接。旧数据缺少身份范围、
ID 类型和可信绑定依据，不能自动迁移成新权限。管理员需补齐新配置、核实身份并重建
绑定和路由。历史迁移不修改；降级需要按备份恢复方案处理。
先执行数据库迁移，再部署 API、Worker 和 Gateway；程序版本升级需要重启这些进程，
日常配置与插件变更无需整体重启。

0.1.2 使用迁移 `0007_gateway_admission`，增加共享群路由、审核状态和 Session 范围映射。
先暂停通道并排空 submitted 消息和旧 Run，再备份、迁移和部署新进程。
迁移将旧 group.history_scope 转为 session_scope，删除旧历史拼接选项；不保留运行时兼容分支。
旧原始收件、Session 文件和映射记录保留，新范围从新 Session 开始，不合并不同旧用户历史。
该迁移不支持安全反向拆分共享 Session；降级必须恢复升级前的协调备份。
完整证据与限制见 [验收记录](../../../docs/acceptance.md)。
本次同时改变 Agent 模型引用与 Temporal 流式调用路径。发布前必须针对已有历史做重放验收；
未完成重放验证时，先排空旧 Agent Workflow，再切换新 Worker，不能直接热替换运行中的历史。

静态检查不代表真实 QQ、钉钉、PostgreSQL、Temporal 和模型链路已经验收。
