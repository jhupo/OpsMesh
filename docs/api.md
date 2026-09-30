# OpsMesh API

> 当前接口索引基于 `backend/app/api/router.py` 和当前路由实现。默认前缀为 `/api/v1`；精确请求和响应 schema 以运行中的 OpenAPI 为准。

## 发现接口

开发环境默认地址为 `http://localhost:8000`：

- OpenAPI JSON：`GET /openapi.json`（由 FastAPI 应用配置提供）
- Swagger UI：`GET /docs`
- 健康检查：`GET /api/v1/health`
- 就绪检查：`GET /api/v1/health/ready`
- 指标：`GET /api/v1/metrics`

生产环境可以通过 `OPSMESH_API_PREFIX` 覆盖 `/api/v1`。

## 认证

用户认证接口位于 `/api/v1/auth`：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/auth/register` | 注册用户 |
| POST | `/auth/login` | 密码登录并返回 API Token |
| POST | `/auth/logout` | 撤销当前 Token |
| GET | `/auth/me` | 查询当前用户 |
| PATCH | `/auth/me` | 更新当前用户资料或密码相关资料 |
| PUT | `/auth/password` | 修改密码并按策略撤销活动 Token |
| GET/POST/DELETE | `/auth/tokens` | 查询、创建和撤销当前用户 Token |

登录成功后使用 `Authorization: Bearer <token>` 访问受保护接口。平台管理员接口还会检查平台管理员权限。

## 工作空间接口

工作空间接口使用 `/api/v1/workspaces`：

- 工作空间、成员、邀请和配额：`/workspaces` 及其子路径
- 项目：`/workspaces/{workspace_id}/projects`
- 文件与 Artifact：`/workspaces/{workspace_id}/files`、`/workspaces/{workspace_id}/exports`
- 知识与记忆：`/workspaces/{workspace_id}/knowledge/sources`、`/workspaces/{workspace_id}/memories`
- 团队、Team Session、Team Runtime 和执行：`/workspaces/{workspace_id}/...`
- 域任务和导入导出：`/workspaces/{workspace_id}/...`

所有工作空间资源都必须同时校验认证用户、工作空间成员关系和具体操作权限。请求体不能覆盖 URL 中的 `workspace_id`。

## Agent 与执行接口

- Agent Profile：`/workspaces/{workspace_id}/agents`
- Agent Session：`/workspaces/{workspace_id}/agents/{agent_id}/sessions`
- Agent 消息：工作空间 Agent 消息路由
- 编排定义：`/workspaces/{workspace_id}/orchestrations`
- Task、计划、事件、流式状态和操作：`/workspaces/{workspace_id}/...`
- Run：`/workspaces/{workspace_id}/runs` 及事件和流式子路径
- 审批：`/workspaces/{workspace_id}/approvals`

创建长任务时，接口只创建持久状态并入队；执行由 Worker 在授权 Runtime 中完成。客户端通过任务、Run、事件或流式接口读取进度和结果。

## 能力与 Marketplace

- 工作空间能力：`/workspaces/{workspace_id}/capabilities`
- 工作空间插件：`/workspaces/{workspace_id}/plugins`
- Marketplace：`/marketplace`
- Talent catalog：`/talent-market`
- MCP 凭据、Server、Tool allowlist 和观测接口都挂在能力路由下。

能力的创建、安装、发布、审核和撤销都要经过工作空间或平台治理权限；公开 Marketplace 资源不能绕过审核。

## 运行与运维

- 工作空间运行状态：`/workspaces/{workspace_id}/operations`
- Runtime 与 Runtime Space：`/workspaces/{workspace_id}/runtimes` 及相关子路径
- 成本：`/workspaces/{workspace_id}/costs`
- 通知：`/workspaces/{workspace_id}/notifications`
- 定时任务：`/workspaces/{workspace_id}/scheduled-jobs`
- Webhook：`/workspaces/{workspace_id}/webhook-subscriptions`
- Automation：`/workspaces/{workspace_id}/automations`

## 平台管理员

平台管理员接口统一位于 `/api/v1/admin`，所有路由都经过平台管理员依赖：

- `/admin/overview`：平台总览和工作空间摘要
- `/admin/users`：用户列表、创建、状态、Token、成员关系等管理
- `/admin/workspaces`：跨工作空间摘要和管理
- `/admin/announcements`：公告发布和撤回
- `/admin/capabilities`、`/admin/plugins`：能力和插件治理
- `/admin/workers`、`/admin/runtimes`、`/admin/queues`、`/admin/leases`：运行控制面
- `/admin/policies`、`/admin/security-events`、`/admin/system`、`/admin/updates`：策略、安全、系统和更新

管理员接口不得被普通工作空间角色调用；跨工作空间读取必须通过平台服务完成范围控制和审计。

## 自托管 Worker 与插件 Runtime

自托管 Worker 路由由 `backend/app/api/routes/self_hosted/` 提供，插件 Runtime 路由使用 `/api/v1/plugin-runtime/{workspace_id}/{install_id}`。这些接口使用独立的 Worker、Runtime 或安装凭据，不能被普通用户 Token 代替。

## 通用约定

- 列表接口使用分页参数和 `PageResponse`，具体字段以 OpenAPI schema 为准。
- 资源查询必须带工作空间范围，不能仅凭资源 ID 加载 workspace-owned 数据。
- API 只负责输入校验、授权上下文和持久化意图；长时间执行不在请求线程中完成。
- 错误使用 HTTP 状态码和响应模型返回；服务层错误由 Route 映射为稳定的公开错误信息。
- 认证信息、Provider Key、MCP Credential、签名材料和完整授权 URL 不得出现在响应、日志或审计详情中。
- 对需要审批的动作，接口返回持久化的审批或 Run 状态，Worker 在执行前再次检查当前授权和策略。

接口源代码：`backend/app/api/routes/`；应用路由汇总：`backend/app/api/router.py`。

