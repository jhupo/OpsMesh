# 重构实施记录

本记录区分已验证的功能点和待实施方案，不以移动文件数量宣布完成。

| 阶段 | 功能点 | 状态 | 验证 |
| --- | --- | --- | --- |
| R0 | 全量结构清单与处置方案 | 已完成 | 1,029 个源文件的审查基线与处置表 |
| R1 | 显式 ORM 注册，解除数据库层反向加载业务模型 | 已完成 | 模型注册、数据库模型、健康检查和任务转移相关测试通过；新增独立进程边界检查 |
| R1 | 访问域与 HTTP 依赖边界 | 已完成 | 用户/令牌/权限迁至 `domains/access`；FastAPI 认证、平台管理员和队列依赖迁至 `api/dependencies`；认证、授权和架构门禁通过 |
| R1 | 平台控制面与外部集成归属 | 已完成 | 管理策略、发布、更新迁至 `domains/platform`；Webhook 迁至 `domains/integrations/webhooks`；模型注册、动态入口和相关导入已更新 |
| R2 | API 路由/schema 清理 | 进行中 | 已删除转发文件、tasks 聚合文件、公共模型和通知转发文件；混合 schema 不再导出领域合同，管理路由也不再使用只改类名的空继承包装，路由、测试和 E2E 脚本直接引用所属模块。任务转移、通知租户隔离与脱敏、任务领域视图、管理队列/Worker/Runtime 三个功能场景通过；剩余路由归属和 OpenAPI 对照仍待完成 |
| R3 | Agent/provider/session/mailbox/tools/MCP | 进行中 | Mailbox 命令、校验、读取、收件箱和汇总已分别收敛到 `domains/agents/messages/service.py` 与 `queries.py`，删除 5 个唯一调用的 Mixin 碎片；工具组合模块已按功能收敛为 `tools/{files,mailbox,memory,normalization,events,service}.py`；Provider catalog 的 capability/metadata/model_api/policy/view 已扁平到 `domains/agents/providers` 并删除空 catalog 包；持久会话已迁移到 `domains/agents/sessions`，ORM、仓储、管理和视图的导入已全部更新，旧 `runtime/sessions` 空包已清理。MCP 的 `catalog/execution/transport` 是实际协议与生命周期边界，按最终方案保留，不做机械合并；删除了无生产调用方的 SSE 手工解析，远程传输继续使用 SDK/客户端路径，保留 stdio 合同封包和结果校验。Mailbox、工具、Provider、会话、MCP 相关场景通过 |
| R4 | 任务/Run/工作流职责 | 已完成 | 计划模板已提升到 `workflows/templates`；任务控制与原 operations 已收敛到 `tasks/control`，任务管理与交接归入 `tasks/collaboration`，执行诊断/载荷归入 `tasks/observation`，任务转移及脱敏交接包拆到 `tasks/collaboration/{transfers,transfer_package}.py`，任务 CRUD、Task 状态、Step 状态分别归入 `tasks/service.py`、`tasks/state.py`、`tasks/steps.py`，Run 状态合同并入 `runs/state.py`，计划变更入口改为 `planning/mutation.py`，纯图操作和 Step 投影分别抽到 `mutation_operations.py`、`mutation_materialization.py`，工作流图操作与持久化引用校验分别归入 `definitions/graph.py`、`definitions/validation.py`，旧 `tasks/operations`、`management`、`execution`、`status.py`、`step_service.py`、`step_status.py`、`runs/status.py` 及 `future_plan_mutation.py` 已删除。模板生成、成员匹配、条件校验、Step 物化、计划变更、转移和 Run 生命周期场景级验证通过。 |
| R5 | Workspace/team/project/archive/data lifecycle | 已完成 | Workspace 原 `domains` 子包已按方案归并为 `extensions`；全量归档与导入导出已从 `projects/{exports,imports}` 收敛到 `data_transfer`，其中归档/仓储/序列化归入根模块，导入实现归入 `data_transfer/importers`；Workspace 生命周期已从 `tenants/lifecycle` 收敛到 `data_lifecycle`，动作、诊断、调度分别合并为稳定边界模块，旧嵌套目录与空包已删除。团队执行入口、人工控制台、项目 dashboard、运行时绑定、组织上下文和团队主服务已分别收敛到 `teams/{execution,operations,projects,runtime,organization}`，根目录仅保留模型与主服务。项目、归档导入导出、运行 I/O、生命周期调度、配额、团队控制台和工作区 API 场景通过。 |
| R6 | 容器池/后端/Worker/recovery | 待实施 | 保留既有未提交租约和队列工作 |
| R7 | 观测与成本边界 | 进行中 | 观测实现已按最终目录归入 `observability/{audit,costs,notifications,telemetry}`，模型注册和所有生产/测试引用已切换；成本、通知、审计、OpenTelemetry 相关功能测试通过。后续继续核对观测服务内部职责和运行时聚合边界 |
| R8 | 入口核对、旧引用和残留清理 | 进行中 | 已完成 `runtime/workers/execution` 层压平，处理器归入 `runtime/workers/handlers`；Agent SDK 适配器去掉无职责的 `runtime/providers` 包壳，直接归入 `runtime/openai` 与 `runtime/claude`；旧导入路径、空包和旧目录门禁已清理。仍需继续核对其他跨领域入口与历史快照引用。 |

原始审查文件为实施前快照，不随代码迁移覆写。`audit_app_layout.py --check` 用于检查原始快照，
重构后出现源文件差异是预期结果，不能用它代替当前阶段测试。
