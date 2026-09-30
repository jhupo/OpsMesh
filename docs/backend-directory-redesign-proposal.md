# OpsMesh 后端目录重新分类方案

日期：2026-09-30。状态：**提案，待确认，尚未执行目录迁移**。

范围：以当前工作区 `backend/app` 的代码、路由、模型注册、部署入口和导入约束为依据；同时考虑 `backend/tests`、migration、独立 `runtime` 与 `operator` 包。本文只提出分类与迁移方案，不表示下列目标目录已经存在。

## 一、先给结论

**建议调整。业务代码采用“一级业务功能 → 二级功能模块 → 三级具体代码文件”，而不是继续在全局 `api`、`domains`、`runtime` 等技术层之间重复铺设业务目录。**

但需要同时保留两点：

1. **文件放在一起，不等于取消分层。** 同一模块内的路由、业务策略、持久化、外部适配仍各有职责；HTTP 入口不能直接执行 Agent 或绕过服务层。
2. **默认三级，不强制所有代码只有三级。** MCP、Agent SDK 适配、复杂任务执行等允许有明确职责的第四级；不要为了目录浅而把几十个不同职责塞进一个文件夹或巨型文件。

推荐形态是：**按功能组织的模块化单体 + 小型启动装配目录 + 小型公共基础设施目录**。不是拆微服务，也不是把所有代码扁平化。

本次建议不改变 API URL、数据库表名、权限语义、队列协议、部署单元和前端导航。目录结构不必与菜单一一对应。

## 二、当前问题到底是什么

### 2.1 当前结构的真实规模

本轮扫描 `backend/app/**/*.py`，排除 `__init__.py` 和缓存，共 **861 个 Python 实现文件**。按当前一级目录统计如下，包含工作区中尚未提交的邮件邀请代码：

| 当前区域 | 实现文件数 | 主要情况 |
| --- | ---: | --- |
| `api` | 153 | 业务路由、请求响应 schema、HTTP 基础设施集中在这里 |
| `domains` | 515 | 业务实体、服务、部分基础设施适配和执行逻辑 |
| `runtime` | 147 | 隔离环境、Worker、队列、自托管协议及运维读模型 |
| `observability` | 19 | 同时包含遥测基础设施、审计、成本、通知业务 |
| `core` | 20 | 配置、DB、Redis、安全、公共类型等 |
| `bootstrap` | 5 | ORM 注册、资源装配、Provider 装配、运行环境与遥测装配 |
| `main.py`、`delivery.py` | 2 | 应用与独立发行版入口 |
| 合计 | 861 | 不是重复文件数量 |

**目录同名不等于代码重复。** 本轮没有做函数级语义克隆分析，因此不能据此删除“看起来重复”的实现。可以确认的是：同一功能的代码位置分散，部分目录把不同层次的职责混在一起。

### 2.2 具体症状

**用户与邀请分散。** 用户管理路由在 `api/routes/platform/users.py`，用户服务在 `domains/access/admin.py`，模型在 `domains/access/models.py`。邀请又同时涉及 `api/routes/access/invitations.py`、`api/routes/platform/mail.py`、`api/schemas/platform/mail.py`、`domains/access/invitations.py`、`domains/platform/mail.py` 和 `core/mail.py`。修改完整邀请流程需要跨越多个技术层及命名不一致的目录。

**`workspace` 过于宽泛。** `domains/workspace` 下有租户、配额、文件、备份、审核、团队执行与团队运行状态。某项资源带 `workspace_id`，不代表它必须属于工作空间管理模块；工作空间是隔离边界，不是收纳所有业务的目录。

**`runtime` 有多种含义。** `domains/agents/runtime` 是 Agent 执行契约和 Provider 适配；`domains/workspace/teams/runtime` 是团队会话、绑定、心跳、mailbox 等协作状态；`runtime/environment` 则负责隔离执行环境。三者不能直接合并，只因它们都叫 runtime。

**`observability` 混合业务与底座。** tracing、logging 是公共遥测；审计记录、模型成本、通知中心则有自己的实体、权限与生命周期，不能都作为日志工具处理。

**`platform/admin` 按调用者聚类。** 当前同时收纳用户、工作空间、队列、Worker、Runtime 等管理能力，容易形成各业务的第二套归属。管理员是权限视角，不是所有业务的所有者。

**迁移风险不仅是修改 import。** 当前 ORM 注册、独立发行版、容器命令、Worker 入口、Alembic 和 import-linter 都引用现有模块路径。纯粹批量移动文件，会遗漏这些运行时入口。

### 2.3 依赖扫描提示

AST 扫描中，`runtime → domains` 有 197 处导入，`domains → runtime` 有 147 处导入；`domains → observability` 有 113 处，反向有 10 处。

口径：统计绝对 `backend.app.*` 的 import 语句目标，包含类型检查分支；不等于独立依赖边数，也不证明某两个具体模块已经产生运行时循环。它说明：**现有一级目录不是严格的单向技术层，不能只换目录名而不处理边界。**

## 三、目标分类：一级大功能、二级模块

建议保留 11 个业务大类，以及 `bootstrap`、`shared` 两个技术例外。一级目录可能比现在多，但每项业务不再同时铺在多套全局树下；目标是“容易定位、唯一归属”，不是让一级目录数字最小。

