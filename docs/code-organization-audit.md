# OpsMesh 代码组织与架构边界

状态：当前有效；更新日期：2026-09-16

本文档是 `backend/app` 代码组织、目录归属和依赖方向的唯一权威说明。它合并并取代了
历史目录重构方案、全量结构审查方案和实施记录。当前行为仍以源码、迁移和架构检查为准；
本文档不把历史计划、旧提交或旧测试结果当作当前实现证据。

生产化工作的优先级和未完成事项由
[平台收口与生产化计划](platform-productionization-plan.md) 维护，不在本文重复建立任务清单。

## 1. 合并结论

此前几份重构文档分别描述了设计目标、逐文件审查和 R0-R8 实施记录，但在重构完成后产生了
三个问题：

- “目标尚未实现”和“R0-R8 已完成”同时存在，状态互相冲突；
- 旧 HEAD、旧文件数量和旧目标树长期保留，容易被误认为当前 checkout；
- 当前所有权规则、历史迁移步骤和后续生产化任务重复维护，更新时容易遗漏。

合并后只保留三类信息：

1. 当前目录和唯一 owner；
2. 仍然约束代码的架构决策；
3. 可由脚本重新生成和验证的结构证据。

历史阶段编号和逐批迁移过程不再作为规范。机器生成的审查证据继续保存在
`docs/reviews/app-layout-2026-09-14/`，但它们是结构证据，不是第二套设计文档。

### 1.1 架构设计依据

本架构不是照搬某个开源项目的目录，而是从成熟系统中抽取稳定边界：

- n8n：工作流合同、执行核心、传输入口与扩展节点分离；
- Temporal：持久控制状态、任务调度、Worker 和 SDK 客户端分离；
- OpenHands：Agent/Controller/Event state 与实际 Runtime/Sandbox 分离；
- LangGraph：图执行合同与 checkpoint、Postgres/SQLite 等持久化实现分离；
- PydanticAI：Provider-neutral Agent 核心与 Toolset/MCP 能力扩展分离；
- Prefect：SDK、API/Server、Worker 和 Integration adapter 分离。

OpsMesh 只采用这些项目共同证明有效的原则：

1. 合同先于具体实现；
2. Durable control state 与短期执行资源分离；
3. 一个状态机和副作用只有一个产品 owner；
4. Provider、Runtime、Storage 和 Telemetry 通过窄 adapter 接入；
5. 只有真实生命周期、协议、安全或持久化边界才建立子包。

目录名称和层级仍以 OpsMesh 的 Workspace 隔离、Agent 编排、Capability Gateway、Runtime
执行及 durable evidence 需求为准，不追求与参考项目一一对应。

### 1.2 旧方案到当前设计的演进

| 设计点 | 旧方案 | 当前决定 |
| --- | --- | --- |
| 顶层结构 | `api/core/domains/runtime/observability` 五个目录 | 增加 `bootstrap`，形成六个稳定边界 |
| 认证与身份 | 认证仍部分属于 `core` | 用户、令牌和 RBAC 归 `domains/access`；HTTP 认证依赖归 `api` |
| 具体实现装配 | 只描述 composition root，没有明确目录 | ORM、SDK、Runtime、Storage 和 Telemetry 统一在 `bootstrap` 装配 |
| 业务域范围 | 主要描述 Agent、Capability、Orchestration、Workspace | 增加 Access、Knowledge、Platform 和 Integrations 的明确 owner |
| Agent Provider | 模型配置和 SDK adapter 容易混在 `providers` | 模型配置属于 Agents；SDK 执行 adapter 属于 `agents/runtime/providers` |
| Runtime | 目标目录名为 `runtime/execution` | 中立合同位于 `runtime/contracts.py`，资源生命周期归 `runtime/environment` |
| 目录深度 | 普通功能建议最多两层 | 不设机械深度上限，只按真实边界决定是否保留子包 |
| 验证方式 | 迁移批次和模块级测试并行维护 | 结构改造使用静态门禁；行为变化只更新已有产品流程测试 |

这些变化是旧设计落地后的校正，不是重新建立一套架构。尤其是 `bootstrap`：旧方案要求具体
实现只能在组合根装配，却没有给组合根稳定位置；当前结构补齐了这个缺口，并阻止装配逻辑
再次进入 `core` 或 import-time side effect。

## 2. 当前结构

当前 `backend/app` 是模块化单体，顶层只有稳定的产品或基础设施边界：

