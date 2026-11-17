# channels 重复实现与调用链专项评审

评审日期：2026-11-17。对象为 `C:/Users/Administrator/Desktop/github/OpsMesh` 当前未提交工作区，包含刚完成的精简修改，不是旧提交或设计稿。

本次没有修改生产代码。按发送、流、HTTP 请求与清理、机器人配置和生命周期调用链阅读源码，并执行无网络探针。GitNexus 绑定 OpsMesh，但索引时间为 2026-11-17 02:21 UTC，早于最近工作区重构，图只用于定位，结论以当前源码及探针为准。没有运行全仓库测试，也没有调用真实机器人。

## 结论

不能把剩余约 8,090 行全部解释成不可减少的必要实现。仍有一处响应对象契约问题、一处输出预算设计不一致，以及可明确收敛的校验、机器人分支和生命周期结构。之前的评审主要验证功能和资源释放，没有充分追踪同一规则在不同层重复执行的问题；通过 321 项受影响测试不等于这些结构合理。

本轮采用 Python Clean Code 的 G5（重复）、G9（无调用代码）、G30（单一职责）、G31（时序耦合）作为检查线索，不机械按文件数、函数参数数目判断质量。

## 1. P2：HTTP 响应保存两份状态，修改外层响应头不生效

位置：`packages/channels/src/api/streaming.py:137`，`MessageResponse.__init__` 和 `__call__`。

现有路径：

```text
MessageExchange.respond
→ json_response 创建 Response
→ MessageResponse 保存 self.response
→ 再调用 Response.__init__ 复制 body/status/headers
→ __call__ 实际调用 self.response，而不是自身的 Response 实现
```

无网络探针：创建 MessageResponse，设置 `wrapper.headers["x-probe"] = "present"`，再执行 ASGI 调用。外层可以读到该值，实际 `http.response.start` 的 headers 中却没有它。

这是重复状态已经产生可观察差异，不只是多了一层方法。尚未证明现有生产处理器正在修改该外层头，因此不把它描述成已发生的线上响应丢失。

建议：Response 只保留一份状态。MessageResponse 自己持有编码后的 body/status/headers，通过 `super().__call__` 写出，仅包住插件作用域和 finally 清理；删除内层 Response 的长期持有。保持实际写出后才交付回执。

需要补的回归：响应头修改、BackgroundTask、写出异常、实际写出后的回执与插件调用释放。

## 2. P2：流与 HTTP 输出各有一套预算，配置无法统一控制

位置：

- `api/config.py:79`：HTTP 默认 128 条、8 MiB。
- `streaming.py:86`、`streaming.py:103`：MessageStream 独立统计字节、ID 和条数，固定 1 MiB、100 条。
- `api/output.py:58`、`api/output.py:67`：MessageOutput 再统计字节、ID 和条数。
- `dingtalk/cards.py:65`：卡片另算 1 MiB，并计算平台消息字符数。

通过直接调用真实 MessageOutput 的无网络探针得到：

| 内容 | 逐次 send | 同一 stream |
| --- | --- | --- |
| 101 条短消息 | 101 条全部接受 | 第 101 条抛出 Too many stream messages |
| 单条 2 MiB 文本 | 接受 | 抛出 Stream output exceeds the size limit |

探针使用相同 HTTP Settings：128 条、8 MiB。区别来自内部发送方式，不是 JSON 和 SSE 编码方式。文档确实提到了额外流上限，因此这属于需要修正的设计一致性问题，不能伪称从未声明过限制。

单流和整次请求的预算可以不同；问题在于当前两套规则独立写死、重复计数，插件调用方式改变了实际可用上限。

建议：明确一个输出预算来源。HTTP 的累计额度由 MessageOutput 管理，流读取器负责序列、块边界、单次 delta、空闲读取等流协议约束；确需单流总额时从调用侧传入并明确其含义，不再静默附加 100 条/1 MiB 常量。机器人使用对应协议的预算。钉钉的原生卡片字符限制应保留，不与通用字节限制混为一谈。

需要补的回归：同一份配置下 send/stream 的总量行为、多次 stream 的请求累计额度、JSON/SSE 一致性、机器人平台上限。

## 3. 明确存在重复校验，但不能删除发送边界验证

位置与调用证据：

| 重复规则 | 当前路径 | 建议 |
| --- | --- | --- |
| QQ 媒体格式 | `qq/sending.py:56` 调用 media_type，随后 `qq/media.py:53` 在 upload 中再次调用同一函数 | 预检保留并产出 file_type，上传直接使用预检结果 |
| duration_ms 类型及正数 | `messages/schemas.py:58` 的 MediaData 已校验；normalize 后又进入 `sending.py:163` 的手写校验 | 内部准备函数接收已校验段，删除重复类型/范围判断 |
| Markdown 标题 | `messages/segments.py:41` 手写规则；`messages/schemas.py:27` 再定义规则 | 规则只定义在一个 schema，构造辅助方法复用该规则 |
| DingTalk webhook scheme/host | `dingtalk/sending.py` 的 _webhook_url 检查，紧接着 `dingtalk/client.py` 的 post_json 再检查 | URL 规则保留在传输入口；发送层只判断 webhook 存在及是否过期 |

QQ 媒体探针执行真实预检加 upload，模拟底层发送，media_type 调用计数为 2。

消息段 data 可修改，调用者也能直接构造 MessageSegment，因此 normalize 的发送边界校验必须保留。附件所属事件、实际文件大小、真实媒体字节、厂商支持格式是不同检查，不能因为都叫 validate 就一起删掉。

## 4. P3：QQ 和钉钉的通用发送分支仍重复