| 一级目录 | 功能归属 | 主要二级模块 | 不应放入 |
| --- | --- | --- | --- |
| `identity` | 用户身份与访问 | `users`、`auth`、`authorization`、`invitations` | 工作空间配额、SMTP 配置 |
| `workspaces` | 租户与工作空间管理 | `management`、`members`、`quotas`、`projects`、`domain_items` | 所有带 workspace_id 的资源 |
| `agents` | Agent 定义、会话与执行适配 | `profiles`、`sessions`、`messages`、`providers`、`execution` | Docker 生命周期、Task 状态机 |
| `teams` | Agent 团队与协作 | `management`、`organization`、`projects`、`execution`、`sessions`、`providers`、`operations` | 人类用户管理、底层 Worker 循环 |
| `execution` | 任务编排与触发执行 | `tasks`、`planning`、`orchestrations`、`runs`、`approvals`、`requests`、`automations`、`scheduling`、`webhooks` | SMTP、容器后端 |
| `capabilities` | 可复用能力与治理 | `catalog`、`governance`、`skills`、`tools`、`mcp`、`plugins`、`marketplace`、`references` | 通用文件存储、全平台策略集合 |
| `resources` | 数据与内容资源 | `files`、`artifacts`、`storage`、`knowledge`、`memory`、`transfers`、`lifecycle` | Runtime 容量、能力引用描述 |
| `runtime` | 隔离环境与作业基础设施 | `instances`、`spaces`、`pools`、`commands`、`backends`、`workers`、`queues`、`self_hosted`、`recovery`、`operations` | Agent SDK 业务适配、团队业务状态机 |
| `messaging` | 消息通知与发送通道 | `notifications`、`email` | 用户邀请 token 和激活状态机 |
| `governance` | 横切治理、审计与计量 | `audit`、`security_events`、`policies`、`reviews`、`costs`、`credentials` | 业务资源的 CRUD 副本、OTel 底层工具 |
| `platform` | 系统级管理与聚合视图 | `overview`、`settings`、`announcements`、`releases`、`updates`、`health` | 用户/工作空间/Runtime 模型的第二份实现 |
| `bootstrap` | 进程启动与依赖装配 | 显式路由注册、模型注册、Worker handler 注册、资源初始化与关闭 | 业务规则、业务数据查询 |
| `shared` | 无业务归属的稳定底座 | 配置、DB、Redis、HTTP 通用设施、安全原语、遥测、公共类型 | 为解决循环依赖而随手搬来的业务逻辑 |

这些名字是本项目的建议归属，不是框架强制规定。尤其要区分：`teams/projects` 是团队项目协作与执行配置，`workspaces/projects` 是租户范围内的项目资源；可以调用彼此公开接口，但不复制同一项目的持久事实。

### 3.1 目标目录示意

```text
backend/
├── app/
│   ├── main.py                  稳定 API 入口，调用 bootstrap
│   ├── delivery.py              稳定发行版入口，调用 bootstrap
│   ├── identity/
│   │   ├── users/               routes.py / admin_routes.py / schemas.py / service.py / models.py
│   │   ├── auth/                routes.py / schemas.py / service.py / dependencies.py
│   │   ├── authorization/       context.py / policy.py / resource_queries.py
│   │   └── invitations/         routes.py / admin_routes.py / schemas.py / service.py / models.py
│   ├── workspaces/
│   │   ├── management/
│   │   ├── members/             含工作空间成员邀请，不是平台注册邀请
│   │   ├── quotas/
│   │   ├── projects/
│   │   └── domain_items/
│   ├── agents/
│   │   ├── profiles/
│   │   ├── sessions/
│   │   ├── messages/
│   │   ├── providers/           Provider 配置、模型能力与运行诊断
│   │   └── execution/           执行契约与 SDK 适配，保留必要的 Provider 子包
│   ├── teams/
│   │   ├── management/
│   │   ├── organization/
│   │   ├── projects/
│   │   ├── execution/
│   │   ├── sessions/            团队绑定、会话、心跳、mailbox 状态
│   │   ├── providers/
│   │   └── operations/
│   ├── execution/
│   │   ├── tasks/
│   │   ├── planning/            Task 的内部子流程，不增加独立 /plans 资源
│   │   ├── orchestrations/      可版本化的可复用执行定义
│   │   ├── runs/
│   │   ├── approvals/
│   │   ├── requests/
│   │   ├── automations/
│   │   ├── scheduling/
│   │   └── webhooks/
│   ├── capabilities/
│   │   ├── catalog/
│   │   ├── governance/
│   │   ├── skills/
│   │   ├── tools/
│   │   ├── mcp/                 允许 catalog / execution / transport 子包
│   │   ├── plugins/             含原 plugin_runtime 接入
│   │   ├── marketplace/
│   │   └── references/          能力资源描述与定位，不是文件实体
│   ├── resources/
│   │   ├── files/
│   │   ├── artifacts/
│   │   ├── storage/             内容存储与 S3 等适配
│   │   ├── knowledge/
│   │   ├── memory/
│   │   ├── transfers/
│   │   └── lifecycle/
│   ├── runtime/
│   │   ├── instances/           原 environment 的实例生命周期及租约
│   │   ├── spaces/
│   │   ├── pools/
│   │   ├── commands/
│   │   ├── backends/
│   │   ├── workers/
│   │   ├── queues/
│   │   ├── self_hosted/
│   │   ├── recovery/
│   │   └── operations/          跨运行资源的健康、容量与时间线聚合
│   ├── messaging/
│   │   ├── notifications/
│   │   └── email/              配置、密文、模板发送、SMTP 适配
│   ├── governance/
│   │   ├── audit/
│   │   ├── security_events/
│   │   ├── policies/
│   │   ├── reviews/             当前 workspace/reviews 的安全审查
│   │   ├── costs/
│   │   └── credentials/         跨模块密钥轮换编排，不复制各模块凭据表
│   ├── platform/
│   │   ├── overview/
│   │   ├── settings/
│   │   ├── announcements/
│   │   ├── releases/
│   │   ├── updates/
│   │   └── health/
│   ├── bootstrap/
│   │   ├── api.py
│   │   ├── routers.py
│   │   ├── models.py
│   │   ├── resources.py
│   │   ├── providers.py
│   │   ├── worker.py
│   │   ├── job_handlers.py
│   │   └── telemetry.py
│   └── shared/
│       ├── config/
│       ├── db/
│       ├── redis/
│       ├── http/
│       ├── security/
│       ├── telemetry/
│       ├── contracts.py
│       ├── pagination.py
│       └── errors.py
├── migrations/                 保留单一迁移链，不按功能复制 migration
└── tests/                      后续按业务场景分组，保留完整流程测试
```

