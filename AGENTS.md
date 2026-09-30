# OpsMesh Repository Instructions

> 当前版本：2026-09-29。以代码、migration 和聚焦流程测试为准；本文只保留后端接口任务流、前端技能任务流和必要工程规范。

OpsMesh 是企业级 Agent 控制面。后端负责身份、工作空间、Agent、任务编排、能力、隔离 Runtime、审批、审计和运行运维；前端只通过稳定 API 合同访问这些能力。

## 后端 API

默认接口前缀是 `/api/v1`，由 `OPSMESH_API_PREFIX` 配置覆盖。总路由入口是 `backend/app/api/router.py`。

| 接口族 | 主要范围 | 代码位置 |
| --- | --- | --- |
| 身份与访问 | `/auth/*`、资源访问控制 | `backend/app/api/routes/access/` |
| 健康与指标 | `/health`、`/health/live`、`/health/ready`、`/metrics` | `backend/app/api/routes/operations/` |
| 工作空间 | 工作空间、成员、邀请、配额、项目、域、文件、知识、记忆、团队、导入导出 | `backend/app/api/routes/workspace/` |
| Agent 与模型 | Agent Profile、Session、消息、Provider、Provider 能力 | `backend/app/api/routes/agents/` |
| 编排与执行 | Orchestration、Task、Run、Approval、计划、事件、流式状态 | `backend/app/api/routes/orchestration/` 与 `workspace/teams/` |
| 能力中心 | Skill、Tool、MCP、凭据、插件、能力目录、Marketplace | `backend/app/api/routes/capabilities/` |
| 运行运维 | Operations、队列、Worker、Runtime、Runtime Space、成本、通知、定时任务 | `backend/app/api/routes/operations/` |
| 平台管理员 | `/admin/*`：总览、用户、公告、策略、能力治理、插件治理、Worker、Runtime、队列、安全、系统和更新 | `backend/app/api/routes/platform/` |
| 外部集成 | Webhook、Automation、Plugin Runtime | `backend/app/api/routes/integrations/` |
| 自托管 Worker | 注册、身份、Worker 控制、Job、MCP Job、Artifact | `backend/app/api/routes/self_hosted/` |

接口实现以当前路由和 schema 为准；旧文档中的计划接口不能当作已实现接口。存量接口按上表路由族组织；已迁移的功能模块就近放置 routes/schema/service/model，再由总路由显式注册。业务策略和持久化由服务层负责。

首批已迁移模块为 `backend/app/messaging/email/`：邮件配置、测试发送、SMTP 和邮件配置模型归拢于此，管理路由自带管理员依赖，由 `api/routes/platform/router.py` 注册。账号邀请路由仍在 `api/routes/platform/invitations.py`，账号业务仍在 `domains/access/invitations.py`。其他目标目录以 `docs/backend-directory-redesign-proposal.md` 为准，不能当作已迁移。

## 后端请求任务流

1. 客户端向 `/api/v1` 发起请求，认证依赖解析用户、Token 和当前工作空间。
2. Route 只负责 HTTP 参数、分页、响应模型和错误映射；不在 Route 中执行 Agent 或编写业务策略。
3. Application/Domain service 校验工作空间范围、成员角色、资源状态、配额、能力和审批要求。
4. 短请求在 PostgreSQL 事务中完成状态变更；长任务只持久化意图和初始状态，然后写入 Redis 队列并返回任务或 Run 标识。
5. Worker 领取任务后重新检查工作空间、授权、配额、冻结配置和当前能力状态，不能信任请求阶段的临时结果。
6. Agent SDK 和工具只在批准的 isolated、pooled 或 persistent Runtime 中执行；API 和 Worker 主进程不能运行用户或 Agent 控制的代码。
7. Run、Task、事件、审计、成本和 Artifact 写回 PostgreSQL；Redis 只保存队列、锁、发布订阅和短期派生状态。
8. 前端通过查询或流式接口读取状态和用户可见结果。失败必须进入明确的重试、暂停、取消或恢复状态，并留下审计证据。

