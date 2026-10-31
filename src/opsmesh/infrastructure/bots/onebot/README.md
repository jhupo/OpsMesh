# OneBot v11 兼容适配器

独立协议实现，使用框架的 `OpsMeshBotAdapter`、`BotConnectionConfiguration` 和
`BotMessageEvent`；不读取平台数据库，不管理用户、权限或 Agent 绑定。
QQ 官方机器人接入使用相邻的 `qq` 适配器，本包仅用于已有 OneBot/NapCat 部署。

`settings` 由 `OneBotSettings` 校验，支持 `http`、主动 `websocket`、Universal
`reverse_websocket`。`credentials` 仅接受 `access_token`、`webhook_secret` 对应的
`env:NAME` 引用。HTTP 必须提供 webhook secret；反向 WebSocket 必须提供 access token。

协议转换保留 CQ/数组消息段；未知框架消息段显式拒绝。`file_upload="napcat"`
显式启用 NapCat 文件接口，不推断所有 OneBot 实现都支持文件。确认发送可由平台层持久去重；
网络断开或超时后的不确定发送不会自动重放。连接任务由 Driver 监督，禁用账号或关闭 Driver
会排空连接。反向连接兼容路径为 `/plugins/onebot/ws`。

验收覆盖受控 HTTP 和本地真实 WebSocket 对端，不代表任何实际 QQ 账号已验收。

协议来源：[OneBot v11](https://github.com/botuniverse/onebot-11)、
[NapCat 文件上传](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-onebot/action/go-cqhttp/UploadPrivateFile.ts)。
