# 平台收口与生产化阶段计划

状态（2026-09-17）：P1-8 已在当前 checkout 完成；P1-9 至 P1-11 仍在计划中。完成状态只由当前代码、迁移、
定向流程和静态检查证明，不由本文档单独宣称。

本文档是当前阶段的唯一执行计划。它把已有的 Agent、编排、运行时、能力、数据、运维和
发布基础收敛成可上线、可恢复、可审计的产品闭环。实现时必须以代码、迁移和定向测试为
证据；`Done`、`planned` 或历史发布记录不能单独作为完成证据。

## 1. 阶段边界

### 1.1 本阶段目标

- 让一次任务从创建、排队、执行、等待、恢复到完成/失败都能被可靠地追踪和恢复。
- 让 API、Worker、Agent SDK、运行时、文件、MCP、审批、审计、成本和发布系统各自只有
  一个明确的状态机与责任边界。
- 让生产部署可以通过固定的 release identity 安装、升级、回滚和恢复，而不是依赖手工
  命令或不可验证的 `latest`。
- 让操作人员能从日志、指标、链路、审计和成本视图定位问题，并拥有可执行的修复动作。
- 在不引入兼容性别名、静默回退或第二套实现的前提下，完成安全、隔离、幂等和数据恢复
  的门禁。

### 1.2 明确排除

- 通用 Plugin Center、外部插件 SDK、插件热加载和示例连接器（例如消息平台连接器）不
  属于本阶段；它们作为下一阶段单独立项。
- 前端画布、计费结算、多地域控制面、Postgres 大版本迁移和滚动升级不属于本阶段。
- 不为了“看起来完整”新增 LangChain、LlamaIndex、通用工作流框架或自制协议实现。
- 不把用户/Agent 代码加载到 API 或 Worker 进程，不以动态 `import`、在线 `pip install`
  或重启进程模拟热加载。

### 1.3 当前基线的使用方式

以下文档是基线索引，但其中的完成标记必须回到代码、迁移、工作流和测试复核：

- [Architecture Gates](architecture-gates.md)
- [Release Delivery Plan](release-delivery-plan.md)
- [Observability, Audit, and Cost Operations](observability-audit-and-costs.md)
- [代码组织与架构边界](code-organization-audit.md)
- [Agent Runtime Architecture](agent-runtime-architecture.md)

本阶段开始前先生成一次“现状快照”：当前分支、迁移 head、工作流文件、运行时镜像摘要、
定向测试基线和未提交改动。历史 release 的通过不等于当前 `master` 已通过；当前 checkout
必须重新验证。

### 1.4 当前执行证据

`P0-1` 至 `P0-6`、`P1-7` 以及 `P1-8` 已在当前 checkout 完成代码合同收口和对应的定向验证；`P1-9`
至 `P1-11` 仍未完成。此次收口建立了领域合同的唯一
所有者、API 到领域/运行时的单向映射和可执行的反向依赖门禁；迁移 head 复核为
`0092_observability_evidence_closure`。当前证据命令及结果为：

- `pytest backend/tests/test_architecture.py`：51 passed；
- P0-1 影响的能力、MCP、Marketplace、Agent 消息、Workspace 导入导出、Runtime space、
  Operations、Task 定向回归：全部通过；
- `pytest backend/tests/test_workspace_export_api.py`：全部通过，包含 v2 项目/memory round-trip、
  对象 checksum、篡改归档拒绝和 disposable restore drill；
- `ruff check backend/app backend/tests/test_architecture.py`：通过；
- `lint-imports --no-cache`：8 kept, 0 broken；
- `git diff --check`：通过。

P0-4 的代码合同已经收口；Docker/池/隔离的真实环境验收仍需在带 Docker daemon 的 release
门禁中执行。本地本轮只执行静态、编译和导入检查，不把缺少 Docker daemon 的工作站结果冒充
运行时环境验收。P0-5 已完成代码收口，真实双 SDK、审批恢复和取消演练仍需 release 门禁；
P1-9 至 P1-11 仍保持未完成状态。当前迁移 head 已推进到
`0092_observability_evidence_closure`。

P1-8 的当前实现证据：

- HTTP、Redis queue、Worker、Run event、Worker lease、Runtime event、MCP tool log、Audit event
  和 Model usage record 共用 request/trace/span/task/run/worker/runtime correlation；即使关闭
  OTel exporter，产品上下文仍会生成并写入 durable evidence。
