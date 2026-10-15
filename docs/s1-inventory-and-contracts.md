# S1 完整盘点与合同：实施记录

日期：2026-10-16
状态：S1 已完成（implemented + verified）。本记录是 V4 方案第 16.2 节 S1 的验收材料，不替代 V4 设计本身；后续步骤按第 16.2 节顺序实施，本文件只登记 S1 实际完成范围。

## 1. 前置条件确认

### 1.1 代码基线（本地仓库）

| 项目 | 实际情况 | 结论 |
| --- | --- | --- |
| 分支 / HEAD | `codex/temporal-native-execution` @ `7ebaff04`（snapshot workspace before identity refactor） | 归档快照提交，与 V4 声明一致 |
| `src/opsmesh` | 不存在（仅剩 `src/opsmesh.egg-info`） | 参考源码已迁出，处于重新实施前状态 |
| `backup/opsmesh` | 841 个源文件（排除 `__pycache__`/`.pyc`） | 与 V4 第 16.1 节声明一致 |
| `backup_2/opsmesh` | 125 个源文件（排除 `__pycache__`/`.pyc`） | 与 V4 第 16.1 节声明一致 |
| `docs/` | 仅 `source-refactor-plan-v4.md` | 单一设计入口 |
| 顶层空源码目录 | `backup/opsmesh/runtime/`（含 commands/environments/sessions/spaces 子目录）仅存 `__pycache__`，无源文件 | 属已迁出后遗留空目录，无内容可迁移；按规范不进入新树 |
| 仅存 `__pycache__` 的目录 | `governance/credentials/`、`governance/observability/`、`platform/capabilities/`、`platform/capability_catalog/`、`platform/marketplace/routes/` | 同上，无源文件可迁移 |

### 1.2 测试服务器运行环境（192.168.232.129，root）

| 项目 | 实际情况 |
| --- | --- |
| 操作系统 | Ubuntu 26.04 LTS（kernel 7.0.0-38），x86_64 |
| 部署形态 | Docker Compose；`/opt/opsmesh` 为部署根，无 git 仓库（发行制品部署） |
| 当前发行 | `v0.1.0rc23`（commit `bf39a476`，数据库修订 `0123_mcp_call_diagnostics`），符号链接 `current -> releases/v0.1.0rc23-bf39a476…` |
| 容器 | `opsmesh-api-1`、`opsmesh-worker-1`、运行时容器、`opsmesh-redis-1`、`opsmesh-postgres-1`（pgvector/pgvector:0.8.6-pg16）全部 Up 4 天 |
| 健康检查 | `GET /api/v1/health/live` → 200；`GET /api/v1/health/ready` → 200，dependencies：database/redis/storage/worker_queue/workers_online 全部 ok |
| 主机 Python | 3.14.4（应用本体运行于发行自带 venv，Python 3.12） |
| 主机服务 | `opsmesh-updater.service` 运行中（旧架构应用内更新 daemon，V4 删除对象，S13 由 `deploy/server/opsmesh-server` 替代） |

结论：服务器承载重构前基线发行且业务正常运行，可作为 S2 起的真实运行/集成验证环境；其上运行的 `platform.updates` daemon 属 V4 明确删除项，不影响 S1 盘点。

### 1.3 依赖配置

- 本地：`uv.lock`（567,041 字节）与 `pyproject.toml` 随仓库维护，`.venv` 存在。
- 服务器发行：`requirements.lock.txt` 1,419 行，全部依赖带 SHA-256 哈希锁定（alembic 1.18.4、anthropic 1.4.0 等）。

### 1.4 前置条件结论

代码基线（两份参考源码、单一设计文档、归档状态）、运行环境（API/Worker/Redis/Postgres 健康）、依赖配置（哈希锁定）均符合 V4 规定的重构前置条件。S1 不依赖服务器执行代码变更；服务器仅作为后续步骤验收环境在本文登记。

## 2. 盘点方法与范围

- 方法：对两份参考源码逐文件阅读（模块 docstring、导入与主体逻辑），按 V4 第 9–11 节目标树、第 14 节去向表、第 2 节职责与第 12 节依赖约束确定每个文件的去向。共 10 个并行调研任务覆盖全部目录，逐文件产出"功能 / 目标 / 决定 / 理由"。
- 决定定义：
  - `keep`：职责保留，仅按 V4 合同做接口与命名校准；
  - `rewrite`：职责保留，但按 V4 合同重新实现（如 HTTP routes 变为扩展 exports、ORM 变为包 schemas + repository）；
  - `merge`：内容折叠进另一目标文件（消除重复实现与浅层分包）；
  - `delete`：V4 无目标（废弃能力、兼容层、被替代实现）。