位置：`qq/adapter.py:77`、`dingtalk/adapter.py:72`、`qq/channel.py:109`、`dingtalk/channel.py:35`。

两种协议重复执行：识别 stream/send_message、取 event/delivery、校验回执归属、建立 send 闭包、调用 send_stream。两个 Channel.send 又先做一次事件类型判断，进入 RobotAdapter._validate_reply 后再次判断。流入口已校验 event，每条完整消息进入 _send_message 时又走同一判断。

当前完整消息链路：

```text
Matcher.send
→ Channel.send
→ 框架 Channel.call / _invoke
→ 协议 Adapter._call
→ 协议 Adapter._send_message
→ QQSender / DingTalkSender.send
→ 原生 SDK
```

其中框架 Channel.call 必须保留，它执行插件调用准入和调用前后钩子。Sender 也有实际协议编码、媒体上传及部分成功处理，不是空转发。

可以减少的是两个协议重复的通用操作分支及 Channel.send 的重复类型检查。将相同逻辑收进已有 RobotAdapter/RobotChannel，厂商只实现发送和可选卡片处理；不新增通用操作注册中心。归属和事件校验放在所有调用都能到达的 Adapter 发送边界，不能只放 Channel.send，否则直接 call 会绕开。

需要补的回归：send 和 call 两种入口错误一致、外来连接事件拒绝、流式发送每次实际发送前的连接状态检查、钉钉卡片分支。

## 5. P3：HTTP 清理链层级过多，职责可以收拢

位置：`api/streaming.py:12`、`:110`，`api/exchange.py:112`、`:127`，`api/requests.py:87`。

SSE 收尾路径：

```text
MessageStreamingResponse.__call__
→ close_response（10 秒 shield）
→ MessageStreamingResponse.aclose（再次 10 秒 shield）
→ MessageExchange.close
→ RequestResources.close（再次 10 秒 shield）
→ AsyncExitStack
```

process 在业务执行结束已调用 RequestResources.close，最终响应清理又调用一次。AsyncExitStack 本身避免同一退出回调执行两遍，因此这里没有证据说明业务资源被重复关闭；问题是职责分散和重复的清理预算。RequestResources 只持有一个 stack 并包装 close，目前只被 MessageExchange 使用。

建议：MessageExchange 明确拥有 close_dependencies 和 finish_transport 两个阶段；依赖退出只由一处带屏蔽和预算的实现负责，响应对象只触发传输结束并确保幂等。取消 RequestResources 这层仅用于转发的对象，避免同一收尾路径叠加三份 10 秒超时。

依赖栈与实际响应传输的占用不能合成一个生命周期：前者须在业务处理结束释放，后者须覆盖 ASGI 实际写出和回执。原有取消、断开、失败保留首个异常的测试应保留。

## 6. P3：生命周期协调器保留了没有生产调用者的通用分支

位置：`lifecycle/transports.py:32`、`:46`、`:97`。

生产调用 ChannelTransports.use 的只有 QQAdapter.configure 和 DingTalkAdapter.configure，且都来自消费者插件启动作用域。API 直接通过 startup(entrance) 加入。当前 use 仍支持无插件 transport、独立引用计数 _declared，并用 _managed 判断走框架生命周期还是启动时直接启动。无插件分支的现存调用主要是测试探针。

因此 _declared 引用计数和 _managed 插件集合在真实机器人路径上重复描述拥有关系；增加第二套通用启动模式没有当前功能依据。

建议：明确机器人 transport 必须属于已加载插件，保留按 transport 记录的插件集合，移除无归属 use 分支及对应独立计数；测试使用真实 Plugin 来验证生产语义。API 仍作为 channels 自有资源。

不能把 _owners、_started、_dirty 简单全删掉：已声明拥有者、当前运行拥有者、部分初始化失败是不同状态。源码中的 ready 和 replies_available 也分别表示入站在线与出站资源存活，不能合并。

## 7. 可直接清理的小项

- `lifecycle/transports.py:42` 的 has_declarations 没有仓库调用者。
- `api/events.py` 的 payload 保留了原始 MessageRequest，构造后没有生产读取者；转换后的 message 和 conversation_id 已包含分发所需内容。删除该冗余字段前同步更新事件构造测试。

这些是小项，不拿它们充当大幅精简的主要成果。没有把 Pydantic validator、框架注册回调或 SDK 回调因“搜索不到直接调用”误判为死代码。

## 不应删除的现有实现

- 框架 Channel.call、Matcher、Dependent、dispatch_event 的路径：存在准入、钩子、规则和依赖作用域。
- 机器人有界队列和去重：SDK 回调到框架处理间的容量、会话顺序及 ACK 时机。
- SDK 平台差异、卡片状态及分段发送部分成功：必须保留实际传输语义。
- HTTP 响应背压和断开取消：只返回 SDK 迭代器不能替代。
- 附件集合与上传缓存：一个按事件生存，一个跨 HTTP 请求生存。
- 公开方法仅在框架/SDK 回调中被使用的情况：不能仅根据文本引用计数删除。

## 建议修改顺序

1. 修正 MessageResponse 的双份状态；统一输出预算的责任和配置。
2. 收敛媒体预检和发送分支，同时保留所有真正的边界验证。
3. 收拢 HTTP 清理责任，删除无归属 transport 分支和无调用小项。
4. 仅运行受影响的通道、消息、流式和热启停测试；验证文件上传、断开取消、部分发送失败、恢复启用。当前探针是评审证据，不代替修改后的回归测试。

不承诺先删固定行数。目标是让每条规则只在一个明确位置定义、每份状态只有一个拥有者，并保留必要的边界和资源语义。
