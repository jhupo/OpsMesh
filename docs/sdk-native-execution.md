# SDK 原生执行边界

OpenAI Agents SDK 以 `uv.lock` 中锁定的 `0.17.2` 为准。本文描述当前代码合同，部署状态由目标环境独立验收。

## 职责

SDK 管理模型与工具循环、会话消息、handoff、工具审批中断与 RunState 恢复、结构化输出验证及按需启用的 Sandbox Memory 流水线。OpsMesh 管理租户身份、资源权限、冻结配置、配额、队列、租约、审批记录、审计、Runtime 和知识治理。意图路由由用户配置的 Manager 指令与工具决定，平台没有额外分类模型或外层强制 Agent 循环。

Worker 的每个活动 Job 是一个 asyncio Task；模型等待和自托管 MCP RPC 等待释放事件循环。同步数据库和基础设施操作进入有界 I/O 线程池，每次操作拥有独立事务。线程池是同步基础设施适配边界，不保存 Agent 对话状态。

## 会话与恢复

- Runtime 中的 `RpcSession` 实现官方公共 Session 接口，将会话操作提交给控制面授权网关；Runtime 不接收数据库凭据。控制面的官方 `SQLAlchemySession` 使用 AsyncEngine 写入 `sdk_agent_sessions` / `sdk_agent_messages`。Alembic 管理表结构，运行时禁止自动建表。`persistent_agent_sessions` 只保留工作空间、用户作用域、Agent、Session key 和管理状态。
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

会话直接以原生 Session 传入 `Runner.run` / `Runner.run_streamed`，平台不安装 `OpenAIResponsesCompactionSession` 或 Sandbox Compaction，不设置压缩触发器、阈值或 `context_management`，也不主动调用独立 compact 接口。SDK 的可选压缩包装层已删除；原生 Session 本身不会因条目数达到某个值而启用该包装层。`openai-agents.openai.compaction` 日志仍经过脱敏过滤，仅观察 SDK 行为，不启用压缩。新增产品输入的预算限制不读取或改写 Session 历史。

知识搜索工具默认返回前 5 条、最多 10 条匹配摘要和引用定位；完整片段通过 `get_knowledge_citations` 按需读取。排名诊断和内部存储元数据留在平台检索审计中，不自动进入模型工具输出。

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

启用后必须使用 OpenAI Agents SDK、已授权用户及 Agent Profile，并绑定支持持久存储的 `shared` Runtime；不符合条件直接报错。文件根目录为 `.opsmesh/memory/{workspace}/{user}/{agent}/`，包含 SDK 管理的 `memories` 与 `sessions`。首次没有记忆文件时按 SDK 的 not-found 合同返回，不能把空文件当成运行失败。

`generate=true` 启用 SDK Memory 的提取与汇总两阶段，使用已冻结的 Provider 凭据、模型及模型设置，生成用量计入同一 Run。同一个执行请求的 Memory 策略应用到嵌套/交接 Agent，文件目录按目标 Profile 隔离；独立委派 Run 使用自身冻结的策略。文件命名空间不等于操作系统 ACL；需要用户级文件隔离时应分配各自 `isolated` Runtime，不能把多人共享可写容器当作隔离边界。SDK Memory 是文件经验流水线，不替代工作空间知识库及受治理的 PostgreSQL 长期记忆。

工作记忆只保留显式 `put`、查询、过期和 `promote`；已删除自动复制 objective、plan、tool_result 以及下一次请求自动重新注入的逻辑。Session、SDK Memory、产品知识库分别承担对话、经验文件和受治理检索职责。

结构化输出在 SDK output schema 中验证一次。OpenAI runner 仅映射已验证结果；Claude 仅使用 SDK `structured_output`，缺少时失败，不再解析普通文本冒充结构化输出。

## 运行边界与升级

SDK Runner 在 Runtime 子进程执行，通过私有 RPC 与 Worker 交换 Session 操作、授权工具回调及事件；API 与 Worker 不执行用户或 Agent 控制的代码。共享宿主的执行槽、私有目录、进程组、容量等待及取消合同见 [共享 Runtime](shared-runtime-hosts.md)。RPC 是授权与隔离边界，不复制 SDK 状态机。

API、Worker 和 Runtime 必须使用同一版本执行合同。升级前停止领取并排空活动任务，备份数据库和存储，统一构建控制面与 Runtime 镜像，执行 `alembic upgrade head`，再按宿主生命周期更新运行中的 Runtime。只更新模板镜像不会改变已运行容器的代码。不要混跑新旧模块路径或合同版本。

数据库历史迁移保留明确的升级和降级逻辑；运行源码不识别旧 Session 字段或旧包名。降级前排空活动执行并确认外部副作用，不能把进程重启视为安全重放工具的依据。

## 验证边界

相关流程覆盖 SDK Session 多轮续接、审批 RunState 恢复、原始 tool call ID、自托管 RPC 去重及取消、MCP 返回、结构化输出和持久文件记忆；SDK 离线传输测试不等同于真实供应商请求。共享宿主生命周期测试与 PostgreSQL 并发测试分别验证进程释放和执行槽准入。

自托管 Agent MCP 的 HTTP/SSE 合同当前仍明确拒绝；stdio 路径可用。队列在同优先级按 workspace 公平轮转，但不承诺外部副作用的 exactly-once 执行。Linux 是现有服务器运行环境；Windows 的完整 PostgreSQL/Claude 子进程组合不是已验收部署能力。

官方入口：[SQLAlchemySession](https://openai.github.io/openai-agents-python/sessions/sqlalchemy_session/)、[Session](https://openai.github.io/openai-agents-python/sessions/)、[MCP](https://openai.github.io/openai-agents-python/mcp/)、[本地 Context](https://openai.github.io/openai-agents-python/context/)、[锁定 SDK 源码](https://github.com/openai/openai-agents-python/tree/v0.17.2/src/agents)。
