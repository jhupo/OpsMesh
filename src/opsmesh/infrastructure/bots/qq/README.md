# QQ 官方机器人适配器

`QQAdapter` 实现 `OpsMeshBotAdapter`，只负责 QQ 协议、访问凭证、验签、消息转换和发送；账号授权、身份映射、Agent 绑定与持久去重由平台 Bot 服务拥有。

```json
{
  "id": "support-qq",
  "adapter": "qq",
  "self_id": "机器人用户ID",
  "settings": {
    "app_id": "12345678",
    "transport": "webhook"
  },
  "credentials": {
    "client_secret": "env:QQ_CLIENT_SECRET"
  }
}
```

`app_id` 是 QQ 开放平台应用 ID；`self_id` 用于识别消息中的机器人 mention，不应混作用户 OpenID。默认 API 地址为 `https://api.bot.qq.com`，通过 `/app/getAppAccessToken` 获取凭证并在临近过期时刷新。同一账号并发调用共用令牌刷新锁；更改配置或禁用账号会丢弃缓存。密钥只保存环境引用，适配器校验配置时不解析密钥、不发起网络连接。

QQ 的全局机器人用户 ID 与群内 `member_openid` 可能不同。可在可信账号设置中配置 `group_member_openids={"群OpenID":"机器人在该群的member_openid"}`（值可从有权限的群状态接口核对）。映射仅影响对应群事件中自身 mention 的识别；Webhook 与 Gateway 使用同一逻辑，不从成员自述、`bot=true` 或任意其他机器人 mention 推断自身身份。未配置群使用账号 `self_id` / Gateway READY 用户 ID。

API 地址只接受官方 HTTPS 主机或显式 loopback 测试地址，不跟随重定向。兼容官方旧文档列出的 `https://sandbox.api.sgroup.qq.com`，但它的凭证获取仍使用统一 `api.bot.qq.com`；沙箱域名的当前部署可用性没有通过真实 QQ 账号验收，新配置使用统一默认地址。

已实现：

- HTTP Webhook 的 Ed25519 验签、时间窗口、应用 ID 校验和 `op=13` 注册验证。普通事件未经签名不接收；注册 token 仅接受有界 URL-safe 字符，防止注册接口成为任意事件签名器。
- 可选 `settings.transport="websocket"`：通过官方 `/gateway` 获取地址、QQBot AccessToken 鉴权、Hello/Identify/READY、独立心跳、断线 Resume、无效 Session 重新 Identify。默认 `intents=33554432`（`GROUP_AND_C2C_EVENT`）、`shard=[0,1]`；其他事件按应用实际获批的 intent 配置。连接由现有 Driver 管理，不需要公网 HTTPS 回调入口。
- 单聊 `C2C_MESSAGE_CREATE`、群聊 `GROUP_AT_MESSAGE_CREATE` / `GROUP_MESSAGE_CREATE`、频道和频道私信事件转换为统一 `BotMessageEvent`。去重身份包含消息索引，传输序号和重试 envelope 不改变消息身份。
- 事件 payload 白名单保留官方 `author.member_role`（仅 `member/admin/owner`）和最多 128 字的 `display_name`，仅为提供商上报元数据，不授予平台权限。`mentions[]` 与原始 content 中的 `<@ID>` 相互匹配后生成 `qq:mention` 消息段；与当前机器人 ID 严格匹配才令全量群消息 `to_me=True`，`@all`、其他机器人、转义文本不算明确 @ 本机器人。
- 统一文本消息与单个 `markdown` / `qq:markdown` 消息段发送。Markdown 数据为 `{"content":"# 标题"}`；不混入文本字段。
- 群聊和 C2C 支持一个图片、语音或视频资源段及可选文字。先使用官方 `/files` 上传（`srv_send_msg=false`），取得 `file_info` 后发送 `msg_type=7` 消息；只有最终发送返回消息 ID 才确认成功。资源使用 HTTPS URL，不隐式读取本机文件；多媒体混排和直接文件上传不支持。入站按 MIME 保留 image/audio/video 类型，不自动下载或声称识别成功。
- `c2c:<user_openid>`、`group:<group_openid>`、`channel:<channel_id>`、`dms:<guild_id>` 四种目标；回复保留 `message_id`，C2C/群回复通过 `ReplyTarget.delivery_sequence` 设置 `msg_seq`，默认 1。
- 区分连接前失败、限流、明确拒绝和发送结果未知。不会自动重发可能已提交的消息；成功必须有官方返回的消息 ID。
- `Bot.call_api("delete_group_message", target_id=群OpenID, message_id=消息ID)` 调用群消息撤回，支持官方 HTTP 200 空响应；200/204 空体返回 `{}` 仅表示官方明确确认撤回。失败保留 `OpsMeshError.info.provider_code`，权限不足 `40062003`、超时 `40064004` 不伪装成成功。
- `get_self`、`get_group_state(target_id=...)`、`get_group_info(target_id=...)` 只读接口。群状态接口可返回 `member_role`、`recv_msg_setting`、`allow_proactive_msg`；该接口仍为白名单能力，`11253` 表示没有接口权限，不能据此判断账号接入或全量接收失败。