- Model usage record 按 `job_attempt + request_sequence` 幂等保存每次模型尝试，固定 pricing version、
  metering status、attempt outcome 和 budget decision；失败与取消也留下 missing-usage 证据。
- Audit integrity、unpriced/missing usage、预算预警/耗尽会生成 workspace notification，并在
  operations control-plane 暴露有界摘要和审计、成本、MCP、runtime、queue、worker、approval、数据
  生命周期的 drill-down 路径；原始 prompt、工具参数和凭据仍不进入摘要。
- 成本、审计完整性、Telemetry、MCP observability 与 operations event 流程回归通过；Ruff、compile
  和 import 检查通过。

`P0-2` 已在当前 checkout 完成配置、身份与租户安全收口。运行时 egress、模型 provider
和 secret redaction 现在共享统一的 URL 形状校验与 host 提取规则：带 userinfo、fragment
或敏感 query 参数的 URL 在配置和调用边界直接拒绝；生产 release API 强制 HTTPS；S3/OTEL
endpoint 也在启动校验；redacted summary、审计和 provider policy 不会回显 URL 凭据。现有
authenticated context、workspace membership、role/capability grant 和 webhook 的权限回归
继续作为身份边界证据。当前定向证据命令及结果为：

- `pytest -q --basetemp=.pytest-tmp-p0-2 backend/tests/test_architecture.py backend/tests/test_config.py backend/tests/test_runtime_egress.py backend/tests/test_model_provider_base_url.py`：80 passed；
- `pytest -q backend/tests/test_model_provider_service.py backend/tests/test_auth_api.py backend/tests/test_authorization.py backend/tests/test_webhooks.py`：通过；
- `ruff check backend/app backend/tests/test_architecture.py`：通过；
- `lint-imports --no-cache`：8 kept, 0 broken；
- `alembic heads`：`0092_observability_evidence_closure`（单 head）；
- `git diff --check`：通过。

`P0-3` 已在当前 checkout 完成 durable queue/worker recovery 收口。Redis 只作为队列投影；
AgentRun、WorkerLease 和既有领域调度记录作为 durable 状态，维护任务可重建缺失的队列项。
claim、heartbeat、retry、dead-letter 和人工死信重放的关键转移使用原子 Redis 脚本；恢复
入队会检查 queued/processing/retry 三类活动投影，不会因旧幂等键制造重复投递；WorkerLease
对已完成/失败的 job 拒绝重复执行，Redis visibility timeout 会同步撤销对应的 Postgres
lease。stale-run、queue-governance、dead-letter、worker runner 和 agent recovery 流程均有
流程级证据：

- `pytest -q backend/tests/test_redis_queue.py backend/tests/test_worker_runner.py backend/tests/test_agent_runtime_recovery_e2e.py`：通过；
- `pytest -q backend/tests/test_operations_api.py -k stale`：通过；
- `pytest -q backend/tests/test_operations_api.py -k queue_governance`：通过；
- 受影响模块 Ruff 与 `git diff --check`：通过。

## 2. 目标架构与不变量

```text
                    ┌──────────────────────────────┐
                    │ API / CLI / Admin commands   │
                    │ validate + authorize only    │
                    └──────────────┬───────────────┘
                                   │ durable intent
                    ┌──────────────▼───────────────┐
                    │ Postgres control plane       │
                    │ tasks/runs/leases/policies   │
                    │ outbox/audit/cost evidence   │
                    └───────┬───────────┬───────────┘
                            │           │
                     queue/lock      read models
                            │           │
                    ┌───────▼───┐ ┌─────▼──────────┐
                    │ Workers   │ │ Operations     │
                    │ scheduler │ │ health/metrics │
                    │ recovery  │ │ audit/cost     │
                    └───────┬───┘ └────────────────┘
                            │ frozen run snapshot
              ┌─────────────▼────────────────────┐
              │ Agent SDK adapter + capability   │
              │ gateway + approval/policy check  │
              └─────────────┬────────────────────┘
                            │ typed runtime request
              ┌─────────────▼────────────────────┐
              │ Runtime registry                 │
              │ none / isolated / pooled /       │
              │ persistent; Docker/hosted/self   │
              └─────────────┬────────────────────┘
                            │
              ┌─────────────▼────────────────────┐
              │ isolated execution + project I/O│
              │ files/artifacts/network/limits   │
              └──────────────────────────────────┘
```

必须持续满足以下不变量：