目录树是目标信息架构，不要求先创建所有空文件夹。未迁移的区域继续留在原路径；只有明确归属和真实代码时才建立目标模块。`__init__.py` 在图中省略，不使用它做隐式初始化或大规模 re-export。

### 3.2 哪些名称不再作为全局业务分类

- **`api`：** 业务 routes 和 schemas 迁入各自模块；通用 HTTP 机制进入 `shared/http`，总注册进入 `bootstrap/routers.py`。最终不保留另一棵完整业务 API 树。
- **`domains`：** 去掉中间容器层，其内容按功能直接成为 `app` 下的业务目录；不是把 `domains` 改名成 `modules` 后继续套一层。
- **`core`：** 按职责精简为 `shared`，SMTP 等有明确归属的内容先迁出；不能仅更名后继续当杂物箱。
- **`observability`：** 技术遥测归 `shared/telemetry`；审计、成本、安全事件归 `governance`；通知归 `messaging`。
- **`integrations`：** 按实际用途归属：Automation、Webhook 触发归 `execution`；Plugin Runtime 接入归 `capabilities/plugins`。后续有独立集成产品模块再单独评估，不提前建万能集成层。
- **`bootstrap`：** 保留。它不是重复业务目录，而是装配根；不过业务代码不应反向依赖它创建默认实现。
- **`runtime`：** 保留但限定含义，只表示隔离环境与作业基础设施，不再泛指所有“运行中的业务”。

## 四、三级代码文件如何组织

以 `identity/users` 为例，建议按实际需要选用以下文件，不强制每个模块拥有整套模板：

| 文件 | 职责 | 约束 |
| --- | --- | --- |
| `routes.py` | 普通用户 HTTP 入口 | 解析参数、鉴权入口、响应与错误映射 |
| `admin_routes.py` | 管理员 HTTP 入口 | 同一用户业务的管理视角，不建立第二套用户服务 |
| `schemas.py` | HTTP 请求响应结构 | 保持现有 API 字段和 OpenAPI 契约 |
| `service.py` | 业务用例与事务策略 | 校验业务状态，调用查询、持久化和外部端口 |
| `models.py` | ORM 持久实体 | 一个表只定义一次，使用同一 Base/metadata |
| `queries.py` | 查询及读模型 | 返回稳定 DTO；复杂 SQL 聚合不塞进 router |
| `repository.py` | 复杂持久化操作 | 按复杂度使用，不为单次 select 强制新增抽象层 |
| `policy.py` | 权限和业务规则 | 不依赖 HTTP Request、FastAPI Depends |
| `contracts.py` | 模块对外契约 | 不导出私有 ORM 写操作或 HTTP 细节 |
| `ports.py` | 外部协作协议 | 在确需解耦或隔离替换时引入 Protocol |
| `jobs.py` | 本功能的队列任务 handler | Worker 调用本模块服务，任务类型及序列化保持兼容 |

补充规则：

1. 只有管理员入口的模块可以直接使用 `routes.py`；文件命名不是权限机制。
2. `schemas.py` 与 `contracts.py` 不能机械复制同一份类型；需要不同边界、版本或语义时才分开。无 HTTP 耦合的稳定 DTO 可以直接复用。
3. 已有合理的 `commands.py`、`lifecycle.py`、`diagnostics.py` 等细粒度文件应保留，不为了统一名称全改成 `service.py`。
4. 持久模型与业务服务同目录，不要求本轮重写成纯领域实体 + Repository + Unit of Work 全套架构。
5. 默认路径如 `identity/users/service.py`。确有规模需要时允许 `agents/execution/providers/openai/runner.py`；这个额外层级比十几个 `openai_*` 文件散落更清楚。
6. 不再新增全局 `services/users.py`、`models/users.py`、`schemas/users.py`，否则又恢复技术分层优先。
7. 公共库必须不依赖业务模块；业务专用工具留在其所有者模块，不靠建立 `common`、`misc`、`helpers2` 规避分类。