```text
backend/app/
├── api/             # HTTP transport、schemas、dependencies、middleware、routes
├── bootstrap/       # 组合根、模型注册、具体 adapter 和进程资源装配
├── core/            # 配置、数据库、Redis、安全及 provider-neutral 技术原语
├── domains/         # 产品业务、状态机、策略和应用服务
├── observability/   # audit、cost、notifications、telemetry
├── runtime/         # 执行环境、worker、operations、self-hosted runtime
├── main.py          # API 进程入口
└── delivery.py      # 交付/运维进程入口
```

当前结构快照包含 935 个 Python 文件，其中 810 个为非 `__init__.py` 实现文件，共 125 个
包目录。这些数字用于发现遗漏，不是代码质量目标，也不能为了降低数字而合并真实边界。

`__pycache__`、临时测试目录、构建产物和运行时数据都不是源码结构，不得提交。

## 3. 顶层边界与依赖方向

### 3.1 API

`api` 只负责传输层：解析请求、校验 HTTP DTO、取得 authenticated context、调用用例并映射
响应和错误。路由不得拥有 SQL、状态转换、Provider SDK 调用、Docker 生命周期或长任务执行。

### 3.2 Bootstrap

`bootstrap` 是唯一允许认识多种具体实现的组合根。它负责 ORM 模型注册、Provider adapter、
Runtime backend、队列、存储和 telemetry 的构造与生命周期。具体实现不得通过 import-time
副作用自行注册，也不得把装配逻辑塞回 `core`。

### 3.3 Core

`core` 只保存跨领域、低依赖的技术基础：配置、数据库/Redis 连接、安全与脱敏、通用错误、
资源生命周期和小型纯值原语。它不能依赖 `api`，也不能成为业务 `utils`、service 或 DTO
的收容目录。

### 3.4 Domains

`domains` 保存产品状态、策略、命令、查询和应用服务：

```text
domains/
├── access/          # 用户、令牌、认证、RBAC、authenticated context
├── agents/          # profile、provider、session、message、memory、Agent SDK runtime
├── capabilities/    # catalog、tool、skill、resource、MCP、governance、marketplace
├── integrations/    # Webhook 等外部产品集成
├── knowledge/       # 知识来源、索引和检索生命周期
├── orchestration/   # request、run、approval、task、workflow、planning
├── platform/        # 平台管理、release、update、credential rotation
└── workspace/       # tenant、project、file、artifact、team、data lifecycle
```

领域拥有自己的状态机和稳定合同。领域模块不能导入 API route 或 transport-only schema，也不
能直接构造 Docker、Redis client 或厂商 SDK client。

### 3.5 Runtime

`runtime` 管理真实执行资源，而不是 Agent 业务编排：

```text
runtime/
├── contracts.py     # provider-neutral execution mode、policy、manifest、session
├── environment/     # backend、pool、lease、command、space、staging、cleanup
├── workers/         # queue、runner、handler、node、lease、scheduling、recovery
├── self_hosted/     # enrollment、dispatch、project transfer、worker protocol
└── operations/      # 只读诊断、容量、时间线和治理入口
```

Agent SDK adapter 通过中立合同请求运行环境，但不拥有 Docker pool、租约或项目文件 staging。
Runtime backend 不构建 Agent、MCP 或业务授权对象。具体绑定只在组合根完成。

### 3.6 Observability

`observability` 统一持有审计、成本、通知和 telemetry 证据。领域和 Runtime 产生 typed event；
它们不各自实现一套审计、计费或 trace 协议。Provider trace 不能取代产品审计。

### 3.7 允许的依赖

```text
HTTP/CLI/Worker entrypoint
          │
          ▼
application/domain service
          │
          ▼
domain contract / policy / state machine
          │
          ▼
infrastructure port

bootstrap ──> concrete SDK/runtime/storage/telemetry adapters
```

禁止的反向依赖包括：

- `domains`、`runtime`、`core` 或 `observability` 导入 API route；
- Worker 执行合同依赖 HTTP response schema；
- Provider-neutral 合同依赖 OpenAI、Claude、Docker 等厂商对象；
- Runtime backend 反向构造 Agent/MCP 业务服务；
- `core` 依赖具体业务领域以完成隐式注册。

## 4. 领域唯一归属

| 能力 | 唯一 owner |
| --- | --- |
| 用户、令牌、RBAC、authenticated context | `domains/access` |
| Workspace、成员、邀请和 tenant settings | `domains/workspace/tenants` |
| Agent profile、模型凭据、provider 解析与健康 | `domains/agents` |
| OpenAI/Claude SDK 执行适配 | `domains/agents/runtime/providers` |
| Agent session、message 和三层 memory | `domains/agents` 对应子域 |
| Tool、skill、resource、MCP 和 capability policy | `domains/capabilities` |
| Task、Run、Step、Workflow、Approval 和 Planning | `domains/orchestration` |
| Project 文件、artifact、导入导出和数据生命周期 | `domains/workspace` |
| Team 组织、执行、运维、provider 和 runtime binding | `domains/workspace/teams` |
| 发布、更新和平台管理策略 | `domains/platform` |
| Docker/runtime pool、Worker、queue 和 self-hosted 执行 | `runtime` |
| Audit、cost、trace、metrics 和 notification | `observability` |
| 具体实现注册和进程装配 | `bootstrap` |

