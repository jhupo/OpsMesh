# Python 命名与模块职责

适用于 `src/opsmesh` 和 `operator/opsmesh_operator`。这是仓库的语义约定；Ruff 的大小写检查不能替代职责复核。

- 文件、函数和字段使用 `snake_case`，类使用 `CapWords`，常量使用 `UPPER_SNAKE_CASE`。保留 `__init__.py`、`conftest.py` 等工具入口名称。
- 先表达产品事实，再表达角色：执行过程用 `AgentExecution`，用户配置的执行环境用 `RuntimeEnvironment`，原生容器会话用 `Sandbox`，团队操作状态用 `TeamOperation`。Task 的工作计划用 `TaskPlan`，工作空间文件项目用 `WorkspaceProject`。
- HTTP 输入、输出使用 `Request`、`Response`；业务写入参数使用 `Command`；只读数据使用 `Snapshot`、`Summary`、`View` 或 `Result`，依据数据的实际含义选择。SQLAlchemy 类型使用实体名或 `Record`，Pydantic 基类不能叫 ORM Model。
- `Query`/`QueryService` 只读取数据；`Recorder` 明确记录事实；`Service` 负责业务操作。方法名必须揭示写入、提交、轮换或创建行为，例如 `transaction`、`rotate_credential`、`ensure_member_sessions`。先查后建且没有并发唯一性保障的方法不能宣称 `create_once`。
- `Repository` 必须有真实持久化职责；`Adapter`、`Hooks`、`Activities`、`Workflow` 用于实际协议或执行边界。纯转换、无状态构建直接使用函数，不为统一后缀增加对象、接口和转发层。
- 文件按 feature-first 归属。`routes.py` / `*_routes.py` 放 HTTP 端点，`schemas.py` 放 HTTP 数据合同，`models.py` 放 ORM，`contracts.py` 放跨边界数据，`activities.py` 放 Temporal I/O，`workflow.py` / `workflows.py` 放确定性编排。功能明确时使用 `selection.py`、`authorization.py`、`pool_sizing.py` 等具体名称，避免用 `dependencies.py` 隐藏路由或用 `policy.py` 隐藏配置读写。
- 跨模块导出的名称需要足够语境；局部类、SDK 协议方法和通用技术类型不强制增加产品前缀。已有 `ApprovalPolicyDecision` 与审批风险评审结果不是同一合同，后者使用 `ApprovalRequirementReview`。

Python 名称、API 合同与持久身份分别处理。改类名时同步调用方和生成的 OpenAPI；改数据库字段或持久 JSON 键时提供明确的 Alembic 升降级。Temporal 注册名称、数据库表名、历史 migration revision、SDK hook 方法不随可读性更名。`FileIOLimits` 的现有 JSON 字段和调度配额键沿用已发布合同；它们不需要为缩短类名而迁移。

`RuntimeSpace.storage_metadata` 与 `cleanup_metadata` 是描述性数据，不提供自动存储、清理或归档能力。删除未接入执行的 feature flags 展示，不能用名称暗示不存在的控制能力。
