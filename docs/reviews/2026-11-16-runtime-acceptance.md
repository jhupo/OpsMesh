# v0.5.5 运行验收记录

日期：2026-11-16（Asia/Shanghai）。版本：v0.5.5；提交：`2a2c17983cb1cf5f2a2fa4ac096fc9b359c7d9be`。

## 结论

本次 210 项针对性自动化测试和 12 项服务器专项检查通过。发布二进制完成并发消息分发、热启停、调用排空、进程故障恢复及 10 分钟持续负载验证。真实模型普通执行、流式执行、SDK Session 跨进程保存，以及 Langfuse 的成功/失败观测均获得实际证据。

不能据此认定所有外部链路已经稳定：额外模型检查中出现两次连接失败，后续调用恢复；QQ、钉钉的真实收发经用户明确选择暂缓。10 分钟负载不等于全天稳定性或生产容量认证。

## 环境与隔离

- 服务器使用已发布的 Linux x86_64 冻结程序及 v0.5.5 官方插件运行包，没有用源码代替宿主程序。
- 临时测试插件使用独立的宿主状态目录、SDK 会话数据库、API 令牌和 `127.0.0.1:18085` 监听端口。
- 热启停、SIGKILL、源码声明撤销和进程重启均针对隔离实例；没有停止、重启或修改正式服务的配置。
- 并发和持续负载以消息回显及原生 SDK `ScriptedModel` 为主，没有对外部付费模型发起压力请求。SDK 确定性调用关闭追踪，避免向 Langfuse 灌入压力数据。
- 真实模型：`gpt-6.1-sol`，reasoning effort 为 `medium`。凭据只从服务器既有环境读取。
- 验收后已停止隔离实例并删除临时 API 令牌文件。正式服务为 `active/running`，健康、就绪接口均返回 200；检查时 `NRestarts=0`。

## 自动化测试

共 210 项通过：第一组 207 项，补充 execution 资源关闭失败重试的 3 项。未重跑全仓测试。

范围：

- Framework 插件生命周期、依赖调用准入、同步任务取消、Matcher 续接。
- channels 传输生命周期、统一 HTTP 消息协议。
- QQ 官方 SDK 适配与钉钉 Stream 适配的确定性测试。
- execution 原生 Runner、回调、审批、并发 TraceContext 隔离、敏感信息策略、导出结果和关闭恢复。
- 导出失败不重跑模型；关闭失败保留资源供后续重试。

对应测试文件为 `tests/unit/framework/test_plugin_lifecycle.py`、`test_plugin_invocation.py`、`test_sync_cancellation.py`、`test_matcher_continuations.py`，以及 `tests/unit/` 下的 `test_channel_lifecycle.py`、`test_api_message_protocol.py`、`test_qq_channel.py`、`test_dingtalk_channel.py`、`test_execution_runner.py`、`test_execution_tracing.py`、`test_execution_tracing_exports.py`、`test_execution_tracing_shutdown.py`、`test_execution_plugin.py`。

## 发布二进制专项检查

| 场景 | 实际验证 | 结果 |
|---|---|---|
| 身份与并发准入 | 同一用户、同一对话重叠请求返回 409；不同用户使用相同对话 ID 不串话；正文伪造身份返回 422 | 通过 |
| 处理函数异常 | 注入 RuntimeError，响应隐藏内部异常文本，下一次请求仍成功 | 通过 |
| 续接生命周期 | pause 后续接、用户隔离、停止后清除续接、过期后按新消息处理 | 通过 |
| 并发分发 | 64 个客户端并发，共 1,280 次请求，逐条比对响应与输入；其中 160 次执行原生 SDK 确定性模型 | 全部成功，P95 390.87 ms，最大 476.64 ms |
| 热启停 | 30 次 disable/enable；停用期间拒绝请求，启用后重新可用 | 通过；前后任务数 5、文件描述符 11、Matcher 数 3 |
| 在途调用排空 | 8 个已接纳的慢请求执行中停用插件 | 8 次全部完成；停止等待约 0.36 秒，关闭回调确认无活动调用 |
| 启动故障及依赖保护 | 注入启动异常，修复配置后启用；尝试停止受保护且被依赖的 provider | 故障恢复成功，非法停止被拒绝 |
| 真实普通模型 | 原生 execution.run 调用，写入 SDK SQLiteSession | 成功；输出 OK，原生 Usage 为 41 tokens |
| 进程崩溃恢复 | SIGKILL 隔离宿主，由 systemd 重启，检查 PID 变化和后续调用 | 通过；不恢复被杀进程中的执行 |
| 真实流式及会话保存 | 新进程通过 execution.stream 读取同一 SDK Session，回答上一次测试口令 | 成功；17 个流事件，72 tokens，准确读回口令 |
| 持续负载 | 每秒一批 16 路并发，累计 600.48 秒、9,600 次请求；其中 600 次执行原生 SDK 确定性模型 | 全部成功，P95 209.19 ms |
| 源码声明撤销 | 停止后 revoke；尝试同进程重新启用；再重启宿主加载 | 撤销成功，同进程重新导入被拒绝，重启后恢复 |

