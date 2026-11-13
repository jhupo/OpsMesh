# 多模态消息与附件

channels 使用框架 Message / MessageSegment。公共实现位于
`opsmesh.plugins.channels.messages`，消息段包括 text、image、file、audio、video、mention。
QQ、钉钉仍保留协议 Event；插件使用 `event.get_message()` 读取有序内容，
使用 `event.attachments.get(id)` 和 `await event.attachments.read(id)` 读取当前事件的附件。
附件内容不会进入 UserContext。原始平台 URL、downloadCode 和凭据不进入公共消息段。

## 入口行为

chat 和 sandbox 示例使用低优先级 `on_message` 处理私聊与群内发给机器人的消息。
无需 `/chat`；`/chat`、`/help`、`/session`、`/history`、`/clear` 命令优先处理。
未识别的命令返回提示，不触发模型。API 消息入口绑定相同的命令和消息 Matcher，
普通 HTTP 路由保留自己的业务 payload。入口写法见[通道与执行回调](channels-and-callbacks.md)。
群会话仍按机器人连接、群、用户隔离，不自动合并成全群共享历史。

QQ 解析 C2C_MESSAGE_CREATE、GROUP_AT_MESSAGE_CREATE 中的 attachments；
钉钉解析 text、markdown、picture、richText、file、audio、video。
这里只声明适配器能解析的格式；群聊是否推送文件或音视频由平台与应用权限决定。
群消息接收范围由平台订阅及应用权限决定，持续连接本身不授予全量监听权限。

## API 上传、使用及下载

向 `POST /attachments/{plugin_id}?name=report.pdf` 发送原始文件内容，
Content-Type 填写文件媒体类型，并携带现有 Bearer 凭据。不使用 JSON/Base64 上传或 multipart。
返回 201，包含 `id`、`name`、`media_type`、`size`。

上传和下载要求目标插件已加载且 ready。引用由插件 ID 与认证用户 ID 共同限定，
其他用户或其他插件的请求不能借用该 ID。正文中的用户信息无法改变认证结果。

随后调用智能体插件自己的聊天入口，例如：

```json
{
  "session_id": "existing-conversation-id",
  "message": [
    {"type": "text", "text": "分析这个文件"},
    {"type": "file", "attachment_id": "32-character-attachment-id"}
  ]
}
```

示例中的 session_id 和 attachment_id 均需替换成实际返回值。
普通文本仍可使用 `"message": "你好"`。纯附件输入也有效。
`GET /attachments/{plugin_id}/{attachment_id}` 提供经过认证的下载，强制下载并禁止内容嗅探。
原始磁盘路径、平台下载 URL 和模型供应商 file_id 均不能替代附件 ID。

API 上传是有期限的内存缓存，不是文件资产中心：默认单文件 8 MiB、单条消息 24 MiB、
每条消息最多 8 个附件、缓存总量 64 MiB、最多 256 个条目、有效期 900 秒。
缓存满时拒绝新增；过期、停机、重启后引用失效。多进程部署需保证上传与引用落到同一实例。
这不影响已经写入 SDK 历史或沙箱工作区的内容。

限制配置放在 `channels.config.api.attachments`、`qq.attachments` 或
`dingtalk.attachments`，均由所属协议校验。下载默认超时 30 秒，限制实际读取字节数，
不跟随重定向，只允许 HTTPS 官方媒体域名，不向媒体下载地址转发 API 凭据。
API 文件正文仍有服务端边界限制；附件大小与普通 JSON 请求大小分别配置。

## 智能体与持久化

`examples/common/conversation_support/inputs.py` 是示例复用代码，
不进入框架或 execution。普通 Agent 将 PNG/JPEG/GIF/WebP 转成原生 input_image，
PDF 转成 input_file，UTF-8 文本与 JSON 转成 input_text。
图片校验基本文件签名，PDF 校验文件头；这些不是完整文件安全扫描。
未知二进制文件、音视频在普通 Agent 中明确拒绝，不静默丢失；处理它们请使用沙箱或自行提供处理工具。
所选模型供应商仍需实际支持对应原生输入，框架不伪造模型能力。

图片/PDF 内容以 data URL 放在原生输入中，SDK SQLAlchemySession 持久保存完整输入。
因此后续轮次与重启后不依赖平台临时链接。没有额外复制一套 SDK 会话或附件索引表。
原生历史包含文件数据，数据库容量和历史接口返回大小会随之增长，配置追踪时也应按实际需要控制敏感内容采集。

SandboxAgent 通过当前 SandboxSession.write 写入生成的唯一相对文件名，
将路径作为用户消息内容交给智能体。它只接收文件，不承诺环境已安装所有音视频处理工具。
上传文件不会自动解压或执行。工作区沿用现有 SDK 会话状态与 filesystem 目录；
清理历史保留工作区，删除会话或 reset_workspace 移除工作区。

execution.run/stream 原样接收已有的原生 SDK 输入类型，没有新增多模态执行器。
普通 Agent 的会话清理和删除沿用 SDK 历史清理，因此同时移除保存在历史中的附件内容。

## 回复能力

各 Channel 的 receive_types / send_types 声明当前实现的消息类型。
API 可以将公共 Message 中的附件转换为受保护的临时下载引用。
插件先把生成的 bytes 加入 `event.attachments.add_bytes(...)`，再通过 `Channel.send` 回复公共 Message。
对应路由的返回契约需要允许该消息段列表；也可以直接返回原生 Response。

