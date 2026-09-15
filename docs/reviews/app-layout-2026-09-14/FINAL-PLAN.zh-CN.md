# app 全量结构审查与最终重构方案

日期：2026-09-14。状态：方案已形成；R1 的显式模型注册、访问域、平台控制面与外部集成边界已落地，后续批次仍按实施表推进。

## 1. 审查范围与结论边界

本方案以当前工作区代码为依据，没有使用已有架构设计文档作为目录设计依据。
仓库开发约束仍然适用。审查基线 HEAD 为 `e9aabf1e29bb0bb8fe7ce8c7b2b1178fcf4d47f1`，
同时包含工作区中尚未提交的 Worker、租约、队列恢复修改，不把它们视为已验证完成的功能。

覆盖范围是 `backend/app` 下全部非缓存源码文件：

| 范围 | Python 文件 | 实现文件 | 包目录，含该层目录 |
| --- | ---: | ---: | ---: |
| api | 181 | 159 | 22 |
| core | 85 | 71 | 14 |
| domains | 567 | 495 | 72 |
| runtime | 182 | 156 | 26 |
| observability | 11 | 10 | 1 |
| app 根目录文件 | 3 | 2 | 1 |
| 合计 | 1,029 | 893 | 136 |

全部源码共 138,694 行；136 个 `__init__.py` 单独纳入处置。没有发现非 Python 的非缓存文件。
Grimp 的 `backend` 图包含 1,030 个模块、4,678 条导入边；图包含类型检查导入，不能把它的候选断边数量当作运行时循环依赖数量。

审查方式必须说清楚：

- 每个文件都读取了完整源码，进行了 AST 结构、定义、导入、调用表达式、事务调用和内容哈希分析。
- 人工逐项核对了全部实现文件的声明与职责摘要，包入口也纳入检查；对高风险边界、疑似重复和碎片化实现进一步阅读了源代码。
- **这不是对 138,694 行源码逐行人工认证，也不是全量功能、安全、并发测试。** 静态无调用方不等于可以删除；结构合理不等于功能已经接通。
- 本轮没有运行全量测试，没有修改 app 实现、数据库迁移或已有待提交功能，没有提交、推送。

证据文件：

- `source-inventory.json`：全部文件的 SHA-256、声明、实际依赖、调用方、私有导入、事务位置。
- `declaration-review.txt`：按包排列的全部 893 个实现文件声明摘要。
- `file-dispositions.csv`：1,029 行逐文件处置表，包含目标路径、保留/移动/合并/拆分决定、阶段及理由。
- `disposition-rules.json`：处置依据，不是迁移脚本，不可作为直接拼接源码的操作指令。
- `target-footprint.json`：目标文件与来源关系，用于检查计划遗漏和多对一归并。

## 2. 最终判断

当前 app 根目录实际上只有 `api`、`core`、`domains`、`runtime`、`observability` 五个源码目录。
所以问题已经不是“app 根目录堆了几十个目录”，也不应再简单压缩根目录数量。

真正的问题是以下情况同时存在：

1. 基础设施目录中放了业务、HTTP 依赖和跨模块装配逻辑。
2. 同一个业务功能被分成很多薄包装、Mixin、字典转换和私有辅助函数文件。
3. 少数大文件仍然同时负责策略、事务、资源生命周期和结果拼装。
4. 持久化模型、SDK 会话实现、读模型、后台控制命令存在归属混杂。
5. 部分功能只有类和测试或只有定义，没有找到生产入口，不能据此宣布功能完整。

因此采用：**模块化单体，按功能聚合，在真实的协议、状态和生命周期边界上分层。**
不是全盘重写，不增加微服务，不引入通用工作流框架、依赖注入容器或插件平台，也不把所有模块改成固定的四层模板。

## 3. 有代码依据的问题

### 3.1 core 不是纯基础设施