## 五、现有代码的归属映射

下表中的源路径以 `backend/app/` 为根，目标也位于 `backend/app/`。标注“拆分”的文件不能整文件直接搬迁；执行前须按声明、调用者和 ORM 关系生成精确清单。

| 现有位置 | 建议位置 | 处理方式 |
| --- | --- | --- |
| `api/routes/access/auth.py`、`api/schemas/access/auth.py` | `identity/auth` | 路由与 schema 就近归拢 |
| `domains/access/service.py` | `identity/auth`、`identity/users` | 拆分认证、账号初始化/管理用例；不复制共享规则 |
| `api/routes/platform/users.py`、`domains/access/admin.py`、`domains/access/avatars.py` | `identity/users` | 管理入口、用户用例、头像管理归同一模块 |
| `domains/access/models.py` | `identity/users`、`identity/auth`、`identity/invitations`、`identity/authorization` | 按 User、Token、Invitation、授权记录拆分；同一 ORM 类仅有一份 |
| `domains/access/context.py`、`permissions.py`、`resources.py`、`resource_queries.py`、`execution.py` | `identity/authorization` | 保留上下文、资源范围、执行授权校验语义 |
| `api/dependencies/auth.py`、`admin.py` | `identity/auth/dependencies.py` 及 `identity/authorization/dependencies.py` | HTTP 依赖可以依赖服务；服务不得反向依赖 HTTP 依赖 |
| `domains/access/invitations.py`、`api/routes/access/invitations.py` | `identity/invitations` | 平台账号邀请与接受邀请同属一个生命周期 |
| `api/routes/platform/mail.py`、`api/schemas/platform/mail.py` | `messaging/email` 与 `identity/invitations` | 拆分邮件配置/测试和账号邀请/激活接口 |
| `domains/platform/mail.py`、`core/mail.py` | `messaging/email` | 配置服务与 SMTP 适配分别成文件，但同一功能归属 |
| `domains/platform/admin/models.py` | `messaging/email/models.py`、`governance/policies/models.py` | 邮件配置和平台策略不是同一实体族 |
| `domains/workspace/tenants` 及 workspace 的 workspaces/members/invites/quotas 路由 | `workspaces/management`、`members`、`quotas` | 整理租户资源与成员关系；保留租户范围校验 |
| `domains/workspace/projects`、对应 projects 路由/schema | `workspaces/projects` | 归拢项目资源 |
| `domains/workspace/extensions`、对应 domains 路由/schema | `workspaces/domain_items` | 先保留当前 DomainProject/DomainItem 语义；不能当成重复 Project 删除 |
| `domains/agents/profiles`、`sessions`、`messages`、`providers` | `agents` 下同名模块 | 合并对应路由/schema，保留现有执行接口 |
| `domains/agents/runtime` | `agents/execution` | 更名消除与隔离环境的歧义，保留 Provider 适配子包 |
| `domains/workspace/teams` 与 workspace/teams 路由/schema | `teams` 下对应模块 | 管理、组织、项目、执行、运维分别归拢 |
| `domains/workspace/teams/runtime` | `teams/sessions` | 会话绑定、mailbox、生命周期等归协作会话；迁移时复核各文件语义 |
| `domains/orchestration/tasks` 与 tasks 路由/schema | `execution/tasks` | 保留控制、交付、协作等必要子包，不强行合成一个 service |
| `domains/orchestration/workflows/planning` 与 tasks/plans 路由 | `execution/planning` | 内部规划模块；URL 仍在 Task 下 |
| `domains/orchestration/workflows/definitions` 与 definitions 路由/schema | `execution/orchestrations` | 执行定义及版本管理 |
| `domains/orchestration/workflows/steps`、`statuses.py`、`domains/orchestration/models.py` | `execution/orchestrations` 或 `execution/runs` 的唯一实体所有者 | 逐声明分类；定义期图校验与执行期步骤状态不能混为一类 |
| `domains/orchestration/runs`、`approvals`、`requests` | `execution` 下同名模块 | 接入对应路由/schema |
| `domains/integrations/automation*`、automations 路由 | `execution/automations` | 自动化触发与任务提交 |
| `runtime/workers/scheduling`、scheduled_jobs 路由/schema | `execution/scheduling` | 定时规则是业务模块；底层队列仍归 runtime |
| `domains/integrations/webhooks`、webhooks 路由/schema | `execution/webhooks` | 触发入口、验签、去重和投递结果 |
| `domains/capabilities` 与 capabilities 路由/schema | `capabilities` 下对应模块 | 主体按模块搬迁；避免无必要重写 |
| `domains/capabilities/resources` | `capabilities/references` | 资源描述与 locator；不与文件/知识实体混合 |
| `api/routes/integrations/plugin_runtime.py` | `capabilities/plugins` | 插件专属运行接入协议保留独立文件 |
| `domains/workspace/storage`、files 路由/schema | `resources/files`、`artifacts`、`storage` | 元数据、产物业务、内容存储适配分别拥有职责 |
| `domains/knowledge`、workspace/knowledge 路由/schema | `resources/knowledge` | 知识业务与摄取任务同模块 |
| `domains/agents/memory`、workspace/memory 路由/schema | `resources/memory` | 记忆是租户内容资源，Agent 通过公开接口访问 |
| `domains/workspace/data_transfer`、`data_lifecycle`、exports 路由 | `resources/transfers`、`lifecycle` | 保留恢复、导入导出、留存策略和任务审计 |
| `domains/workspace/reviews` | `governance/reviews` | 安全/语义审查，不与 execution/tasks/delivery 的成果验收简单合并 |
| `runtime/environment` | `runtime/instances`、`spaces`、`pools`、`commands`、`backends` | 原环境管理按资源职责归拢，Runtime 租约仍属于实例管理 |
| `runtime/workers` | `runtime/workers`、`queues`、`recovery` | Worker 生命周期、队列协议、恢复调度分开 |
| `runtime/workers/handlers/*` | 所属业务模块的 `jobs.py` 或必要子包 | handler 注册由 bootstrap 负责；不在 Worker 核心枚举导入全部业务 |
| `runtime/self_hosted`、self_hosted 路由/schema | `runtime/self_hosted` | 注册、身份、调度、Job 与 Artifact 协议就近归拢 |
| `runtime/operations`、operations 路由/schema | `runtime/operations` 或对应资源模块 | 跨资源汇总保留；单资源诊断分别归 workers/queues/instances；Provider 诊断归 agents/providers |
| `observability/audit` | `governance/audit`、`security_events` | 审计证据与安全事件按实体区分，保持关联 |
| `observability/costs`、costs 路由/schema | `governance/costs` | 使用记录、计价、成本查询一起归拢 |
| `observability/notifications`、notifications 路由 | `messaging/notifications` | 通知收件箱、偏好与生命周期不是日志设施 |
| `observability/telemetry` | `shared/telemetry` | 仅日志、trace、metrics 基础机制；业务指标计算仍归功能模块 |
| `domains/platform/admin`、platform 管理路由 | 对应资源模块 + `platform/overview` | 用户/工作空间/运行资源管理回到所有者；真正跨域总览留平台 |
| `domains/platform/releases`、`updates`、相关路由 | `platform/releases`、`updates` | 保留更新锁、恢复与发行版入口的兼容性 |
| `domains/platform/credential_rotation.py` | `governance/credentials` | 调用各模块轮换接口；加解密原语仍归 shared/security |
| `core/db`、`redis`、`security`、配置、分页等 | `shared` 中对应位置 | 技术通用部分整体保留，明确禁止反向依赖业务 |
| `api/router.py`、HTTP 公共文件、`bootstrap/*` | `bootstrap` 与 `shared/http` | 显式装配；不建立第二套业务目录 |

