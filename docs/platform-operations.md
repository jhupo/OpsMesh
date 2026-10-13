# 平台运维与治理 API

本文描述当前源码的运维合同，默认前缀 `/api/v1`。参数和响应以生成的 [API 参考](api/README.md) 为准；验证及部署状态见 [架构与验收记录](architecture.md)。

## 权限上下文

`GET /workspaces/{workspace_id}/access/context` 返回用户、成员角色、空间状态和 `allowed_actions`。权限取角色与 Token scope 的交集；空间非 active 时动作列表为空。非成员、失效成员及 Token 不允许读取该空间时返回 403，平台管理员不绕过成员限制。响应使用 `Cache-Control: no-store`。

这些动作是空间级权限上限。具体资源的授权、审批、状态、配额以及执行时的检查仍由服务端完成。

## 诊断与管理员操作

以下路径位于 `/admin` 下：

| 方法与路径 | 行为 |
| --- | --- |
| `GET /operations/summary` | 待审批、等待执行、阻塞任务以及失败和安全原因统计 |
| `GET /operations/history` | 分页读取指定时间范围的持久化状态快照 |
| `GET /operations/requests` | 按空间分页读取管理员操作 |
| `GET /operations/requests/{id}` | 操作状态、结果与错误代码 |
| `GET /operations/audit-integrity` | 各空间最新审计校验状态 |
| `POST /workspaces/{id}/operations/audit-integrity/verify` | 提交原生 Workflow 审计校验，返回 202 |
| `POST /workspaces/{id}/operations/runs/{run_id}/cancel` | 提交持久化取消请求，返回 202 |

POST 要求可归属用户的、未限制 scope 的平台管理员身份，以及 UUID `request_id` 和非空 `reason`。同一身份、空间、操作和参数重试返回原请求；冲突复用返回 409。配置型静态管理 Token 可以读取，不能提交这些写操作。

短事务同时保存 `AdminOperationRecord`、审计和 Temporal Outbox。原生 Workflow 执行前重新校验用户、管理员权限和 Token 是否仍有效。GET 不触发执行；不再依赖 Worker 定时扫描启动管理员操作。

状态为 `pending -> running -> completed/failed`。返回结果脱敏，异常只保留安全错误代码。取消使用现有 Run 和任务取消服务，把取消信号投递给对应 Workflow；不清空其他任务、整个沙箱宿主或全局队列。旧的陈旧 Run 强制重放和自建 Worker 租约恢复接口已删除；Temporal 负责持久历史、任务分发及故障恢复，结果未知的外部副作用必须显式核对。

工作空间观察接口保留 Run、任务、审批、审计、安全与执行关联查询，使用 PostgreSQL 中的产品事实；不存在第二套 Worker 心跳和队列台账。

## 原生维护调度

Worker 启动时创建或更新四个 Temporal Schedule，使用 `SKIP` 重叠策略：

| Schedule 后缀 | 工作 |
| --- | --- |
| `history` | 平台状态采样和快照保留期限 |
| `audit` | 按工作空间检查审计完整性 |
| `lifecycle` | 工作空间生命周期、保留策略和归档意图 |
| `mcp_diagnostics` | 清除过期的私有诊断证据 |

完整 ID 为 `opsmesh:platform:maintenance:<后缀>`。维护按空间分页，每批最多并行处理 20 个原生子 Workflow。某空间失败不取消其他空间的维护；父 Workflow 最终报告失败，具体证据保存在相应子 Workflow 的历史中。

Chat 和正常任务的启动、完成、结果回写由事务投递及 Workflow 驱动，不等待维护 Schedule。维护不增加业务 Agent 外层强制循环。

历史快照包含 Run、Runtime、Approval 的状态数量和待投递 Temporal 意图数量。配置为 `OPSMESH_PLATFORM_HISTORY_ENABLED`、`OPSMESH_PLATFORM_HISTORY_INTERVAL_SECONDS` 和 `OPSMESH_PLATFORM_HISTORY_RETENTION_DAYS`；关闭时暂停原生采样 Schedule。时间桶具有数据库唯一约束，多 Worker 不重复保存同一桶；停机期间不虚构采样。

## 成本与市场审核

`GET /admin/costs/summary` 聚合持久化用量账本，支持时间范围、币种、空间及 workspace/provider/model/day 分组。总量不受分页影响，不做汇率换算；无价格的用量保留 unpriced 状态，不虚构完整账单。

`GET /admin/marketplace/reviews` 提供发布审核队列。`POST /admin/workspaces/{id}/marketplace/reviews/{approval_id}/decision` 接受 `approved|rejected` 和必填原因，复用资源审核 Approval。相同决定重试幂等；版本变化、相反决定或目标丢失返回冲突，平台封禁不能通过批准绕过。

## 升级与验证

API 和 Worker 使用同一发布版本的独立入口。升级前停止旧执行服务并完成执行迁移前置检查，随后显式运行 Alembic；不能让旧 Worker 访问新 schema。Temporal 自身 schema 由原生工具管理，不由 Alembic 接管。

安装器为托管 Worker 生成 `OPSMESH_TEMPORAL_WORKER_IDENTITY`。宿主机更新程序用 Temporal `DescribeTaskQueue` 检查主队列及插件队列的 poller，发现未托管身份时拒绝自动维护。进入维护状态后拒绝新任务，排空活动执行，再停止本机 API/Worker 并备份。此检查不是分布式栅栏；集群升级和恢复要求操作者先停止整个 Worker 集群及其他写入方。

真实 PostgreSQL 产品验证见 `tests/test_platform_dashboard.py`、`tests/test_platform_dashboard_postgres.py`、`tests/test_platform_updates.py` 和 `tests/test_execution_admission_postgres.py`；原生 Schedule 验证见 `tests/test_platform_maintenance_temporal_postgres.py`，原生 poller 拒绝验证见 `tests/test_temporal_worker_ownership.py`。配置 `OPSMESH_TEST_POSTGRES_URL` 与 `OPSMESH_TEST_TEMPORAL=1` 后运行；跳过项不能作为验收证据。
