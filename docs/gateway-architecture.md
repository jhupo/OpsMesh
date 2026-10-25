# 网关设计与维护边界

QQAdapter 和 DingTalkAdapter 直接继承 `gateway/adapters/base.py` 的同一个 `Adapter(ABC)`，每个已配置通道对应一个实例。唯一的抽象接口为 `capabilities / run(sink) / send(message) / call(operation, parameters)`。准备状态、事件发布和明确拒绝是公共辅助行为；没有第二层 ChannelAdapter、公共 Bot 或 BotT 泛型。

## 所有权与生命周期

Gateway Worker 持有通道租约、加载配置工厂、保存能力目录，并监督连接、Outbox 和操作队列。`run(sink)` 持续接收事件并负责重连，取消或致命错误退出时在 finally 中释放资源。Worker 关闭时先取消并等待发送与操作任务，再取消并等待 run。取消中的副作用记为 uncertain，不自动重发。

`Adapter.state` 区分 connecting、connected、reconnecting、failed 和 stopped，只有 connected 设置 ready。准备状态属于运行实例，能力目录只描述实现、配置、事件、消息和操作，不以网络状态改变其声明。通道改配置后由 Worker 创建新代际实例。

入站去重、身份绑定、权限、审计、配额、加密 Inbox/Outbox 和平台回执属于宿主业务层。适配器不负责组装任务或访问其他用户的工作区；API 进程不启动平台连接。

## 目录

```text
gateway/
  worker.py                 # 租约、工厂、监督与取消顺序
  service.py                # 持久 Inbox、身份与任务路由
  delivery.py               # 持久 Outbox 与投递回执
  operations.py             # 授权复核、幂等操作与不确定结果
  adapters/
    base.py                 # 唯一 Adapter ABC
    schemas.py              # Event/Message/Capabilities/Operation/Receipt/Result
    errors.py               # 可公开的明确拒绝代码
    qq/
      adapter.py            # QQAdapter(Adapter)
      api.py                # 官方 BotAPI 的固定操作目录
      events.py             # botpy 事件及宿主映射
      messages.py           # 官方消息请求转换
      SDK.md, SDK.json      # 官方 SDK 来源记录
    dingtalk/
      adapter.py            # DingTalkAdapter(Adapter)，官方 Stream SDK
      config.py, events.py, messages.py
      handlers.py           # 原生回调与持久化后的 ACK
      api.py                # 固定主机、鉴权、响应校验与媒体上传
      schemas.py            # 卡片操作的固定路由与严格参数模型
```

QQ 的连接、分片、令牌、事件解析和 HTTP API 都由官方 `botpy.Client` 与 `botpy.api.BotAPI` 持有。OpsMesh 只在回调边界将官方事件对象标准化，并通过固定目录暴露官方 API。钉钉直接组合锁定的官方 Stream 客户端。

## 能力与副作用

能力目录与 call 的可调用集合一致。每项操作声明参数和结果 JSON Schema，以及管理员权限、禁止自动重试、并发、平台限流和回执约束。QQ 只声明固定的官方 `BotAPI` 消息和媒体方法；钉钉仅声明 message.send、media.upload 和 dingtalk.card.create/deliver/update/stream。方法、端点和鉴权由实现固定，调用者不能指定 URL、路径或鉴权头。

操作入队时检查目录，执行前再核对当前目录和数据库中的管理员身份。持久幂等键防止重复提交；一个通道顺序处理操作，消息投递可与操作重叠。参数和回执限制为最多 8 MiB 的有限 JSON。平台限制不触发隐式重试。

send 返回 Receipt，表示平台接受或 QQ pending_audit，不证明用户实际收到。call 返回 Result，包含 operation 与 value。明确拒绝记为 failed；超时、取消、连接中断、无有效回执等结果不能确认的情况记为 uncertain。人工核对后才可显式重试。

QQ 官方 WebSocket 回调和钉钉回调都在宿主 sink 成功后完成事件处理。进程重启仍受平台历史窗口限制，不能承诺端到端恰好一次。群管理员等外部角色只作为业务元数据，不能授予 OpsMesh 平台权限。

## 插件 SDK 边界

传输值、错误和 Adapter 位于 gateway 内部，不再生成 contracts.channels 或 SDK 传输协议。通道插件工厂直接返回匹配宿主的 Adapter；setup(ctx) 只声明工厂，连接由 Worker 管理。任务仍通过 opsmesh.contracts.gateway.Channel 的 reply/request 提交持久操作，不获得连接或凭据。

其他公开扩展合同由 opsmesh.contracts 拥有，框架不导入生成 SDK。SDK 工程在独立 sdk 分支维护，发布阶段从固定输入生成运行绑定与类型声明。生成器在普通开发目录遇到已移除合同的残留文件时拒绝覆盖，发行工具只在刚创建并验证的 staging checkout 中清理这些文件。

## 第三方来源与验证

QQ 直接使用腾讯官方 `qq-botpy==1.2.1`，不复制平台协议或引入其他框架。SDK 身份和仓库记录见 [QQ SDK 来源](../src/opsmesh/gateway/adapters/qq/SDK.md)；安全工具检查锁定依赖中存在该官方包且不存在 NoneBot 依赖。

钉钉使用锁定的 dingtalk-stream 0.24.4b1 预发布版本。卡片端点与字段对照安装源码 card_replier.py 和 [官方源码](https://github.com/open-dingtalk/dingtalk-stream-sdk-python/blob/main/dingtalk_stream/card_replier.py)。原生便捷方法存在吞错或阻塞，因此持久操作使用严格 HTTP 响应检查，同时继续使用 SDK 令牌缓存和 Stream 协议。静态检查与真实平台验收的边界见 [校验记录](verification.md)。