1. Workspace 是所有数据、能力、运行时、文件、记忆和证据的租户边界；任何查询、缓存、
   worker payload、对象存储路径和工具调用都携带 workspace scope。
2. Postgres 是持久状态唯一事实源；Redis 只用于队列、锁、pub/sub、幂等窗口和短期缓存。
3. API 不执行长任务；用户或 Agent 控制的代码只在批准的隔离运行时执行。
4. 模型只能提出计划和动作；服务层负责授权、策略、状态转换、配额、幂等和副作用。
5. 凭据只在最窄的执行边界解密；日志、事件、响应和快照不含原始 secret、完整授权头或
   带凭据 URL。
6. 高风险动作默认 fail closed，并留下审批、审计或安全证据。
7. 每个状态机只有一个 owner；SDK/基础设施适配器只做协议转换、策略和证据，不复制 SDK
   已有能力。
8. `execution_mode=none` 明确表示没有命令、stdio MCP、本地文件或本地代码能力；不能
   隐式降级为宿主机执行。

## 3. 工作流、状态机与责任归属

目标状态需要和现有枚举逐项对照；如果命名不一致，应直接迁移调用方和数据，不增加旧名
别名或双写路径。

| 状态机 | 唯一 owner | 目标状态/关键转移 | 恢复规则 |
| --- | --- | --- | --- |
| Run | orchestration run service | `queued → running → waiting_approval/waiting_runtime → running → succeeded/failed/cancelled` | 依据 lease、心跳和最后事件判定 stale；恢复动作幂等且不重放已确认的外部副作用 |
| Worker lease | runtime worker service | `offered → claimed → heartbeat → released/expired/revoked` | 过期 lease 只能由 recovery 流程回收；drain 阻止新 claim，不破坏运行中的 lease |
| Runtime lease | runtime environment service | `requested → provisioning/ready → in_use → draining → released/failed` | 释放必须清理容器、卷、临时目录和凭据注入证据；清理失败进入可观测的 remediation 状态 |
| Approval | approvals service | `requested → pending → approved/rejected/expired` | 审批决定绑定 run snapshot 和动作 fingerprint；过期或快照不匹配时拒绝继续 |
| Update job | host updater/control-plane service | `planned → ready → applying → waiting_recovery → succeeded/rolled_back/restored/failed` | 通过 fsynced journal 恢复；不重放模型/工具调用，不使用未验证的 release 或数据库版本 |
| Backup/restore | workspace lifecycle service + host updater | `requested → running → verified/failed`, restore 需显式确认数据丢失窗口 | 备份必须有 checksum、来源版本和恢复验证证据；恢复前后保留可审计的文件快照 |

所有状态转移都要：

- 在同一事务中更新 durable 状态并写出必要的事件/outbox；
- 带 actor、workspace、correlation/run ID 和幂等键；
- 对重复请求返回已有结果，而不是重复外部副作用；
- 在失败时保存可诊断的、已脱敏的原因和下一步 operator action。

## 4. 按依赖排序的功能点

每个功能点是一个独立提交单元。完成一个点后才进入下一个点；同一提交不混入无关的目录
整理或格式化。下列 commit 名称是建议的固定前缀，实际提交必须保留一个清晰的功能边界。

### P0-1 合同、边界和架构门禁

目标：冻结领域合同，阻止继续产生重复实现和反向依赖。

实现要点：

- 对 API、orchestration、Agent runtime、capability、runtime execution、project I/O、
  audit event 和 cost usage 建立 ownership matrix；每个 state machine、policy evaluator、
  redaction helper 和 evidence writer 只保留一个 owner。
- 继续使用 `backend/app/api`、`backend/app/core`、`backend/app/domains`、
  `backend/app/runtime`、`backend/app/observability` 五个顶层边界；只有独立生命周期、
  持久化边界、安全边界或可替换适配器才保留子包。
- 增加/收紧 Import Linter 与架构负向测试：API 不导入 Docker/SDK 实现，领域不导入 API，
  Worker 不依赖 API response schema，基础设施不反向调用路由。
- 清除已经迁移的空目录、one-line forwarding module、旧内部别名和重复实现；不留下兼容
  shim。
- 对迁移 head、公共错误 envelope、分页、事件 envelope 和 workspace scope 做 contract
  snapshot。

验收：架构负向测试通过；`rg` 能找到的每个旧入口都有明确替换结果；不存在仅为保留旧
import 而存在的模块；`git diff --check` 通过。

建议提交：`platform-contract-and-architecture-gates`

### P0-2 配置、身份与租户安全收口

