# SDK 原生执行边界

核对日期：2026-10-10。OpenAI Agents SDK 在控制面和 Runtime 中均锁定为 `0.17.2`。本文描述当前实现，不代表服务器已经部署。

## 职责

SDK 管理模型与工具循环、会话消息、handoff、工具审批中断与 RunState 恢复、结构化输出验证、Responses 压缩及 Sandbox Memory 流水线。OpsMesh 管理租户身份、资源权限、冻结配置、配额、队列、租约、审批记录、审计、Runtime 和知识治理。意图路由由用户配置的 Manager 指令与工具决定，平台没有额外分类模型或外层强制 Agent 循环。

Worker 的每个活动 Job 是一个 asyncio Task；模型等待和自托管 MCP RPC 等待释放事件循环。同步数据库和基础设施操作进入有界 I/O 线程池，每次操作拥有独立事务。线程池是同步基础设施适配边界，不保存 Agent 对话状态。

## 会话与恢复

- 官方 `SQLAlchemySession` 使用 AsyncEngine 写入 `sdk_agent_sessions` / `sdk_agent_messages`。Alembic 管理表结构，运行时禁止自动建表。`persistent_agent_sessions` 只保留工作空间、用户作用域、Agent、Session key 和管理状态。
- `AuthorizedSDKSession` 通过公共 Session 接口增加执行身份、会话绑定及租约检查，消息增删读全部交给父类；没有另一套消息存储算法。Session key 全局唯一，读取管理视图时必须联接租户元数据。
- 平台只提供当前新增输入。同会话按 turn 排序，专家拥有独立 Session。后续输入、审批恢复和自托管 MCP 返回都不拼成旧历史或伪造的用户消息。
- 审批使用加密的 SDK RunState 和原工具 call ID。自托管 MCP Job 通过 `(agent_run_id, tool_call_id)` 唯一约束去重；SDK 工具协程等待原 RPC 结果。恢复到已保存的 SDK 工具调用时先核对持久 RPC，再复用结果，不重复计费配额或再次执行远程工具。没有 SDK 快照的进程中断不等同于任意指令断点恢复；外部副作用仍不保证 exactly-once。
- 普通工作流的直接工具节点完成后重新排队做领域结算；SDK 正在等待的工具调用不会因此重新排队。审计日志同时进入终态。
- 不再保存 `resume_input`、`sdk_continuation`、`pending_tool_results` 或 Provider conversation ID；不再同步第二套服务端会话。切换 OpenAI/Claude SDK 家族前必须显式清空有历史的 Session，禁止静默解释另一家格式。
- Claude 的 SDK 自己恢复 transcript；平台存储适配器只负责授权持久化 SDK transcript，不能把 OpenAI 消息拼成 Claude prompt。

## MCP 与工具

Agent 通过原生 `Agent.mcp_servers` 发现工具，SDK 负责 MCP 到 FunctionTool 的转换、调用和返回内容转换。平台产品工具仍使用原生 FunctionTool。所有 Run 显式设置 `ToolExecutionConfig(max_function_tool_concurrency=1)`；不同 Run 并发，单 Run 的产品副作用工具按顺序执行。用户的 `parallel_tool_calls` 模型设置仍按配置传递。

以下扩展只处理 SDK 未覆盖的产品职责：

| 扩展 | SDK 缺口及平台职责 |
| --- | --- |
| `RuntimeMCPServer` | SDK 不认识 OpsMesh Runtime RPC；冻结工具清单、原始 call ID 和授权调用通过公共 MCPServer 接口传递。 |
| `GovernedAgent.get_mcp_tools` | 0.17.2 的 MCP approval 回调没有参数及 call ID；先调用 SDK 工具转换，再用公开 FunctionTool approval 回调做依赖参数的权限审查。审批暂停和恢复仍由 SDK 完成。 |
| Runtime stdio 传输 | 公共 `create_streams` 扩展给官方 `stdio_client` 设置 stderr sink，避免用户 MCP 进程直接把凭据打印到 Worker 日志。没有自写 JSON-RPC。 |
| MCP 发现 | SDK `list_tools` 不遍历分页；平台通过公开 MCP ClientSession 做有界分页，限制工具数量和游标循环。 |
| Runtime 生命周期 | 容器分配、托管进程重启、租约、凭据解密、网络审批和审计属于平台，不由 SDK 传输负责。 |

stdio、Streamable HTTP、SSE 的实际连接在批准的 Runtime 执行，使用官方 SDK 传输，关闭传输层自动重试。HTTP/SSE 的凭据通过临时请求文件传入，不进入命令行；保留完整 CallToolResult，包括结构化内容及图片等类型。Agent 通过自托管 Runtime 执行 stdio；自托管 Runtime 的 HTTP/SSE 当前明确拒绝，尚未实现相应远程合同。独立 MCP 管理/探测 API 的控制面连接不属于 Agent 工具执行路径。

## 压缩、记忆与输出

