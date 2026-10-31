# 机器人适配器与平台边界

## 已实现的目录

```text
src/opsmesh/
├── framework/adapters/
│   ├── adapter.py             # Adapter 注册、连接 Bot 与事件派发基础
│   ├── bot.py                 # Bot、BotRef、ReplyTarget、发送与文件扩展合同
│   ├── configuration.py       # 中立账号配置、连接状态、能力/配置 Schema
│   ├── ingress.py             # 统一 BotMessageEvent、Webhook 请求与协议 ACK
│   ├── managed.py             # OpsMeshBotAdapter 配置、连接、收发、关闭接口
│   └── registry.py            # Driver 上的适配器查找、事件接收绑定、资源释放
├── infrastructure/bots/
│   ├── dingtalk/              # 钉钉应用机器人：Stream + OpenAPI
│   │   ├── adapter.py / configuration.py
│   │   ├── bot.py / client.py
│   │   └── event.py / message.py / stream.py
│   ├── qq/                    # QQ 官方机器人：签名 Webhook + OpenAPI
│   │   ├── adapter.py / configuration.py
│   │   ├── bot.py / client.py
│   │   └── event.py / message.py / authentication.py
│   └── onebot/                # 独立兼容实现：原 HTTP/WS/NapCat 能力
├── modules/bots/
│   ├── api.py                 # 平台账号、身份、Agent 绑定和投递公开接口
│   └── internal/
│       ├── service.py         # 组装中立入口与生命周期
│       ├── management.py      # 权限、账号与身份/路由管理
│       ├── configuration_store.py  # 授权快照与事务并发控制
│       ├── ingress.py         # 身份映射、Agent 选择、持久事件去重
│       ├── delivery.py        # 投递授权、幂等与回执状态
│       ├── delivery_outcomes.py # 未尝试、明确拒绝、安全重试与未知结果分类
│       ├── delivery_records.py # 持久投递占用
│       └── account_migration.py # 读取旧账号配置的兼容转换
└── bootstrap/bot_adapters.py  # 内置实现的唯一组装入口
```

协议实现不再放在 `modules/bots/internal/adapters`。平台与框架都不导入具体协议包，具体协议也不导入业务模块；仓库静态检查会阻止这些依赖重新出现。基础 `OpsMeshAdapter` 继续支持 NoneBot 风格的 Driver/Adapter/Bot 插件，`OpsMeshBotAdapter` 增加受平台管理的账号配置与生命周期合同。

## 责任分工

| 所有者 | 负责 | 不承担 |
| --- | --- | --- |
| Driver | 网络能力、后台任务、启动和关闭 | Bot 用户权限、Agent 绑定 |
| 具体 Adapter | 配置校验、协议认证、令牌、连接、事件/消息转换、协议 ACK | 本地用户、数据库、Run 创建 |
| 具体 Bot | 当前账号的协议 API、消息发送、可选文件能力 | 绕过平台投递授权与幂等的业务入口 |
| Bot 平台服务 | 账号、外部身份、本地主体授权、Agent 路由、投递状态 | QQ Ed25519、钉钉 ticket、OneBot echo 等协议细节 |
| Bootstrap | 注册实现，注入 Driver/HTTP 客户端/密钥解析器，绑定统一事件接收者 | 在业务模块中按厂商编写分支 |

配置中的 `adapter` 是注册名称，不是限定为某个厂商的 Literal。`settings` 由对应适配器的类型化模型校验，未知字段拒绝；`credentials` 仅保存具名环境引用。平台先检查每个引用的 `secret:use`，全部授权后才解析凭据或更改连接。

## 内置协议的实际范围

| 适配器 | 接收 | 发送与目标 | 当前边界 |
| --- | --- | --- | --- |
| `dingtalk` | 应用机器人 Stream，处理票据、ACK、心跳、重连和停止 | OpenAPI 文本/Markdown，`private:<senderStaffId>`、`group:<conversationId>` | 未实现 HTTP 回调、文件/图片上传、卡片及第三方企业授权 |
| `qq` | QQ 官方 Webhook，Ed25519 验签和注册挑战 | 文本/Markdown；`c2c:<openid>`、`group:<openid>`、`channel:<id>`、`dms:<guild_id>` | 当前不启动 QQ WebSocket；频道/DMS 发送仍须满足官方在线条件。文件、卡片、C2C 流式未实现 |
| `onebot_v11` | 兼容原签名 HTTP、主动/反向 WebSocket | 原文本/消息段与可选 NapCat 文件 | 是独立兼容协议，不代表 QQ 官方 API |

QQ 群聊和 C2C 作为官方接入的主要路径。被动回复保留来源 `message_id`，多次回复使用递增 `delivery_sequence` 映射 `msg_seq`；默认 1 的字段不额外序列化，以保留已有目标指纹。是否允许主动发送、回复有效期、频次和 Markdown 权限由 QQ 平台决定，返回的明确拒绝不会被包装成发送成功。

新的钉钉与 QQ 会话键包含适配器与 Bot 账号身份；OneBot 沿用已有账号/频道/用户键以保持历史会话连续。同一频道被不同机器人接收时不会共用平台会话，平台还按本地主体隔离会话。钉钉 `sessionWebhook`、QQ 授权 token 不作为持久事件数据保存；入站附件保留资源引用，不自动下载执行。

