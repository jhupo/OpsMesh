# 重构实施记录

本记录区分已验证的功能点和待实施方案，不以移动文件数量宣布完成。

| 阶段 | 功能点 | 状态 | 验证 |
| --- | --- | --- | --- |
| R0 | 全量结构清单与处置方案 | 已完成 | 1,029 个源文件的审查基线与处置表 |
| R1 | 显式 ORM 注册，解除数据库层反向加载业务模型 | 已完成 | 模型注册、数据库模型、健康检查和任务转移相关测试通过；新增独立进程边界检查 |
| R1 | 访问域与 HTTP 依赖边界 | 已完成 | 用户/令牌/权限迁至 `domains/access`；FastAPI 认证、平台管理员和队列依赖迁至 `api/dependencies`；认证、授权和架构门禁通过 |
| R1 | 平台控制面与外部集成归属 | 已完成 | 管理策略、发布、更新迁至 `domains/platform`；Webhook 迁至 `domains/integrations/webhooks`；模型注册、动态入口和相关导入已更新 |
| R2 | API 路由/schema 清理 | 已完成 | 转发 schema、空继承响应和旧管理路由已清理；传输 DTO、领域合同和 Runtime 运营合同直接引用所属模块。任务、通知、平台队列/Worker/Runtime 与路由装配场景通过。 |
| R3 | Agent/provider/session/mailbox/tools/MCP | 已完成 | Provider、会话、Mailbox、产品工具与 MCP 归属完成；产品工具由文件/记忆/邮箱处理器显式组合，不再依赖多继承 Mixin 和共享隐式属性；两厂商、会话、工具授权、MCP 相关场景通过。 |
| R4 | 任务/Run/工作流职责 | 已完成 | 计划模板已提升到 `workflows/templates`；任务控制与原 operations 已收敛到 `tasks/control`，任务管理与交接归入 `tasks/collaboration`，执行诊断/载荷归入 `tasks/observation`，任务转移及脱敏交接包拆到 `tasks/collaboration/{transfers,transfer_package}.py`，任务 CRUD、Task 状态、Step 状态分别归入 `tasks/service.py`、`tasks/state.py`、`tasks/steps.py`，Run 状态合同并入 `runs/state.py`，计划变更入口改为 `planning/mutation.py`，纯图操作和 Step 投影分别抽到 `mutation_operations.py`、`mutation_materialization.py`，工作流图操作与持久化引用校验分别归入 `definitions/graph.py`、`definitions/validation.py`，旧 `tasks/operations`、`management`、`execution`、`status.py`、`step_service.py`、`step_status.py`、`runs/status.py` 及 `future_plan_mutation.py` 已删除。模板生成、成员匹配、条件校验、Step 物化、计划变更、转移和 Run 生命周期场景级验证通过。 |
| R5 | Workspace/team/project/archive/data lifecycle | 已完成 | Workspace 原 `domains` 子包已按方案归并为 `extensions`；全量归档与导入导出已从 `projects/{exports,imports}` 收敛到 `data_transfer`，其中归档/仓储/序列化归入根模块，导入实现归入 `data_transfer/importers`；Workspace 生命周期已从 `tenants/lifecycle` 收敛到 `data_lifecycle`，动作、诊断、调度分别合并为稳定边界模块，旧嵌套目录与空包已删除。团队执行入口、人工控制台、项目 dashboard、运行时绑定、组织上下文和团队主服务已分别收敛到 `teams/{execution,operations,projects,runtime,organization}`，根目录仅保留模型与主服务。项目、归档导入导出、运行 I/O、生命周期调度、配额、团队控制台和工作区 API 场景通过。 |
| R6 | 容器池/后端/Worker/recovery | 已完成 | 四种执行模式、Docker 后端/池、租约与 fencing token、恢复和 Worker 处理器归属已收口；维护调度接入 Worker 主循环，健康快照计数进入心跳与运行汇总；相关 Runtime、队列、Worker 和自托管场景通过。 |
| R7 | 观测与成本边界 | 已完成 | 日志、指标、trace、审计链、成本定价/预算和通知已归入 `observability` 的真实子功能；模型注册及生产引用完成切换，脱敏、精度、权限和链完整性相关场景通过。 |
| R8 | 入口核对、旧引用和残留清理 | 已完成 | 原孤立 Task 状态/记录/恢复、Team dashboard、Workspace health 已接入 API；FeatureFlag 决策接入平台配置；重复的 RuntimeTool/RuntimeFile 路径明确退役。目标检查为 810/810 个实现文件、125/125 个包目录、0 漏项、0 旧路径。 |

原始审查清单保留实施前哈希；处置目标与最终实现已同步更新。
`audit_app_layout.py --check` 只校验原始快照，`audit_app_layout.py --target-check` 校验最终目录和逐文件处置目标；两者都不能替代相关功能测试。

R8 最终核对已覆盖 API、Worker、delivery 和 updater 入口、Alembic 显式模型注册，以及全部测试模块的收集。核对中发现的旧 `core.db.models`、`core.common.config` 和 `bootstrap.runtime` 导入已直接迁移到当前所有者，没有增加兼容导出。
