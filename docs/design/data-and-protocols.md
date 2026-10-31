# 数据、接口与事件协议设计

本文保留目标实体与协议设计，不作为当前接口的逐字段说明。已实现服务合同位于各模块`api.py`，实际HTTP入口见[API使用指南](../runtime-api.md)，版本化schema与CAS/Outbox实现见[运维说明](../operations.md)。目标字段在落地前仍可调整；已持久化的schema变更必须使用明确版本和迁移策略。

## 1. 持久实体

| 所有者 | 主要实体 | 关键约束 |
|---|---|---|
| accounts | User、Department、Membership、Role、Grant、LoginSession | 密码强哈希；账号停用立即影响授权；资源作用域显式 |
| agents | AgentDefinition、AgentRevision、Publication、FactoryRef | 修订不可变；发布指针带版本更新；代码包版本固定 |
| models | ProviderConnection、ModelProfile、ModelRevision | 只保存SecretRef；模型功能与端点兼容性明确 |
| bots | BotAccount、ExternalIdentity、RouteBinding、Delivery | Bot/频道绑定固定Agent发布版本；外部账号唯一键含adapter与bot；发送使用交付幂等键 |
| scheduling | PersistentSchedule、触发ID、执行租约 | 计划保存执行主体和Agent引用；授权和租约由平台服务执行，不依赖插件加载 |
| sessions | Session、Participant、DisplayMessage、ConversationBranch、ExecutionClaim | 展示消息与SDK历史独立；分支拥有独立顺序 |
| capabilities | CapabilityRevision、SkillArtifact、MCPBinding、ToolInvocation | 制品内容摘要；调用标识和副作用类别；调用时授权 |
| runtime | Run、RunAttempt、ModelHistoryItem、Checkpoint、Approval、RunError | 终态单次提交；恢复CAS；checkpoint绑定版本及历史引用 |
| execution | Environment、SandboxSession、ResourceAllocation、Lease | 单一owner、资源实际值、lease代次、终止/回收状态 |
| workspaces | Workspace、FileArtifact、MaterializationPlan、Snapshot | 内容与来源版本；snapshot完整性；读写/挂载权限 |
| memory | Namespace、Policy、GenerationJob、Candidate、PublishedVersion | 授权边界、来源集合、生成幂等键、单写发布或CAS |
| configuration | SettingRevision、QuotaPolicy、PluginPolicy | 版本化配置；启动设置和业务在线配置分开 |
| audit | AuditRecord | append-only语义、操作者/对象/决定/结果、留存策略 |
| usage | UsageFact、PriceVersion、BudgetReservation、CostEntry | 稳定调用键、Decimal、币种与价格版本、缺失值标记 |

PostgreSQL是这些业务事实的主要存储；大文件、rollout、状态blob和快照可在对象存储，以不可变引用和摘要连接。逻辑删除、留存与实际删除由所有者实施，不能删除仍被审批checkpoint引用的版本。

SDK Session条目可以使用SQLAlchemy适配到PostgreSQL，但表归runtime；业务会话不因此转移所有权。上线迁移由`migrations/`控制，初始化不自动create_all。

## 2. 五种状态不可混存

| 状态 | 用途 | 恢复语义 |
|---|---|---|
| Session历史 | 下轮对话输入 | 恢复消息/工具调用顺序 |
| RunCheckpoint | 继续Runner执行和审批 | 绑定SDK/后端/Agent/代码/工具版本 |
| SandboxSessionState | 重连/恢复执行资源 | 由相同或兼容Provider验证；不能自行授权路径 |
| WorkspaceSnapshot | 恢复文件和制品 | 初始化新环境；不代表执行到原来的步骤 |
| MemoryVersion | 复用提炼的经验 | 按namespace和权限读取；不是原始事实或授权来源 |

## 3. 事务与事件投递

模块内部事务保存业务记录和Outbox事件。Outbox投递至少一次，消费者用event_id幂等处理，并保存消费进度。状态查询返回提交后的事实，消息到达顺序不单独构成事实来源。

跨模块长流程由runtime协调公开API和补偿。需要原子性时明确限定同一数据库事务的参与接口，禁止各模块各开连接后宣称“一起提交”。初期审批、checkpoint、Run状态同属runtime，可在同一事务更新。

外部工具副作用无法靠数据库事务撤回。ToolInvocation保存调用键、阶段、授权与结果引用；在工具服务支持时传递幂等键，否则失败重投前需确定操作是否已完成。

## 4. Python公开接口

| 接口 | 代表操作 |
|---|---|
| Accounts / Authorization | authenticate、get_principal、authorize |
| AgentCatalog / AgentFactories | create、publish、get_revision；AgentFactories.register登记代码工厂 |
| ModelProfiles | get_revision、validate_compatibility |
| CapabilityCatalog / CodeTools | register_definition、resolve、invoke；CodeTools.register登记代码工具 |
| Runtime / RuntimeExtensions | submit、cancel、subscribe、decide、resume；注册backend/guardrail/hook声明 |
| Execution | allocate、acquire_session、execute、read/write、release |
| Workspaces | prepare_manifest、resolve_artifact、create_snapshot、publish_artifact |
| MemoryService | resolve_binding、get_revision、append_source、request_generation、publish_candidate、forget |
| Usage | reserve_budget、record_usage、settle、query_cost |