- 交付物：`docs/s1-reference-file-mapping.csv`（966 行 = 表头 + 841 + 125 条逐文件记录；列：source_tree、source_path、function、v4_target、decision、reason、duplicate_in_other_source）。
- 重复来源处理：两份源码职责相同的文件在 CSV 的 `duplicate_in_other_source` 列互相标注（104 条互为重复），V4 目标只有一个实现，禁止双轨。

## 3. 盘点统计

### 3.1 决定分布

| 来源 | keep | rewrite | merge | delete | 合计 |
| --- | --- | --- | --- | --- | --- |
| backup（841） | 272 | 72 | 433 | 64 | 841 |
| backup_2（125） | 89 | 31 | 5 | 0 | 125 |
| 合计（966） | 361 | 103 | 438 | 64 | 966 |

### 3.2 目标模块分布

| V4 目标 | 文件数 | 说明 |
| --- | --- | --- |
| bundled/extensions | 189 | task-collaboration 49、team-collaboration 31、model-memory 21、workflow-planning 16、notification-delivery 15、document-knowledge 14、conversation-app 9、announcement-center 8、expert-matching 6、capability-marketplace 6、release-catalog 6、review-process 5；coding 工具等 bundled/tools 在 S12 按第 11 节树新建 |
| src/opsmesh/execution | 145 | runs 70、runtime 55、sessions 16 及根合同 |
| src/opsmesh/workspaces | 122 | capabilities 36、projects 33、model_providers 17、members 12、capacity 8 等 |
| src/opsmesh/platform | 77 | backups 28、operations 17、mail 8 等；announcements/marketplace/releases/updates 已剥离 |
| src/opsmesh/orchestration | 71 | runs 33、triggers 22、definitions 12 及根合同 |
| src/opsmesh/shared | 64 | http 19、db 12、security 11、redis 8 等 |
| src/opsmesh/governance | 55 | approvals 13、audit 13、costs 8、policies 8 等 |
| src/opsmesh/capabilities | 53 | teams 13、skills 7、mcp 6 及根级 lifecycle/packages/validation |
| src/opsmesh/identity | 45 | auth 14、users 11 等 |
| src/opsmesh/bootstrap | 28 | 显式装配 |
| src/opsmesh/resources | 23 | files 10、artifacts 8、storage 等；knowledge/memory 已剥离 |
| src/opsmesh/telemetry | 8 | 观测 SDK 机制 |
| src/opsmesh/temporal | 8 | codec 与唯一 Outbox/dispatch |
| src/opsmesh/extensions | 2 | 新模块（安装/调用宿主），S8 按第 9 节树新建，参考来源仅 importer 回调合同 |
| deploy/server | 1 | `delivery.py` 重写为主机 CLI |
| delete | 64 | 见第 4.1 节 |

## 4. 关键架构决定

### 4.1 彻底删除（64 个文件，不保留任何兼容逻辑）

| 删除对象 | 文件 | 理由 |
| --- | --- | --- |
| 应用内主机更新 | `platform/updates/*`（7）、`bootstrap/update_cli.py`、`delivery.py` 的 updater 命令 | V4 第 2.3/11.1 节：主机安装/更新归独立部署工具；不保留更新 API、daemon、安装版本副本或启动兼容层 |
| Runtime Space / 宿主池 | `workspaces/runtime/spaces/*`（12）、`workspaces/teams/runtime_spaces.py`、`orchestration/tasks/runtime_spaces.py` | V4 第 14.1 节：自建宿主池/UID 槽/租约继续删除；环境显式创建、续接、释放 |
| working memory | `resources/memory/working.py`、`orchestration/runs/memory.py`、`orchestration/runs/authorization/memory.py`、`execution/runs/memory.py` | V4 第 14.2 节明确取消 working.py 概念，不复制 SDK Session |
| agents 根包与 workspace agents 包 | `agents/*`（2）、`workspaces/agents/*`（16） | V4 第 14.1 节：不恢复独立 agents 根包；定义归 capabilities，绑定归 workspaces/capabilities |
| 页面聚合指标 | `platform/operations/metrics/*`（6）、`platform/operations/history.py` | V4 第 14.1 节：页面聚合快照删除；observability 只做原始查询 |
| 业务流程模板 | `orchestration/definitions/templates.py` | 工作计划模板是 bundled workflow-planning 业务 |
| Task 进度投影 / 记忆索引 | `orchestration/runs/task_progress.py`、`orchestration/runs/memory_index.py` | 业务投影归 task-collaboration / document-knowledge 包 |
| 被溶解的子包标记 | orchestration 各业务子包 `__init__.py`、importers 包标记等 | 子包折叠为单模块，消除浅层分包 |

### 4.2 业务移入 bundled 扩展包（189 个文件）