目标：让所有执行和运维操作从明确的身份、workspace scope 和经过校验的配置开始。

实现要点：

- 统一 authenticated context、workspace membership、role/capability grant 和 platform
  admin policy 的解析入口；服务不得自行从裸 ID 推断租户。
- 配置在启动/变更时校验 URL、路径、镜像、网络、资源上限和生产安全开关；响应只返回
  redacted settings summary。
- 凭据使用已有加密或外部 vault 引用；加入 rotation/revocation 的审计和执行边界，禁止
  把原始 secret 放进任务 payload、缓存、日志、导出或 trace attributes。
- 将 SSRF、URL credential、任意路径、任意镜像、host socket、危险网络和跨 workspace
  访问全部建成 fail-closed policy checks。

验收：伪造 workspace/resource ID、禁用凭据、跨租户缓存键、secret-bearing URL 和不安全
生产配置均被拒绝并产出脱敏证据。

建议提交：`platform-identity-and-security-closure`

### P0-3 Durable queue、worker lease 与故障恢复

目标：Worker 重启、重复投递、网络抖动和死 worker 不会丢任务、超卖配额或重复副作用。

实现要点：

- 明确 Postgres job/run 状态与 Redis queue item 的 reconciliation 方向；Redis 丢失时可由
  durable 状态重建，不能反过来把短期队列当事实源。
- 统一 claim、lease、heartbeat、ack、retry、dead-letter、cancel、drain 和 shutdown；
  每个 job 绑定 workspace、run snapshot、attempt 和 idempotency key。
- 定义 stale detection 与 recovery worker：只回收确实过期的 lease，保留原始 attempt、
  原因和 operator action；禁止自动重放已经提交的不可逆外部动作。
- 将并发、优先级、runtime-space、member、CPU/memory/storage 和 artifact quota 的预留
  与释放放在同一套事务/锁规则中。
- 暴露 queue governance、stale runs、dead letters、worker capacity 和 reconciliation
  的 metadata-only operations API。

验收：并发 claim 只有一个赢家；进程终止后任务可恢复或明确失败；重复消息不产生第二次
外部副作用；取消、失败、超时和 stale recovery 都释放所有相关 reservation/lease。

建议提交：`platform-durable-worker-recovery`

### P0-4 Runtime registry、Docker 池与隔离闭环

当前代码状态（2026-09-16）：已实现 runtime registry 的组合根装配、四种执行模式的
fail-closed 合同、Docker live sandbox session、OpenAI Agents SDK `SandboxRunConfig` 注入、
pooled run workspace/reset/reclaim、digest-pinned image guard、非 root/只读根文件系统与
资源限制，以及 workspace/team/task/project/runtime-space 身份元数据。Claude Agent SDK 不
再接收宿主机 CLI 路径；其本地执行能力不稳定时直接关闭，命令、stdio MCP 和项目文件只可
通过已授权的 OpsMesh runtime gateway。待补证据：真实 Docker pool 并发复用、清理失败隔离、
跨租户拒绝和实际容器 hardening 的流程验收。

目标：所有需要执行环境的 Agent、MCP 和项目文件操作都通过一个运行时合同完成。

实现要点：

- 统一 `none`、`isolated`、`pooled`、`persistent` 的能力授权和资源生命周期；`none`
  不创建 runtime lease，也不允许 shell、stdio MCP、项目文件 staging 或本地代码执行。
- 复用现有 `backend/app/runtime` 环境、pool、policy、project-file 和 self-hosted 边界；
  Docker 池只负责预热成员、租约、Run 专属目录、清理和资源限制，不承担 Agent 编排。
- 所有容器使用允许的、按 digest 固定的镜像；强制非 root、只读根文件系统、capability drop、
  CPU/memory/pid/storage/timeout 限制和显式 egress policy。
- workspace/team/task/runtime-space identity 写入运行时 metadata；目录、卷、网络和
  临时凭据按 Run 隔离；释放时清理并保存证据。
- provider-native sandbox（如果稳定 SDK 合同可用）与 Docker/self-hosted backend 都
  通过同一 OpsMesh runtime contract 注册；不可用时拒绝，不静默切到宿主机。

验收：容器池可复用但不会复用另一个 Run 的文件/凭据；跨 workspace、越权网络、超额资源、
容器清理失败和 runtime revoke 都有拒绝/恢复/审计证据；API/Worker 主机没有用户代码执行。

建议提交：`platform-runtime-isolation-and-pool-closure`