`core/auth/dependencies.py` 使用 FastAPI 的 Request、Header、Depends，并执行认证失败的 HTTP 处理；该模块现已迁移为 `api/dependencies/auth.py`。
`core/redis/dependencies.py` 同样读取 HTTP 请求。
`core/admin` 管理 Worker、Runtime、Workspace 和更新任务，Webhook 则拥有订阅、投递、重放与调度。
这些都不是“公共工具”。

更关键的是，数据库会话模块直接导入模型注册汇总，使创建底层数据库依赖时加载整个业务模型图：
[core/db/session.py](C:/Users/Administrator/Desktop/github/OpsMesh/backend/app/core/db/session.py:9)。

决定：认证和用户归 `domains/access`；平台管理归 `domains/platform`；Webhook 归 `domains/integrations/webhooks`；
HTTP 依赖归 `api/dependencies`；ORM 注册归显式启动装配。加密、脱敏、数据库连接、Redis 等才留在 core。
当前队列 HTTP 依赖位于 `api/dependencies/workers.py`，平台管理员依赖位于 `api/dependencies/admin.py`。

### 3.2 转发文件和必要的注册入口被混在一起

完整 AST 检查发现 25 个只含导入/导出的实现文件。其中 23 个位于 API schema，1 个为 Webhook service，
另 1 个是 ORM 模型注册文件。

决定：前 24 个在调用方直接引用所属合同后移除；ORM 注册保留其必要职责并迁往 bootstrap，不能当作无用兼容文件删除。
真正具有 HTTP 校验、字段脱敏、序列化、响应形状的 schema 保留。

### 3.3 邮箱和产品工具靠共享隐式状态拼装

[消息服务](C:/Users/Administrator/Desktop/github/OpsMesh/backend/app/domains/agents/messages/service.py:18)
继承六个 Mixin，成员共同依赖 `_session`、`_page`、`_require_thread` 等隐式接口。
[产品工具服务](C:/Users/Administrator/Desktop/github/OpsMesh/backend/app/domains/capabilities/tools/service.py:23)
也通过文件、记忆、邮箱三组父类共享状态。

决定：邮箱收敛为命令服务、查询服务、合同、模型四个文件；产品工具保留各业务处理器，但改为显式组合和注入，
不将文件/记忆/邮箱拼成一个超大类。SQLAlchemy 模型 Mixin、Protocol 组合和 Pydantic 泛型不在这次清理范围内。

### 3.4 模型凭证选择存在实际重复

[执行解析器](C:/Users/Administrator/Desktop/github/OpsMesh/backend/app/domains/agents/providers/resolution/resolver.py:19)
和[快照解析器](C:/Users/Administrator/Desktop/github/OpsMesh/backend/app/domains/agents/providers/resolution/snapshot.py:73)
分别实现活跃凭证、默认凭证、可选状态与模型选择；旁边的 service 仅转发调用。
两个不同模块还都定义 `ModelProviderResolutionService`。

决定：保留一份凭证选择策略和查询。执行结果允许在授权后解密；展示快照只接收脱敏数据，不能为复用代码而解密。
缺少凭证时，展示允许给出诊断，执行仍必须拒绝，不能错误地统一这两种语义。

### 3.5 目录名称没有对应实际资源职责

[run_environment.py](C:/Users/Administrator/Desktop/github/OpsMesh/backend/app/runtime/environment/run_environment.py:61)
有 1,211 行，负责 isolated/persistent/pooled、池成员选择、释放、重置、孤儿回收和元数据；
`pool/` 目前只有 `leases.py` 一个实现。

同时，[后端注册模块](C:/Users/Administrator/Desktop/github/OpsMesh/backend/app/runtime/environment/backends/registry.py:8)
直接依赖 Agent 沙箱合同、MCP 适配器和 Self-hosted MCP Job 服务。

决定：池逻辑真正归 pool；中立运行时合同归 `runtime/contracts.py`；资源后端不再负责构造 MCP/Agent 业务适配器。
具体实现装配上移 bootstrap，通过命令/文件系统/会话端口连接 SDK。

### 3.6 模型文件混入会话实现