Gateway 队列默认最多 100 条，消费默认 30 秒超时，可通过 `callback_queue_size` / `callback_timeout_seconds` 配置；回调消费和心跳互相独立。Resume 序号在平台接收事件成功后更新，队列满、消费失败、心跳丢失会重连并从最后成功序号恢复。平台仍须持久去重；Gateway Session 只保存在当前进程内，进程重启后重新 Identify，不承诺补齐离线期间 QQ 未投递的消息。默认发现地址缓存 10 分钟，发现失败至少间隔 30 秒，避免突破官方 `/gateway` 的 2 QPM 常规配额。关闭或更换账号会取消连接和子任务，凭证及原始授权字段不写入日志。

能力清单表示协议实现，实际使用还需要 QQ 授予相应权限：

- 群开启“接收所有消息”后才会推送 `GROUP_MESSAGE_CREATE`；否则仅能接收应用和群设置允许的消息。适配器无法绕过群开关。
- 撤回限消息发出后 **2 分钟内**。普通机器人只能撤回自己的消息；设为群管理员后可以撤回普通群成员消息，最终以 QQ 返回结果为准。审查应独立于慢速 Agent 回复处理，避免排队超过撤回窗口。
- 不带 `ReplyTarget.message_id` 的群消息按主动消息发送。当前官方限额为单群 20 QPM、单群每日 1000 条，另受认证状态/机器人总频率和群“允许主动消息”开关限制；不要依赖旧版文档中已移除主动消息的描述。被动回复须保留消息 ID，当前通常为 5 分钟内最多 5 次，并使用不同 `delivery_sequence`。
- 频道/DMS 发送仍应满足 QQ 的在线和 intent 要求，使用 `websocket` 可建立本进程的官方 Gateway 会话。Markdown、富媒体权限、格式及大小限制仍由 QQ 校验；通用文件、卡片、互动按钮与流式 C2C 尚未实现。富媒体协议依据 [QQ 官方 Python SDK](https://github.com/tencent-connect/botpy/blob/master/botpy/api.py) 的群聊/C2C 上传与发送接口。

测试使用官方公开 challenge/公钥样例、真实 Ed25519 算法、受控 HTTP 与本地 WebSocket peer，覆盖鉴权拒绝、目标映射、去重、并发刷新、回复序号、配置轮换、撤回结果、权限错误、Resume、消费阻塞时心跳、背压与关闭清理，没有发送真实 QQ 消息。

协议依据：[认证](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/api-use.html)、[签名](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/sign.html)、[回调与注册](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html)、[消息收发](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html)、[频道发送](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/channel/message/send.html)。

本轮新协议依据（核对 2026-11-01）：[全量群消息](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_message_create.html)、[群消息撤回](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages_message_id.delete.html)、[群消息发送](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html)、[群内机器人状态](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_bot_state.get.html)、[Gateway 地址](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/gateway.get.html)。
