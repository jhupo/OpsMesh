# 消息网关

独立 Gateway Worker 持有连接；API 只管理配置、身份绑定、持久事件与操作队列。平台目录分别为 `gateway/adapters/qq/` 和 `gateway/adapters/dingtalk/`，配置、事件、消息、API 与连接生命周期分别组织。

## 配置

管理员通过 `POST /v1/channels` 创建通道，填写 `name`、`adapter`、`agent_id`、`config`。凭据加密存储，配置不会在查询响应中返回。`PUT /v1/channels/{id}/identities/{external_user_id}` 绑定平台用户；未绑定消息保留事件记录，不执行 Agent。

QQ 的 `config` 示例：

```json
{
  "app_id": "YOUR_APP_ID",
  "secret": "YOUR_APP_SECRET",
  "intents": {
    "direct_message": true,
    "public_messages": true,
    "public_guild_messages": true
  }
}
```

QQ 和钉钉直接继承 `gateway/adapters/base.py` 的自有 Adapter。QQ 直接调用腾讯官方 `qq-botpy==1.2.1`，由官方 SDK 负责 WebSocket、认证、心跳、重连和事件解析。`intents` 字段映射到 `botpy.flags.Intents`；官方 SDK 不提供 webhook 入口，因此 QQ 只支持官方 WebSocket 生命周期。

QQ SDK 身份和官方仓库记录见 [QQ SDK 来源](../src/opsmesh/gateway/adapters/qq/SDK.md)。宿主直接锁定 `qq-botpy==1.2.1`；`scripts/audit_requirements.py` 校验该包存在并拒绝 NoneBot 依赖。

每个通道持有独立连接、配置和事件接收函数；QQ 不初始化其他框架的全局运行时。QQ 的鉴权、心跳、分片、恢复、事件解析、消息审核和 API 由官方 SDK 提供，宿主只保留事件标准化和持久化边界。钉钉使用官方 SDK 传输。QQ 平台是否允许某种连接或事件取决于应用类型、权限和平台政策；连接失败不能用模拟成功代替。

钉钉的 `config` 示例：

```json
{
  "client_id": "YOUR_CLIENT_ID",
  "client_secret": "YOUR_CLIENT_SECRET",
  "robot_code": "YOUR_ROBOT_CODE",
  "subscribe_events": true,
  "card_callbacks": true,
  "graph_callbacks": true
}
```

锁定 dingtalk-stream **0.24.4b1 预发布版本**，使用其异步启动、停止与重连生命周期。机器人、委托机器人、事件订阅、卡片及 Graph 回调使用原生注册和解析。`card_callback_response` 可设置卡片 ACK JSON。原生便捷回复/卡片方法存在吞错或同步阻塞，持久操作改用严格响应检查的 httpx 请求；仍使用 SDK 的访问令牌缓存和 Stream 协议。

钉钉入站事件与 QQ 共用公共事件片段：`text`、`image`、`file`、`audio`、`video`、`link`、`mention` 和 `platform`。原生内容不因规范化丢失，保存在 `event.data.payload`；Stream frame headers 在 `event.data.headers`，SDK 解析出的消息或卡片字段在 `event.data.parsed`。富文本、@用户、媒体元数据、卡片、订阅和 Graph 回调均以结构化片段保存；`isAdmin` 只作为原生字段保存，不代表平台角色。

## 能力与操作

`GET /v1/channels/{id}/capabilities` 返回 Worker 已加载的实现、配置与操作目录；连接准备状态由运行实例单独管理。QQ 只开放与官方 `botpy.api.BotAPI` 对应的固定操作：频道消息、C2C 消息、群消息以及 C2C/群文件发送；参数直接按官方方法边界传递，目录不伪造未实现的 QQ API。目录表示适配器能力，不表示对应应用已经获批权限。

管理员通过 `POST /v1/channels/{id}/operations` 提交：

```json
{
  "operation": "set_group_members_mute",
  "parameters": {
    "group_id": "GROUP_OPENID",
    "members": [{"op": "add", "member_openid": "MEMBER_OPENID", "mute_expire_at": "2026-10-25T00:00:00Z"}]
  },
  "idempotency_key": "mute-member-20261024-1"
}
```