### P0-5 Agent SDK 生命周期与执行证据

目标：OpenAI Agents SDK、Claude Agent SDK 和未来 provider 只通过一个产品合同运行，且不
重复实现 SDK 已提供的会话、工具、审批、事件和压缩能力。

实现要点：

- 在 `domains/agents` 与 Agent runtime contract 中固定 provider-neutral request、response、
  event、session、continuation、approval 和 cancellation 类型；厂商对象只存在于 adapter。
- OpenAI/Claude adapter 直接调用官方 SDK public API；OpsMesh 只负责 workspace authorization、
  snapshot、redaction、runtime injection、durable evidence 和 product event mapping。
- 每个 Run 冻结 provider/model、prompt/tool/resource grants、runtime mode、memory/context
  budget 和 capability fingerprint；续接、压缩、handoff、人工接入和审批恢复必须引用同一
  snapshot。
- 处理 worker restart、SDK timeout、cancel、partial output、tool result continuation、
  provider unavailable 和 malformed response；不得隐式 provider fallback。
- 授权快照使用 v3：冻结根 Agent runtime Profile、解析后的 provider 与 fallback 候选；运行时
  只检查实时 Profile 是否仍可执行，不读取其可变提示词或模型设置。
- Claude 审批恢复按精确 `tool_call_id + tool_name` 一次性消费，禁止扩大权限；OpenAI SDK
  interruption 也要求唯一匹配。工具决定在成功注入后持久化消费时间，worker 崩溃可重放，
  已消费决定不会污染下一次续接。
- OpenAI/Claude 取消路径同时中断 SDK 和活动工具，SDK 错误按限流、传输不可用、格式错误、
  最大轮次和策略错误分类；只有明确的 Provider 错误才允许 fallback。
- 对现有代码做一次“SDK 已有能力 vs 自研重复逻辑”清单，删除等价的会话/压缩/重试/事件
  解析实现；保留的 OpsMesh 代码必须能说明产品策略或证据价值。

验收：同一 Run 可安全续接；压缩/记忆/上下文不会跨 workspace；审批拒绝不会调用工具；
SDK/模型错误保留可诊断事件但不泄露 secret；恢复不会修改已冻结的授权快照。

建议提交：`platform-agent-sdk-lifecycle-closure`

### P0-6 Capability、工具和 MCP 生产闭环（不含插件中心）

状态（2026-09-17）：代码合同已完成。MCP server、tool allowlist 和 credential reference
使用单调配置版本；有效 catalog 冻结精确 server/tool 版本、credential binding、参数和策略，
未健康、健康过期、缺少凭据或 transport 配置不完整的工具不会进入 Run 的有效执行清单。
执行前再次校验实时 active 状态、版本、workspace、health 和凭据绑定，配置重连、凭据轮换或
禁用后，旧 Run 均 fail closed。调用的完成、失败、等待审批、自托管等待和策略拒绝同时写入
Run event、调用日志和 WORM audit，并以 `agent_run_id + trace_id + span_id` 关联模型成本与链路。
2026-09-18 后续阶段已接入签名远程插件安装；生命周期和限制以
[自动化与扩展合同](automation-and-extension-contracts.md) 为准。旧元数据插件不会自动获得执行权限。

本地定向证据：MCP 授权到执行/证据输出、MCP 健康拒绝、官方 SSE 调用和 Marketplace
plugin 安装四条既有产品流程通过；受影响模块 Ruff、mypy、compile、架构/导入边界、单
Alembic head 和 `git diff --check` 通过。
真实远程 MCP、隔离 stdio 与 PostgreSQL upgrade/downgrade 仍由 release tag 门禁验收。

目标：动态工具清单、资源、参数、部门/团队授权和 MCP 执行都使用现有 capability plane，
不再增加平行的插件系统。

实现要点：

- capability catalog 是工具/技能/MCP/资源的有效视图；来源可以是 workspace、team、agent、
  run grant，但最终由 gateway 计算一次并写入 Run snapshot。
- 参数 schema、默认值、locked fields、allowlist、quota、approval policy 和 resource scope
  都在调用前验证；模型输出永远不是授权来源。
- 使用官方 MCP Python SDK 处理 HTTP/SSE/Streamable HTTP 和隔离 stdio；stdio 只能进入已
  绑定的 runtime/self-hosted job，不能由 API/Worker 直接 spawn。