[sessions/models.py](C:/Users/Administrator/Desktop/github/OpsMesh/backend/app/domains/agents/runtime/sessions/models.py:96)
不仅定义 ORM，还实现 `SQLAlchemyAgentSession` 的读取、追加、清空、锁和重试。

决定：产品持久会话归 `domains/agents/sessions`，模型与存储适配拆开；SDK 原生续接标识和 SDK 特有映射仍归厂商适配器。
并发序号、租户限定、冻结/归档状态、清空后的续接标识处理必须保持。

### 3.7 团队、归档和数据生命周期存在碎片化协作

团队服务入口散在 `teams/` 根目录，实现散在 execution、operations、providers 等子目录。
大量 `console_*`、`command_center_*` 文件互相导入 `_dict`、`_runtime_payload`、`_summary` 等。
归档导出 builder 导入 15 个私有符号；生命周期诊断 service 导入 18 个。

静态计数共 65 个文件含 284 处私有符号导入，包含第三方 SDK 入口，不能全部视为错误。
重点清理本项目内部跨文件共享私有实现的情况。

决定：入口移到功能所属目录；单一用例的细小投影文件合并；跨模块复用改为有名字的公共查询、合同或纯函数。
只在两个以上真实调用场景需要相同语义时共享，不能单纯去掉下划线了事。

### 3.8 观测目录中存在执行控制职责

Worker 模型、租约写入、节点状态变更、恢复、运行时清理被放在 `runtime/operations`。
执行器反向依赖这个“观测/运营”目录，造成读与写的所有权不清楚。

决定：Worker 的模型、租约、节点、恢复写操作归 workers；环境清理归 environment；调度器控制归 orchestration。
operations 保留诊断、容量、时间线和聚合查询，调用明确的控制用例，不再拥有其底层状态变更。

## 4. 目标目录

下列树展示模块和真实子功能，不展示每一个文件；逐文件目标以 CSV 为准。
最多通常使用“一级职责 / 业务域 / 子功能 / 具体实现”；SDK 厂商目录允许更深一层，不设机械层数上限。

```text
backend/app/
|-- main.py
|-- delivery.py
|-- bootstrap/
|   |-- models.py
|   |-- resources.py
|   |-- providers.py
|   |-- runtime.py
|   `-- telemetry.py
|-- api/
|   |-- dependencies/       # HTTP 身份、Redis、队列、Runtime 注入
|   |-- routes/             # access/platform/agents/capabilities/...
|   |-- schemas/            # 只保留真实 HTTP DTO，不保留导入转发层
|   |-- router.py
|   |-- middleware.py
|   |-- errors.py
|   `-- pagination.py
|-- core/
|   |-- config.py
|   |-- contracts.py
|   |-- errors.py
|   |-- utils.py
|   |-- executors.py
|   |-- resources.py
|   |-- db/
|   |-- redis/
|   `-- security/           # 加密、脱敏、出网地址校验、限流
|-- domains/
|   |-- access/             # 用户、令牌、认证、授权
|   |-- agents/
|   |   |-- profiles/
|   |   |-- providers/      # 凭证/模型配置，不再拆五个微型子包
|   |   |-- sessions/
|   |   |-- messages/
|   |   |-- memory/
|   |   `-- runtime/        # SDK 执行合同、注册表、状态；含厂商与工具适配
|   |       |-- providers/openai/
|   |       |-- providers/claude/
|   |       `-- tools/
|   |-- capabilities/
|   |   |-- catalog/
|   |   |-- skills/
|   |   |-- resources/
|   |   |-- governance/
|   |   |-- marketplace/
|   |   |-- tools/
|   |   `-- mcp/            # catalog/execution/transport 保留
|   |-- knowledge/
|   |-- orchestration/
|   |   |-- approvals/
|   |   |-- requests/
|   |   |-- runs/           # authorization 为独立子功能
|   |   |-- tasks/          # control/collaboration/delivery/observation
|   |   `-- workflows/      # definitions/planning/templates/scheduling/steps
|   |-- workspace/
|   |   |-- tenants/        # Workspace、成员、邀请、配额
|   |   |-- teams/          # organization/execution/operations/providers/...
|   |   |-- projects/       # 配置版本、文件边界、快照、运行输入输出
|   |   |-- storage/
|   |   |-- data_transfer/  # Workspace 全量导入、导出、归档
|   |   |-- data_lifecycle/ # 备份、恢复演练、保留策略
|   |   |-- reviews/
|   |   `-- extensions/    # 原 domains/workspace/domains
|   |-- platform/           # admin/releases/updates/credential_rotation
|   `-- integrations/
|       `-- webhooks/
|-- runtime/
|   |-- contracts.py        # 模型厂商无关的执行模式、能力、沙箱合同
|   |-- environment/
|   |   |-- backends/
|   |   |-- pool/
|   |   |-- policies/
|   |   |-- commands/
|   |   `-- spaces/
|   |-- workers/            # runner、queue、leases、nodes、state
|   |   |-- handlers/
|   |   |-- scheduling/
|   |   `-- recovery/
|   |-- self_hosted/        # enrollment/dispatch/projects/worker
|   `-- operations/         # 只读诊断与运营聚合
`-- observability/
    |-- telemetry/          # 日志、指标、trace/context
    |-- audit/
    |-- costs/
    `-- notifications/
