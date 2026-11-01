# 架构审查整改

日期：2026-11-01。本轮落实上次源码审查的七组问题，包含当前工作区的既有变更。本轮没有提交、发布或更新远端服务。

## 一、索引查询与显式迁移

`modules/runtime/internal/records.py` 定义 Run 和流事件的私有查询，替代读取全表后在 Python 中筛选。Run 按状态、主体、会话和过期时间过滤；容量使用 SQL 统计；流事件按 Run ID、sequence 和上限查询。后台遍历使用公开的 `queries.iter_runs` 稳定游标，避免处理过程中改变状态导致漏页。

JSON 聚合、CAS、索引投影与 Outbox 事实在同一事务机制内更新。通用 RecordStore 重开已登记的表时沿用该表投影声明，防止受信写入漏更新索引列。索引投影不构成第二套事实存储。

`runtime_runs`、`runtime_stream` 表由 v1 显式升级为 v2，保留旧记录、版本和时间戳。启动只验证，禁止静默升级。停止旧写入程序并备份后，执行 `opsmesh migrate`、`opsmesh check` 再重启；禁止新旧程序混合写入。见[迁移 0002](../../migrations/0002-runtime-projections.md)。

## 二、审核后记忆边界

受审核会话拒绝 `memory_generate=True`，由 `conversations/validation.py` 统一校验。覆盖网关解析、应用提交的有效配置，以及持久 Run 重建上下文三条路径，最后一项保护升级前已进入队列的任务。

会话输出经过审核和发送确认后，由既有 `ConversationOperations.capture` 采集知识或记忆。普通 Agent 的自动记忆策略保留；两个策略不在同一受审核会话同时执行。冲突配置明确失败，不被静默改写。

## 三、显式依赖与应用分工

删除大型 `ApplicationServices`、`ApplicationHost` Protocol。应用流程显式接收实际协作者，群聊操作不再通过全局 Provider 获取内部依赖。`OpsMeshServices` 保留在 bootstrap 负责组装。

| 对象 | 拥有的行为 |
| --- | --- |
| AgentPublicationValidator | 默认模型准备、凭据约束、发布引用验证 |
| AgentExecutionBridge | 授权执行上下文、资源准备与 Skill 文件同步 |
| AgentContextBindings | 当前 Run 的授权、模型、工具与计量函数绑定 |
| RunResourceScope | 资源获取、检查点与释放 |
| RunResultFinalizer | 沙箱产物、展示历史、rollout、计量与普通自动记忆收尾 |

HTTP 和 Python 应用继续使用同一授权管理用例。NoneBot 风格插件保留 Depends 与当前上下文，它们不再成为内部应用流程的隐式服务定位器。

## 四、类型化上下文与中立沙箱

`framework/agents/resources.py` 提供 `AgentExecutionServices`、`AgentExecutionResources`、`AgentExecutionState`，替代字符串键约定。插件自定义数据使用 `extensions`。模型历史采用中立 `ModelHistorySession` 协议，SDK 历史及压缩适配留在后端。

`OpenAIExecutionState` 拥有 OpenAI 客户端、配置、profile 缓存和待观察请求，平台流程不读写这些字段。沙箱 provider binding、session factory 和生命周期使用中立合同，组合根按后端注入实际工厂。子环境独立持有历史、状态及后端缓存，共享委派取消信号，产物回传父任务。

回归使用第二种测试后端工厂验证注入与状态隔离，不等于已经实现 Claude SDK。

## 五、运行时所有权

`PersistentAgentRuntime` 保留提交、执行尝试、取消、检查点和终态编排。审批行为归 `internal/approvals.py`；worker 领取、租约和丢失恢复归 `internal/workers.py`；记录转换与查询归 `internal/records.py`。

SessionClaims 必须实现 `active_claims`，回收只核对当前占用。删除额外的 `session_claim_reader` 参数和退回全量终态 Run 扫描的兼容分支。释放仍检查 Run ID 和 CAS，不释放后来任务取得的占用。

## 六、能力管理入口

Skill 发布归 SkillManagement，MCP 注册归 MCPManagement，Script 发布归 ScriptManagement，普通工具注册归 CapabilityManagement。HTTP 与代码发布调用同一入口。聚合服务保留统一查询、引用和启停分派，删除重复发布转发。

`internal/availability_management.py` 复用授权、引用检查、版本可用性和审计规则；资源差异仍由原服务拥有。删除的内部容器、字典服务键、旧 Provider 合同和重复方法不提供兼容别名，部署插件必须同版本升级。

## 七、后台监督与领域事件

| 对象 | 责任 |
| --- | --- |
| BackgroundCoordinator | 监督注入的任务、周期与停止 |
| RunWorkerCoordinator | 活动容量、待处理 Run 与 worker 启动 |
| MemoryJobCoordinator | 记忆任务启动、跟踪和计量 |
| ApprovalNotificationCoordinator | 审批到期和提醒 |
| OutboxDelivery | 领取、续租、投递、确认与失败重试 |
| RuntimeEventHandler | Run 事实的会话投影、结算与通知/回复协调 |

组合根显式注入审计、Run 事实和框架事件处理函数，没有新增事件总线或工作流引擎。共享 `wait_for_run` 只等待持久状态：群聊取消等待不取消 Run，拥有记忆 Run 的任务仍显式取消它。

## 体量与限制

源文件由 387 增至 404，物理行数由 40,034 增至 40,966。新增主要是类型合同、查询/迁移和职责分离，本轮不是总代码缩减。运行时主文件由 965 行降至 721 行，主类由 903 行降至 660 行。必要的公开接口委托保留，没有为了减少方法数而破坏调用合同。

本轮没有把其他全部模块的 JSON 列表改为专用 SQL 查询，也没有做生产规模压测。Outbox 仍为至少一次投递，外部副作用保留不确定结果。Linux 原生隔离、Docker、QQ、外部模型、S3 和 Kubernetes 不属于本轮离线回归，不能据此宣称这些环境已重新验收。

## 验收

- `uv run ruff check .`、`uv run ruff format --check .`：通过。
- `uv run mypy`：404 个源文件通过。
- `uv run pytest -q --tb=short --maxfail=10`：999 passed、32 skipped，503.14 秒。
- 补充的已持久化 Run 审核记忆策略回归：1 passed，6.44 秒；加入时前一项全量测试已完成收集，故单独验收。
- 显式临时 PostgreSQL 回归：10 项通过，覆盖已有数据升级、漂移拒绝、事务与并发。
- `uv run python scripts/check_repository.py`：文档、静态边界、源码所有权和包内容通过。
- `uv build`：`opsmesh-0.3.2.dev0` wheel 与 sdist 构建成功。

32 项跳过包括需显式环境的 PostgreSQL、Redis、付费模型、Docker、Linux 沙箱和 Kubernetes，以及未安装 boto3 的 S3 路径。PostgreSQL 随后单独启动临时实例验收，其他跳过项仍未在本轮实测。所有验证均未连接生产数据库或调用付费模型。
