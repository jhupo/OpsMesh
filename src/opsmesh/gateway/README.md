# Gateway

OpsMesh 自己管理通道、唯一执行目标、外部身份和可靠投递。内置 QQ 官方机器人和
钉钉应用机器人；仅参考 octop 的协议处理方式，不加载它的网关运行库。
来源与 MIT 许可见 [NOTICE](../../../NOTICE) 和 `adapters/UPSTREAM-LICENSE`。
其他通道使用插件 SDK 的 `ChannelAdapter` 注册。

## 所有权与目录

| 文件或目录 | 职责 |
| --- | --- |
| contracts.py、ports.py | 通道目标、事件与执行端口 |
| api/ | 管理路由和 HTTP 输入；不启动连接 |
| channels/ | 配置策略、注册、插件适配、租约、生命周期与扫码任务 |
| identity/ | 外部身份、账号绑定、已见会话与成员事实 |
| routing/ | 通道目标授权、原生 Session 映射 |
| messaging/ | 原始收件、过滤插件、合批、输入转换、调度、发件、恢复、推送与附件 |
| management/ | 会话操作授权、幂等回执、审批恢复、独立执行队列与非聊天事件 |
| storage/ | ORM、加密配置与事务审计 |
| adapters/qq、adapters/dingtalk | 配置校验、官方认证、连接、事件解析和发送 |

```text
gateway/
  __init__.py
  contracts.py
  ports.py
  api/          routes.py, schemas.py, dependencies.py, management.py
  channels/     manager.py, leases.py, registry.py, plugins.py, policy.py, bindings.py
  identity/     identities.py, conversations.py
  routing/      service.py, sessions.py
  messaging/    inbox.py, filters.py, inputs.py, dispatcher.py, commands.py, outbox.py, recovery.py, push.py, media.py
  management/   contracts.py, authorization.py, service.py, worker.py, events.py, tools.py
  storage/      models.py, store.py
  adapters/
    qq/         adapter.py, config.py, client.py, connection.py, events.py, content.py, messages.py, management.py, binding.py
    dingtalk/   adapter.py, config.py, client.py, connection.py, events.py, messages.py
```

通道目标与提交前授权统一在 `routing/service.py`，执行事件统一在 `contracts.py`；
原顶层路径不提供兼容包装。连接、令牌和厂商错误归适配器，投递队列、顺序、限流与恢复
归消息处理；不在两层分别实现一套自动重发。
会话管理的授权配置、操作 API、审批、恢复和平台限制见 [管理模块](management/README.md)。

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

1. 创建已有 Agent 或 Workflow 的持久执行实例；为通道选择有权访问该实例的受限平台账号。
2. `POST /gateway/channels` 创建通道，在 `target` 内提交
   `user_id`、`target_kind: "agent" | "workflow"`、`target_id`，可选 `output_mode`。
   一个通道只有这一个执行目标，私聊和所有允许群都使用它。
   QQ 的 `config` 为 `app_id/app_secret`，可选 `sandbox/intents`；钉钉为
   `app_key/app_secret/robot_code/corp_id`。可先创建未绑定的禁用通道。
3. `policy.private_enabled` 控制私聊入口；`policy.groups` 的键是允许接入的群/频道外部 ID，
   值是该群完整的 GroupPolicy。空字典不接入任何群，`policy.group` 仅提供策略默认值。
   QQ 应填官方 group_openid，不能用群号码替代。
4. `PATCH /gateway/channels/{id}` 提交当前 `revision`、完整 `target` 和 `enabled: true`。
   启用必须有目标；新绑定或启用时核对执行账号及实例访问权限，权限撤销后仍可停用通道。
5. 消息入站保留发送者 ID、群角色、提及与引用，不要求群友逐个绑定平台账号。
   `GET .../inbox` 和 `.../deliveries` 返回最近 100 条状态；修正配置后可显式
   `POST .../inbox/{inbox_id}/retry`。提交、读取结果和发送前重新校验通道目标及权限。

例如，通道的目标字段为：

