# OpsMesh Repository Instructions

OpsMesh 是企业级 Agent 控制面。后端负责身份、工作空间、Agent、编排、能力、隔离 Runtime、审批、审计和运维；独立客户端项目通过稳定 API 合同访问这些能力；本仓库不包含前端。以当前代码、schema、migration 和受影响产品流程的验证结果为准。

## 后端结构与请求任务流

运行包为 `src/opsmesh`，统一使用 `opsmesh.*` 导入。测试位于 `tests`，Alembic 迁移位于 `migrations`。禁止恢复旧包名或路径兼容层。详细接口参考由当前应用路由和 schema 生成，见 `docs/api.md`；接口变更必须重新导出并通过一致性检查。

- 默认 API 前缀为 `/api/v1`，由 `OPSMESH_API_PREFIX` 覆盖。总路由入口为 `src/opsmesh/bootstrap/routers.py`，平台管理员入口为 `src/opsmesh/bootstrap/platform_routes.py`。
- 保持 feature-first 结构：identity、workspaces、agents、teams、capabilities、resources、orchestration、runtime、platform、messaging、governance、shared 和 bootstrap。功能模块就近放置 routes/schema/service/model，由 bootstrap 显式注册；不要改成全局按技术层分目录。
- 依赖方向为 `routes -> service/domain -> infrastructure`。Route 负责 HTTP 参数、分页、响应和错误映射；业务策略、授权、持久化和长流程由服务层负责。模块依赖遵守 `pyproject.toml` 的 import-linter 合同。

请求与执行遵循以下流程：

1. 认证依赖解析用户、Token 和当前工作空间；服务校验租户范围、角色、资源状态、配额、能力和审批要求。
2. 短请求在 PostgreSQL 事务中变更状态；长任务在同一事务中持久化业务状态和 Temporal 投递意图，提交后立即返回 Task、Turn 或 Run 标识。PG 通知驱动 Outbox 投递，HTTP 请求不等待模型或批量发布其他任务。
3. Temporal 是唯一持久执行引擎。Workflow 只编排确定性的状态和等待；数据库、模型、工具及外部 I/O 在 Activity 中执行。Activity 重新检查工作空间、授权、冻结配置和当前能力，不能信任请求阶段的临时结果。
4. 直接使用官方 Temporal OpenAI Agents 集成及 SDK 公共接口；模型 Activity 在 Worker 中执行，用户代码通过原生 SandboxClient 执行，API/Worker 不直接运行用户控制的代码。
   - Runtime 是用户配置的执行环境和原生沙箱会话引用，不是另一套任务调度或 RPC 服务。删除旧宿主池、UID 槽、自研租约、恢复扫描和网络网关，不保留兼容实现。
   - 沙箱按用户配置及后端原生会话合同创建、续接和释放，MCP 生命周期独立于 Agent；不能按每次 Run 或每个 Agent 自动创建容器。原生 SDK 未提供的通信能力，须验证具体缺口后使用成熟组件。
   - 同一会话的 Turn 串行，不同会话并发。取消只作用于对应 Workflow、Activity 和沙箱会话，审批由 Workflow 持久等待且不占用 Activity 槽。排队时间不计入执行超时。
   - Workflow 参数及历史仅携带已授权的业务引用；凭据在 Activity 最窄边界解密。业务敏感载荷使用 Temporal payload codec；未知结果的外部写入不能自动重试。
5. Run、Task、事件、审计、成本和 Artifact 写回 PostgreSQL；客户端通过查询或流式接口读取状态与结果。失败进入明确的重试、暂停、取消或恢复状态，并留下审计证据。

## 后端技能任务流

技能位于 `.agents/skills`。按任务触发技能，先读对应 `SKILL.md`，再按其要求读取相关 `references`；不要只按名称推断用法。上游来源、固定版本和文件范围见 `.agents/skills/README.md`。

本仓库仅保留后端技能与文档徽章技能，不维护 UI 技能、Node 项目或页面验收规则。独立客户端只依赖已发布 API 合同，不依赖 Python 包内部实现。

| 技能 | 何时使用 | 用法与输出 |
| --- | --- | --- |
| `architecture-patterns` | 明确需要 Clean Architecture、六边形架构或 DDD，或排查复杂依赖方向与循环依赖 | 按实际边界参考具体模式；先读现有调用链和架构合同，说明新增抽象解决的具体问题。普通模块设计使用 `codebase-design`。 |
| `temporal-python-sdk` | 修改 Workflow、Activity、Worker、恢复、取消、SDK 或沙箱集成 | 先读 `.agents/skills/temporal-python-sdk/SKILL.md` 和相关官方 SDK 文档，按 `uv.lock` 核对公共 API；直接使用 Temporal 官方 AI 集成，验证真实重放和受影响产品流程。 |
| `codebase-design` | 设计或重构 Python 模块接口、职责、组合与封装，判断抽象是否必要 | 先读调用方和合同，应用删除测试；用小接口隐藏真实复杂度，复用 SDK，删除空壳转发层。 |
| `improve-codebase-architecture` | 明确要求架构评估，或处理跨模块耦合、重复策略与职责不清 | 结合 `codebase-design` 追踪完整调用链，给出具体问题、最小改法、取舍和流程验收；按需要调用，不自动生成报告或扩大重构。 |
| `code-simplifier` | 定向清理 Python 命名、控制流、冗余状态和无效包装，或实现后的可读性复核 | 读取修改及调用方，在授权范围内保持行为与事务边界，使用具体业务命名；验证受影响合同。 |
| `python-testing-patterns` | 行为变更、缺陷修复，设计 API、数据库、队列或 Runtime 验证 | 先定义可观察验收条件，再选择受影响产品流程和必要测试；设计 fixture、参数化、隔离与清理，覆盖相关拒绝、幂等、重试和恢复路径。 |
| `request-refactor-plan` | 请求重构方案、RFC 或分步实施计划 | 核对实际代码、合同和测试，明确模块职责、接口、迁移及验收；默认在聊天中给出方案，仅按用户要求生成文档或发布 issue。 |
| `verification-before-completion` | 准备宣称完成、修复成功、检查通过，或提交代码与创建 PR 前 | 对最终变更运行相关验证，读取输出与退出码，对照验收条件；报告证据、覆盖范围和未验证项，不能用 lint 或测试数量代替产品验收。 |

