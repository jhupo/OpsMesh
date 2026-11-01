# 钉钉适配器

`DingTalkAdapter` 实现框架的 `OpsMeshBotAdapter`。协议代码不依赖平台 `modules/bots`；平台负责账号、身份映射、权限、Agent 绑定和持久幂等。

企业内部应用机器人使用 Stream 接收文本消息，OpenAPI 向群聊或单聊发送文本、Markdown。`self_id` 默认同时作为 Client ID 和 robotCode；不同值可在 settings 中显式设置。密钥只接受 `credentials.client_secret` 的环境引用。

```json
{
  "id": "operations-dingtalk",
  "adapter": "dingtalk",
  "self_id": "ding-example-app-id",
  "settings": {"transport": "stream"},
  "credentials": {"client_secret": "env:DINGTALK_CLIENT_SECRET"}
}
```

单聊目标为 `private:<senderStaffId>`，群目标为 `group:<conversationId>`。OpenAPI 所需的企业内机器人发送权限及机器人安装范围由钉钉控制台配置。发送结果使用 `processQueryKey` 标识已受理操作，不承诺终端用户已经阅读。

连接票据每次重连重新取得，不持久保存；Driver 监督后台连接，处理 ping、disconnect、消息 ACK、队列背压、退避重连和停止。账号更新由平台先断开旧连接，再配置新版本。发送 token 按账号配置版本缓存且并发刷新串行化；读取超时保留未知结果，不自动重发。

回调只向统一事件暴露必要字段，不持久化 `sessionWebhook` 或下载凭据。默认 API 为官方 HTTPS；设置中的 API 地址只允许官方域名或显式 loopback 测试地址，返回的 Stream 端点同样检查。

当前不接受 HTTP 回调，也不支持文件/图片上传、第三方企业授权流程、卡片和消息撤回；这些能力返回明确错误。入站非文本/Markdown 类型不静默改写成字符串。Markdown 消息段格式为 `{"type":"markdown","data":{"title":"标题","text":"**正文**"}}`。

协议依据：[Stream 协议](https://open-dingtalk.github.io/developerpedia/docs/learn/stream/protocol/)、[官方 Python Stream 实现](https://github.com/open-dingtalk/dingtalk-stream-sdk-python)、[发送群聊消息](https://open.dingtalk.com/document/orgapp/the-robot-sends-a-group-message)、[发送单聊消息](https://open.dingtalk.com/document/orgapp/chatbots-send-one-on-one-chat-messages-in-batches)、[获取应用令牌](https://open.dingtalk.com/document/orgapp/obtain-the-access_token-of-an-internal-app)。实现使用异步框架 Driver，没有直接运行阻塞 SDK。协议测试使用本机 WebSocket 服务及 HTTP 模拟，不代表真实钉钉租户权限已验收。