```json
{
  "target": {
    "user_id": "执行账号 UUID",
    "target_kind": "agent",
    "target_id": "Agent 实例 UUID",
    "output_mode": "message"
  }
}
```

不再提供 `/channels/{id}/routes`，也不按群或个人身份另选执行目标。
`binding_revision` 由服务维护，仅目标变化递增；普通配置更新不改变它。
换绑前必须排空待处理消息、运行和 pending/sending/uncertain 投递；换绑后旧回执不能用于新目标推送。
入站与换绑在同一通道行锁下提交，收件从入站起固定执行账号、目标和绑定版本。
旧连接的配置版本失效后不能继续入站；人工重试保留原绑定，跨绑定旧消息必须重新发送。
解绑目标需同时禁用通道，不删除已有实例、Session 或消息历史。

`GET .../identities`、`.../conversations` 和 `.../conversations/{key}/members` 返回已观察事实，
不是全量组织目录。`PUT .../identities/{identity_id}/binding` 的可选人工账号关联仍保留，
其核实过程不能根据昵称推断；关联和解绑都不选择执行账号或改变通道目标。
钉钉 isAdmin 和 QQ 群角色不会升级为平台管理员。
QQ 的 user_openid、member_openid 和 guild_user_id 分开保存，不能互相推断。
`POST .../probe` 只校验凭据，不启动消息连接。

Session 按通道、执行账号、目标实例和会话隔离：同群所有成员共用一个 Session，
私聊按外部用户独立；不同群、通道或目标互不混用。
不再提供 `GroupPolicy.session_scope`。配置 revision 更新不丢失历史；换目标后原历史仍保留。
`/help`、`/new`、`/reset`、`/cancel`、`/stop` 支持 Agent 目标；群内非 help 命令还需
匹配 control_roles，默认 owner/admin。未知角色不获得控制权限。
Workflow 输入为 `{"messages":[{"role":"user","content":[...]}],"gateway":{...}}`。
gateway 包含平台用户及角色、外部身份、会话、提及/引用和当前批次；不包含回复凭据或历史拼接。
实例的输入 schema 必须接受该结构，其 Agent 节点继续继承可信调用来源。
网关来源的编排 Agent Session 按用户、会话范围、编排实例和 Agent 绑定隔离，
不会复用其他用户的固定 Session；非网关编排仍使用显式实例绑定。

通道目标默认 `output_mode: "text"`。Agent 或 Workflow 可显式使用 `output_mode: "message"`，
输出必须为 `{"action":"reply","content":[{"type":"text","text":"..."}]}` 或
`{"action":"ignore","content":[]}`。其他对象不会被当作决策指令，结构错误会阻断该收件。
管理操作使用独立的类型合同、授权和持久队列，不编码为 Agent 回复或 OutboundMessage。
旧的 `allow_recall`、审核 action=recall 与消息 event=recall 已删除。
TypeSafe 只存在于[独立审核插件示例](../../../sdk/python/examples/typesafe-decision/README.md)，
网关不选择决策厂商、不根据外部群角色授权工具、不固定业务分支。

## 消息策略

通道 `policy` 独立于厂商 `config` 加密存储。`private_enabled` 控制私聊激活；
`group` 设置默认群策略，`groups` 列出允许接入的会话 ID 及完整群策略；不配置目标分流。
默认只在提及机器人时激活。`visibility` 描述收到事件的可见性，不负责读取历史。
`activation: "always"` 必须同时显式配置 visibility=all，且该厂商事件确认为全消息可见；
配置不能使 QQ/钉钉获得厂商未授予的事件订阅权限。