会话压缩只有一个所有者：OpenAI Responses 路径通过原生 `OpenAIResponsesCompactionSession` 包装同一个 SQLAlchemySession。平台只配置按预算触发的阈值，不再安装 Sandbox Compaction；Chat Completions 和其他不支持该能力的 Provider 不执行自制压缩或静默降级。第三方网关是否支持 `responses.compact` 需要部署方验证。

Agent Profile 的 `memory_policy.sdk_memory` 默认关闭。例如只读取持久文件记忆：

```json
{
  "sdk_memory": {
    "enabled": true,
    "read": true,
    "generate": false,
    "live_update": false,
    "max_raw_memories": 32
  }
}
```

启用后必须使用 OpenAI Agents SDK、persistent Runtime、已授权用户及 Agent Profile；不符合条件直接报错。文件根目录为 `.opsmesh/memory/{workspace}/{user}/{agent}/`，包含 SDK 管理的 `memories` 与 `sessions`。首次没有记忆文件时按 SDK 的 not-found 合同返回，不能把空文件当成运行失败。

`generate=true` 启用 SDK Memory 的提取与汇总两阶段，使用已冻结的 Provider 凭据、模型及模型设置，生成用量计入同一 Run。同一个执行请求的 Memory 策略应用到嵌套/交接 Agent，文件目录按目标 Profile 隔离；独立委派 Run 使用自身冻结的策略。文件命名空间不等于操作系统 ACL；需要用户级文件隔离时应分配各自 persistent Runtime，不能把多人共享可写容器当作隔离边界。SDK Memory 是文件经验流水线，不替代工作空间知识库及受治理的 PostgreSQL 长期记忆。

工作记忆只保留显式 `put`、查询、过期和 `promote`；已删除自动复制 objective、plan、tool_result 以及下一次请求自动重新注入的逻辑。Session、SDK Memory、产品知识库分别承担对话、经验文件和受治理检索职责。

结构化输出在 SDK output schema 中验证一次。OpenAI runner 仅映射已验证结果；Claude 仅使用 SDK `structured_output`，缺少时失败，不再解析普通文本冒充结构化输出。

## 升级

`0114_sdk_sessions` 将旧 Session items 按顺序迁入 SDK 表，并保留 call ID；删除旧消息表与旧 Provider 会话列，清除重复历史快照和自动工作记忆副本。降级恢复旧表、消息顺序与旧列；已经删除的冗余快照和自动记忆不重新生成。

这是一次明确切换，不支持新旧 Worker 混跑：

1. 停止领取任务并排空 Worker，备份 PostgreSQL；有未知副作用的运行先由恢复流程确认，不依赖重放。
2. 构建包含 `openai-agents==0.17.2` 的新 Runtime 镜像，同时更新控制面、Worker、自托管连接器。Runtime MCP 合同为版本 2，旧版本必须重新构建，禁止回退旧合同。
3. 执行 `uv run alembic upgrade head`，确认只有一个 head；之后启动新版本 API 与 Worker。
4. 验证既有会话续接、审批恢复、工具结果与租户隔离。回滚时停新 Worker，执行降级后统一恢复旧镜像，不能只回滚部分进程。

Session 管理 API 的 message ID 现在是 SDK 整数 ID；不再返回 `sequence` 和 `openai_conversation_id`。仓库前端没有依赖这些字段；外部消费者需同时更新。

## 验证范围

已运行真实 SDK 配合离线模型、SQLite 产品流程，以及独立本地 PostgreSQL schema 的完整 Alembic 升级/降级测试。覆盖会话隔离、审批恢复、原始 call ID、自托管 RPC 中断续接与配额、原生 stdio/HTTP、持久 Memory 初次与后续读取。没有调用真实业务订单或外部模型。

最终聚焦回归：210 passed / 2 skipped；Ruff、mypy（1082 个源文件）、12 项 import-linter 合同、架构归属检查和依赖锁检查通过。额外执行调度器测试时有两项既有失败：Runtime Space 用例缺少模型 Provider，提前得到 `model_provider_unavailable`；在修改前 `829bf6ab` 的完整源码快照中复现了相同结果，不作为本次 SDK 验证通过项。

Linux 是现有生产 Worker 环境。Windows 下 psycopg 异步连接要求 Selector event loop，PostgreSQL 集成夹具显式设置该 loop；尚未验证同时运行 Claude 子进程等能力的 Windows PostgreSQL Worker。

尚未验证：真实 Provider 的 Memory 生成与费用、第三方 compact 接口、生产 Runtime 镜像切换及负载。SDK 宿主进程整体迁到 Runtime host/RPC、自托管 HTTP/SSE、完整控制 Inbox、跨租户公平队列和独立 CPU 计算服务仍未实现；本次没有部署或重启服务器 Worker。

官方入口：[SQLAlchemySession](https://openai.github.io/openai-agents-python/sessions/sqlalchemy_session/)、[Session 与压缩](https://openai.github.io/openai-agents-python/sessions/)、[MCP](https://openai.github.io/openai-agents-python/mcp/)、[本地 Context](https://openai.github.io/openai-agents-python/context/)、[锁定 SDK 源码](https://github.com/openai/openai-agents-python/tree/v0.17.2/src/agents)。