## 前端任务流与技能边界

这五个技能按下面顺序协作：

1. `frontend-design`：先确定页面目标、信息层级、布局、字体、间距和响应式策略；不负责替代组件库。
2. `radix-colors`：涉及配色、状态色或明暗主题时选择 Radix 色阶，并映射到现有 shadcn 语义变量；不要把原始颜色散落在组件中。
3. `shadcn`：查询并复用现有 shadcn/ui 组件，使用 `frontend/components.json` 的配置和 Lucide 图标；不要引入第二套运行时组件库或手写替代组件。
4. API 接入：请求封装放在 `frontend/src/api`，产品组合放在 `frontend/src/features`，路由保持轻量，用户可见文案放在 `frontend/src/i18n`。
5. `web-design-guidelines`：功能完成后检查真实渲染页面的可访问性、键盘操作、交互、明暗主题，以及桌面和移动宽度。
6. `markdown-badges`：只用于 README 或 Markdown 徽章；不用于 Web UI，不引入运行时图片或组件。

新增控件先查 shadcn 现有组件；新增页面或路由必须由需求明确授权。完成 UI 改动前必须做真实页面检查，不能只凭 lint、类型检查或源码阅读宣布完成。

### UI 真实截图验收（硬性规则）

- 任何新增或修改布局、样式、视觉层级、字体、间距、颜色、图标、响应式行为或组件组合的改动，都必须在真实运行页面中逐个检查受影响的可视组件；禁止用 Story、静态 HTML、源码阅读、lint、类型检查或构建结果代替真实页面验收。
- 每个受影响组件都必须截图其主要可见状态，包括默认状态，以及本次改动涉及的弹窗、菜单、展开、选择、加载、空态、错误态或交互后状态；同一张截图只有在能够清楚辨认各组件布局时才可同时作为多个组件的证据。
- 每个受影响组件至少检查桌面宽度和移动宽度；涉及主题、颜色或阴影时，还必须分别检查浅色与深色主题。检查内容包括溢出、遮挡、错位、截断、异常留白、滚动、层级、焦点、文案和点击区域。
- 截图生成后必须由执行者实际打开并逐张查看，发现问题后修复并重新截图；截图文件存在但未查看，不算验证。
- 完成报告必须列出已截图检查的组件、视口和状态，并明确区分已验证与未验证项。缺少任何受影响组件的真实截图证据时，不得宣称 UI 改动完成。
- 页面标题和主内容区域禁止添加解释产品功能、用途或操作方式的可见段落文案；只保留必要的标题、字段、状态、动作、空态和错误信息。弹窗或组件为无障碍提供的描述必须使用 `sr-only`，不得以可见说明文字代替界面信息层级。

## 必要工程规范

- 工作空间是租户边界。所有工作空间资源的 API、查询、缓存、Worker、存储和工具调用都必须带工作空间范围。
- 依赖方向保持 `api -> domains/application -> infrastructure`；Route 不直接操作基础设施或拥有长流程。
- PostgreSQL 是持久事实来源；Redis 不保存唯一业务状态。
- 密钥只在最窄执行边界解密；禁止写入日志、响应、事件、缓存或完整授权 URL。
- 风险操作默认拒绝，并按需要创建审批、审计或安全事件。
- 数据库变更使用 Alembic，并保留升级和降级路径。
- 使用完整产品流程验证行为；只对跨边界拒绝、租户隔离、幂等、重试、恢复和脱敏保留测试。
- 文档必须描述当前代码，不把 planned、future 或旧路线图写成已实现能力。
- 代码提交使用 feature branch 和 Pull Request；完成状态必须区分 implemented、verified、unverified 和 blocked。

## 常用验证

后端：

```bash
uv sync --all-groups
uv run ruff check .
uv run mypy
uv run pytest backend/tests/test_health.py
```

前端：

```bash
cd frontend
pnpm lint
pnpm typecheck
pnpm build
```

功能变更运行受影响的产品流程；仅文档或组织调整不重复运行无关的完整测试套件。
