# 后端对话与 Manager 委派

对话接口位于 `/api/v1/workspaces/{workspace_id}/conversations`，接口前缀仍由
`OPSMESH_API_PREFIX` 决定。客户端只依赖此 API 合同。

## 使用前准备

升级数据库到最新 Alembic head，启动 API、Redis 和 Worker。准备有模型凭据、
能力授权及有效 Runtime 绑定的 Agent。对话 Run 缺少 active/running 的 isolated、pooled
或 persistent 执行 Runtime 时会失败，不能以无 Runtime 模式执行。

自动模式需要一个已配置的入口 Agent。需要发现并委派专家或团队时，由用户在该 Agent 的 `tool_policy.allowed_tools` 中授权：

```json
{
  "allowed_tools": [
    "discover_conversation_targets",
    "delegate_conversation_task"
  ]
}
```

这两项仍经过现有有效能力目录、工具网关和审批策略。其余工具、MCP、Skill 按现有
Agent 配置安装与授权；创建对话不会自动授予任何能力。专家和团队使用现有 description
说明擅长的工作、输入要求与交付内容，供 Manager 检索和选择。

创建自动对话时显式提供 Manager 的 `agent_profile_id`，或者在工作空间设置中配置
`conversations.manager_agent_profile_id`。缺少有效入口 Agent 时返回 409。平台不要求入口 Agent 必须拥有委派工具，也不注入业务路由指令。

```json
{
  "title": "项目安全检查",
  "mode": "auto",
  "agent_profile_id": "<manager-agent-id>",
  "workspace_project_id": "<optional-project-id>",
  "runtime_space_id": "<optional-runtime-space-id>"
}
```

省略不使用的可选字段，不要提交占位符。`agent` 模式要求 `agent_profile_id`；`team`
模式要求 `agent_team_id`，不能同时指定二者。自动模式中的 Agent 使用自己的指令处理请求。team 模式可以显式选择已发布的 orchestration_definition_id 和 orchestration_version；单独指定版本被拒绝，工作流授权在创建和执行时均检查。

## API 合同

| 方法 | 相对路径 | 行为 |
| --- | --- | --- |
| POST | `/conversations` | 创建私有对话 |
| GET | `/conversations` | 分页列出本人对话 |
| GET | `/conversations/{cid}` | 对话配置 |
| POST | `/conversations/{cid}/messages` | 接收 `{"body":"…"}`，要求 `Idempotency-Key`，返回 202 和 Turn |
| GET | `/conversations/{cid}/messages` | 按轮次分页返回输入、回复、状态和脱敏后的底层执行错误 |
| GET | `/conversations/{cid}/executions` | 分页返回关联 Task、委派父 Run、轮次及各 Run 的状态与错误；可按 turn_id 筛选 |
| GET | `/conversations/{cid}/events?after_id=0&limit=100` | 按持久游标读取状态事件；下一次使用最后一个 id |
| POST | `/conversations/{cid}/turns/{tid}/cancel` | 取消轮次及其非终态关联任务 |
| POST | `/conversations/{cid}/turns/{tid}/retry` | 显式重试失败准入或失败的当前协调 Run |

messages 的列表项是 Turn：`body` 为用户输入，`reply` 为最终回复，执行中可为 null。
状态事件不包含模型内部思考、凭据或原始工具结果。详细 Run 事件、审批、成本和成果
通过现有 Task / Run 接口查看。事件接口是游标轮询，不是 token 流或 SSE。

同一个幂等键和相同 body 返回原 Turn；换 body 返回 409。同一对话最多 32 个未完成
轮次，调度严格按 sequence 串行。对话仅创建者可读写，同空间管理员也不能通过该
对话 API 读取他人历史；平台已有独立任务运维权限不由此接口改变。

## 执行与恢复

