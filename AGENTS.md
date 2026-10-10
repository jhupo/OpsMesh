# OpsMesh Repository Instructions

OpsMesh 是企业级 Agent 控制面。后端负责身份、工作空间、Agent、编排、能力、隔离 Runtime、审批、审计和运维；前端通过稳定 API 合同访问这些能力。以当前代码、schema、migration 和受影响产品流程的验证结果为准。

## 后端结构与请求任务流

- 默认 API 前缀为 `/api/v1`，由 `OPSMESH_API_PREFIX` 覆盖。总路由入口为 `backend/app/bootstrap/routers.py`，平台管理员入口为 `backend/app/bootstrap/platform_routes.py`。
- 保持 feature-first 结构：identity、workspaces、agents、teams、capabilities、resources、orchestration、runtime、platform、messaging、governance、shared 和 bootstrap。功能模块就近放置 routes/schema/service/model，由 bootstrap 显式注册；不要改成全局按技术层分目录。
- 依赖方向为 `routes -> service/domain -> infrastructure`。Route 负责 HTTP 参数、分页、响应和错误映射；业务策略、授权、持久化和长流程由服务层负责。模块依赖遵守 `pyproject.toml` 的 import-linter 合同。

请求与执行遵循以下流程：

1. 认证依赖解析用户、Token 和当前工作空间；服务校验租户范围、角色、资源状态、配额、能力和审批要求。
2. 短请求在 PostgreSQL 事务中变更状态；长任务持久化意图、初始状态和事务事件，经队列投递后返回 Task 或 Run 标识。
3. Worker 领取后重新检查工作空间、授权、配额、冻结配置和当前能力状态，不能信任请求阶段的临时结果。
4. Agent SDK 和工具只在批准的 isolated、pooled 或 persistent Runtime 中执行；API 和 Worker 主进程不能运行用户或 Agent 控制的代码。
   - Runtime 宿主与任务/MCP 进程的生命周期必须分开。共享宿主按工作空间、可信权限范围和网络策略分组，以持久执行槽限制并发；禁止按 Agent 或 MCP 数量自动创建容器。
   - 取消、重启和回收只作用于对应进程组与私有目录；禁止清空共享宿主的进程或临时目录。容量等待不得视为执行超时，审批暂停必须释放共享执行槽。
5. Run、Task、事件、审计、成本和 Artifact 写回 PostgreSQL；前端通过查询或流式接口读取状态与结果。失败进入明确的重试、暂停、取消或恢复状态，并留下审计证据。

## 后端技能任务流

技能位于 `.agents/skills`。按任务触发技能，先读对应 `SKILL.md`，再按其要求读取相关 `references`；不要只按名称推断用法。上游来源、固定版本和文件范围见 `.agents/skills/README.md`。

| 技能 | 何时使用 | 用法与输出 |
| --- | --- | --- |
| `architecture-patterns` | 新增后端模块，调整服务边界、事务职责或依赖方向，排查循环依赖 | 先读现有调用链和架构合同，确定模块归属、接口、事务与事件投递职责；说明每个新增抽象解决的具体问题。 |
| `python-design-patterns` | 编写或重构 Python 服务，处理巨型函数、职责混杂、重复逻辑、继承或封装 | 按 KISS、单一职责和组合优先选择最小实现；复用现有 SDK 与公共接口，先清理死代码，避免过早抽象和无必要包装。 |
| `python-testing-patterns` | 行为变更、缺陷修复，设计 API、数据库、队列或 Runtime 验证 | 先定义可观察验收条件，再选择受影响产品流程和必要测试；设计 fixture、参数化、隔离与清理，覆盖相关拒绝、幂等、重试和恢复路径。 |
| `python-code-review` | Python 变更复核、PR 审查、架构质量评估或重构规划 | 阅读 diff、调用方和相关合同，检查正确性、竞态、事务、授权、安全、错误处理、超时与资源释放；报告按严重程度排序的具体问题、位置、危害和修复建议。 |
| `verification-before-completion` | 准备宣称完成、修复成功、检查通过，或提交代码与创建 PR 前 | 对最终变更运行相关验证，读取输出与退出码，对照验收条件；报告证据、覆盖范围和未验证项，不能用 lint 或测试数量代替产品验收。 |

普通后端任务按“确认边界 → 实现 → 流程验证 → 代码复核 → 完成验收”推进，只触发相关技能。仅文档或技能安装不运行无关后端测试。

### 后端技能适配规则

- 仓库规范和现有代码合同优先于技能的通用示例。保留 feature-first、Python 版本、uv、Ruff、strict mypy、pytest 和架构检查配置；不照搬示例目录、行宽、依赖或覆盖率阈值。
- Clean Architecture、DDD、Repository、Protocol 和依赖注入按具体边界使用；不强制每层增加接口、UseCase 类或独立领域/ORM 模型，不以模式名称作为扩大重构的理由。
- 通用 TDD、逐层单测和 mock 建议不能覆盖产品流程验证要求。只对跨边界拒绝、租户隔离、幂等、重试、恢复和脱敏保留测试；不为机械覆盖率或实现细节新增测试。
- PostgreSQL 事务、锁、并发或队列交互使用相关真实基础设施验证；SQLite、内存 Repository 和 mock 不能证明 PostgreSQL 或完整任务流正确。
- 复核覆盖受影响的完整调用链：API、服务、事务事件、队列、Worker、Runtime 与结果回写；检查工作空间隔离、领取时重新授权、取消、续接、审批恢复、幂等和密钥脱敏。
- 静态检查、代码复核与运行验收分别提供证据。检查通过只证明其覆盖范围；最后一次相关修改后的结果才能支持完成声明。

