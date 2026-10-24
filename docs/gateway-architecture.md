# NoneBot 接入架构

## 结论

优先复用 NoneBot 生态已有且持续维护的协议适配器。OpsMesh 负责多用户身份、权限、通道配置、连接租约、持久消息、任务和审计；NoneBot Driver/Adapter 负责网络与平台协议。QQ 当前已经采用这个边界。

NoneBot 核心提供框架，平台实现分布在独立适配器包。安装 NoneBot 不会自动安装或配置全部平台，也不能统一各平台的权限与富消息语义。2026-10-24 查阅的官方仓库将钉钉适配器标为寻找维护者、暂不可用，因此钉钉继续使用官方 Stream SDK。

## 从源码吸收的设计

| NoneBot 层 | 职责 | OpsMesh 使用方式 |
| --- | --- | --- |
| Driver / Mixin | HTTP、WebSocket 客户端和 ASGI 服务端，可组合并检查能力 | 复用原生 FastAPI + HTTPX + WebSockets 驱动 |
| Adapter | 协议连接、鉴权、订阅、重连和 API 分发 | QQ 复用原生 Adapter 生命周期与 API 描述符 |
| Bot | 机器人实例、统一 `call_api` 与平台发送方法 | Worker 中调用，返回平台原生回执 |
| Event / Message / MessageSegment | 平台事件、消息和结构化段 | 保留载荷，映射用户/会话/回复目标 |
| 事件预处理 | 接收消息后的拦截和分发 | 落入 OpsMesh 持久事件，再由任务执行插件 |

框架内不再实现 QQ 心跳、鉴权缓存、重连、签名算法和完整平台 API 包装。只保留桥接配置、身份映射、结构化消息转换、持久化与安全边界。

## 扩展新平台

```text
Platform
  -> maintained NoneBot Adapter / vendor SDK
  -> thin ChannelAdapter bridge
  -> ChannelEvent + durable event/inbox
  -> stored external identity + isolated Session
  -> pinned plugin task
  -> durable reply/operation queue
  -> native Bot.call_api / vendor API
```

使用已有的 `opsmesh_sdk.channels.ChannelAdapter` 注册合同，插件 `setup(ctx)` 声明通道工厂。平台凭据在 `connect()` 生命周期注入，`setup()` 不连接网络。增加已受支持平台通常只需桥接事件、目标和消息表示，避免重写通信协议。仍需逐个平台确认维护状态、应用权限、事件确认时机和必要的业务映射。

原生适配器经常依赖 `nonebot.init()` / `get_plugin_config()` 的全局状态。OpsMesh 的独立通道配置不能直接混入一个进程全局配置：QQ 的嵌入只调整构造注入，保留原生协议实现；新增适配器必须核对其配置读取与生命周期，必要时隔离进程。不能承诺任意 NoneBot 包无需审核即可安全热加载。

平台管理员角色不转换为 OpsMesh 管理员；平台会话 ID 不直接作为用户资源目录。模型、SDK Agent、业务工具和工作流继续由 OpsMesh 插件实现。NoneBot matcher/plugin 分发不绕过持久任务、额度、审批和固定版本执行。

## 上游参考

- [NoneBot2 仓库与适配器状态](https://github.com/nonebot/nonebot2)
- [2.5.0 Adapter 合同](https://github.com/nonebot/nonebot2/blob/v2.5.0/nonebot/internal/adapter/adapter.py)
- [2.5.0 Driver 合同](https://github.com/nonebot/nonebot2/tree/v2.5.0/nonebot/internal/driver)
- [QQ 原生适配器](https://github.com/nonebot/adapter-qq)

运行时调用以锁定版本源码为准；仓库首页表示查询时的上游状态。
