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

API 地址只接受官方 HTTPS 主机或显式 loopback 测试地址，不跟随重定向。兼容官方旧文档列出的 `https://sandbox.api.sgroup.qq.com`，但它的凭证获取仍使用统一 `api.bot.qq.com`；沙箱域名的当前部署可用性没有通过真实 QQ 账号验收，新配置使用统一默认地址。

已实现：

- HTTP Webhook 的 Ed25519 验签、时间窗口、应用 ID 校验和 `op=13` 注册验证。普通事件未经签名不接收；注册 token 仅接受有界 URL-safe 字符，防止注册接口成为任意事件签名器。
- 单聊 `C2C_MESSAGE_CREATE`、群聊 `GROUP_AT_MESSAGE_CREATE` / `GROUP_MESSAGE_CREATE`、频道和频道私信事件转换为统一 `BotMessageEvent`。去重身份包含消息索引，传输序号和重试 envelope 不改变消息身份。
- 统一文本消息与单个 `markdown` / `qq:markdown` 消息段发送。Markdown 数据为 `{"content":"# 标题"}`；不混入文本字段。
- `c2c:<user_openid>`、`group:<group_openid>`、`channel:<channel_id>`、`dms:<guild_id>` 四种目标；回复保留 `message_id`，C2C/群回复通过 `ReplyTarget.delivery_sequence` 设置 `msg_seq`，默认 1。
- 区分连接前失败、限流、明确拒绝和发送结果未知。不会自动重发可能已提交的消息；成功必须有官方返回的消息 ID。

此实现的接收方式为 Webhook，不启动 QQ WebSocket。官方频道文档仍要求发送时保持 WebSocket 在线；频道/DMS 路径的协议转换不代表当前进程提供该在线能力，部署须满足 QQ 平台要求。主动消息、被动回复时效、次数和 Markdown 权限仍受 QQ 平台限制。文件上传、卡片、互动按钮与流式 C2C 尚未实现，能力清单不宣称支持它们。收到的附件只转成资源引用，不自动下载。

测试使用官方公开 challenge/公钥样例、真实 Ed25519 算法及受控 HTTP peer，覆盖鉴权拒绝、目标映射、去重、并发刷新、回复序号、配置轮换、未知结果与不支持路径，没有发送真实 QQ 消息。

协议依据：[认证](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/api-use.html)、[签名](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/sign.html)、[回调与注册](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html)、[消息收发](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html)、[频道发送](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/channel/message/send.html)。