## 前端技能任务流

| 技能 | 何时使用 | 用法 |
| --- | --- | --- |
| `frontend-design` | 新建 UI 或调整布局、视觉层级、字体、间距、响应式行为 | 先确定页面目标、信息层级和布局；不替代组件库，不增加未经需求授权的页面或路由。 |
| `radix-colors` | 配色、状态色或明暗主题变更 | 选择 Radix 色阶并映射到现有 shadcn 语义变量，不把原始颜色散落在组件中。 |
| `shadcn` | 新增、组合、修复或调整 UI 控件 | 先查询并复用现有组件，遵循 `frontend/components.json` 和 Lucide 图标；不引入第二套运行时组件库或手写替代控件。 |
| `web-design-guidelines` | UI 完成后的可访问性、交互与视觉验收 | 检查真实页面的键盘操作、焦点、主题、桌面和移动布局，遵守下面的截图规则。 |
| `markdown-badges` | README 或 Markdown 徽章变更 | 仅用于 Markdown，不用于 Web UI 或引入运行时图片组件。 |

UI 任务依次进行设计、按需配色、组件复用、API 接入和真实页面验收。请求封装放在 `frontend/src/api`，产品组合放在 `frontend/src/features`，路由保持轻量，用户可见文案放在 `frontend/src/i18n`。

### UI 真实截图验收

- 任何布局、样式、视觉层级、字体、间距、颜色、图标、响应式或组件组合改动，都必须在真实运行页面逐个检查受影响组件；Story、静态 HTML、源码、lint、类型检查和构建不能代替验收。
- 每个受影响组件截图默认状态及本次涉及的弹窗、菜单、展开、选择、加载、空态、错误态或交互后状态。一张截图仅在组件布局清晰可辨时可覆盖多个组件。
- 每个组件至少检查桌面和移动宽度；涉及主题、颜色或阴影时还要检查浅色和深色主题。检查溢出、遮挡、错位、截断、留白、滚动、层级、焦点、文案和点击区域。
- 执行者实际打开并逐张查看截图；发现问题后修复、重新截图并查看。只有截图文件存在不算验证。
- 完成报告列出已截图检查的组件、视口、主题和状态，区分已验证与未验证项。缺少任何受影响组件的真实截图证据时，不得宣称 UI 改动完成。
- 页面标题和主内容区域不添加解释功能、用途或操作方式的可见段落；只保留必要标题、字段、状态、动作、空态和错误信息。弹窗或组件的无障碍描述使用 `sr-only`。

## 工程边界

- 修改覆盖受影响的完整调用链、状态流、合同、配置、文档和验证，删除被替代实现；禁止局部绕过、缩短轮询掩盖根因、兼容分支、双轨实现和静默降级。
- 正常请求执行与结果回写由事务事件和任务队列驱动；定时维护只恢复丢失投递、过期租约等，不得成为 Chat 等交互流程启动或完成的必经路径。
- SDK 优先：实现 Agent 循环、历史、压缩、工具、MCP、handoff、审批恢复和记忆前，核对锁定版本的官方 SDK 与文档，直接复用已有能力；不复制内部状态机。
- Session 直接使用原生 Session，不隐式增加压缩包装、阈值或额外模型请求。日志只观察 SDK 行为，不启用可选能力。
- 自定义代码限于 SDK 未覆盖的产品职责，通过公共扩展接口接入。保留适配器须说明具体缺口，“统一封装”或“沿用旧表”不构成理由。
- SDK 替换清理旧实现、调用方、合同、配置、模型和测试，不保留双重历史来源；持久数据使用明确 Alembic 升降级迁移，验证租户隔离、续接、取消、审批恢复和工具结果。
- 工作空间是租户边界；所有工作空间资源的 API、查询、缓存、Worker、存储和工具调用均带工作空间范围。
- PostgreSQL 是持久事实来源；Redis 只保存队列、锁、发布订阅和短期派生状态，不保存唯一业务状态。
- 密钥只在最窄执行边界解密，禁止进入日志、响应、事件、缓存或完整授权 URL；风险操作默认拒绝，按需创建审批、审计或安全事件。
- 数据库变更使用 Alembic 并保留升级和降级路径；文档描述当前实现，不把计划或旧路线图写成已实现能力。
- 提交使用 feature branch 和 Pull Request；完成状态区分 `implemented`、`verified`、`unverified` 和 `blocked`。

## 验证命令

以后端 CI 和 `pyproject.toml` 为准，按变更范围运行：

```bash
uv sync --frozen --all-groups
uv run ruff check .
uv run lint-imports --no-cache
uv run python scripts/audit_app_layout.py --architecture-check
uv run mypy
uv run pytest backend/tests/<受影响的流程测试文件>.py
```

`backend/tests/test_health.py` 只验证健康接口，不能作为通用后端验收。事务、锁和并发场景按相关测试要求配置真实 PostgreSQL；外部 SDK 和长时间运行验证按现有 opt-in marker 执行，不将跳过项报告为已验证。

前端：

```bash
cd frontend
pnpm lint
pnpm typecheck
pnpm build
```

功能变更还需受影响产品流程的运行证据；仅文档或组织调整不重复运行无关完整测试套件。