- MCP server/tool health、credential readiness、connection reconfiguration、rotation、
  stale blocking、调用超时/大小限制、工具审计和成本/trace 关联必须统一走既有边界。
- `Marketplace` 负责发现、审核和安装；远程插件通过后续扩展合同绑定已有资源，
  不动态导入第三方 Python，不将未安装的元数据声明当成执行授权。

验收：禁用 server/credential/tool、过期 health、错误参数、跨 workspace grant、无 runtime
stdio 和超额调用均 fail closed；每次允许/拒绝调用都有 run event、audit 和可追踪 correlation。

建议提交：`platform-capability-and-mcp-production-closure`

### P1-7 Workspace 数据生命周期与灾备（已完成）

目标：文件、artifact、memory、项目版本、导出和备份在正常、失败、升级和恢复后都保持一致且可追溯。

实现要点：

- 复用 workspace storage、project I/O、archive/import/export、artifact version 和 memory
  index 边界；文件元数据、对象内容、运行时临时目录和审计证据不能混成一个存储源。
- 导出使用 `workspace-export.v2`，包含 payload checksum、workspace scope、项目和三层 memory
  元数据、依赖关系和敏感字段策略；文件/artifact 还包含独立 object inventory 与 checksum。导入
  先验证 manifest/object checksum，再 preview conflict，由显式 resolution map 应用，禁止静默覆盖
  或跨租户重绑定。embedding 向量和凭据引用不进入归档，导入后由 memory index 重建。
- 备份包括数据库、配置和必要的对象存储；workspace archive job 记录来源 release/schema、大小、
  archive/manifest hash、对象 inventory、验证状态和敏感字段策略，主机级 `BackupStore` 继续
  记录完整 database/storage/config inventory；恢复演练创建 disposable workspace，真实导入并
  校验关系、对象 checksum 和 artifact supersedes，随后清理临时数据。
- retention 只作用于明确范围，默认要求最近一次成功备份；软删除、清理和失败都留 evidence。
- 运行队列由 durable run rehydration 重建，memory embedding 由 pending 状态重建，audit chain
  保持独立，artifact supersedes/project version 关系随归档导入重映射。

验收：v2 导出/导入可 round-trip 项目、文件、artifact 和 memory 元数据；损坏或篡改 manifest、
object 或主机备份被拒绝；disposable restore drill 后 workspace scope、artifact version、审计
证据和 durable queue 不受污染；保留策略不会删除未备份或仍被运行引用的数据。

建议提交：`platform-workspace-lifecycle-and-recovery`

### P1-8 日志、监控、链路追踪、审计与成本闭环

目标：任何用户可见失败和高风险动作都能从一个 correlation 追到 durable evidence、资源
消耗和修复建议。

实现要点：

- 继续使用 OpenTelemetry、Prometheus client、Loki/Tempo/Collector/Grafana 的既有集成；
  统一 `trace_id`、`span_id`、request/task/run/worker/runtime IDs，控制 label/cardinality。
- 结构化日志只记录 metadata 和 redacted error；原始 prompt、token、credential、文件内容
  和完整 provider URL 不进入日志或 trace attribute。
- 审计事件独立于 provider tracing，按 workspace 链式 hash/WORM 保存；提供 scheduled/on-demand
  integrity verification、stale/invalid alert 和 operator remediation。
- 每个 model attempt 持久化 normalized usage、pricing version、currency、status（priced、
  unpriced、missing_usage）和 budget decision；价格变化不得改写历史账本。
- 运营视图覆盖 API/queue/worker/runtime/MCP/approval/audit/cost/data-lifecycle，并提供
  drill-down link 而不是把所有原始 payload 塞进 aggregate。

验收：从 API 请求能找到对应 worker/run/tool/model spans；日志、trace、audit、cost 四处的
敏感字段一致脱敏；链断、无价格、missing usage、预算耗尽、队列堆积和 telemetry exporter
失败都会触发可操作告警。

建议提交：`platform-observability-audit-cost-closure`

### P1-9 Release supply chain 与 tag 门禁

目标：`push tag → 完整门禁 → 一次构建 → 签名/证明 → 发布 → 公共下载复核` 是唯一发布路径。

实现要点：

- tag、版本、commit、迁移 head、wheel/sdist、native CLI、server archive 和两个 OCI image
  组成一个 immutable release identity。
- Release Gate 在同一 tag 上执行 lint、strict mypy、完整 pytest、真实 Postgres migration/
  schema check、包构建、重定位安装、镜像 digest probe 和安全检查；完整 pytest 只在 release
  gate 运行，日常开发仍使用定向测试。
