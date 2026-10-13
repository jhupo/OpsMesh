# 后端对话与原生 Agent 路由

接口位于 `/api/v1/workspaces/{workspace_id}/conversations`，前缀由 `OPSMESH_API_PREFIX` 决定。

本文维护当前 Chat 接入与会话语义。执行机制与验收范围见 [Temporal 架构](architecture.md)。

## 配置

升级到最新 Alembic head，启动 PostgreSQL、Redis、持久 Temporal、API 与 Worker。入口 Agent 需要活动模型凭据及能力授权；使用文件、命令或托管 stdio 时绑定有效的 SDK 原生沙箱环境。

自动模式显式提供 `agent_profile_id`，或在工作空间设置配置 `conversations.manager_agent_profile_id`。入口可为小模型 Router，也可直接回答；平台不注入业务路由指令。Router 使用 `tool_policy.handoffs`，强模型 Manager 使用 `tool_policy.agent_tools`，配置及权限见 [Agent 工具](agent-tools.md)。创建对话不授予能力。

```json
{"title":"项目安全检查","mode":"auto","agent_profile_id":"<router-agent-id>"}
```

替换为真实 UUID。`agent` 模式要求 `agent_profile_id`；`team` 模式要求 `agent_team_id`，二者不能同时指定。team 模式可指定已发布的 `orchestration_definition_id` 和 `orchestration_version`，不能单独指定版本。

## API 合同

| 方法 | 相对路径 | 行为 |
| --- | --- | --- |
| POST | `/conversations` | 创建本人私有对话 |
| GET | `/conversations` | 分页列出本人对话 |
| GET | `/conversations/{cid}` | 对话配置 |
| POST | `/conversations/{cid}/messages` | 接收 `{"body":"…"}`，要求 `Idempotency-Key`，返回 202 和 Turn |
| GET | `/conversations/{cid}/messages` | 分页返回输入、回复、最终回答者、状态和脱敏错误 |
| GET | `/conversations/{cid}/executions` | 返回每轮唯一 Task 及各次 Run 状态，可按 turn_id 筛选 |
| GET | `/conversations/{cid}/events?after_id=0&limit=100` | 持久游标事件轮询 |
| GET | `/conversations/{cid}/turns/{tid}/events/stream` | 单轮 SSE：排队状态、Task 关联、执行进度、工具、文字预览与最终回复 |
| POST | `/conversations/{cid}/turns/{tid}/cancel` | 取消轮次及关联任务 |
| POST | `/conversations/{cid}/turns/{tid}/retry` | 显式重试失败准入或失败 Run |

Turn 的 `body` 是输入，`reply` 是最终回复，`final_agent` 为回答者的 `name`、`profile_id`、`role`，完成前可为 null。详细进度、工具、审批、成本和成果通过 Task / Run API 查询。事件不包含内部思考、凭据或原始工具正文。

消息 POST 返回后立即显示 Turn 状态，并订阅该轮 `/turns/{tid}/events/stream`。无需轮询消息或等待 Task ID。`turn.snapshot` 包含 `turn` 和可为空的 `task_id`；`task.event` 复用持久 Task 事件。SDK 事件的 `event_type` 为 `agent.stream.<SDK事件名>`，`payload.runtime_stream_event` 包含 `event_type`、`provider_sequence`、`payload`、`delta` 和 `is_terminal`。例如 `agent.tool.started`、`agent.tool.completed` 提供工具进度，`output.text.delta` 提供脱敏文字增量，客户端按游标追加。配置结构化输出或输出 Guardrail 时，未验证的文字增量不会发布，工具进度仍可订阅。`stream.completed` 携带持久最终 Turn，并关闭连接；审批等待仍可订阅，审批通过审批接口执行。

每个 `task.event` 的 SSE `id` 是 PostgreSQL 事件 UUID；重连通过 `Last-Event-ID` 或 `event_cursor` 续读，初始值为 `0`。事件按租户内 Task 的持久序号排序；游标未保留时先返回 `stream.reset`，客户端清空预览并用状态快照恢复。`stream.revoked` 表示授权被撤销，`stream.unavailable` 表示实时传输不可用，不自动重发消息。订阅只读取，不取消或重新执行任务。服务使用 PostgreSQL 通知与原生异步查询，不为每个订阅者占用等待线程，数据库读取结束后释放事务，再等待通知或客户端。

相同幂等键和正文返回原 Turn，不同正文返回 409。同一对话最多 32 个未完成轮次，按 sequence 串行。对话仅创建者可读写，独立任务运维权限不变。

## 会话、回答与恢复语义

API 接收消息与最终回答是两个阶段，以 Turn/Task/Run 状态及持久结果为准。handoff 由实际目标接管回答；专家工具把结果返回调用者，由其用户配置决定后续处理。框架不追加总结 Task。

SDK Session 按工作空间、用户、对话和入口 Agent 隔离。平台只提交当前输入，不拼接历史、专家摘要或自定义压缩阈值；下一条消息从入口开始，使用同一 Session。审批通过 SDK RunState 恢复到实际专家和工具；自然语言“同意”不自动批准，仍须审批 API。运行中不修改冻结输入。

澄清以正常回复结束，补充信息作为新轮次。失败须显式重试；存在后续轮次时拒绝原地重试。重试保留原身份和快照。授权快照 v4 升级后，旧版本历史执行不能重新执行，应发送新消息。

## 客户端与验证

`scripts/conversation_client.py` 使用 `OPSMESH_URL`、`OPSMESH_TOKEN`、`OPSMESH_WORKSPACE_ID`；内部 Token 联调可另设 `OPSMESH_USER_ID`。

```bash
python scripts/conversation_client.py --agent <router-id> "检查项目权限"
python scripts/conversation_client.py --conversation <conversation-id> "继续检查租户隔离"
```

脚本显示 ID、状态、执行进度、工具事件、文字预览和最终回复，发送后直接订阅轮次 SSE；不轮询消息和执行列表。断线只恢复读流，不重发输入；失败、审批等待、超时显示执行入口，不自动重试任务。

测试覆盖实际安装 SDK 的 Router→专家、Router→Manager→专家工具、Session、审批恢复、权限撤销、冻结能力和 PostgreSQL 事件断线续读。离线模型测试不能证明外部模型或业务 MCP 的可用性；Linux Docker 与产品持久引用另有显式启用的验收测试。
