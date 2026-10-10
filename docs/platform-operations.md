# 平台运维与治理 API

本文描述当前平台运维合同，默认前缀 `/api/v1`。完整参数与 schema 见 [API 参考](api/README.md)。

## 用户工作空间权限上下文

`GET /workspaces/{workspace_id}/access/context` 返回当前用户、成员角色、工作空间和成员状态，以及 `allowed_actions`。

权限取角色与 Token scope 的交集。工作空间非 active 时返回空动作列表；非成员、失效成员、Token 不允许读取该空间时返回 403。平台管理员身份不绕过该接口的成员限制。响应使用 `Cache-Control: no-store`。

这些动作只是工作空间级权限上限。客户端可据此控制入口，但资源级授权、审批、状态、配额和 Worker 执行检查仍以服务端为准。

## 平台诊断、恢复与审计

| 方法与路径（在 `/admin` 下） | 行为 |
| --- | --- |
| `GET /operations/runs` | 分页查看 failed 或 stale Run；可按 workspace_id 过滤 |
| `GET /workspaces/{id}/operations/scheduler` | 指定空间的调度积压、阻塞原因、优先级和策略 |
| `POST /workspaces/{id}/operations/stale-runs/recover` | 提交持久化恢复请求，返回 202 |
| `GET /operations/requests` | 按空间、状态分页查看运维请求 |
| `GET /operations/requests/{id}` | 查看运维请求状态、结果和错误代码 |
| `GET /operations/audit-integrity` | 分页查看各空间最新审计验证，区分 missing、invalid、stale、valid |
| `POST /workspaces/{id}/operations/audit-integrity/verify` | 提交持久化审计验证请求，返回 202 |

两个 POST 均要求客户端生成 UUID `request_id` 和非空 `reason`。恢复还接受 `stale_after_seconds`（默认 900）、`statuses`（queued/running/waiting_runtime）、`limit`（最多 100）。队列由 Worker 配置决定，不接受调用方覆盖。

同一用户以相同 ID、空间、操作和参数重试，返回原请求；冲突重用返回 409。PostgreSQL 保存 pending 意图和审计，Worker maintenance 每轮最多处理 5 个请求，执行前重新验证账号、Token 和管理员权限。恢复操作还要求空间 active。查询接口不执行恢复。

恢复沿用现有语义：queued 重新入队，running/waiting_runtime 标记失败并释放相关租约。恢复查询先按陈旧时间过滤再分页，并锁定待处理 Run，避免并发恢复同一行。后续执行仍使用 Run 的冻结执行身份，不把管理员身份授予 Agent。

请求状态为 pending → running → completed/failed。异常输出只返回通用错误码，具体结果需结合审计检查；running 超过一小时会标记 `interrupted_outcome_unknown`，不会自动重放可能已发生的副作用。检查审计和资源状态后，使用新 request_id 提交新的人工恢复请求。

新增写入接口必须使用可归属到用户的、未限制 scope 的平台管理员凭据。配置型静态管理 Token 可读取管理接口，但不能用于这些新增写入。受限用户 Token 一律不能访问 `/admin`，即使其用户是平台管理员。

## 平台成本

`GET /admin/costs/summary` 聚合持久化用量账本，支持时间范围、currency、workspace_id，以及 workspace/provider/model/day 分组。分组支持 limit/offset/has_more，totals 不受分页影响。默认最近 30 天，时间跨度最多 366 天。

只汇总所选币种及币种未知的用量记录，不做汇率换算。返回 priced/unpriced 计数和 Token 总量；无价格记录不虚构费用，不能把 total_cost 当作完整账单。

## 市场发布审核

`GET /admin/marketplace/reviews` 提供跨空间发布审核队列，支持 workspace_id、status 和分页。默认 pending。

`POST /admin/workspaces/{id}/marketplace/reviews/{approval_id}/decision` 接受 `decision: approved|rejected` 和必填 reason。普通市场条目与人才市场 Agent 均复用既有 resource_review Approval；批准后 public，拒绝后 rejected。同决定重试幂等，反向决定、版本变化或目标丢失返回冲突；空间不匹配或非发布审核返回 404。平台封禁不能通过批准操作绕过。

发布审核原本已由策略按条件触发。平台复用既有审批状态机，不将所有发布强制转为人工审核。

## 历史监控

`GET /admin/operations/history` 按时间范围分页读取 PostgreSQL 中的状态数量快照，内容为 Run、Worker、Runtime、Approval 按 status 的数量。默认查询最近一天，最多 366 天。

Worker maintenance 采样，多个 Worker 同一时间桶只保留一份。配置：

- `OPSMESH_PLATFORM_HISTORY_ENABLED=true`
- `OPSMESH_PLATFORM_HISTORY_INTERVAL_SECONDS=60`（60–3600）
- `OPSMESH_PLATFORM_HISTORY_RETENTION_DAYS=30`（1–366）

实际采样频率受 Worker maintenance 调度限制；停机期间不补造历史。历史采样异常不会阻止原有维护工作。这里保存的是控制面状态数量，不包括 CPU、内存、网络、Redis 队列时序或 Prometheus 查询代理。

## 部署与验证

先执行 `alembic upgrade head`，再更新 API 和 Worker。`0109_platform_dashboard` 增加运维请求和历史快照两张表，支持 downgrade；降级会删除这两类新数据。

聚焦测试：`tests/test_platform_dashboard.py`。PostgreSQL 并发测试：`tests/test_platform_dashboard_postgres.py`，使用独立测试数据库的 `OPSMESH_TEST_POSTGRES_URL`，单独运行以避免现有 SQLite 测试对 ORM 类型的修改。

实现不等于生产部署：数据库迁移、Worker 启动以及生产负载下的采样和验证耗时，需在部署环境确认。