- packages/images 只构建一次，以 run/attempt-specific candidate tag 保存；签名/发布 job 下载
  已验证产物并 promote 同一 digest，禁止重新构建或使用 mutable `latest`。
- 使用 GitHub Actions 官方 attestation、最小 job permissions、固定 Action SHA、SBOM 和
  checksum；已存在的 draft asset 只允许 hash/size 完全匹配的幂等补传。
- 发布后通过真实公共 release source 下载每个资产，校验 inventory、size、SHA-256、签名/
  provenance、tag/commit/workflow；失败则 release 不报告成功。

验收：失败门禁不会发布任何可安装 release；重跑 publication 不会重建不同字节；任何 digest、
  版本或资产冲突都 fail closed；candidate tag 不参与更新发现。

建议提交：`platform-release-gate-and-supply-chain`

### P1-10 Managed deployment、更新、回滚与恢复

目标：Compose 和 systemd 单主机拓扑都能在维护窗口内安全安装、升级、中断恢复和回滚。

实现要点：

- 保持 immutable release directories、独立 updater、installation journal/status、外置配置
  和数据目录；API/Worker 不获得 systemd/Docker host authority。
- `update plan` 先检查 release provenance、数据库兼容矩阵、配置 fingerprint、备份要求和
  maintenance/data-loss 声明；`apply` 只接受已批准的 fingerprint/idempotency key。
- updater 在每个外部动作前后 fsync checkpoint；SIGKILL、端口占用、启动失败、网络中断和
  数据库不可用都能被识别并安全 resume/rollback/restore。
- rollback 只允许目标应用明确支持当前 schema；restore 必须显式确认数据丢失窗口，并保留
  restore 前文件供人工 salvage。
- 更新期间不重放模型/工具调用，不强杀未完成 Agent 任务；队列、审批和 run 状态由产品控制面
  独立保留。

验收：在 disposable Compose/systemd 主机完成 fresh install、跨版本 upgrade/rollback、
  updater kill/resume、occupied port rollback、数据库/文件恢复和再次升级；每一步都有审计
  与可下载的脱敏验证报告。

建议提交：`platform-managed-deployment-recovery`

### P1-11 可靠性演练、SLO 与运行手册

目标：把“代码通过”转换成可重复的生产操作证据。

实现要点：

- 建立 staging acceptance matrix：API/Worker 重启、Redis 丢队列、Postgres 短暂不可用、
  Docker daemon/reclaim、provider timeout、MCP stale/断网、审批超时、磁盘/配额耗尽、
  self-hosted revoke 和 telemetry backend 故障。
- 每个故障场景记录触发条件、预期状态转换、禁止动作、恢复命令、审计/指标证据和退出条件；
  运行手册只能调用固定的 CLI/API 操作，不能把任意 shell 字符串下发给 updater。
- 设定并记录部署级 SLO/告警阈值：readiness、queue age、lease age、recovery duration、
  runtime saturation、approval age、backup freshness、audit verification age、unpriced
  usage 和 telemetry export failure。
- 每次阶段验收生成带 commit、migration head、image digest、测试命令和环境摘要的 evidence
  artifact；不以“本地看起来正常”替代证据。

验收：所有 P0 演练至少有一次可重复成功记录；故障不会造成跨租户泄露、未审计副作用、
  未释放 reservation 或不可恢复的 host 资源；runbook 与实际 CLI/API 一致。

建议提交：`platform-reliability-drills-and-runbooks`

## 5. 依赖与执行顺序

```text
P0-1 contracts/gates
        ↓
P0-2 identity/security
        ↓
P0-3 queue/worker recovery
        ↓
P0-4 runtime isolation/pool
        ↓
P0-5 agent SDK lifecycle ─────┐
        ↓                     │
P0-6 capability/MCP closure ─┘
        ↓
P1-7 data lifecycle ──┐
P1-8 evidence plane ───┼──> P1-9 release supply chain
                       │             ↓
                       └──────> P1-10 managed delivery
                                      ↓
                               P1-11 reliability drills
```

允许并行的只有不共享迁移、状态机或同一文件边界的工作；如果两个工作项修改同一状态机，
先合并 owner 合同，再继续实现。插件中心不得插入上述依赖链。

## 6. 数据库、事件和迁移纪律

- 先检查现有模型和 migration，再决定是否新增表/字段；能复用现有 durable record 就不另
  建平行表。