调用返回 HTTP 202 和操作 ID。查询 `/operations/{operation_id}` 获取状态及解密后的平台回执。相同键和参数返回原操作，不同输入返回冲突；执行前重新验证管理员角色。失败或不确定操作只能显式调用 `/operations/{operation_id}/retry` 重试。后续升级以能力目录中的参数和结果 JSON Schema 为准。目录还声明权限、并发、重试及限流约束；目录加载前不能提交操作。

共同的 `message.send` 接收 `target`、`text`、`content`、`reply_to`、`scope`、`sequence`。QQ 目标为 `{"kind":"c2c|group|guild|dms","id":"..."}`；当前 QQ 消息片段只支持 `text`、`markdown` 和 `reference`，其余富消息类型通过固定原生操作调用官方 `BotAPI`。`message.send` 不读取宿主本地文件；文件发送使用官方 C2C/群文件操作提供的 URL 和参数。

钉钉主动目标为 `{"kind":"private","user_ids":["..."]}` 或 `{"kind":"group","conversation_id":"..."}`，支持文本与 Markdown。当前对话回复使用加密的 session webhook，支持原生 webhook 消息体。卡片创建通过固定操作提交：

```json
{
  "operation": "dingtalk.card.create",
  "parameters": {
    "cardTemplateId": "TEMPLATE_ID",
    "outTrackId": "YOUR_UNIQUE_ID",
    "cardData": {"cardParamMap": {"title": "Hello"}},
    "callbackType": "STREAM"
  },
  "idempotency_key": "create-card-1"
}
```

钉钉目录仅开放 `message.send`、`media.upload` 以及 `dingtalk.card.create`、`dingtalk.card.deliver`、`dingtalk.card.update`、`dingtalk.card.stream`。HTTP 方法、路径、官方主机和鉴权由实现固定，不能传入 `api.request`、URL、路径或鉴权头。卡片投放要求与 openSpaceId 匹配的一种投放目标；具体字段以目录 Schema 为准。`media.upload` 接收 `type`、`filename`、`content_base64`，不读取本地文件。单次队列 JSON 上限 8 MiB。回执保存 Result 的 operation/value，消息保存 Receipt 的 status/value；accepted 或 pending_audit 均不证明最终送达。

## 事件、回复与隔离

所有事件按通道和原生 ID 去重，查询 `/v1/channels/{id}/events` 及 `/events/{event_id}`。消息正文、图片/富文本、文件、音视频和未来字段保留结构化载荷；插件从 `context.metadata["channel_event"]` 读取公开内容，自行构建模型的多模态输入。接收到附件不等于已自动提交模型处理。

QQ C2C、群、频道与频道私信分别标识；钉钉私聊和群聊分别标识。它们与 Agent、通道共同定位持久 Session，不共享用户工作区。群管理员等外部角色只是业务元数据，不能授予 OpsMesh 平台权限。

`context.channel.reply(text, content=[...], key="...")` 将回复加入当前对话 Outbox，并返回 delivery ID；`context.channel.request(...)` 仅允许当前平台管理员。查询 `GET /v1/deliveries/{id}` 可获取消息回执。原生被回复消息 ID、QQ 序号、钉钉 webhook 地址与有效期保留在宿主加密路由信息中，不作为业务元数据暴露。群聊人工审批仅发送提醒，决定通过指定用户的受权接口提交。

非消息事件、卡片和 Graph 回调会持久化，但不会自动启动 Agent 或替业务做审批。Graph 返回原生 **202 Accepted** 及事件关联信息，不伪造同步业务执行结果；需要平台同步业务响应的应用应注册专用插件通道。钉钉回调在持久化后返回；QQ 官方 WebSocket 事件交由宿主 sink 持久化；平台历史窗口和进程重启仍限制上游重投递。

副作用发送与数据库不是一个事务。HTTP 明确拒绝标记 `failed`，连接中断或结果不确定标记 `uncertain`，不自动重发。人工重试前应核对平台结果。回执中的 `pending_audit` 仅代表 QQ 接受审核，不代表最终送达；平台过期 webhook 不会静默替换为另一身份发送。

## 验证

授权后可执行 `python -m unittest discover -s tests -v`。数据库回归需显式提供 `OPSMESH_TEST_DATABASE_URL`，每个测试创建并删除独立临时 schema。没有配置数据库时跳过必须如实报告。模拟 HTTP/签名回调、SDK 单元测试与真实平台联调分别记录，见 [校验边界](verification.md)。