```

新增 bootstrap 使 app 一级源码目录从 5 个变为 6 个，是为了解决实际存在的模型注册和具体实现装配问题。
不是为了减少数字而把启动代码继续塞进 core，也不再新增顶层 services、managers、repositories、utils 大杂烩。
`core/utils.py` 由现有高复用纯转换函数收敛而来，不额外创建 utils 包；业务规则仍归所属功能。

## 5. 各模块的最终处置

### 5.1 API 与访问控制

- 保留 routes/schemas 两个清晰的传输层角色，不把整个 HTTP 层重新按每个端点建立子包。
- 平台管理 routes 与 platform schema 对齐；账户认证单列 access；Webhook schema 与 integrations 对齐。
- API 仅做认证入口、传输校验、请求/响应转换、HTTP 状态映射和用例调用。队列消息、领域服务不得依赖 FastAPI。
- 用户和令牌由 access 唯一持有；Workspace、成员、邀请继续由 tenants 唯一持有，不复制一套“组织权限模型”。
- 跨域授权使用明确的主体、Workspace 和权限输入。SQLAlchemy 类型关联可保留必要类型引用，不通过业务 service 互相装配。
- 删除 schema 转发不等于合并所有 DTO。HTTP 表单、上传、序列化与领域执行合同确实不同时，应保留转换。

### 5.2 Agent、模型和会话

- Profile CRUD、生命周期和仅转发的查询包装收敛到 profiles/service；独立的版本记录、模型校验和安全评审保留。
- providers 去掉 audit/catalog/credentials/health/resolution 五个微型子包，按凭证、解析、健康探测、快照、审计等文件组织。
- providers 的审计四文件合并；健康写入/状态/摘要合并；实际 SDK 探针继续独立。
- runtime/execution 泛化层去掉。厂商注册装配进入 bootstrap；纯合同和执行控制留在 Agent runtime。
- Claude runner 中的会话持久化、SDK 工具/审批钩子与执行生命周期分开；OpenAI 已有 tools/results/guardrails/session/compaction 等真实边界，保留。
- SDK 自带的运行、续接、压缩和协议处理继续由 SDK 实现。产品记忆授权、持久化、配额和审计不因为“SDK 也有 memory”而删除。
- memory 的 working/episodic/semantic、检索、嵌入与生命周期有不同状态和策略，不为了减少文件将三层记忆合成一个 service。

### 5.3 能力、工具、MCP 和知识

- 顶层 capabilities/models 中的 Capability/ToolGroup、Skill/Install、MCP 记录、Resource 分别归实际子功能。
  数据表名、约束名、ForeignKey、迁移历史不因文件迁移改变。
- governance 的通用 action 转发层去掉；不同风险的 agent/skill/MCP 操作保留明确入口和审批检查。
- MCP 的 catalog、execution、transport 是配置、授权执行、外部协议三种真实边界，保留。
- execution 的小型错误/上下文/合同文件合并；调用日志和通知证据合并为 events；审批和凭证校验不能并入传输客户端。
- 当前 `transport/payloads.py` 中 `result_from_sse_body`、`sse_data_messages` 未找到调用方；
  核对生产远程调用使用 SDK 后删除这两个旧解析函数，保留容器 stdio 结果封包的合法解析。
- 产品工具改为显式处理器组合，不再从多个父类借用 `_session`、文件读取器和事件方法。
- 知识 ingestion 拆出内容加载/准备，生命周期和引用证据继续由 ingestion 用例控制。Local/S3 存储使用既有 SDK 实现，不另造对象存储层。
- marketplace 当前是真实产品功能，不趁目录调整引入插件中心、热加载或独立插件 SDK。

### 5.4 编排、任务与 Run

- 保留 Task、Run、Step 三种状态机，但每种状态规则与其状态服务放在一起，不做新的泛型状态机框架。
- Run 的授权快照、工具授权、运行环境授权与执行前重验集中到 runs/authorization。
  requests 只构建模型请求、上下文、会话、提示词和预算，移除重复转发方法。
- tasks/control 与 tasks/operations 合并控制职责；任务 CRUD 回 task 根服务；任务转移归 collaboration；
  execution 中的纯诊断归 observation；manager 诊断/复核归 collaboration。
- 任务观察卡片和 section 包装收拢，但领域专用视图、查询和权限过滤不塞进统一大字典工具。
- 工作流定义的 CRUD/发布、校验、应用到任务拆开；1,014 行的 future_plan_mutation 分成事务协调、纯图操作、Step 更新三部分。
- 组织结构计划模板提升为 workflows/templates，去掉 planning/project_plan 这一层；成员匹配与图校验只有一个权威实现。
- 自动规划、用户编排、组织模板共用 `WorkflowNode -> 图/条件校验 -> 可行性校验 -> Step materializer -> 调度`。
  计划来源只影响生成方式和可编辑权限，不复制三套执行器。
- 保留条件分支、数据绑定、子工作流、审批等待、版本检查和锁定区域；不能以“结构重构”为由缩减为演示型 DAG。
- 保留标准库 TopologicalSorter 和已有 Pydantic/JSON Schema 校验，不自行再写图引擎。

### 5.5 Workspace、项目、团队、存储

- 项目文件管理仍属于 projects；Workspace 全量归档实际覆盖 Agent/Team/Task/Run，迁到 data_transfer。
- 数据备份、恢复、保留策略从 tenants/lifecycle 移到 data_lifecycle，清除 actions/diagnostics/scheduling 中单用例的多层包装。
- 归档 importer 通过明确 ImportContext 和解析/冲突合同协作，不继续跨文件导入大量 `_xxx` 函数。
- 项目输入快照、runtime staging、输出 harvest、产物版本和外部存储补偿保留，不能因为都是“文件”就混成一个 storage_service。
- teams 根目录中的 loop/overview/console/readiness/project/context 入口分别回 execution/operations/providers/projects/organization。
- 团队控制台与其私有 payload 合并；共享格式用显式类型和纯函数；执行循环、人工操作、运行时绑定仍是不同生命周期。
- team runtime 的 workspace_binding、committer、payloads 合并为一个绑定用例；提交由它拥有，不单独建立 committer 抽象。
- tenants 的 workspace_* 前缀去掉；quota 配置和 reservation 预留分别命名，避免两个同名 WorkspaceQuotaService 误导调用方。
- Workspace 健康快照与通知等功能不能因为找不到调用方被顺手删除，按照第 8 节处理。

### 5.6 Runtime 与 Worker

建议池目录具体为：

```text
runtime/environment/
|-- run_environment.py      # 执行模式选择、分配/释放协调
|-- leases.py               # 普通 Runtime 租约
|-- pool/
|   |-- service.py          # 获取可用成员、按需扩容、归还
|   |-- leases.py           # 原子租用和所有权检查
|   |-- policy.py           # 池匹配、隔离键、限制
|   |-- reset.py            # 回收前清理和重置
|   `-- reclaim.py          # 孤儿成员/异常租约回收
|-- backends/               # 资源协议和真实后端
|-- policies/               # 模板、配额、安全、网络
|-- commands/               # 命令审批、入队、执行、结果
`-- spaces/                 # 空间配额、绑定、预留
```

- `none/isolated/pooled/persistent` 的产品语义不能改变。none 不提供宿主机执行兜底；SDK 无沙箱时不能自动获得 shell/文件/stdio 权限。
- execution mode 只保留一份中立词汇定义；资源后端依赖 runtime 合同，不依赖 Agent 厂商类。
- 池重用保留按 Workspace/策略隔离、独占租约、Run 专属目录、资源和网络限制、重置失败隔离/销毁及回收审计。
- Worker 节点/租约模型和写服务回 workers；状态、心跳、fencing token、ack/reclaim 属于一个执行协议。
- RedisQueue 的原子操作和 Lua 脚本继续放在同一实现，不能为减少行数拆断租约所有权判断。
- operational diagnostics 与 effectful recovery 分开。Postgres 是持久事实，Redis 是可恢复的投递状态，这个约束不能因为文件合并变成 Redis 优先。
- Self-hosted 的 enrollment、dispatch、projects、worker 有独立安全和执行边界，保留；projects 即使只有两个文件，也不是无意义目录。
- Host updater 继续是独立高权限运行入口。只调整归属与启动注册，不允许 API 进程直接执行更新脚本。

### 5.7 观测与公共基础设施

- telemetry 聚合日志、指标、trace/context；SDK exporter 实现与 API/Worker instrumentation 装配分开。
- audit 聚合普通审计与安全事件；costs 聚合用量、定价、预算、查询；notifications 保持独立业务通知模型。
- costs 拆成用例/定价/查询三个真实职责，不为每个金额字段创建服务；精度、币种、预算窗口和幂等用量记录保持。
- telemetry 是低依赖技术子包；audit/costs/notifications 是持久化观测功能，不能假定整个 observability 都是无业务依赖的工具层。
- 对运行时事件先规范成中立的用量/审计数据，再交给观测记录器。观测底层不能反向创建 Agent runner、Docker client 或 Worker。
- 目标 core/utils.py 只容纳纯值转换。Workspace 权限、项目路径授权、工具参数策略、状态转换不放入公共 utils。

## 6. 依赖与代码质量规则

### 6.1 装配与依赖

- bootstrap 可以依赖具体实现，负责模型注册、启动资源、适配器选择和销毁。
- core 不依赖 api、domains、runtime、observability 或 bootstrap；DB 初始化不得隐式加载所有业务服务。
- telemetry 仅依赖 core、标准库与已采用 SDK；HTTP instrumentation 由 bootstrap 执行。
- API 可以调用业务用例，不拥有持久业务状态；领域与运行时不能导入 API。
- 具体 Agent SDK 导入限制在厂商适配目录；运行环境后端不导入厂商 SDK；MCP 传输使用既有官方客户端。
- runtime/environment 的资源实现依赖中立合同和基础设施；MCP 与 Agent 绑定在上层装配，不反向要求 Docker backend 构建它们。
- 跨域命令调用进入对方明确的用例入口；复杂跨域读模型允许在其聚合模块内读取多个模型，但不得偷偷写入他域状态。
- 不为每个 service 抽象接口。只给可替换后端、SDK、队列、存储以及需要隔离副作用的边界提供 Protocol/ABC。
- 用 Import Linter/Grimp 校验方向和明确边界。类型检查引用与启动注册应有显式规则，不拿所有图边强行套一个绝对分层顺序。

### 6.2 文件合并和拆分

- 一个用例的一次事务、共享私有状态、只被唯一调用方使用的细小包装，优先合并。
- 只有 DTO 和一次转发的服务优先去掉包装，不给它再增加 interface/factory/base。
- 拆分依据是独立状态/事务/安全/协议，而不是超过 300 行就拆。
- 单文件 600 行以上作为检查信号，不作为强制失败；OpenAI runner、RedisQueue 等允许有理由的保留。
- 小于三个实现文件的目录不自动删除。厂商适配、项目 IO、安全协议可以是合理的小边界。
- models 仅包含 ORM/必要持久化约束；repository 只有在集中查询确实消除重复或封装锁时才引入。
- 重复函数必须先比较语义。例如同样的 UUID 转换可能一个拒绝异常、一个生成诊断；不能仅按函数名合并。
- 不保留旧导入路径、兼容 re-export、旧类别名和 fallback import。真正的公开合同和注册入口不属于兼容代码。

### 6.3 事务与外部副作用

当前同时存在 API commit、服务 commit、底层 flush。发现这种分布不代表每个调用点都有事务 bug，
但实施每个批次时必须明确命令的提交责任。

- 普通业务命令由一个用例拥有最终提交，内部协作者只 flush 或返回结果，不多层隐式提交。
- SDK 会话追加、租约抢占、命令排队、外部文件补偿等有特殊并发和可见性要求，不能统一“把 commit 移到路由最后”。
- 外部调用采用已存在的意图/执行/结果记录方式；不能持有数据库锁等待网络调用，也不能在提交失败后丢掉存储补偿。
- 项目 archive/restore 的预览令牌、版本检查和幂等标识保持；Worker 的旧 token 不可续租、ack 或完成新持有者的任务。

## 7. 实施顺序与相关验证

R0 是已经完成的审查基线。R1 的显式 ORM 注册、访问域/HTTP 依赖、平台控制面与外部集成边界已实施并通过相关验证；R2-R8 尚未实施。
CSV 中的 R0 保留项表示“结构保留，导入随依赖模块批次更新”，不表示对应功能已验收。

| 批次 | 实施范围 | 必须检查 | 建议的相关测试入口 |
| --- | --- | --- | --- |
| R0 | 全量盘点、逐文件处置、哈希基线 | 1,029 文件覆盖无遗漏，工作区既有变更保留 | 审查脚本、处置表一致性 |
| R1 | bootstrap/core/access/platform/integrations | API/Worker/CLI/Alembic 显式注册模型；认证权限与更新入口不变 | test_auth_api.py、test_authorization.py，受影响的注册/启动检查 |
| R2 | API 依赖、schema 转发和路由归属 | URL、方法、鉴权、operation id、字段脱敏与 OpenAPI 形状不变 | 受影响 API 测试和 schema 比较 |
| R3 | Agent/provider/session/mailbox/tools/MCP | 两厂商续接/审批、凭证选择、邮箱租户隔离、工具授权不变 | test_model_provider_service.py、test_agent_runtime_sessions.py、test_agent_messages_api.py、test_product_tools.py、test_mcp_adapters.py |
| R4 | 任务/Run/工作流职责与共享图管线 | 自动/用户计划共用校验与物化；锁定区域、条件、子工作流、转移权限不变 | test_user_orchestration.py、test_agent_planning.py、test_task_transfer.py、test_run_runtime_authorization.py |
| R5 | Workspace/team/project/archive/data lifecycle | 文件边界、导入预览与补偿、版本/产物历史、团队人工控制不变 | test_workspace_projects.py、test_workspace_export_api.py、test_run_project_io.py，受影响团队/生命周期用例 |
| R6 | 实际容器池、后端装配、Worker 状态和恢复 | 四种模式、并发租用、重置失败、旧 token、进程恢复、自托管隔离 | test_run_runtime_environment.py、test_runtime_command_security.py、test_redis_queue.py、test_worker_runner.py、test_self_hosted_runtime.py |
| R7 | telemetry/audit/costs/notifications | trace 关联、脱敏、审计链、成本精度、预算与通知权限不变 | test_telemetry.py、test_cost_accounting.py、test_cost_api.py，受影响审计/通知测试 |
| R8 | 无入口项确认、旧引用/空包/配置/文档清理 | 模型、CLI、构建、容器入口均可加载；无兼容路径，无静默删除功能 | import-linter、相关 architecture 检查、轻量启动/装配检查 |

批次是边界和顺序，不要求一次提交几百个文件。每个可独立完成并验证的功能点提交一次。
结构迁移与真实行为修复应分开，不能在大范围移动中夹带未说明的语义改变。
本地只跑当前功能相关测试，不跑全量套件、不等待非正式包的 GitHub 发布任务。

当前未提交的 Worker claim token、heartbeat、queue rehydration 工作应先明确是否已完成并独立验证，
再进行 R6 文件归属迁移，不能把这部分既有修改覆盖掉，也不能顺便声明它已完成。

## 8. 无生产调用方的处置

以下项目在静态生产导入图中没有调用方，部分还做了仓库符号搜索复核：

- FeatureFlagService、MaintenanceRunner。
- RuntimeToolService、RuntimeFileService。
- TaskCollaborationRecoveryService、TaskExecutionStatusService、TaskInteractionTranscriptService。
- TeamProjectDashboardService、WorkspaceHealthService。

决定是**保留并核对接入**，不是默认删除或默认开启。在 R8 验收中逐项记录：
入口在哪里、是否被动态注册、关联产品能力是否仍承诺、是否需要接线，或经过明确确认后退役。
如果能力仍应存在，接入与相关测试必须独立完成；不能只留下一个无调用类后标记“功能完成”。

`main.py`、Worker CLI、delivery CLI、Host updater daemon 是进程入口，无普通 import 调用方是正常情况，不能按死代码删除。
孤立 schema 转发和无引用的旧 SSE 解析函数属于另一类，按明确的符号/配置检查后清理。

## 9. 完成判据

目录重构只有同时满足以下条件才能称为完成：

1. 逐文件表中每个移动/合并/拆分项已落地，或有明确、可复核的调整记录。没有“只改了目录图”。
2. 同一目标文件的多来源代码经过职责重写和去重，不是机械拼接；所有公共调用方、类型检查和测试 patch 路径同步更新。
3. 没有旧路径兼容导出、重复服务实现、已失效的模型注册、包导入副作用或运行时动态 import 字符串残留。
4. 租户边界、授权快照、审批、执行模式、租约 token、文件/网络隔离、审计和成本约束保持。
5. API、Worker、Self-hosted、Alembic、打包及 updater 的入口和显式装配完成检查。
6. 相关功能测试与依赖规则通过；架构测试由真实所有权规则约束，不再以“目录必须等于某组名字”代替设计验证。
7. 旧包只有在无源码/资源/调用方后清理；删除路径必须解析并核实在仓库中。不能删除项目工作文件、运行时数据或用户未提交文件。
8. 对无生产入口项给出逐项结论；未接通的功能不得算作已实现。未做的生产并发/外部环境验证明确列出。

## 10. 预计收敛结果

当前处置表推导的目标约为 813 个实现文件、125 个包目录（含 app 根目录），
对比当前 893 个实现文件、136 个包目录。目标一级目录为六个。

这不是验收硬指标，也不是已经完成的统计：合并会减少文件，拆开真实职责和新增装配边界也会增加文件。
不能为了更漂亮的数字删除能力、把 1,000 行文件拼得更大，或将复杂逻辑转移到无边界的 utils。

最终应达到的是：找一个功能时能找到它的入口、状态、策略和副作用所有者；改一个功能时无需理解十几个只做转发的碎片；
接一个 SDK/后端时不需要修改基础设施和业务模块的双向依赖。