延迟包含隔离测试客户端到 HTTP 入口、框架处理和响应读取；不代表外部模型生成延迟。压力请求在服务器本机发起，测试客户端也消耗服务器资源。

### 持续负载资源采样

| 时间（秒） | RSS（KiB） | asyncio 任务数 | 文件描述符 | Matcher 数 |
|---:|---:|---:|---:|---:|
| 0.21 | 182004 | 5 | 12 | 3 |
| 61.22 | 185228 | 5 | 13 | 3 |
| 122.24 | 185292 | 5 | 13 | 3 |
| 244.30 | 185492 | 5 | 13 | 3 |
| 427.59 | 185516 | 5 | 13 | 3 |
| 549.40 | 185576 | 5 | 13 | 3 |
| 600.48 | 185576 | 5 | 13 | 3 |

RSS 增长约 3.49 MiB 后趋于稳定，未观察到持续增长的任务或 Matcher。文件描述符预热增加 1 后保持稳定。该窗口内未发现明显泄漏迹象，不能替代小时/天级运行验证。

## 真实模型与 Langfuse

总共进行了 5 次真实模型调用尝试：3 次成功，2 次连接失败。未启用 SDK 自动重试；失败后的再次调用是明确的独立验证操作。

1. 初次普通调用成功，41 tokens；随后为测试崩溃恢复立即强杀进程。该次尚未批量导出的 Trace 未出现在 Langfuse，SDK 会话内容已保存。
2. 重启后的流式调用成功，72 tokens。Langfuse 中的 GENERATION 记录为 input=62、output=10，与原生 Usage 相符。
3. 补测普通调用两次出现 `httpx2.ConnectError: All connection attempts failed` / `openai.APIConnectionError`，HTTP 入口返回安全错误；Langfuse 分别保存了 ERROR 观测。
4. 使用同一发布二进制进行无模型调用的网络诊断，DNS、TLS、SDK HTTP 传输可用；重新启动隔离实例后再次普通调用成功，104 tokens，输出 VERIFIED。

两次连接失败的最终根因未确定，不能据此归咎于框架，也不能写成外部模型服务全程稳定。

Langfuse 最终查得 20 条 observation，属于两条成功链路和两条失败链路。成功调用逐项核验了：

- 每次模型调用只有一条对应的 GENERATION，没有重复生成记录。
- `model=gpt-6.1-sol`，User 为 `api:verification`，Session 为本次唯一测试会话。
- 输入、输出存在；标签包含 `release-verification`；metadata 保存原生 SDK trace ID。
- 72 / 104 tokens 分别与流式、普通执行的原生 Usage 相等。
- 最早被强杀的 41-token 调用不在导出结果中，没有伪造补齐。

可用于定位的成功记录：

| 调用 | SDK trace ID | Langfuse trace ID |
|---|---|---|
| 流式 | `trace_02cc9bb4516a438d922c1ed3df89bac7` | `f45e975238a5152eed30941a5c4ce16f` |
| 普通 | `trace_083f2030f8b94929ad84e63280e75bfe` | `815f2e64318e0107c06c7eef00701035` |

服务器处于 Langfuse v4 `events_only` 模式。旧 `/api/public/traces` 返回 404；验收脚本改用 `/api/public/v2/observations` 后查询正常。这是查询契约差异，没有修改 execution 的 OTLP 导出实现。依据：[Langfuse 官方查询说明](https://langfuse.com/faq/all/export-single-trace-or-session)。

## 仍需保留的边界

- QQ、钉钉本次只有确定性适配测试，没有接收真实群消息、上传真实媒体或执行平台管理操作。用户已明确选择后续补机器人实测。
- 追踪是进程内批量导出；SIGKILL 可能丢失尚未上报的数据，不能用它充当可靠计费账本。
- 热启停资源与重新导入源码是不同操作；当前同进程源码重新加载会被拒绝，需要重启宿主。
- 本次没有小时/天级浸泡、跨主机压力、真实群消息高负载或模型高并发验证。
- 本次未修改产品源码，也未提交或发布新版本；新增此验收报告。

本地原始检查结果及临时测试脚本保存在 `.git/verification/`；其中 `report.json` 保存 12 项检查和资源采样，`langfuse-verification.json` 保存成功观测的逐项校验结果。报告不包含凭据。