知识/记忆（resources/knowledge 13 + resources/memory 21 → document-knowledge 14 + model-memory 21）、Task 协作（orchestration/tasks 全子树 52 → task-collaboration 49）、团队执行（team_execution + mailbox 27 → team-collaboration 31）、规划（planning 22 → workflow-planning 16）、补员（staffing 5 → expert-matching 6）、对话（conversations 9 → conversation-app 9）、评审（review_execution 5 → review-process 5）、公告/市场/发行（platform 25 → announcement-center 8 + capability-marketplace 6 + release-catalog 6）、通知/出站 Webhook（orchestration/alerts、webhooks、automations 部分 → notification-delivery 15）。核心 API 不再持有这些业务 routes/ORM；调用统一走第 8 节固定 export 与 orchestration.triggers。

### 4.3 核心模块重组（保留职责、按 V4 树重排）

- execution：吸收 orchestration/agent_execution（18）与 tool_execution（35）的执行职责，runs/runtime/sessions 三分治；官方 Agents/Temporal 公共接口集成。
- orchestration：仅留 definitions/runs/triggers；automations/scheduling → triggers；approvals → governance/approvals；runs/authorization、runs/scheduling、runs/steps 折叠为 access.py、scheduling 归 execution、steps.py。
- workspaces：agents/teams 分包解散；runtime 全子树移出；quotas 拆分为 identity/quotas（用户账本）与 workspaces/capacity（空间并发）。
- governance：credentials → platform/credentials；observability → platform/observability；audit/approvals/costs/policies/reviews/security_events/alerts 保留并接入扩展来源合同。
- platform：backups/importers 14 文件折叠为 importers.py，业务导入改所有者回调；resource_control → operations/resources.py（统一封禁/解封）。
- shared/telemetry/temporal：职责保留；shared 增加 files.py、serialization.py、http/client.py、http/context.py（V4 第 9 节新文件，S2 实施）。

### 4.4 新模块（S1 只定合同与边界）

- `src/opsmesh/extensions`（S8 实施）：安装/实例/单次 Invocation/宿主/state/hooks/事件发布；不复制目录或流程/订阅引擎。
- `src/opsmesh/channels`（S11 实施）：账号/身份映射/路由/连接/Inbox/Delivery；厂商协议归 bundled/channels/{qq,dingtalk}。
- `sdk/python`（S3 起）：独立 `opsmesh-sdk`；api 分组由平台 OpenAPI 生成，宿主/扩展合同手写。
- `deploy/server/opsmesh-server`（S13）：主机 CLI，替代 `delivery.py` 中的进程内 updater。

## 5. 合同确定（S1 范围：主体/作用域、对象、节点/effects、错误、事务）

以下合同以 V4 第 3、4、6、7、15 节为准，S1 逐项确认并作为后续实施检查表：

1. 主体与授权交集：扩展上下文由宿主生成只读 principal、workspace/project 引用、installation/instance/invocation ID、配置修订、允许操作范围、deadline、取消信号；有效权限 = 安装授权上限 ∩ 实例/配置所有者授权 ∩ 当前执行主体权限 ∩ 本次调用范围 ∩ 资源当前权限。普通客户端走 `X-Session-ID` 登录 Session，不引入第二种用户 Token 机制；扩展不接收用户/管理员 Session，走受限宿主委托。
2. 对象唯一所有者：CapabilityDefinition/Version → capabilities；ExtensionInstallation/Instance → extensions；CapabilityBinding/项目覆盖 → workspaces；ChannelAccount → channels；HookBinding → extensions；FlowDefinition/Revision/Run/StepAttempt → orchestration；Trigger/EventSubscription → orchestration.triggers；ExtensionInvocation/Namespace state → extensions；Run/模型 Session/Environment → execution；文件/Artifact → resources；审批/费用/审计 → governance；主机部署操作 → 外部部署工具。引用关联不复制推进状态机。
3. 节点与 effects：九类流程节点（agent/operation/condition/parallel/join/foreach/repeat/subflow/wait/expand）语义与边界按 V4 第 7.2 节；operation 每项声明 effects（无外部效果 / 可重试外部效果 / 需核对外部效果），未知效果进入 needs_attention，核对不重跑外部写入。
4. 钩子合同：固定阶段（context.prepare、model.before/after、tool.before/after、input/output guardrail、on_error/finalize），返回值仅 Continue/Patch/Block；补丁白名单字段，主体/租户/授权上限/凭据引用/审批决定/实际 usage 不可变；每次发生有持久回执，回执键含 invocation、发生序号、binding、阶段与有效输入摘要。
5. 错误合同：shared/errors.py 提供 NotFound、Conflict、Forbidden、InvalidArgument 等中性错误；扩展调用错误进入类型化拒绝/冲突/限流/未知效果；路由只做 HTTP 映射，不在 Route 执行业务策略。
6. 事务合同：PostgreSQL 为唯一事实来源；短请求同事务完成状态变更，长任务同事务写事实与 Outbox 意图、提交后由 temporal.dispatch 唤醒；Redis 不保存唯一业务状态；进程内跨所有者账本函数共用同一 PG Session 且不自行 commit；锁序为 FlowRun/步骤 → Session/Run/Invocation → 用户额度 → 空间容量 → budget，相同 owner 按稳定 ID 排序，网络 I/O 不持 DB 锁。
7. 审计合同：扩展经 `context.platform.governance.audit` 委托写入（`extension_reported` 来源），写入幂等键 `(installation_id, invocation_id, idempotency_key)`；record 与 list/get/verify 权限独立；平台自身接口授权、安装变更、审批决定、执行操作由各所有者自动审计。

