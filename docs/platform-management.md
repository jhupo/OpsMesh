# 平台管理与自动化

这些功能由服务端 API 提供；路径默认以 `/api/v1` 开头。HTTP 参数和响应以当前 `/openapi.json` 为准。
登录认证使用 `X-Session-ID`。平台不内置企业身份、组织、SSO 或服务账号。

## 配置、发布与凭据

`agents/catalog` 保存加密草稿和不可变发布版本。Agent 配置归创建者；系统种子配置、Provider
和凭据由管理员管理。其他用户只能发现系统配置和明确公开的 Agent。

1. 管理员通过 `/agent-catalog/credentials/{identifier}` 写入或轮换凭据；接口只返回元数据。
2. Provider 的 `credential_ref` 使用 `secret:identifier` 或 `env:VARIABLE`。环境引用在执行所在
   进程解析，因此 API 与 Worker 必须具有相应环境变量。
3. 在 `/agent-catalog/provider/{id}/draft` 保存 Provider，再调用 `publish` 发布。
4. 用户在 `/agent-catalog/agent/{id}/draft` 保存配置，再发布。首次保存使用
   `expected_revision=0`；编辑与发布必须携带最新 revision。
5. 发布校验已发布 Agent 的模型及传递依赖；不可通过禁用依赖破坏已有发布图。公开 Agent
   不能依赖其他用户不可读取的私有配置。
6. 发布请求的 `restore_number` 可恢复历史版本，但会新增发布版本，保留完整历史。
7. `/agent-catalog/published/models` 提供可选模型及配置价格，不返回地址或凭据引用；
   `/agent-catalog/published/agents` 提供可创建实例的配置。

创建 execution 实例时冻结已发布 Agent 图；运行准入时再冻结 Provider 参数、价格、工具版本
和审批策略。发布新配置只影响之后创建的实例或准入的 Run。凭据轮换仍由引用解析器读取最新值。
已有实例、Session 和沙箱不会因发布、回滚或 Run 结束被删除。

`OPSMESH_AGENT_CATALOG_PATH` 是首次导入种子文件。API 与 Worker 在数据库锁下只插入不存在的
记录，重启不会覆盖在线修改。以后使用上述 API 发布，不靠编辑文件热更新或重启生效。
种子 JSON 仍须结构与引用有效；环境凭据在真正使用时检查。

## 模型重试与时限

Provider 配置如下：

```json
{
  "timeout": 60,
  "max_retries": 2,
  "request_timeout": 300
}
```

`max_retries` 为额外重试次数，取值 0–10；默认最多尝试 3 次。`timeout` 是原生 OpenAI Client
的单请求传输超时，`request_timeout` 是 OpsMesh 单次模型操作的总时限，包含 SDK 重试及退避，
最大 330 秒。Run 的 `timeout_seconds` 进一步限制整次执行，先到的时限生效。

连接错误、传输超时和可重试 HTTP 状态使用原生 SDK 的退避策略；认证失败、无效参数等错误
不属于通用重试范围。流式输出已经开始后不重新发送一整段输出。Temporal 模型 Activity
保留一次执行策略，不叠加另一层模型重试；工具和 MCP 外部效果也不随模型重试再次执行。

模型调用最终失败不能证明服务商未执行或未计费。此时回执进入不确定状态，保留额度和费用
预留，交由显式对账处理；不会自动当作零用量释放。

Provider 的 `network` 独立选择网络路径：`{"mode":"direct"}` 默认直连并忽略环境代理，
`{"mode":"proxy","proxy_ref":"secret:model-proxy"}` 使用指定 HTTP(S) 代理，
`{"mode":"environment"}` 使用客户端的环境代理规则（含 NO_PROXY）。
proxy_ref 只接受 env:VARIABLE 或 secret:identifier，代理 URL 必须有明确端口，不能写进模型参数。
每个模型 Client 拥有自己的网络配置，不修改进程级环境变量。
同一厂商需要同时代理和直连时，注册两个 Provider ID；不同子 Agent 的 ModelRef 分别选择它们，
base_url、模型、API 凭据和代理可各自不同，不要求整个 Agent 图走相同网络。
新增环境引用需配置到实际执行进程；Provider/插件配置更新不等于自动分发服务器环境变量。