用户权限和 Workspace 成员关系是相邻但不同的边界：`access` 解析主体及平台权限，`tenants`
维护 Workspace 资源关系。不得再建立一套平行的“组织权限”系统。

## 5. 已确定的关键设计

### 5.1 Agent SDK 与运行时分层

OpenAI Agents SDK 和 Claude Agent SDK 适配器实现同一个 OpsMesh Agent Runtime 合同。厂商
SDK 自带的 run、session、tool、approval、continuation、compaction 和事件能力，应通过公开
接口直接使用；OpsMesh 只补充 workspace 授权、快照、策略、脱敏、运行时注入和 durable
evidence。

沙箱概念分成两层：

```text
Agent SDK adapter
      │ requests execution capabilities
      ▼
runtime/contracts.py
      │ allocates concrete resources
      ▼
runtime/environment/
```

执行模式只有：

- `none`：没有 shell、stdio MCP、项目文件 staging 或本地代码执行能力；
- `isolated`：为 Run 创建独立运行环境；
- `pooled`：租用预热成员，并使用 Run 专属目录和租约；
- `persistent`：绑定可连续使用的固定运行环境。

`none` 永远不表示在 API/Worker 宿主机执行。厂商沙箱不可用时必须拒绝或选择已授权的
OpsMesh backend，不能静默降级。

### 5.2 Capability 与 MCP

Capability Catalog 是工具、技能、资源和 MCP 的有效视图；Gateway 根据 workspace、team、
agent、run grant 和 policy 计算授权。模型输出不是授权来源。

MCP 的 catalog、execution 和 transport 是真实边界。HTTP/SSE/Streamable HTTP 和 stdio
协议使用官方 MCP SDK；stdio 只能在绑定的隔离 runtime 或 self-hosted job 内执行。

### 5.3 Orchestration

Task、Run、Step、Workflow 和 Approval 保留独立状态机，但每个状态转换只有一个 owner。
自动规划、用户编排和组织模板共享同一套图校验、条件判断、Step materialization 和调度
执行链；它们只在计划来源、编辑权限和锁定区域上不同，不复制执行器。

### 5.4 Workspace 与文件

Project 文件、运行输入快照、runtime staging、输出 harvest、artifact version 和外部存储
补偿是不同生命周期，不得合并成一个通用文件服务。Workspace 全量导入导出归
`data_transfer`，备份、恢复和保留策略归 `data_lifecycle`。

### 5.5 Worker 与 durable state

Postgres 是任务、Run、Lease 和业务调度记录的事实源；Redis 只保存队列投影、锁、短期幂等
窗口和 pub/sub。Redis 状态丢失后应从 durable state 重建，不能反向把 Redis 当作最终事实。

Claim、heartbeat、ack、retry、dead-letter、cancel 和 recovery 必须保持 fencing、幂等和
workspace scope。重复投递不能造成第二次不可逆副作用。

### 5.6 Observability

日志、指标、trace、审计和成本共享 correlation identifiers 与统一脱敏规则，但拥有不同的
持久化语义。审计和成本是 durable product evidence；telemetry exporter 故障不能删除业务
证据，也不能阻塞状态提交。

## 6. 文件和目录设计规则

### 6.1 何时保留子包

只有以下情况值得建立子包：

- 独立状态机或生命周期；
- 持久化、事务或并发边界；
- 安全、租户或隔离边界；
- 稳定协议或可替换 adapter；
- 多个实现共享的明确公共合同。

文件少于三个不自动说明目录错误；Provider adapter、项目 I/O 和安全协议即使规模小，也可能
是必要边界。反过来，文件数量多也不能成为继续拆分的理由。

### 6.2 何时合并

以下内容应优先回到所属用例：

- 只有一次转发的 service、factory 或 wrapper；
- 只被唯一调用方使用的 payload/summary/support helper；
- 共享同一个私有状态和同一次事务的碎片；
- 仅为保留旧 import 路径存在的 re-export；
- 与官方 SDK 或现有权威路径重复的实现。

### 6.3 何时拆分

入口函数同时承担查询、授权、纯策略、事务、外部调用和响应拼装时，应按执行阶段拆分。
拆分依据是职责与副作用，不是机械行数。600 行以上只是审查信号；Redis 原子协议、SDK runner
等高度内聚实现可以在理由明确时保留。