## 六、以用户邀请为例，迁移后如何找代码

### 6.1 用户管理与邀请

```text
identity/
├── users/
│   ├── admin_routes.py       用户列表、编辑、状态变更、密码重置
│   ├── schemas.py
│   ├── service.py
│   ├── queries.py            用户/成员关系/配额摘要的公开查询
│   ├── avatars.py
│   └── models.py             User、UserAvatar
├── auth/
│   ├── routes.py             登录、Token 相关入口
│   ├── service.py            密码验证、Token 生命周期
│   ├── dependencies.py
│   └── models.py             UserAPIToken
└── invitations/
    ├── admin_routes.py       创建邀请、重新发送
    ├── routes.py             接受邀请
    ├── schemas.py
    ├── service.py            待激活、重发、过期、一次性消费
    ├── contracts.py
    └── models.py             UserInvitation

messaging/email/
├── routes.py                 管理员邮件设置与测试邮件
├── schemas.py
├── service.py                配置、发送策略、错误脱敏
├── contracts.py              发送接口与结果，不暴露 SMTP 密码
├── smtp.py                   SMTP/STARTTLS/TLS 适配
└── models.py                 PlatformMailSettings
```

这样的分离不是重复：**邀请属于身份业务；邮件属于投递通道**。未来告警或其他通知可以使用邮件发送能力，不必反向调用用户邀请服务。邀请文案和链接语义由邀请模块负责，SMTP 连接与凭据解密由邮件模块负责。

现有接口路径保持：

- `/admin/users` → `identity/users`。
- `/admin/user-invitations`、`/admin/users/{user_id}/invitation/resend`、`/auth/invitations/accept` → `identity/invitations`。
- `/admin/system/mail`、`/admin/system/mail/test` → `messaging/email`。

不是因为 URL 带 `/admin/system`，就必须把邮件模型放入 `platform/admin/models.py`。同时，**不能在分散路由时丢掉现有 `/admin` 聚合路由的管理员依赖**。迁移后的每个管理 router 明确声明管理员依赖，统一注册后用普通用户拒绝用例验证，不能只看前端是否隐藏入口。

### 6.2 工作空间邀请要独立

`identity/invitations` 负责创建/激活平台账号；`workspaces/members` 负责把账号加入某个工作空间及赋予角色。二者可复用邮件通道，但有不同状态、token 用途、权限和租户范围，不能仅凭“都是邀请”合表、合 token 或互相接受链接。