QQ、钉钉均通过 `channel.send(event, message)` 发送媒体，接收能力与发送格式限制分别声明：

| 通道 | 图片 | 文件 | 语音 | 视频 |
|---|---|---|---|---|
| QQ | PNG、JPEG | 使用官方 file_type=4；是否获准由账号和平台返回决定 | SILK | MP4 |
| 钉钉 | PNG、JPEG、GIF | 文件名需带扩展名 | AMR，需真实 duration_ms | MP4，需真实 duration_ms 和图片封面 |

这里的图片/音视频检查是文件头和元数据检查，不是完整编解码验证，不自动转码。
不符合发送格式的音视频可以由插件明确选择作为普通文件发送；通道不做静默降级。
接收附件与插件生成的附件分别计算数量和总字节限额，收件不会占用生成回复文件的额度。
API 上传引用通过 `receive_bytes` 进入接收集合；插件输出继续使用 `add_bytes`。
发送时还会按本条回复实际引用的附件（包括视频封面）校验数量与总字节限额，不能通过混用收件和生成文件绕过限制。
平台还可能施加更严格的大小、频率和权限限制，
失败会返回明确错误，不承诺所有账号都已开通这些接口。

QQ 把本地内容以 `file_data` 上传，使用返回的 `file_info` 发送 `msg_type=7`，并按协议填写空格 `content`；
不需要公网 URL，`srv_send_msg` 固定 false，防止上传时意外产生主动消息。
钉钉使用官方 multipart 素材上传，保留原始 media_id，再使用 sampleImageMsg / sampleFile /
sampleAudio / sampleVideo；群聊与私聊分别走 groupMessages/send 与 oToMessages/batchSend。
单聊媒体要求回调携带 senderStaffId，不能把 senderId 当成员工 ID。
企业机器人需要开通对应单聊、群聊及素材接口权限。纯文本继续使用现有 sessionWebhook。

协议转换位于各通道的 sending.py / media.py；连接、接收和事件分发留在 adapter.py。
现有异步客户端承接 HTTP，框架和 execution 不新增发送引擎。当前锁定的 dingtalk-stream
上传方法使用无 timeout 参数的同步 requests，媒体路径因此沿用相同官方协议的异步调用，
没有套用可遗留后台线程的同步包装。QQ botpy 旧方法缺少 file_data 参数，因此对照腾讯较新
Python SDK 的 RichMediaMessage 协议扩展现有 HTTP 路径，没有另起一个 SDK 连接或 token 缓存。

组合消息会先解析所有附件、检查格式、上传全部素材，再按内容顺序发送；相邻文本合并。
这不提供跨平台原子发送：上传成功但后续发送失败，平台素材可能保留至平台规定的过期时间。
多段发送失败抛出 MessageSendError，completed 包含已确认的原生回执，failed_index 为失败段的
零基索引；失败段可能已送达但响应丢失，因此不自动重发。取消会继续传播，不启动后续发送。
单段成功返回原生回执；多段成功返回顺序回执列表。

QQ 每条来信最多回复五次。通道在上传前检查当前记录的剩余次数，同一来信的发送串行执行，
不同来信可并行。只有实际尝试发送才递增序号；上传失败和发送前取消不消耗回复次数。
发送超时仍可能已经送达，因此已尝试的次数不会退回。该记录属于当前连接的有界内存状态，
不替代 QQ 服务端限额，也不承诺跨进程、重启或记录淘汰后的全局精确计数。

API 的 `Channel.send` 同时接受完整 `Message` 和单个 `MessageSegment`。
多附件响应先完成所有解析及额度检查，再批量写入下载缓存；解析失败或缓存不足时不会留下部分文件。

`Message.extract_plain_text()` 只按顺序拼接文本段，保留原有空白，不读取附件，也不插入文件名或占位符。
命令参数、文本匹配可使用它；向模型传递多模态输入时必须保留完整 `Message` 和对应附件集合。

插件生成附件的最小示例（report_bytes 由插件生成）：

```python
from opsmesh.plugins.channels import Attachment, Message, MessageSegment

attachment = event.attachments.add_bytes(
    Attachment.create("report.pdf", "application/pdf"), report_bytes
)
message = Message("Report generated.")
message.append(MessageSegment.attachment(attachment))
await channel.send(event, message)
```

视频发送时，先把封面也加入当前事件的附件集合，再构造
`MessageSegment.attachment(video, duration_ms=actual_duration, thumbnail=cover)`。
语音使用 `duration_ms` 提供真实时长。附件来源仍由插件与 event.attachments 管理，
不把本地路径交给机器人平台，不向插件暴露平台 token。

协议依据：[腾讯 Python SDK](https://github.com/tencent-connect/qqbot-agent-sdk/blob/main/src/qqbot_agent_sdk/dto.py)、
[钉钉素材上传](https://github.com/open-dingtalk/dingtalk-stream-sdk-python/blob/main/dingtalk_stream/stream.py)、
[钉钉机器人消息类型](https://open.dingtalk.com/document/orgapp/types-of-messages-sent-by-robots)。

## 验证范围

受影响测试覆盖事件转换、延迟下载、消息顺序、身份隔离、过期、大小限制、取消后重试、
API 上传与下载、原生 Session 内容持久化、沙箱文件写入及普通机器人对话。
平台下载流程使用确定性 HTTP 替身验证；真实 QQ/钉钉账号和实际模型媒体能力需在对应部署中验收。