消息顺序为：接收入库 → 配置的 message_filter 逐条审核 → 激活检查 → 有界合批 →
目标 Agent/Workflow。审核使用原始消息，禁止把未审核的后续收件先拼进当前消息。
每个过滤器返回 allow/drop；异常与超时进入有界重试，默认 3 次、间隔 1 秒，
耗尽后 blocked，管理员可以修复配置后显式 retry。审核结果加密保存在原始收件。
drop 可在 operations 中提交类型化的撤回、禁言等操作；自动授权来自当前群策略中该插件的 grant。
审核器只能撤回被拒绝的原文或管理该发送者，不能借审核结果指定另一个群或成员。
过滤器可通过有界 `rejection_input` 请求生成拒绝说明；allow 不能携带该请求。
通道 `rejection_agent_id` 指向已创建的纯文本 Agent 实例，`rejection_timeout_seconds` 默认 20 秒。
宿主只接受无沙箱、工具、MCP、回调、远程提示词和不透明请求覆盖的文本实例；
独立 Run 不关联 Session，不读取或污染群聊历史。请求应只含可信文案要求与命中类别。
拒绝结论和管理操作在同一事务持久化，拒绝说明使用独立消息投递，不等待撤回成功。
撤回失败、待审批或结果未知都不能宣称成功。生成失败不能改变拒绝结论或放行原文。
拒绝收件进入独立 responding 状态，不占普通群聊合批队列；失败 Run 不能通过收件 retry 重跑。
框架不定义审核类别、分数阈值或拒绝文案；这些内容由过滤插件及部署配置拥有。
配置版本变化时待提交消息重新审核；已经提交的 Run 不重新执行。
拒绝生成单独核对拒绝 Agent 与超时策略；改名不使既有拒绝失效，人工 retry 会更新审核版本。

过滤插件只审核内容。需要按机器人名字或接续对话判断回复时，由 Agent 配置/业务插件
负责；全消息激活后可由结构化 Agent 返回 reply/ignore。单 Agent 不需要额外编排。
只有原始消息的协议值转换为原生 SDK 输入。历史由原生 Session 读取和保存，
不保留 history_limit/history_ttl_seconds/history_scope/clear_after_reply 旧配置接口，
不从本地收件或平台 recent_messages 构造另一份聊天历史。

公开群聊使用通道绑定的受限机器人服务账号，无需群成员各自注册。
此时外部发送者、群角色和会话隔离仍保留；平台权限来自服务账号而非群主或管理员身份。
管理员应限制可接入群、Agent 和工具；网关本身不自动创建用户或绑定未知身份。

`workers` 控制每通道独立任务并发，慢审核不会等待整轮任务结束才调度其他会话。
同一 Session 的普通消息按收件顺序处理，
控制命令可以越过正在运行的请求以执行取消。
入站落库后立即唤醒调度，生成发件后立即唤醒投递；运行中结果每 200 ms 检查，
空闲时保留每秒一次恢复扫描，不依赖定时扫描才发现本进程新收到的消息。
`debounce_seconds` 控制合并前的静默窗口，0 表示关闭；batch_max_messages 默认 20，
最多 100 条，每批约束 1 MB 序列化收件。batch_max_wait_seconds 默认 2 秒、最多 30 秒。
原始收件、每条发送者、消息 ID 与通道目标版本保留；运行中到达的消息形成下一批。
混合发送者的 InvocationSource 顶层身份/群角色留空，participants 逐条提供事实；
不能把首位管理员的角色继承给整批。执行提交、恢复和发送前重新核对通道目标和每条发送者事实。

多人群聊始终把每条原文作为独立 user 消息提交，保留发送者、消息 ID、提及和引用；
不把窗口内所有人合成一个发言者，也不把群友标成 assistant。
需要理解未提及机器人的接话时，部署方可配置 visibility=all、activation=always、
目标 output_mode=message，让 Agent 根据共享 Session 返回 reply/ignore。
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
`accept_duplicate_risk: true`。所有配置、绑定与恢复修改同事务写审计。

SDK 接收可信 Principal 与 InvocationSource，工具支持发现过滤和执行前授权。
业务插件负责业务数据范围与返回内容，群回复策略可根据 `reply_scope` 拒绝敏感操作。
身份提示词只是让模型理解调用者；不能替代代码授权，也不保证模型不会复述历史内容。
群聊通道应使用适合公开回答的 Agent 与工具。

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