非 OpenAI 协议的决策服务通过独立插件适配，示例见
[TypeSafe Decision](../sdk/python/examples/typesafe-decision/README.md)。它使用自己的官方 SDK
和重试配置，不伪装成 OpenAI Chat Completions。网关只调用配置的编排，不固定决策厂商。

## 运行事件和追踪

- Agent Run：`/execution/runs/{id}/events/stream`。
- Workflow Run：`/workflows/runs/{id}/events/stream`，已有节点接口提供关联 Agent Run ID。
- Agent 操作追踪：`/execution/runs/{id}/trace`；已确认的模型/工具结果通过
  `/execution/runs/{id}/operations/result?operation_id=...` 读取。

SSE 使用持久事件序号作为 `id`，支持 `after` 和 `Last-Event-ID` 断线续传；每批重新校验登录
与所有权，定期发心跳，连接约 5 分钟后要求重连。终态、审批等待或需对账状态结束当前流。
数据库事务不跨整个 SSE 连接持有。

直接执行的 `/execution/runs/{id}/execute?stream_model=true` 使用原生 `Runner.run_streamed`，
将文本增量分批写入事件日志；调用方可同时订阅 SSE。默认直接执行仍使用普通 Runner。
Temporal 流式调用使用官方 Agents 集成的 `Runner.run_streamed`、`WorkflowStream` 和
模型 streaming Activity。授权的模型 Activity 将公开文本分批写入持久事件，SDK 管理
流事件传输和完整模型结果。QQ C2C 的 text 路由可转发公开根 Agent、handoff 与根沙箱文本；
工具 Agent 及其后代不公开，带守卫或结构化输出的图只发最终结果。
show_thinking 只转发供应商显式提供的公开摘要，不读取隐藏推理。
编排决策路由不提前公开子 Agent 输出。变更了模型引用和流式 Workflow 路径的版本，
升级前应先完成历史重放验证；未验证时先排空旧执行再切换 Worker。

## 定时触发与 Webhook

`/triggers` 创建、列举和编辑用户自己的触发器；每个触发器引用已有 Agent 或 Workflow
实例。删除触发器停止后续触发，不删除实例或已经产生的 Run。

定时配置使用 `kind=schedule`、`cron` 和 IANA `timezone`。Worker 将数据库期望状态同步至
Temporal 原生 Schedule。`applied_revision` 等于 `revision` 才表示同步完成；失败通过
`sync_error` 报告并重试，不把提交成功等同于定时服务已生效。cron 的最终合法性由 Temporal
校验。Schedule 重叠策略为 SKIP，触发 Workflow 等待目标 Run 结束，避免只防止提交动作重叠。
目标 Run 等待审批或对账时，后续定时触发也会跳过；可通过现有 Run 控制接口处理。

Webhook 使用 `kind=webhook`，写入至少 32 字节的 `signing_secret`。调用
`POST /triggers/{id}/webhook` 时携带：

| Header | 内容 |
| --- | --- |
| `X-OpsMesh-Timestamp` | Unix 秒时间戳，允许前后 300 秒 |
| `X-OpsMesh-Event-ID` | 1–200 字符的稳定事件 ID，不含控制字符 |
| `X-OpsMesh-Signature` | `sha256=` 加 HMAC-SHA256 十六进制摘要 |

签名原文为 `timestamp + "\n" + event_id + "\n" + 原始请求体字节`，密钥为 signing_secret。
请求体为有限 JSON，最大 1,000,000 字节。自动化输入为
`{"input": 触发器预设输入, "event": Webhook JSON}`；定时和手动触发直接使用预设输入。