注册接口只接纳代码声明，不在导入阶段连接网络。普通Provider在进程组装后供Depends获取这些公开协议。同步注册与异步业务操作的签名需在实现阶段明确。

## 5. HTTP管理与运行接口

目标基础路径为`/api/v1`。本地登录建立会话或令牌；所有管理和运行路由进行资源级权限判断，Bot及插件调用同一应用服务。

| 资源 | 目标操作示例 |
|---|---|
| `/auth/sessions` | 登录、退出；本地账号流程 |
| `/users`、`/departments`、`/roles` | 用户、组织与权限管理 |
| `/agents`、`/agents/{id}/revisions` | 创建、编辑、校验和发布 |
| `/models`、`/capabilities`、`/bots` | 连接、模型/能力/机器人配置 |
| `/sessions`、`/sessions/{id}/messages` | 会话、输入和展示历史 |
| `/runs` | 提交运行，异步受理返回202及run_id |
| `/runs/{id}`、`/runs/{id}/cancel` | 查询、请求取消 |
| `/runs/{id}/events` | SSE订阅；可选WS使用同一事件合同 |
| `/runs/{id}/approvals/{approval_id}/decisions` | 提交批准/拒绝及expected_revision |
| `/workspaces`、`/artifacts` | 授权文件与产物访问 |
| `/memory/namespaces`、`/memory/jobs` | 记忆作用域、生成、版本与删除 |
| `/audit`、`/usage`、`/configuration` | 管理查询与配置 |

写请求支持Idempotency-Key，并将幂等键限定到主体、操作与请求摘要；重复但参数不同返回冲突。更新使用expected_revision/ETag避免覆盖并发修改。凭据明文仅走专用写入路径，不通过普通配置查询返回。

## 6. 事件外壳与Message

```json
{
  "id": "evt_example",
  "type": "runtime.run.completed",
  "schema_version": 1,
  "occurred_at": "2026-10-30T08:00:00Z",
  "source": "runtime",
  "correlation_id": "run_example",
  "causation_id": "evt_previous",
  "subject": {"run_id": "run_example", "session_id": "session_example"},
  "traceparent": null,
  "payload": {"status": "completed", "result_ref": "artifact_example"}
}
```

外壳是传输合同，进程内DispatchContext另有Bot、当前Matcher和可调用服务。不能序列化Bot对象、Python函数或数据库连接。可选扩展字段须有命名空间，未知版本不能按旧schema静默消费。

Message由带类型的segments组成，支持文本、文件/图像/音频引用、mention和reply等；平台特有段保留adapter命名空间。二进制不塞入所有事件，而使用受控ArtifactRef。Bot消息与模型输入通过专门转换器连接，不能把任意Message直接传入SDK。

## 7. 流式输出

每条输出携带run_id、attempt_id、seq、kind和payload。客户端以事件序号去重；订阅授权检查与Run查询一致。SSE可使用Last-Event-ID，服务端明确缓冲保留窗口；超出窗口返回需重新获取快照的响应。

token delta可以采用有限Redis缓冲；最终消息、审批和终态持久化。客户端断线不自动取消执行。Worker恢复后新attempt继续可识别的序列，不能使重发文本无标识地重复显示。

输出护栏在最终检查前可能尚未放行内容；要求全部输出事先审核的场景使用缓冲模式，交互性与审核延迟由产品配置明确选择。

## 8. 错误合同

所有出口使用ErrorInfo：error_id、code、category、safe_message、phase、retryable、retry_after，以及可选run_id/tool_call_id/trace_id/backend_id/diagnostic_ref。

HTTP状态与错误码分开：无效请求400、身份缺失401、拒绝403、不存在404、版本冲突409、能力/语义校验422、速率429、内部故障500、依赖不可用503。已受理Run内部失败通过Run状态/事件表达，不事后修改原202响应。

等待审批是业务状态；取消是控制路径；Matcher finish/pause/reject不映射500。提供商原始错误、提示词和秘密不直接返回用户。模型请求重试、工具重试和Worker重投分别执行预算与副作用策略。

## 9. 数据留存与删除

按主体/部门/Agent授权管理历史、文件、记忆、审计与用量。删除用户可识别内容时，需要处理派生记忆、rollout及快照；审计/财务留存按明确政策保留必要最小信息。对象存储清理采用引用扫描和宽限期，避免GC删除仍在运行/审批中的制品。

数据迁移和恢复演练需要验证数据库与对象引用的一致性；只有备份文件存在不等于平台可恢复。详见[运维设计](../operations.md)。