- 每个 schema change 使用 Alembic，保持单 head、可 downgrade，并在真实 PostgreSQL 上做
  upgrade/downgrade/schema drift 检查；SQLite 只用于不依赖 PostgreSQL 语义的纯逻辑测试。
- 状态转移、审计、outbox、usage/cost 和 reservation 的提交边界必须可见；不能在无文档的
  helper 中隐藏跨领域 commit，也不能持有数据库事务进行不受控的外部网络调用。
- 事件 payload 使用 typed envelope 和 bounded metadata；任何导出、日志、trace、API 和
  worker response 都复用同一个 redaction policy。
- 不写兼容字段、旧 endpoint、版本分支或静默 fallback。合同变化在同一个提交里更新所有
  调用方、测试、迁移和文档。

## 7. 测试与发布门禁

### 日常每个功能点

- 代码改造和目录收敛默认不新增、也不反复执行单点 `pytest`；只有用户可见行为或跨边界
  合同发生变化时，才在已有流程测试中补充一条端到端路径。
- 流程测试只覆盖从请求入队、创建 team/project（适用时）、编排/执行到输出的完整链路，
  以及该链路可观察的拒绝、重试/恢复、workspace isolation、幂等和 secret redaction。
- 运行触及文件的 `ruff check`、必要的 `mypy` 子集和 `git diff --check`。
- 运行架构/迁移/配置检查（如果功能点跨越对应边界）。
- 不在本地开发阶段运行全量 pytest，不使用真实 provider credentials。

### Release tag

- 在不可变 tag 上运行完整 pytest、Ruff、strict mypy、真实 PostgreSQL、镜像/包构建、
  公共下载验证和 Compose/systemd managed acceptance。
- 只有所有门禁成功才允许签名、发布和更新发现；失败时保留脱敏报告，不移动已有 tag。
- 发布证据必须包含：source commit、tag、migration head、package hashes、image digests、
  workflow run、runner policy、测试摘要和 deployment acceptance 摘要。

### 流程测试中的负向分支

以下只作为完整业务流程中的可观察分支，不为每个类、函数或 helper 单独建测试：

- 跨 workspace resource/task/team/member/file/artifact/runtime/tool/memory ID；
- disabled/revoked credential、MCP server、runtime、worker 和 provider；
- stale snapshot、重复 idempotency key、冲突的结果提交和重复队列 item；
- secret、完整 URL、容器 ID、文件内容、provider token 和 trace attribute 脱敏；
- 超额 CPU/memory/storage/active-run/tool-call、非法网络和未批准高风险动作；
- Worker/API/Updater 重启、中断、恢复、回滚和清理失败。

## 8. 阶段完成定义（Definition of Done）

只有全部条件满足，才能把本阶段标为完成：

- [ ] P0-1 到 P0-6 的合同、状态机、隔离和 SDK 委托均有代码、必要的流程证据或静态检查证据。
- [ ] P1-8 到 P1-11 的证据平面、发布、托管更新和演练均有实际环境证据；P1-7 已有当前
  checkout 的流程证据，但完整灾备主机验收仍属于 release gate。
- [ ] 没有 API/Worker 宿主机执行用户或 Agent 控制代码，没有未授权的 host socket、网络或
  文件访问。
- [ ] 所有状态转移、配额 reservation、审批、高风险拒绝、清理失败和恢复动作可审计、可
  重放查看但不会重放不可逆外部副作用。
- [ ] 当前 commit 的迁移 head、包/镜像 digest、测试结果和文档状态一致；没有把历史 release
  的结果冒充当前分支结果。
- [ ] 生产部署可从固定 release 安装，且经过升级、中断、回滚和恢复后仍能通过 readiness、
  schema、数据完整性和 observability smoke。
- [ ] 文档、runbook、操作 API 和告警字段与实际实现一致；过时的完成标记已更新或删除。
- [ ] 每个功能点都有单独 commit；全量 pytest 只作为 release gate 运行并记录确切结果。

## 9. 下一阶段接口（仅记录，不在本阶段实现）

完成本阶段后，再单独启动 Plugin Center：外部仓库、版本化 manifest、独立 SDK/CLI、签名
artifact、声明式/远程/MCP/OCI 组件、workspace 安装授权和无重启版本激活。Plugin Center
必须复用本阶段已经收口的 Marketplace、Capability Catalog、Runtime Registry、Audit、Trace、
Cost 和 Approval 合同，不得重新实现一套权限、运行时或发布系统。