事件 ID 按触发器去重，同 ID 不同载荷返回冲突。目标授权、创建 Run、投递回执和 Outbox
在同一事务提交。所有触发都重新检查创建者账号和目标权限。Webhook 不接受请求方指定平台用户。
`/{id}/fire` 提供登录用户手动触发，`/{id}/deliveries` 查询交付与 Run 的关联。
调度及评测的数据库控制操作使用 Temporal 原生重试，退避间隔上限 30 秒，避免短暂数据库
故障结束调度 Workflow 后遗留仍在运行的目标。该策略不改变模型或工具的重试规则。

## 数据集、评测与反馈

`platform/evaluations` 管理用户私有数据集的不可变版本。一次 Experiment 固定数据集版本、
现有实例列表和 Evaluator 的 `(name, revision, config)`；单个数据集最多 200 个用例、
最多 5 个目标，执行并发 1–8。不同模型或配置先创建不同实例，再使用同一数据集比较。

`/evaluations/experiments` 提交评测后通过事务 Outbox 启动 Temporal EvaluationWorkflow。
每个用例调用已有的执行接口，继承所有权、额度、预算、审批、工具回执及取消规则。稳定用例
标识避免重试重复创建 Run。取消只停止本实验产生的 Run，不删除目标实例；不确定效果保留
需处理状态，不自动审批或对账。用户通过现有 Run 审批接口继续后，实验继续轮询结果。

当前内置 `exact@1` 和 `contains@1`，输出为 0–1 分。`EvaluatorRegistry` 是可信宿主的适配器
边界，API 与 Worker 必须注册相同名称和修订；自定义宿主可向 `create_app` 和
`build_dispatch_worker` 注入同一注册方案。适配器应为有界、确定性的评分函数；平台不内置
特定厂商的 LLM Judge、知识库或业务连接。

用例结果包含关联 Run、分数、原生用量、配置成本和经过时间。`/evaluations/compare` 返回
完成率、状态分布和平均分；平均分只统计已完成且成功评分的用例。数据集版本或评分器不同
时返回 `comparable=false`，不能据此宣称同条件提升。
`/evaluations/feedback` 保存自己的 Run 的评分、备注和期望答案；期望答案可用于后续数据集版本。

## 价格与预算

Provider 的 `models[].pricing` 可配置 `currency`、`input_per_million`、`output_per_million`。
金额使用 Decimal；币种之间不做隐式汇率转换。价格由部署者维护，不是实时厂商价格服务。

管理员通过 `/billing/budgets/{user_id}/{currency}` 设置每月额度与预警比例；用户通过
`/billing/usage?period=YYYY-MM` 和 `/billing/charges` 查看自己的已用、预留、分项用量和金额。
月份按 UTC 计算。预算已存在时，没有对应价格和币种的模型会拒绝准入。

模型调用前同时预留 Token 与金额，金额上界考虑配置的尝试次数；成功后按原生确认的输入、
输出 Token 和本次价格快照结算。不确定回执继续占用预留，管理员对账需要提供输入/输出
Token 及证据，确认未执行才释放。不提供缺失回执的静默补账。
原生 SDK 保留服务商原始用量；缺失或与规范化用量不一致时拒绝自动结算，避免把缺失用量
误记为零。自定义模型适配器也必须提供可核对的 `ModelResponse.raw_usage`。

该统计基于收到的原生用量和配置价格，不是服务商账单。失败重试中服务商未返回的用量、
托管工具收费、存储费等不在已确认 Token 成本中；缓存 Token 暂按普通输入单价计算。

## 部署与验证边界

使用 Alembic 升级至 `0005_platform_management`，随后升级 API 和 Worker。
这是新增代码部署所需的进程升级；之后在线发布配置无需重启。
定时触发和评测需要 Temporal Worker 与 Outbox Dispatcher 持续运行。

测试合同位于 `tests/agents/test_model_retry_configuration.py`、
`tests/execution/test_model_usage_receipts.py`、`tests/platform/test_management_contracts.py`
和 `tests/platform/test_management_postgres.py`。
静态检查不能证明真实 PostgreSQL 迁移、Temporal Schedule、Worker 重启、Webhook 收发、
流式断线恢复或模型重试已经验收；这些测试及真实联调只在用户明确要求时执行。