普通后端任务按“确认边界 → 实现 → 流程验证 → 代码复核 → 完成验收”推进，只触发相关技能。仅文档或技能安装不运行无关后端测试。

### 后端技能适配规则

- 仓库规范和现有代码合同优先于技能的通用示例。保留 feature-first、Python 版本、uv、Ruff、strict mypy、pytest 和架构检查配置；不照搬示例目录、行宽、依赖或覆盖率阈值。
- 行宽保持 `pyproject.toml` 的 100 字符，格式及 import 排序使用现有 Ruff，类型检查使用 strict mypy。`codebase-design` 管模块职责与接口，`improve-codebase-architecture` 管按需架构评估，`code-simplifier` 管局部命名与简化，`request-refactor-plan` 管重构方案；正确性复核仍需检查 diff、调用链、事务、授权和资源释放，不能由计划代替。
- 函数超过约 40 行只触发可读性复核，不能机械拆分事务、Workflow、资源释放或完整测试场景；不为风格增加包装类或 getter/setter。SDK 覆写、序列化字段、ORM 和迁移名称按具体合同判断例外，不为命名机械改动外部合同。风格报告须区分确认问题、工程适配和待复核候选，Ruff 通过只证明其配置覆盖的规则。
- Clean Architecture、DDD、Repository、Protocol 和依赖注入按具体边界使用；不强制每层增加接口、UseCase 类或独立领域/ORM 模型，不以模式名称作为扩大重构的理由。
- 通用 TDD、逐层单测和 mock 建议不能覆盖产品流程验证要求。只对跨边界拒绝、租户隔离、幂等、重试、恢复和脱敏保留测试；不为机械覆盖率或实现细节新增测试。
- PostgreSQL 事务、锁、并发或队列交互使用相关真实基础设施验证；SQLite、内存 Repository 和 mock 不能证明 PostgreSQL 或完整任务流正确。
- 复核覆盖受影响的完整调用链：API、服务、事务事件、队列、Worker、Runtime 与结果回写；检查工作空间隔离、领取时重新授权、取消、续接、审批恢复、幂等和密钥脱敏。
- 静态检查、代码复核与运行验收分别提供证据。检查通过只证明其覆盖范围；最后一次相关修改后的结果才能支持完成声明。

## 工程边界

- 修改覆盖受影响的完整调用链、状态流、合同、配置、文档和验证，删除被替代实现；禁止局部绕过、缩短轮询掩盖根因、兼容分支、双轨实现和静默降级。
- 正常请求执行与结果回写由事务事件和 Temporal 驱动；定时维护只补偿丢失的投递，不得成为 Chat 启动、回复、审批恢复或 Automation 执行的必经路径。定时编排使用 Temporal Schedule，入口插件复用公共 messaging 合同。
- SDK 优先：实现 Agent 循环、历史、压缩、工具、MCP、handoff、审批恢复和记忆前，核对锁定版本的官方 SDK 与文档，直接复用已有能力；不复制内部状态机。
- Temporal 集成优先：当前仅集成 OpenAI Agents SDK，通过官方 `temporalio-openai-agents` 插件、工具/MCP/沙箱公共接口接入。未来新增 AI 框架也先查 Temporal 官方集成，禁止另建 SDK 执行器。官方文档入口与版本边界见 `temporal-python-sdk` 技能；Workflow 确定性沙盒与执行用户代码的 SDK Docker 沙箱必须区分。
- Session 直接使用原生 Session，不隐式增加压缩包装、阈值或额外模型请求。日志只观察 SDK 行为，不启用可选能力。
- 自定义代码限于 SDK 未覆盖的产品职责，通过公共扩展接口接入。保留适配器须说明具体缺口，“统一封装”或“沿用旧表”不构成理由。
- SDK 替换清理旧实现、调用方、合同、配置、模型和测试，不保留双重历史来源；持久数据使用明确 Alembic 升降级迁移，验证租户隔离、续接、取消、审批恢复和工具结果。
- 工作空间是租户边界；所有工作空间资源的 API、查询、缓存、Worker、存储和工具调用均带工作空间范围。
- PostgreSQL 是业务事实来源；Temporal 持久保存执行历史和调度状态；Redis 只保存发布订阅及短期派生状态，不保存唯一业务状态，不再承载另一套任务队列。
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
uv run python scripts/export_api_docs.py --check
uv run pytest tests/<受影响的流程测试文件>.py
```

`tests/test_health.py` 只验证健康接口，不能作为通用后端验收。事务、锁和并发场景按相关测试要求配置真实 PostgreSQL；外部 SDK 和长时间运行验证按现有 opt-in marker 执行，不将跳过项报告为已验证。


功能变更还需受影响产品流程的运行证据；仅文档或组织调整不重复运行无关完整测试套件。