QQ C2C 的 Agent text 目标可启用 streaming：使用官方 Temporal Agents 集成的
`Runner.run_streamed`、`WorkflowStream` 和原生 `Model.stream_response`，
仅把文本增量写入持久事件日志，再经发件箱生成 replace 帧及结束帧。
未发送的增量会合并，已发送或结果不确定的请求不改写，重连不重新生成模型输出。
替换帧保留平台已接受的前缀，最终文本发生修订时不会删除已经公开的内容。
支持根 Agent、公开 handoff 与根 SandboxAgent；Agent-as-tool 及其后代不公开中间文本，
同时作为工具和 handoff 的 Agent 也按内部角色处理。
图内存在输入/输出守卫或结构化输出时只发最终结果，防止绕过校验。
不转发隐藏 reasoning；Workflow/message 决策目标也不提前转发子节点文本。
QQ群、频道和钉钉当前使用完整消息回复，不承诺逐 Token 送达。

## 主动推送与扫码

`POST /gateway/channels/{id}/push` 提交 request_id、recipients 和 content。
recipients 是本通道已完成或被动收件的 UUID，作为已核实身份与会话的依据；
不接受任意目标地址。一次最多 100 个收件依据、500 条投递，多个依据不能指向同一目标。
可以发送文本或 ContentPart 媒体。`GET .../subjects` 列出仍匹配当前通道目标和允许范围的已知会话，
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
扫码只获取连接配置；选择执行目标和启用通道由管理员明确完成。

## Octop 能力对照

源码对照基线为 [octop-gateway 224ed07](https://github.com/TencentCloud/octop-gateway/tree/224ed078ce20042fb5fa59240d6e3c7c34bcd67f)，
不代表与上游所有版本或所有参数完全一致。

| 能力 | OpsMesh 当前实现与范围 |
| --- | --- |
| 统一消息、媒体与适配器扩展 | SDK 合同；内置 QQ/钉钉，其他通道走插件 |
| 多通道、工作池与批处理 | 通道租约、受限并发、会话顺序、静默合并、控制命令、配置/插件热重载 |
| 消息、完成、错误与工具事件 | 持久 Run 事件和终态、节流步骤提示、公开推理摘要；错误不静默重跑 |
| 群提及、可见性、激活与会话 | 默认/分群策略、通道唯一目标、群共享原生 Session；不复制上游短期历史拼接 |
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

当前新增迁移 `0008_channel_targets`，历史迁移保持不变。
先停止入站、排空 pending/ready/dispatching/submitted/responding/batched 收件和
pending/sending/uncertain 投递，再停进程、备份并迁移；迁移会拒绝未排空的数据库。
全部启用旧路由归于同一个执行账号、目标和输出模式时自动填入目标。
只有不含个人路由的共享群通道可保留启用状态；没有目标、多目标或含个人路由的通道会停用，
管理员需检查执行账号、唯一目标与允许会话后再启用。
所有旧通道迁移后关闭私聊入口：新入口允许任意私聊发送者，需管理员明确开启，不能沿用个人绑定授权。
允许群列表只从启用的旧共享 group/guild 路由及已观察到的会话迁移；个人群路由和未使用的策略项不转换为公开授权。
`gateway_routes` 和归档表 `gateway_route_receipts` 保留历史路由及原始加密批次证据，
应用不再读取旧表；旧路由 API 和运行时兼容分支移除。
原生 Session 文件和映射保留；原有同群共享范围继续使用，旧按人划分的群历史不自动合并。
该迁移不能安全还原个人路由，降级须恢复升级前的协调备份。
迁移与真实 PostgreSQL/通道联调尚未在本轮执行。
完整证据与限制见 [验收记录](../../../docs/acceptance.md)。
本次同时改变 Agent 模型引用与 Temporal 流式调用路径。发布前必须针对已有历史做重放验收；
未完成重放验证时，先排空旧 Agent Workflow，再切换新 Worker，不能直接热替换运行中的历史。

静态检查不代表真实 QQ、钉钉、PostgreSQL、Temporal 和模型链路已经验收。