### 6.4 命名与公共工具

- 文件和目录使用业务词汇，不使用无归属的 `helpers`、`common_service`、`models_layer`；
- `models.py` 只包含持久化模型及必要约束，不混入会话存储或业务执行；
- `repository` 只在集中查询、锁或持久化协议确实消除重复时存在；
- `core/utils.py` 只容纳 provider-neutral、无业务状态的小型纯原语；
- `build`、`resolve`、`import`、`run` 等入口应体现阶段，不得隐藏全部查询与副作用。

### 6.5 禁止兼容代码

内部合同变化时一次性更新所有调用方、迁移、文档和必要的流程测试。禁止旧路径别名、
deprecated wrapper、fallback import、双写、重复 endpoint 和静默行为回退。

## 7. 事务、安全与状态不变量

1. Workspace 是数据、Agent、Task、文件、Memory、Capability 和 Runtime 的租户边界。
2. 资源查询不能只使用资源 ID；API、Service、Worker、Cache、Storage 和 Tool 路径都必须携带
   workspace scope。
3. API 不执行长任务，用户或 Agent 控制的代码不在 API/Worker 宿主机运行。
4. 模型提出计划和动作；产品服务决定授权、策略、配额、状态转换和副作用。
5. 凭据只在最窄执行边界解密，不进入 payload、日志、trace、导出或错误详情。
6. 高风险动作 fail closed，并保留 approval、audit 或 security evidence。
7. 普通业务命令由一个用例拥有最终提交；内部协作者只 flush 或返回结果。
8. 外部调用不得在不受控的长数据库锁内执行；意图、执行结果和补偿必须可恢复。
9. 一个状态机、policy evaluator、redaction rule 和 evidence writer 只有一个 owner。

## 8. 已完成重构的保留结论

历史迁移过程不再逐阶段维护，但以下结果必须持续成立：

- ORM 注册和具体实现装配已从底层连接模块移到 `bootstrap`；
- 用户/令牌归 `access`，平台控制归 `platform`，Webhook 归 `integrations`；
- API forwarding schema、空继承响应和旧内部路由包装已清理；
- Agent provider、session、message、tool 与 MCP 已按真实协议和生命周期归属；
- Task/Run/Workflow 的状态、规划、协作和执行职责已收敛到各自 owner；
- Workspace 的归档、导入导出、备份恢复和 Team 子功能已按生命周期归类；
- Runtime 四种模式、Docker pool、Worker lease、recovery 和 self-hosted 边界已统一；
- Audit、cost、notification 和 telemetry 已归 `observability` 的明确子域；
- 旧路径、空包、无效转发和重复 Runtime tool/file 执行路径已移除。

如果当前代码不再满足其中某项，应修正代码或本节，不得增加兼容层维持旧描述。

## 9. 结构证据与验证

机器生成证据位于 `docs/reviews/app-layout-2026-09-14/`：

- `source-inventory.json`：当前 Python 文件的哈希、声明、依赖、调用方和事务调用；
- `declaration-review.txt`：按包排列的实现声明摘要；
- `file-dispositions.csv`：原审查来源到当前目标的逐文件处置记录；
- `disposition-rules.json`：处置规则；
- `target-footprint.json`：当前目标实现文件和包目录集合。

这些文件由 `scripts/audit_app_layout.py` 维护，不手工改写。验证命令：

```bash
uv run python scripts/audit_app_layout.py --check
uv run python scripts/audit_app_layout.py --target-check
uv run ruff check <affected-files>
git diff --check
```

代码组织重构默认不新增、也不反复执行单文件或单类测试。只有用户可见行为或跨边界合同
改变时，才更新已有流程测试；流程应从请求入队、team/project 创建（适用时）、编排执行到
最终输出，并在该链路中覆盖必要的拒绝、恢复、隔离、幂等和脱敏行为。完整测试套件只在
release tag 门禁运行。

## 10. 变更完成标准

代码组织变更只有同时满足以下条件才能完成：

- 新位置有唯一 owner，并符合依赖方向；
- 所有调用方和动态入口已经更新，没有旧路径兼容导出；
- 没有空包、重复实现、隐式注册或循环装配；
- Workspace、授权、事务、运行时隔离和 durable evidence 语义不退化；
- 审查快照和目标布局证据已重新生成；
- Ruff、必要的类型/导入/架构检查和 `git diff --check` 通过；
- 如果改变产品行为，已有端到端流程测试已经同步更新；
- 文档索引和相关架构文档只引用本文件，不再维护第二套目录重构计划。
