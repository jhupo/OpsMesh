# 消息网关

独立 Gateway Worker 持有连接；API 只管理配置、身份绑定、持久事件与操作队列。平台目录分别为 `gateway/adapters/qq/` 和 `gateway/adapters/dingtalk/`，配置、事件、消息、API 与连接生命周期分别组织。

## 配置

管理员通过 `POST /v1/channels` 创建通道，填写 `name`、`adapter`、`agent_id`、`config`。凭据加密存储，配置不会在查询响应中返回。`PUT /v1/channels/{id}/identities/{external_user_id}` 绑定平台用户；未绑定消息保留事件记录，不执行 Agent。

QQ 的 `config` 示例：

```json
{
  "app_id": "YOUR_APP_ID",
  "secret": "YOUR_APP_SECRET",
  "transport": "websocket"
}
```

锁定 nonebot-adapter-qq 1.7.3、nonebot2 2.5.0。默认订阅 SDK 默认事件以及 C2C/群消息、私信和交互，可通过原生 `intents` 字段逐项配置；未声明的名称会拒绝。`shard` 使用原生 `[index, total]`。Webhook 模式设置 `transport: "webhook"`、`webhook_host`、`webhook_port`，入口 `/qq/webhook`，默认仅监听回环地址。通过反向代理配置平台可访问的 HTTPS 地址。SDK 原生 Ed25519 签名验证保留，入口拒绝非法签名编码及超过 1 MiB 的请求。

上游 1.7.3 的 `cryptography<49` 排除了已修复漏洞的版本。本项目维护 [固定源码分支](https://github.com/jhupo/OpsMesh/tree/codex/adapter-qq-crypto50)，仅将依赖上限放宽为 `<51`，原生 Python 代码与 MIT 声明保持原样。依赖固定到完整提交 `e79e4a0830b55107752e706b2807d6651d29d463`，宿主要求 cryptography >=50.0.2。没有忽略安全告警或依赖覆盖；标准 pip/uv 都解析这个已修正元数据的源码依赖。从源码或宿主 wheel 安装需要 Git；生产 Linux 可执行包和镜像不需要现场构建依赖，独立 SDK wheel 也不引入该通道依赖。后续上游发布安全约束后应重新审查并回归官方发行包。

漏洞数据库按包名/版本查询。`scripts/audit_requirements.py` 核对上述 Git 提交，将仅改元数据的 QQ 包映射回 `nonebot-adapter-qq==1.7.3` 的审计身份，其余依赖保留锁定版本；源码和安装来源仍由 uv 的 Git 提交校验。没有排除这个包或忽略任何漏洞编号。

每个通道持有独立驱动和配置，不调用进程全局 NoneBot 初始化。鉴权、心跳、分片、恢复、事件解析、消息审核和 API 调用由原生 SDK 实现。QQ 平台是否允许某种连接或事件取决于应用类型、权限和平台政策；连接失败不能用模拟成功代替。

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

## 能力与操作

`GET /v1/channels/{id}/capabilities` 返回 Worker 已加载的能力目录和配置状态。QQ 从 SDK 的 `API` 描述符自动发现 80 个可调用 API，参数按原生函数签名及 Pydantic 类型校验，覆盖 SDK 已支持的群管理、消息、媒体、频道、成员、权限、互动等接口。目录表示 SDK 能力，不表示对应应用已经获批权限。

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

调用返回 HTTP 202 和操作 ID。查询 `/operations/{operation_id}` 获取状态及解密后的平台回执。相同键和参数返回原操作，不同输入返回冲突；执行前重新验证管理员角色。失败或不确定操作只能显式调用 `/operations/{operation_id}/retry` 重试。后续升级以运行时能力目录中的原生签名为准。

共同的 `message.send` 接收 `target`、`text`、`content`、`reply_to`、`scope`、`sequence`。QQ 目标为 `{"kind":"c2c|group|guild|dms","id":"..."}`；SDK MessageSegment 的 `type/data` 用于富消息、Markdown、键盘、引用、媒体、Ark 和 Embed。二进制参数使用 `{"$base64":"..."}`，不能上传宿主本地路径；原生预签名上传 URL 只允许明确的 QQ 官方 HTTPS 域名。

钉钉主动目标为 `{"kind":"private","user_ids":["..."]}` 或 `{"kind":"group","conversation_id":"..."}`，支持文本与 Markdown。当前对话回复使用加密的 session webhook，支持原生 webhook 消息体。更完整的机器人模板和 OpenAPI 通过 `api.request`：

```json
{
  "operation": "api.request",
  "parameters": {
    "method": "POST",
    "path": "/v1.0/card/instances",
    "api": "modern",
    "body": {"cardTemplateId": "TEMPLATE_ID", "outTrackId": "YOUR_UNIQUE_ID"}
  },
  "idempotency_key": "create-card-1"
}
```

平台通常要求更多业务字段，请按对应应用的官方接口补全。现代 API 固定为 `api.dingtalk.com`，`api: "legacy"` 固定为 `oapi.dingtalk.com`；禁止任意地址、路径穿越和调用者覆盖鉴权。支持卡片创建、投放、更新、流式更新以及其他获批 OpenAPI。`media.upload` 接收 `type`、`filename`、`content_base64`；媒体下载通过官方 API 获取下载信息，再由业务代码在自身网络边界内下载。单次队列 JSON 上限 8 MiB。

## 事件、回复与隔离

所有事件按通道和原生 ID 去重，查询 `/v1/channels/{id}/events` 及 `/events/{event_id}`。消息正文、图片/富文本、文件、音视频和未来字段保留结构化载荷；插件从 `context.metadata["channel_event"]` 读取公开内容，自行构建模型的多模态输入。接收到附件不等于已自动提交模型处理。

QQ C2C、群、频道与频道私信分别标识；钉钉私聊和群聊分别标识。它们与 Agent、通道共同定位持久 Session，不共享用户工作区。群管理员等外部角色只是业务元数据，不能授予 OpsMesh 平台权限。

`context.channel.reply(text, content=[...], key="...")` 将回复加入当前对话 Outbox，并返回 delivery ID；`context.channel.request(...)` 仅允许当前平台管理员。查询 `GET /v1/deliveries/{id}` 可获取消息回执。原生被回复消息 ID、QQ 序号、钉钉 webhook 地址与有效期保留在宿主加密路由信息中，不作为业务元数据暴露。群聊人工审批仅发送提醒，决定通过指定用户的受权接口提交。

非消息事件、卡片和 Graph 回调会持久化，但不会自动启动 Agent 或替业务做审批。Graph 返回原生 **202 Accepted** 及事件关联信息，不伪造同步业务执行结果；需要平台同步业务响应的应用应注册专用插件通道。钉钉回调 ACK 在持久化后返回；QQ SDK 的后台事件分发/ACK 独立于数据库提交，不能承诺进程在落库前退出时零丢失。

副作用发送与数据库不是一个事务。HTTP 明确拒绝标记 `failed`，连接中断或结果不确定标记 `uncertain`，不自动重发。人工重试前应核对平台结果。回执中的 `pending_audit` 仅代表 QQ 接受审核，不代表最终送达；平台过期 webhook 不会静默替换为另一身份发送。

## 验证

授权后可执行 `python -m unittest discover -s tests -v`。数据库回归需显式提供 `OPSMESH_TEST_DATABASE_URL`，每个测试创建并删除独立临时 schema。没有配置数据库时跳过必须如实报告。模拟 HTTP/签名回调、SDK 单元测试与真实平台联调分别记录，见 [校验边界](verification.md)。