### 6.3 不顺带更改运行策略

目录迁移阶段保持现有邀请幂等、SMTP 密码加密、失败状态、重发和激活行为。是否引入专门邮件队列/outbox，应作为另一个明确的可靠性需求；不能在“重新分类文件”时偷偷改变事务或投递保证。

## 七、按功能归拢后，依赖边界仍然要严格

### 7.1 允许的主要方向

```text
main / delivery
       ↓
bootstrap：装配具体实现、路由、ORM 与作业注册表
       ↓
功能模块 routes / admin_routes / jobs
       ↓
功能模块 service / policy
       ↓
本模块查询与持久化 / 明确的跨模块契约 / 外部适配端口
       ↓
shared：通用设施与原语
```

跨模块协作规则：

- 模块服务不能 import 其他模块的 `routes.py`、HTTP dependency 或 `bootstrap`。
- `shared` 不能 import 任意业务模块，也不能依赖 `bootstrap`。有此需求说明分类或依赖注入位置有误。
- 资源的创建、状态机迁移和删除由唯一所有者服务执行；禁止平台总览、其他业务服务直接修改该资源的私有 ORM 状态。
- 跨模块读取优先使用公开查询/DTO。确实需要跨表高效聚合的读模型应集中定义、只读、显式记录允许依赖，不为了纯粹抽象制造 N+1 查询。
- 跨模块写事务必须明确谁拥有提交/回滚；本次整理不批量删除或新增 `commit()`，避免改变原有事务边界。
- 发现 A ↔ B 依赖时，先辨别是共同数据类型、协作编排还是所有权错误；优先引入窄接口/回调或把编排放到真实用例所有者，不把整项业务搬到 shared。
- 纯目录迁移可短暂保留旧路径转发，但只能单向 `旧 → 新`、列入退出清单。新代码不得长期依赖转发层，ORM 模型不能复制定义。

### 7.2 Worker 与业务 handler 的关键边界

当前 `runtime/workers/handlers` 中有 Task 规划、Agent Run、知识摄取、Memory、团队执行等功能。建议将具体 handler 归回功能模块，Worker 只保留：

1. 领取、租约、心跳、确认、重试、死信等通用机制。
2. 稳定的 JobPayload 和 handler 调用协议。
3. 接受启动时注入的 handler registry。

`bootstrap/job_handlers.py` 显式注册业务 handler，避免形成 `execution → runtime/workers → execution` 的业务导入闭环。handler 移动时不改变已有队列的 job type、payload 字段和恢复语义；对已入队任务先做兼容验证。

### 7.3 三类“运行”的归属

| 运行含义 | 归属 | 负责什么 |
| --- | --- | --- |
| Agent 执行 | `agents/execution` | Provider 无关契约、SDK 适配、事件转换 |
| 团队协作会话 | `teams/sessions` | 绑定、会话状态、mailbox、团队生命周期 |
| 隔离运行环境 | `runtime` | 容器/远端后端、空间、池、租约、命令、Worker |

**分类位置不能被当成执行位置授权。** Agent SDK、用户代码和工具仍必须在批准的隔离 Runtime 中执行；不能因为 adapter 文件移动到某模块，就由 API 或 Worker 主进程直接执行它。现有执行桥接与隔离检查必须保持并回归。

仓库根目录的 `runtime/opsmesh_runtime` 是独立发行的隔离运行辅助包，`operator/opsmesh_operator` 是安装与主机运维包。它们与后端控制面不是重复目录，**本方案不把它们合入 `backend/app/runtime`**，也不要求同一轮重命名发行包。

### 7.4 技术安全边界不因目录调整变弱

- 所有租户资源继续携带 workspace 范围；账号和平台聚合能力与租户资源分开授权。
- PostgreSQL 仍保存持久事实，Redis 不成为唯一状态来源。
- Docker、S3、模型 SDK 等外部 SDK 的导入权限更新到新 adapter 路径，而不是删除 import-linter 约束。
- OTel 通用机制归 shared；“某次业务为什么失败”的领域诊断仍归所属业务。
- 密码、凭据和邀请 token 的最小解密边界、脱敏、审计行为不变。

## 八、迁移方式：先建立归属，再逐模块迁移

不建议一次性搬动 861 个实现文件，也不建议把所有旧目录完整复制一份后长期并存。采用可回归、可回滚的纵向切片；每批应包含一个功能的路由、schema、服务、模型注册和调用者修正。

### 阶段 0：建立基线与精确清单

- 先隔离并确认现有未提交改动的归属，不把用户工作区已有删除/重构当作本次迁移成果或自动撤销。
- 为所有 Python 文件（含 `__init__.py`）及动态引用输出 source、target、action、owner、reason、sha256、callers、verification 清单。实现文件数 861 不是总清单行数。
- action 限定为 `move`、`split`、`keep`、`temporary_shim`、`remove_after_proof`；拆分用声明级补充表记录唯一目标。
- 保存规范化 OpenAPI、模型表/约束/索引集合、关键入口和现有失败测试基线。
- 利用现有 `scripts/audit_app_layout.py` 的 AST/依赖扫描能力，更新清单口径；不要新做一套互相冲突的分类规则。

### 阶段 1：准备装配与公共边界