1. API 将消息、冻结的用户/Token 身份、queued 轮次及 conversation.advance 投递意图在同一事务中提交到 PostgreSQL，提交后立即发布到 Redis。
2. Worker 领取 conversation.advance，按工作空间锁定对话，恢复并检查原始执行身份，按 sequence 创建 Task / Run 和事务队列投递记录。
3. Worker 完成任务后立即发布已提交的队列记录和工具/进度事件。独立事件发布通道也会每秒检查未投递的 outbox，支持其他进程写入及 Redis 故障后的投递恢复；不依赖 60 秒维护任务。
4. Worker 的现有授权、配额、审批与 Runtime 链路处理执行。
5. Manager 可以直接回复、使用自己的授权工具/Skill，或调用发现与委派工具。
6. Task 状态变更与会话唤醒记录在同一事务中提交。委派工具返回 Task ID；Manager 结束本轮执行后，对话进入 waiting_tasks；子任务完成事件唤醒同一调度器，创建新的 Manager 执行，注入结果并最终写回 reply。完成、失败或取消后，同一调度器立即启动下一个排队轮次。

正常启动、续答和结果回写只走队列调度。维护任务仅对超过 60 秒未推进的非终态对话补发唤醒，并由现有 QueueRehydrationService 恢复丢失的 queued Run 投递；不执行另一套对话状态机。重复事件通过数据库对话锁和执行唯一键幂等处理；事务回滚不会发布队列任务。

发现工具只返回当前执行身份可 invoke 的 active 专家与团队。委派时再次校验权限；
被委派任务继承原始 Token 权限上限并保存父 Run。稳定 `request_key` 防止模型重试时
重复派单，相同 key 对应不同目标或内容会拒绝。每个 Turn 最多 8 个独立委派任务、
4 次 Manager 执行；最后一次只允许总结现有结果。

本实现对任意工作空间专家/团队采用**持久化平台任务委派**，使不同 SDK 的 Agent
也能协作，并支持独立 Runtime、审批等待和恢复。现有团队内 OpenAI 原生子智能体能力
继续按配置工作。Claude 适配器原有的原生子智能体拒绝逻辑没有取消；这里通过独立
任务调用 Claude Agent，不声称已经实现同一次 Claude SDK 调用内的子智能体权限隔离。

对话历史由 SDK Session 管理，平台只提交当前新增输入。Manager 的 Session 按工作空间、
用户、对话和 Agent 隔离；专家及团队委派使用独立任务 Session。委派结果作为新输入续接，
每个结果最多 16000 字符。原生 Session 直接交给 SDK Runner，不增加压缩包装层、触发条件或阈值。
当前不支持运行中修改冻结输入。

澄清以普通助手回复结束轮次，用户补充信息作为下一轮。审批仍使用现有审批接口，
自然语言“同意”不会自动批准风险操作。失败需要显式重试；存在后续轮次或委派预算
耗尽时拒绝原地重试。重试仍使用原始执行身份和现有冻结快照，不提升权限。

## 代码调用

`scripts/conversation_client.py` 使用 `OPSMESH_URL`（默认本地 8000）、
`OPSMESH_TOKEN`、`OPSMESH_WORKSPACE_ID`。使用正常用户 Token；仅本地内部 Token
联调时可另外设置 `OPSMESH_USER_ID`。

```bash
python scripts/conversation_client.py --agent <manager-id> "检查项目的权限逻辑"
python scripts/conversation_client.py --conversation <conversation-id> "继续检查租户隔离"
```

脚本输出 conversation_id、turn_id、执行状态与工具开始/完成/失败事件，并显示临时输出预览和
最终回复。它订阅现有 `/tasks/{task_id}/events/stream`；持久工具证据仍通过
`/runs/{run_id}/events` 查询。失败时输出脱敏错误，等待审批或超时时输出执行入口，
不会自动重试模型或重新投递任务。
请求密钥从环境读取，不写入文件或日志。

## 验证范围

聚焦流程覆盖消息幂等、Manager 委派及续答、队列重建、取消、权限撤销、私有性、
无 Runtime 拒绝，以及 PostgreSQL 并发准入与事务回滚恢复。流程测试使用模拟任务结果，
不等同于真实模型、实际 Docker Runtime 或外部 MCP 服务联调。