本地校验或未实现能力保存为 `not_attempted`，供应商明确拒绝保存为 `rejected`；重复请求返回同一确定错误，修正内容或配置后使用新幂等键。已知连接失败/限流可安全重试，读取超时、取消和无法判断的协议错误保存为 `unknown`，不会根据任意 retryable 标记自动重发。

## 配置和使用

钉钉账号：

```json
{
  "id": "ops-dingtalk",
  "adapter": "dingtalk",
  "self_id": "ding-application-id",
  "settings": {"transport": "stream"},
  "credentials": {"client_secret": "env:DINGTALK_CLIENT_SECRET"}
}
```

`self_id` 默认用于钉钉 Client ID 和 robotCode，不同时通过 `settings.client_id` 和 `settings.robot_code` 分别配置。Stream 由启用 Worker 的进程建立。

QQ 官方账号：

```json
{
  "id": "support-qq",
  "adapter": "qq",
  "self_id": "机器人用户ID",
  "settings": {"app_id": "12345678", "transport": "webhook"},
  "credentials": {"client_secret": "env:QQ_CLIENT_SECRET"}
}
```

`self_id` 与应用 ID 分开：前者用于识别机器人 mention，后者用于认证。QQ 开放平台回调地址配置为 `https://部署域名/api/v1/callbacks/bots/support-qq`。由适配器处理注册验证和事件 ACK；该入口使用协议认证，不使用平台用户 Bearer Token。

HTTP 入口与插件调用同一公开平台服务：

1. `GET /api/v1/bot-adapters` 查看已注册实现、实际能力、设置 Schema 和凭据名称，需要 `bots.read`。
2. `POST /api/v1/bots` 保存账号；设置 schema、凭据引用和权限在服务层统一检查。
3. `PUT /api/v1/bots/{id}/identities` 绑定外部用户到本地主体；仍要求 `accounts:bot_identity:manage`。
4. `PUT /api/v1/bots/{id}/routes` 将协议频道目标绑定到发布版 Agent。
5. `POST /api/v1/bots/{id}/messages` 发送消息；按账号选择适配器，调用者只需对应 `bots.send`。QQ 被动回复可带 `message_id`、`delivery_sequence`。

已有账号不能通过 update 更换适配器或物理机器人身份；这种变更应创建新账号并显式绑定身份，避免继承错误的外部身份信任。修改连接设置仍按现有全部身份映射检查权限和数据库快照。入站连接持有旧配置版本时，平台拒绝其继续派发事件。

## 扩展与兼容

新增适配器继承公开 `OpsMeshBotAdapter`，实现 `describe`、`validate_configuration`、`get_bot`、`configure`、`disconnect_account`、`connection_status` 和 `close`；Webhook 协议实现 `receive_http`，长连接通过 `emit` 送入统一接收入口。账号配置校验不能解析密钥或发起网络 I/O。由组装代码向 `BotAdapterRegistry` 注册实例，或者由部署插件通过 Driver 注册；平台启动后不接受临时替换实现。

平台会校验已注册适配器所属插件的启用状态，禁用后拒绝新配置、收发和事件派发，并通过已有插件策略同步循环关闭连接；资源清理仍包含已禁用实现。持久账号与代码插件的生命周期分别管理。

旧 OneBot 数据库行在读取时转换成 `settings`、`credentials`，身份、Agent 绑定、回执与 revision 保留；下次显式修改保存新表示，无需新表。新 HTTP/Python 请求须使用通用字段结构；旧的顶层 `api_base_url`、`transport`、`access_token_ref` 等字段已移入各自对象。原 `/callbacks/onebot/{id}` 和反向 WS 路径保留为兼容入口。

## 验证与依据

具体协议测试覆盖签名、配置、token 缓存与轮换、事件/消息转换、错误路径及本机 WebSocket 生命周期。平台测试覆盖第三方适配器注册、权限、身份/Agent 绑定、接收去重、发送和重启恢复。测试使用受控 HTTP 和本机网络服务，未验证真实 QQ/钉钉账号的应用权限及外部消息投递。

2026-10-31 最终验证（Windows / Python 3.13）：

| 检查 | 结果 |
| --- | --- |
| 全量 `uv run pytest -q` | **635 passed，26 skipped，363.36 秒** |
| 显式临时 PostgreSQL 集成 | **12 passed，27.26 秒**，包含 Bot 授权并发与通知回执 |
| Ruff / format | 全部通过，479 个 Python 文件符合格式 |
| mypy | 355 个源码文件通过 |
| 仓库检查 | 文档、包内容、模块与具体适配器的依赖边界通过 |
| wheel / sdist | 构建成功，源码包文档链接通过 |

默认跳过的 26 项中，12 项 PostgreSQL 已单独启用通过；其余外部服务/环境项目本轮未重新执行。QQ 36 项协议回归、钉钉 10 项协议回归、自定义适配器/HTTP 联调、插件启停与投递结果回归都包含在全量结果中。未发送真实机器人消息，未重新部署测试服务器。

旧 `modules/bots/internal/adapters` 源码已移出，检查与构建使用新的实际源码。删除遗留 `.pyc` 与空目录时自动审批返回 `blocked by policy`，因此本地可能仍显示旧 `__pycache__`；它们不属于源码或发行内容。

接口细节与官方链接见 [QQ 适配器](../../src/opsmesh/infrastructure/bots/qq/README.md)、[钉钉适配器](../../src/opsmesh/infrastructure/bots/dingtalk/README.md)、[OneBot 兼容适配器](../../src/opsmesh/infrastructure/bots/onebot/README.md)。
