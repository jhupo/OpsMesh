# opsmesh-qq

核对日期：2026-11-20。状态：已实现，尚未用真实 QQ 账号联调。源码入口：[适配器](src/adapter.py)、[网关](src/gateway.py)。发行包是 `opsmesh-qq`，插件 ID 是 `qq`；直接移植 [nonebot/adapter-qq 1.7.4](https://github.com/nonebot/adapter-qq/tree/11c099152f0c3aa68dffdf1eee103508ab866bca)，保留 [MIT 许可证](LICENSE)。不依赖 NoneBot、QQ SDK 或旧 Channels，不提供旧 `qqguild` 兼容接口。

## 安装和启动

安装同版本的 OpsMesh 和 QQ wheel，或从仓库执行 `python -m pip install . ./packages/qq`。使用支持出站 HTTP 的 Driver：WebSocket 可用 `~aiohttp`；Webhook 可用 `~fastapi+~aiohttp`（另装 FastAPI 驱动依赖）。不要在框架加载插件前直接导入 QQ 模块。

业务插件可在自己的 JSON 里通过 `dependencies.qq` 声明一个专属账号，事件只投递给该插件。参见[配置契约](../../docs/framework.md#6-插件配置与声明式连接)。宿主的全局 QQ 账号是共享入口。独立 Python 启动时调用 `plugin = opsmesh.load_plugin("qq")`，检查加载成功后用 `plugin.manager.enable_plugin(plugin)` 启用，再调用 `opsmesh.run()`；Linux 服务会按运行集自动启用。

~~~json
{
  "accounts": [
    {
      "app_id": "123456",
      "secret_ref": "QQ_BOT_SECRET",
      "transport": "websocket",
      "intent": {"c2c_group_at_messages": true}
    }
  ]
}
~~~

密钥只从进程环境变量读取，不写入 JSON。每个 App ID 只能声明一次；`transport` 为 `websocket`（默认）或 `webhook`。`intent` 按上游布尔位命名；`shard: [index, total]` 仅用于 WebSocket，不填则按网关发现结果运行分片。无账号配置不打开连接。账号配置、启动和停止按进程生效；Matcher 是否接收事件不代表 QQ 已连接。

## 结构与运行

~~~text
packages/qq/
├── LICENSE, README.md, pyproject.toml, requirements*.txt
└── src/
    ├── __init__.py, config.py  # 插件登记、账号、Intent、环境凭据
    ├── adapter.py, gateway.py   # 入站传输、网关生命周期、出站 API 与回复
    ├── event.py, message.py     # QQ 事件、消息及段
    ├── models/                  # common、guild、qq、payload
    └── exception.py, permission.py, store.py, utils.py
~~~

插件加载并启用 → 框架在启动前校验账号并注册适配器 → ready 启动传输 → 验证来路后登记 `qq:<app_id>` 网关 → `handle_event(gateway, event)` → 框架 Matcher 和依赖注入。WebSocket 分片独立维护心跳与恢复状态，一个账号只登记一次，最后一个分片离线时注销；Webhook 在首条通过验签且被接纳的已知事件到达时登记。停机停止接纳、取消并等待传输和事件任务、注销网关并清除令牌。不提供持久事件队列或跨进程去重。

业务可用 QQ 事件类型配合 `on_message`、`on_notice` 等匹配；`Matcher.send` 通过 QQ Gateway 向平台 API 回复，不写入回调 ACK。`gateway.call_api(name, **data)` 与有类型的 API 方法共用框架钩子。移植了上游 C2C、群聊、频道及富媒体模型与 API；实际权限、配额和 Intent 由 QQ 开放平台决定。API 网络超时沿用 Driver，401 刷新令牌后重试一次；其他调用失败不整体重发。部分上传及回复语义由上游 API 决定，取消不会撤销已提交给 QQ 的操作。WebSocket 握手限时 30 秒，掉线后退避重连，尝试按分片会话恢复。

## QQ Webhook 契约

在 QQ 开放平台填写 HTTPS 回调 `POST /qq/webhook`。`X-Bot-Appid` 选择账号；平台验证（`op=13`）返回 `200` JSON `{plain_token, signature}`。事件（`op=0`）必须带 `X-Signature-Ed25519` 和 `X-Signature-Timestamp`，用账号密钥对原始请求字节验签。接纳后返回 `200` 空体；未知事件类型忽略并确认。`400` 表示请求体或协议无效，`403` 表示账号或签名无效，`413` 表示请求超过 1 MiB，`503` 表示未就绪或事件并发已满。Driver 先读请求体，公网部署应在反向代理配置体积和速率限制。ACK 不等待 Matcher，也不保证持久化；签名验证不做防重放存储，有副作用的业务需自行幂等。此回调遵循 QQ 协议，不是通用业务 HTTP API，Driver 的 OpenAPI 仅列出路由。

安装 core 和 QQ 后运行 `python -I -m pytest -o pythonpath= tests/unit/test_qq_adapter.py -q`，避免仓库旧测试的源码路径遮蔽 wheel。GitNexus（OpsMesh，2026-11-19 刷新）定位了 WebSocket 和 Webhook 到 `dispatch_event` 的调用；异步任务到框架 `handle_event` 的边以源码和 Matcher 测试为准。实际 QQ 账号权限、生产网络和平台回调尚未联调。旧 Channels 后续整体删除，本次没有改动其实现或迁移旧消费者。
