# Channels SDK 接入检查

## 修改

钉钉接入 alibabacloud_dingtalk 2.2.67。令牌、五项群管理、单聊和群聊模板消息、
附件下载地址、卡片创建/流式更新/最终更新改用原生异步方法。
删除通用 request_json 路径拼装；请求及响应结构由 SDK 模型处理。
保留消息格式转换、取消清理、交付回执和连接级令牌刷新合并。
SDK 禁止自动重试并设置连接、读取及总调用超时，异常不输出凭据或完整响应。

## 保留的适配及原因

| 范围 | 检查结论 |
| --- | --- |
| 钉钉媒体上传 | service SDK 未提供当前 /media/upload 端点；Stream SDK 使用同步 requests，保留异步 multipart 上传 |
| 钉钉 sessionWebhook | service SDK 未提供会话 Webhook 直接回复方法，保留异步发送 |
| 钉钉 Stream | 复用原生解析与 ACK；同步握手、取消和连接关闭由异步适配处理 |
| QQ 消息、媒体、令牌、网关 | 使用 qqbot-agent-sdk 1.2.2 原生功能 |
| QQ 群和频道管理 | SDK 无专用管理方法，继续通过其 request 入口复用认证与传输 |
| QQ 事件补齐 | SDK parser 未覆盖 GROUP_MESSAGE_CREATE，保留已注明的事件映射 |
| QQ 回复序号 | SDK next_msg_seq 生成随机值；本地序号还承担回复额度与并发控制，不能直接替换 |
| API | FastAPI/Starlette 承担网络入口，框架 dispatch_event、Matcher 和依赖作用域承担处理 |
| 回调队列 | 分离网络 ACK 与后台处理，限制容量和会话顺序；最终仍调用框架分发 |
| 输出与附件 | 消息归一化、格式限制、交付回执、受限下载属于通道边界 |
| 生命周期 | 连接和后台任务接入插件生命周期，厂商无限运行循环不能直接替代 |

下载允许列表是既有地址策略，并非 SDK 功能。保留每跳检查和官方 OSS HTTP 链接
转 HTTPS 的修复；未取消限制、未新增宽泛域名。域名后缀校验不代表完整网络隔离，
不能据此宣称已覆盖所有 SSRF 场景。

## 验证范围

SDK 最底层网络动作注入模拟传输，仍执行原生请求序列化、响应模型和错误处理。
159 项相关测试通过；追加的网络错误不重试、取消传播测试通过。
验证范围为群操作、消息、附件、流式卡片、令牌并发、取消及生命周期。
这不是机器人真实收发验收；本次未提交、发布或更新服务器。


## 结构精简

- `channel.py`：一份机器人连接归属、就绪状态及去重实现。QQ 和钉钉继承此类，框架核心未改。
- `sending.py`：消息归一化、支持类型校验、附件解析统一执行，返回 `PreparedMessage`；两个发送器只处理原生协议编码和发送。
- `qq/operations.py`：合并群和频道管理，共用认证请求入口；删除 `qq/groups.py`、`qq/guilds.py` 和两个旧操作类。
- 两个 `media.py` 保留媒体转换函数，删除 `QQMedia`、`DingTalkMedia` 临时包装对象。
- 状态常量只在 `channels.channel` 定义，不在 QQ、钉钉旧路径提供别名。旧类、旧文件无兼容重导出。
- 保留 SDK 未提供的管理接口、群回复额度、附件限制、队列容量、流式取消及交付回执；没有通过删功能减少代码。

对调用方：管理操作的 `channel.call()` 名称不变；直接导入 QQ 操作类改用
`opsmesh.plugins.channels.qq.operations.QQOperations`。内部发送准备函数必须显式提供
支持的消息类型，返回值改为 `PreparedMessage.parts/kinds`，调用处和测试已同步修改。