- 明确 `bootstrap` 作为唯一组合根；把业务代码对默认工厂的反向引用改为显式依赖。
- 将真正通用的 core/HTTP/telemetry 内容逐批迁至 shared。必须同时修正依赖路径；未处理区域不强行全改。
- 保留 `backend.app.main:create_app`、发行版命令等公开入口；优先在入口内部委托，减少部署切换风险。
- import-linter 新规则先对已迁移模块生效，历史例外明确登记；不通过禁用全部约束来让迁移变绿。

### 阶段 2：先用用户、邀请、邮件完成试点

- 迁移 `identity/users`、`auth`、`invitations` 和 `messaging/email`；必要的授权依赖随切片调整。
- 拆开混合的 platform/mail 路由和 schema；移动 ORM 类后统一更新模型注册。
- 验证账号登录、重复密码重置、管理员拒绝边界、邀请失败/重发、一次性激活、停用行为和密码脱敏。
- 以试点的实际 import 扩散、测试时长和差异量评估下一批规模，不提前承诺固定总工期。

### 阶段 3：迁移租户与内容资源

- 按工作空间管理、成员、配额、项目、文件、知识、记忆、导入导出等独立切片推进。
- 同时归拢对应的 notifications、audit/costs 等依赖，但不在这一批重写所有治理逻辑。
- 特别检查复合外键、资源所有者、workspace 查询范围、恢复任务和历史文件路径。

### 阶段 4：迁移能力、Agent 与团队

- 先稳定 capabilities、agents 的公开契约，再迁移 teams 调用者。
- 保留 SDK、MCP、Plugin Runtime、运行环境之间的边界。
- Team 的管理与执行分别做回归，不把所有 team runtime 文件机械按名字合入 Runtime 实例模块。

### 阶段 5：迁移编排与运行基础设施

- 处理 `execution` 的 Task、Plan、Orchestration、Run、Approval、触发器。
- 归拢 runtime 资源，建立由 bootstrap 注入的业务 handler registry；先拆依赖环，再移动形成环的节点。
- 验证队列旧 payload、重试/死信、租约、暂停/恢复、重启重建、运行隔离和自托管协议。
- 这是依赖最密集的阶段，不能用一次 import 成功或一次健康检查代替完整流程。

### 阶段 6：收口平台聚合与删除旧壳

- 将 platform/admin 的具体资源管理归还所有者，只留下真正系统级配置和跨域总览。
- 移除已无调用者的旧 api/domains/core/observability 转发壳，检查动态字符串、测试 monkeypatch 和部署配置。
- 同步 AGENTS 路由位置说明、文档、打包和 import-linter。执行全量差异门禁后才宣布目录迁移完成。

各阶段的依赖调整可以有重叠，但每个切片必须只保留一个实现来源；不要同时创建“新目录实现”和“旧目录实现”再同步维护。

## 九、容易漏掉的非 Python 导入点

| 检查位置 | 必须保持的内容 |
| --- | --- |
| `backend/app/bootstrap/models.py` | ORM 模块字符串注册、同一 Base、所有表只注册一次 |
| `backend/migrations/env.py` | metadata 注册入口、配置读取；目录移动本身不应产生新 DDL |
| `backend/migrations/versions` | 历史 revision 链与已部署版本兼容；先查历史导入，禁止重编号或重写已发布迁移语义 |
| `backend/app/main.py` | create_app、依赖覆盖、lifespan、全局 API 前缀 |
| `backend/app/delivery.py` | API/Worker/updater 的动态模块名及发行版自检 |
| `deploy/images/Dockerfile`、`deploy/server/compose.yml`、`deploy/local/compose.yml` | API 与 Worker 启动命令 |
| `scripts/build_standalone.py` 及发行版资源 | Python 包收集、入口、资源路径；发现其他打包声明时一起纳入 |
| `pyproject.toml` | import-linter allowlist、SDK 边界、Ruff 文件模式、mypy 检查范围 |
| `backend/tests` | monkeypatch 字符串、fixture、模型注册与公开导入 |
| 作业与持久数据 | 任务类型、序列化字段、恢复版本；若存有 Python 路径字符串需兼容迁移 |
| `AGENTS.md`、当前有效文档 | 路由族位置与工程规范；旧删除文档不能擅自恢复 |

`pyproject.toml` 当前还提到 `backend.app.api.services`，但本轮工作区未发现该目录。迁移前应核对并修正陈旧约束，而不是把旧约束名单直接当成现有架构。

## 十、验收、风险与回滚

### 10.1 迁移完成的必要条件

1. **分类完整：** 清单覆盖所有源文件；每个 ORM 实体、对外契约、队列 handler 有唯一归属；没有凭文件同名删除实现。
2. **结构约束：** 业务服务不依赖 route/bootstrap，shared 不依赖业务；历史例外收敛且可追踪。
3. **接口兼容：** 规范化 OpenAPI 的路径、方法、参数、schema、权限相关行为不变；operationId 和 schema 名称变化也必须解释/处理，不能默默影响客户端。
4. **数据兼容：** Base metadata 的表、字段、约束与索引集合不因移动而变化；纯目录迁移的 schema 差异应为空。
5. **流程验证：** 登录/邀请、租户隔离、Task→Plan→Approval→Run、Worker 重试恢复、Runtime 隔离和跨边界拒绝按受影响范围运行。
6. **入口验证：** API、Worker、migrate、updater、发行版自检和自托管接入均能使用目标路径。
7. **清理完成：** 精确搜索旧 import、动态路径、文档、部署命令；临时转发层退出，不保留永久双目录。

