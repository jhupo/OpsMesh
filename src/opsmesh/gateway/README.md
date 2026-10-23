# Gateway

OpsMesh 自己管理通道、外部身份、账号绑定、路由与可靠投递。内置 QQ 官方机器人和
钉钉应用机器人；仅参考 octop 的协议处理方式，不加载它的网关运行库。
来源与 MIT 许可见 [NOTICE](../../../NOTICE) 和 `adapters/UPSTREAM-LICENSE`。
其他通道使用插件 SDK 的 `ChannelAdapter` 注册。

## 所有权与目录

| 文件或目录 | 职责 |
| --- | --- |
| contracts.py、schemas.py、ports.py | 通道、路由、消息与执行端口 |
| adapters/qq、adapters/dingtalk | 配置校验、官方认证、连接、事件解析和发送 |
| registry.py、plugins.py | 内置和插件通道注册、校验、构造 |
| manager.py、leases.py | 配置协调、数据库租约、连接生命周期与后台发送 |
| identities.py、conversations.py | 有范围和 ID 类型的外部身份、已见群与成员事实 |
| routing.py、access.py、sessions.py | 账号授权、会话路由与私有 Session 映射 |
| inbox.py、dispatcher.py、commands.py | 加密收件、去重、执行提交与控制命令 |
| outbox.py、recovery.py | 加密发件、顺序投递、不确定结果与人工恢复 |
| models.py、store.py、routes.py | 持久化、事务审计和管理员 API |
| media.py | resources 文件接口、有限大小附件和公共 HTTPS 下载 |

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
解绑会禁用相关路由并移除映射，但保留原用户的私有历史。修改路由要提供当前 revision。

Agent 路由按路由、身份与会话隔离创建私有 Session，群成员之间不共享历史。
`/help`、`/new`、`/reset`、`/cancel`、`/stop` 支持 Agent 路由。
Workflow 输入为 `{"messages":[{"role":"user","content":[...]}]}`，实例的输入 schema
必须接受该结构；其 Agent 节点继续继承调用来源。编排中的 Session 由实例绑定管理。

## 投递与权限

输入用厂商稳定事件 ID 去重，持久收件成功后才确认厂商消息。执行提交与收件关联
在同一事务落库。最终文本结果写入发件箱，按分片顺序发送；不提供逐 token 输出。
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
媒体保留与清理由部署方管理。QQ 发送文本、图片、音视频；钉钉发送文本、图片、音频、
文件，具体取决于机器人接口与权限。当前执行桥输出最终文本，厂商适配器的媒体发送
是供插件扩展的能力。没有二维码登录、设备授权、任意地址推送或广播接口。

## 升级

迁移 `0004_native_gateway` 保留旧配置与旧绑定表，并禁用旧连接。旧数据缺少身份范围、
ID 类型和可信绑定依据，不能自动迁移成新权限。管理员需补齐新配置、核实身份并重建
绑定和路由。历史迁移不修改；降级需要按备份恢复方案处理。
先执行数据库迁移，再部署 API、Worker 和 Gateway；程序版本升级需要重启这些进程，
日常配置与插件变更无需整体重启。

静态检查不代表真实 QQ、钉钉、PostgreSQL、Temporal 和模型链路已经验收。
