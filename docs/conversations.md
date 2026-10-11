# 后端对话与原生 Agent 路由

接口位于 `/api/v1/workspaces/{workspace_id}/conversations`，前缀由 `OPSMESH_API_PREFIX` 决定。

## 配置

升级到最新 Alembic head，启动 API、Redis、Worker 和获准 Runtime。入口 Agent 需要活动模型凭据、能力授权及有效 `isolated` 或 `shared` Runtime 绑定。

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
| POST | `/conversations/{cid}/turns/{tid}/cancel` | 取消轮次及关联任务 |
| POST | `/conversations/{cid}/turns/{tid}/retry` | 显式重试失败准入或失败 Run |

Turn 的 `body` 是输入，`reply` 是最终回复，`final_agent` 为回答者的 `name`、`profile_id`、`role`，完成前可为 null。详细进度、工具、审批、成本和成果通过 Task / Run API 查询。事件不包含内部思考、凭据或原始工具正文。

相同幂等键和正文返回原 Turn，不同正文返回 409。同一对话最多 32 个未完成轮次，按 sequence 串行。对话仅创建者可读写，独立任务运维权限不变。

## 执行与恢复

1. API 同事务保存消息、冻结用户/Token 身份、queued 轮次和投递意图，提交后发布 Redis。
2. Worker 锁定对话，恢复身份，为当前轮次创建唯一 Task，再进入 Run、配额、审批和 Runtime 链路。
3. Runtime 构建冻结的 SDK 图。handoff 由专家接管回答；专家工具返回证据给调用者，由调用者模型汇总。全程是一个 Run，不创建额外总结 Task。
4. Task 状态和会话唤醒同事务保存，完成写入 reply 和 final_agent，再调度下一条消息；失败写入明确错误。

事件提交后立即发布，独立 outbox 通道每秒检查未投递记录。维护任务只对超过 60 秒未推进的非终态对话补发唤醒，队列重建恢复丢失投递。数据库锁和每轮唯一执行链接保证幂等，回滚不发布任务。

SDK Session 按工作空间、用户、对话和入口 Agent 隔离。平台只提交当前输入，不拼接历史、专家摘要或自定义压缩阈值；下一条消息从入口开始，使用同一 Session。审批通过 SDK RunState 恢复到实际专家和工具；自然语言“同意”不自动批准，仍须审批 API。运行中不修改冻结输入。

澄清以正常回复结束，补充信息作为新轮次。失败须显式重试；存在后续轮次时拒绝原地重试。重试保留原身份和快照。授权快照 v4 升级后，旧版本历史执行不能重新执行，应发送新消息。

## 客户端与验证

`scripts/conversation_client.py` 使用 `OPSMESH_URL`、`OPSMESH_TOKEN`、`OPSMESH_WORKSPACE_ID`；内部 Token 联调可另设 `OPSMESH_USER_ID`。

```bash
python scripts/conversation_client.py --agent <router-id> "检查项目权限"
python scripts/conversation_client.py --conversation <conversation-id> "继续检查租户隔离"
```

脚本显示 ID、状态、工具事件和最终回复，订阅 Task 事件流；失败、审批等待、超时显示执行入口，不自动重试。

测试覆盖实际安装 SDK 的 Router→专家、Router→Manager→专家工具、Session、审批恢复、权限撤销和冻结能力，以及 PostgreSQL 迁移排空和历史恢复。模拟模型测试不等于真实模型、Docker Runtime 或外部 MCP 联调。