## 6. 依赖图验证（声明根图/二级图无环）

对 V4 第 12.1/12.2/12.3 节声明依赖做了静态校验（脚本编码允许依赖表后做环检测与边一致性检查）：

| 检查项 | 结果 |
| --- | --- |
| 根模块图（14 个模块）无环 | 通过 |
| 二级模块图（48 个节点）无环 | 通过 |
| 第 12.3 节 mermaid 声明边均为第 12.1 表允许边 | 通过（无不一致边） |
| 禁止边（identity→platform/resources/workspaces/capabilities/execution；governance→identity/workspaces/execution/extensions；execution→workspaces/capabilities/extensions/orchestration/channels/platform；extensions→execution/workspaces/channels/orchestration）均不存在 | 通过 |
| shared 零产品模块依赖 | 通过 |

实施时（S2 起）以 import-linter 将上述表格固化为配置，并按实际导入图复核；静态 DAG 不替代运行调用链检查。

## 7. S1 验收标准核对（V4 第 16.2 节）

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| 两份来源每个文件都有决定 | 通过 | 覆盖校验脚本：expected=966、covered=966、missing=0、extra=0；`docs/s1-reference-file-mapping.csv` |
| 知识、编排 84 项目标、平台及历史产品流程均有承接或删除理由 | 通过 | 逐文件表按 V4 第 14.2/14.4/14.5 节权威表校准并交叉核对：14.2 的 24 条中 23 条存在于 backup 且全部有去向（memory/service.py 为 V2 树命名，实际由 memory/semantic.py、lifecycle.py 等承接）；14.4 的 84 条中 60 条存在于 backup 且全部有去向，其余 24 条为 V2 目标树命名（如 team_runs/ 对应实际 team_execution/、tasks/collaboration.py 对应 tasks/collaboration/ 包、automations/workflow.py 对应 automations/workflows.py），实际对应文件均在覆盖范围内；14.5 的 25 条中 22 条存在于 backup 且全部有去向（marketplace/moderation.py、updates/activities.py、updates/workflow.py 为 V2 树命名或删除项） |
| 唯一所有者明确 | 通过 | 第 5 节第 2 项对象归属表 |
| 声明根图/二级图无环 | 通过 | 第 6 节检查结果 |

## 8. 测试与回归验证

| 项目 | 结果 |
| --- | --- |
| S1 代码变更 | 无应用代码变更（仅新增两份盘点文档），不存在功能回归面 |
| 本地旧测试套件 | 无法收集：`tests/conftest.py` 导入 `opsmesh` 失败（`ModuleNotFoundError: No module named 'opsmesh'`）。这是源码迁出后的预期状态；旧测试按 V4 第 14.3 节随各步实施删除/替换，不允许依赖 backup 运行 |
| 服务器业务系统 | 重构前发行 v0.1.0rc23 正常运行：health live/ready 均 200，五个容器 Up，依赖探测全部 ok |
| 结论 | S1 阶段"无功能回归"以系统级健康检查与零代码变更共同证明；单元/集成回归验证从 S2 起随目标代码建立执行，真实 PG/Temporal/沙箱验证按第 16.4 节登记 |

## 9. 状态与后续

- S1 状态：**implemented + verified**（分析类交付物，验证方式为覆盖校验与依赖图静态检查）。
- 待关闭集成门槛：S2 公共机制起按第 16.2/16.3 节逐项验收；S1 不替后续步骤背书。
- 已知待实施时定点项（不影响 S1 通过，登记供对应步骤处理）：
  - `platform/backups/importers/*` 的所有者回调合同在 S13 与各所有者导出/导入合同一起实现；
  - `bootstrap/dependencies` 类装配细节（temporal client provider、gateway policy）在 S2 装配时落位；
  - `shared/utils.py` 中非序列化助手（值强制等）在 S2 按 contracts.py/serialization.py 边界拆分；
  - 旧 `tests/`、`migrations/`、`scripts/`、`deploy/`、`operator/` 的逐项适用性判断随对应步骤进行，第一版以空库初始迁移封版（V4 硬性规则 4）。