建议验证工具：Ruff、mypy、import-linter、pytest 聚焦流程测试、OpenAPI/metadata 快照对比、API 与 Worker 启动冒烟。迁移末期再运行整体回归；本次只是方案文档，不执行无关完整测试。

### 10.2 主要风险与处理

| 风险 | 处理 |
| --- | --- |
| 移动管理路由后丢失管理员依赖 | 每个管理 router 显式鉴权，验证普通用户拒绝访问 |
| User/Member 等关系引用破坏 ORM 注册 | 保留显式模型清单，检查 mapper 配置和表集合；不靠隐式 import 顺序 |
| 旧作业重启后找不到 handler | 保持 job type/payload，注册表兼容已有队列任务 |
| 文件搬了但循环依赖仍在 | 提前划定所有者与窄契约，bootstrap 注入具体实现 |
| shared、platform 又成为万能目录 | 设准入规则与导入门禁，业务资源只由一个模块拥有 |
| 路径整理夹带业务重写 | 将事务/权限/运行策略变更拆成独立需求与差异审查 |
| 批量替换误改 SQL 表名、API URL、审计 action | 使用精确模块映射，审查字符串变更；契约快照对比 |
| 回滚涉及已消费作业或持久协议变化 | 本轮禁止修改持久协议；若必须变化，先单独设计双版本读写与回滚策略 |

回滚以迁移批次为单位：恢复该批代码、注册表、依赖约束和部署入口。纯路径调整不应该要求回滚数据库；如果出现数据库回滚需求，说明变更已经超出“目录重新分类”。正式实施时按仓库规则使用已获授权的 feature branch/PR，不在本次方案阶段创建分支或提交。

## 十一、几个明确不建议的方案

- 不建议仅把 `domains` 改名为 `modules`，而路由/schema 仍留在全局平行树下。
- 不建议按管理员菜单建 `admin/users`、普通菜单建 `workspace/users`，再维护两套 User 模型和服务。
- 不建议把所有“Runtime”目录合成一个模块，或把根目录独立 runtime/operator 包并入控制面。
- 不建议所有模块强制五层抽象、十个空文件，或把 SQLAlchemy 全部替换成新仓储框架。
- 不建议把三级当作物理上限，强制砍掉 MCP/Provider 等有意义的子包。
- 不建议自动扫描整个包动态注册路由/模型/handler；优先显式列表，便于权限审查和打包验证。
- 不建议为了目录好看同批修改接口、数据库、菜单和组件，也不建议以减少文件数作为成功指标。

## 十二、核对依据与本次交付状态

### 仓库依据

本文依据当前工作区文件（不是仅凭旧文档）：

- `backend/app/api/router.py`：各接口族总注册。
- `backend/app/api/routes/platform/router.py`：当前 `/admin` 集中鉴权与路由注册。
- `backend/app/main.py`、`bootstrap/models.py`、`bootstrap/resources.py`、`bootstrap/providers.py`：应用与模型装配。
- `backend/app/domains/access/models.py`、`admin.py`、`invitations.py`：用户、授权与邀请归属。
- `backend/app/api/routes/platform/mail.py`、`domains/platform/mail.py`、`core/mail.py`：邮件/邀请边界示例。
- `backend/app/domains/workspace/teams/runtime`、`teams/execution`：团队状态与执行协作。
- `backend/app/runtime/workers/cli.py`、`runner.py`、`runtime/operations/control_plane_service.py`：Worker 和运维聚合。
- `backend/app/observability/notifications/service.py`：通知有独立业务状态，不是日志工具。
- `backend/migrations/env.py`、`backend/app/delivery.py`、`deploy`、`pyproject.toml`、`scripts/audit_app_layout.py`：迁移、部署与架构门禁。
- `runtime/pyproject.toml`、`operator/pyproject.toml`：独立发行包及入口。

### 框架资料的作用

FastAPI 官方《Bigger Applications - Multiple Files》说明了通过 `APIRouter` 组合路由，以及在注册时指定 prefix、tags、dependencies。本文借用的是这种组合能力；官方文档并未替 OpsMesh 指定上述 11 个业务域，这部分是针对本仓库的设计判断。资料地址：`https://fastapi.tiangolo.com/tutorial/bigger-applications/`。

### 状态

- **已完成：** 当前目录/数量扫描、关键依赖和入口核对、本方案写入本地。
- **未执行：** 文件搬迁、业务拆分、import 改写、数据库修改、部署、分支创建与提交。
- **待实施前细化：** 每个文件/声明的完整迁移清单、混合模型拆分、跨模块事务与 handler 契约、历史动态路径兼容。

**建议先确认本分类，再用“用户管理 + 账号邀请 + 邮件设置”做首个纵向迁移试点。目录整理的最终目标是打开一个功能模块就能找到它的接口、规则和数据，而不是把同一个功能在几棵全局目录树里来回寻找。**
