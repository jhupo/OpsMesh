# S6 空间与项目基础事实交付记录（2026-10-17）

分支：`refactor/s5-capabilities-resources`（基线 `316ab62d`，S5 关闭提交，均未推送）。
设计依据：`docs/source-refactor-plan-v4.md` 第 16.2 节 S6 行及第 9、11、12、13、15 节；
参考源码为 `backup/opsmesh` 与 `backup_2/opsmesh`（只读，不参与运行与检查）。
本记录区分设计与实现：以下"交付物"均为本分支实际提交的代码、迁移、生成物与证据。

## 1. 范围与目标

按 V4 16.2 S6 行实施 `workspaces` 模块，不越界：

- 成员/邀请：空间角色 owner/admin/member/viewer、邀请创建/接受/吊销/过期重发、
  自离与管理移除；空间 owner/admin 对项目角色的继承。
- 空间与项目 CRUD/角色：创建（同事务坐实 owner 与容量账户）、更新、归档
  （读保留、写拒绝）、转移所有权（唯一变更途径）、软删除；项目创建/可见性/
  归档/删除与项目成员、项目邀请。
- credentials：加密凭据创建/轮换/撤销；明文只在探测 Activity 内经
  `resolve_credential_secret` 解密，API/事件/审计只暴露指纹与元数据。
- capacity：空间容量账本（concurrency/environment 两类），账户行锁串行化 +
  幂等键重放；与 identity 用户额度各记一次，互不读取。
- model_providers：OpenAI 兼容连接、默认模型解析、探测 Workflow + Activity
  （claim 解密、httpx 核对、结果回写）。
- notifications：工作空间收件箱（去重创建、已读/归档回执、偏好）。
- 范围授权接入：resources 的 `ScopeAdmission` 与治理审计范围读取经
  bootstrap 装配为基于 `workspaces.members.access` /
  `workspaces.projects.members.access` 的真实成员校验；平台管理员不自动取得
  租户权限。

未实施且明确不做（记入文末待关闭集成门槛）：能力绑定与项目配置版本/执行组装
（S9）、容量等待引用与 dispatch 唤醒（S7）、host.jobs 委托（S10）、通知分批
投递（channels）。

## 2. 交付物

### 源码（新增 45 个 Python 文件）

| 位置 | 内容 |
| --- | --- |
| `src/opsmesh/workspaces/`（根 5 文件） | `models`（Workspace：active/archived/deleted）、`schemas`（CRUD/转移合同 + `CapacityDefaults`）、`service`（创建/列表/读/更新/归档/转移/删除）、`routes`（6 条空间路由）、`__init__` |
| `members/`（6 文件） | `models`（成员复合 PK + 部分唯一 owner 索引；邀请状态机 pending/accepted/revoked/expired 与过期推导）、`access`（唯一空间权限实现：`space_role`/`require_workspace_member`/`lock_workspace`）、`schemas`（Space* 视图）、`service`（角色/移除/邀请/接受/吊销）、`routes`（成员 + 邀请两组路由） |
| `credentials/`（5 文件） | `models`（ciphertext/fingerprint/key_id/revision/status）、`service`（创建/轮换/撤销/解析；撤销即拒、跨空间拒）、`schemas`、`routes` |
| `capacity/`（5 文件） | `models`（账户 + 预留两表）、`service`（`ensure_accounts`/`set_limit`/`reserve`/`release`/快照；`CapacityExceededError` 429）、`schemas`、`routes` |
| `model_providers/`（7 文件） | `models`（连接/目录/探测状态）、`service`（创建/默认/探测暂存/结果回写）、`activities`（claim→httpx→回写）、`workflow`（重试 3 次）、`schemas`、`routes` |
| `notifications/`（5 文件） | `models`（收件箱 + 偏好）、`service`（去重创建/列表/已读/归档）、`schemas`、`routes` |
| `projects/`（5 文件） | `models`（复合唯一 (workspace_id,id)）、`schemas`、`service`（创建坐实 admin 席位/可见性/归档/删除）、`routes` |
| `projects/members/`（6 文件） | `models`（席位 + 项目邀请，复合 FK 级联）、`access`（唯一项目权限实现：空间 owner/admin 继承项目 admin）、`schemas`（Project* 视图）、`service`、`routes` |

### 修改（受影响完整调用链）

- `src/opsmesh/governance/audit/{service,routes}.py`：新增 `AuditReader`/
  `AuditScopeAdmission` 协议、`RejectingAuditScopeAdmission` 与
  `authorize_audit_read`（管理员全量/非管理员仅成员范围/无范围仅管理员）；
  审计 list/get 接入范围准入。
- `src/opsmesh/bootstrap/routers.py`：新增 `WorkspaceScopeAdmission` 与
  `WorkspaceAuditScopeAdmission`（委托两个 access 唯一实现），注册 12 个
  workspaces 路由。
- `src/opsmesh/bootstrap/models.py`：注册 12 个 S6 ORM 模型（含 `Workspace`
  本体——真实 PG `alembic check` 暴露漏注册后补齐，见第 7 节）。
- `src/opsmesh/bootstrap/worker.py`：`feature_registrations` 装配各步骤的
  Workflow/Activity 注册集（审计、邮件、模型探测），`build_feature_worker`
  消费该集；`tests/bootstrap/test_worker_registration.py` 锁定注册面
  （暂存了 Workflow 而未注册的步骤其持久执行永远不会运行）。
- `src/opsmesh/bootstrap/api.py`：`app.state.scope_admission` /
  `app.state.audit_scope_admission` 装配（含 lifespan 清理与初始 None）。
- `src/opsmesh/resources/files/{access,service}.py`：`ScopeAdmission` 协议与
  默认拒绝实现随真实所有者到达改为在调用方会话内执行（与
  `StorageQuotaLedger`、审计准入同模式）；`create_directory` 透传 session。
- `src/opsmesh/config.py`：`default_workspace_concurrency_limit` /
  `default_workspace_environment_limit`（None=无限）与 redacted_summary。
- `pyproject.toml`：新增 7 条 import-linter 合同（workspaces 边界 + 六个
  子模块互相隔离 + 项目成员不导入项目 service）。
- `tests/conftest.py`：`FEATURE_TABLES` 增加 12 个 S6 表。
- `scripts/generate_sdk_api.py`、`sdk/python/codegen.toml`：长 DTO 别名换行
  保护；`capacity_exceeded = "RateLimitError"`。

### 迁移与测试、生成物

- `migrations/versions/0005_workspaces.py`：12 表（workspaces、
  workspace_members、workspace_invitations、projects、project_members、
  project_invitations、workspace_credentials、workspace_capacity_accounts、
  workspace_capacity_reservations、model_provider_connections、
  workspace_notifications、notification_preferences）；server_default 只保留
  模型声明的；复合 FK 按命名约定显式声明。
- `tests/workspaces/test_workspace_primitives.py`（17 项，无 DB）与
  `test_workspaces_postgres.py`（33 项，真实 PG）：生命周期、角色矩阵、
  平台管理员无租户权限、邀请接受/吊销/过期重发、归档隔离、转移所有权、
  项目可见性与邀请、凭据轮换/撤销/最窄解密/审计不泄密、容量幂等与独立账本、
  模型连接与探测（monkeypatch 传输）、通知去重、范围授权三项、并发
  reserve 强制窗口（holder 事务 + `wait_for_lock_waits`）。
- `tests/bootstrap/test_worker_registration.py`（2 项，无 DB）：Workflow/
  Activity 注册集含每个已交付步骤（审计、邮件、模型探测）。
- `sdk/python/tests/test_workspaces_client.py`（5 项生成客户端测试）。
- 生成物：`docs/api/workspaces.md`（新）、`docs/api/schemas.md`、
  `docs/openapi.json`（110 路径、124 模型）、
  `sdk/python/src/opsmesh_sdk/api/workspaces.py`（新）。

## 3. 关键合同与角色

- **角色唯一事实**：空间角色 owner/admin/member/viewer，部分唯一索引保证唯一
  owner；owner 行不可经成员服务改写或删除，转移所有权是唯一变更途径，且降级
  UPDATE 先于晋升 UPDATE 显式发出（同表两行更新无 UoW 顺序保证）。
- **邀请状态隔离**：pending 可接受/吊销；过期由 `effective_state()` 推导、
  重发时关闭；接受时复核 inviter 仍持 owner/admin；已决定邀请不可改写。
- **归档隔离**：归档空间读保留、写拒绝（更新/邀请），owner 生命周期动作
  （再归档幂等、转移、删除）仍可用；软删除空间拒绝一切访问。
- **平台管理员不自动取得租户权限**：空间/项目读取一律走成员校验；平台角色
  只在审计范围读取中放大（管理员可读全量）。
- **凭据最窄边界**：`resolve_credential_secret` 是唯一解密路径，无 actor、
  撤销即拒、按 workspace 隔离；审计 details 只含 fingerprint。
- **容量独立账本**：workspaces.capacity 自建账户/预留，不读用户额度表；
  reserve 账户行锁 + 幂等键重放 + 限额检查；限额低于已预留拒绝。
- **范围授权真实成员**：`WorkspaceScopeAdmission`（用户范围放行/工作空间要
  成员/项目要席位/无 workspace 的 project 拒）与
  `WorkspaceAuditScopeAdmission` 均委托两个 access 唯一实现，无固定放行回调。
- **探测外部效果**：`start_probe` 同事务暂存唯一 dispatch
  （`model-provider-probe:{id}`）；Activity claim 返回解密键或失败原因，
  httpx 超时→needs_attention，撤销凭据→failed/credential_unavailable。

## 4. 事务与外部效果

- 创建工作空间/项目为同一事务：事实行 + owner/admin 席位 + 容量账户一起提交。
- 邀请接受锁序 workspace→invitation（项目：workspace→project→invitation），
  与创建/吊销同序，不成环。
- 容量 reserve/release 锁序：账户行锁先行（release 先锁预留行再锁账户行，
  预留只插新行不成环）；进程内账本使用同一 PG Session，不自行 commit。
- 模型探测经 Temporal dispatch 持久化；Activity 内解密与网络 I/O，回写状态。
- 通知去重依赖 nulls_not_distinct 唯一约束 + 重放返回原行；mark_read 先选
  未读 id 再更新。

## 5. 真实环境验证（服务器）

环境：SSH `root@192.168.232.129`；PostgreSQL 容器
`opsmesh-refactor-validation-pg`（`172.18.0.2:5432`），验证库 `s6_verify`；
Python 3.14.4；虚拟环境 `/root/s5-venv`；代码 `/tmp/s6-verify/app`
（工作树 tar 同步，保留精确字节——`git archive` 会按 `.gitattributes`
转换行尾导致 bundled 摘要失配，已改用工作树打包）。

| 步骤 | 命令 | 退出码 | 结果 |
| --- | --- | --- | --- |
| 干净库 | `psql -c "DROP DATABASE IF EXISTS s6_verify" -c "CREATE DATABASE s6_verify"` | 0 | 重建 |
| 迁移 | `python -m alembic upgrade head` | 0 | 应用至 0005 |
| 迁移漂移 | `python -m alembic check` | 0 | No new upgrade operations detected |
| 全量测试 | `python -m pytest tests -o addopts='' -q` | 0 | **356 passed, 3 skipped** |
| 端到端 | `python /tmp/s6-e2e.py $URL` | 0 | **12/12 PASS** |
| 反向实证 | `python /tmp/s6-reverse.py` | 0 | **11/11 CAUGHT** |

e2e 十二项：空间创建坐实 owner 与双容量账户、平台管理员无租户权限、邀请加入
与吊销拒绝、角色矩阵隔离读写管、所有权转移且唯一 owner、项目创建坐实 admin
席位/对无席位成员隐藏/邀请入席、凭据轮换/最窄解密/审计不泄密、容量单限额+
幂等重放+与用户额度独立、模型连接探测经凭据解密并回写、撤销凭据中止排队
探测、通知去重与回执、资源与审计范围经真实成员校验（无席位成员的项目范围
被拒）。

## 6. 本地静态门（最终状态）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| Ruff | `ruff check src tests sdk/python scripts` | All checks passed |
| Mypy strict | `mypy` | Success：249 个源码文件 |
| 导入合同 | `lint-imports --no-cache` | 28 kept，0 broken |
| SDK 导入合同 | `sdk/python` 内 `lint-imports --no-cache` | 5 kept，0 broken |
| SDK mypy strict | `sdk/python` 内 `mypy`（配置 files=src） | Success：36 个源码文件 |
| 架构审计 | `audit_app_layout.py --architecture-check` | 213 个源码文件、41 个唯一表所有者 |
| 接口生成 | `export_api_docs.py --check` | 9 个生成文件一致 |
| SDK 生成 | `generate_sdk_api.py --check` | 6 个生成文件一致 |
| 测试 | `pytest tests` | 359 项，0 失败，149 跳过（未配 PG） |
| SDK 测试 | `sdk/python` 内 `pytest tests` | 115 项，0 失败 |

生成物零 churn 核对：重命名 S6 视图类为 Space*/Project* 前缀（规避与 identity
既有 `InvitationPage`/`MemberView` 等重名导致的导出器改写）后重新导出，
`docs/api/identity.md`、`sdk/.../api/identity.py` 与 HEAD 零差异；
`capabilities.py` 仅同步 S5 提交 `316ab62d` 漏生成的两处文档串。

## 7. 真实环境暴露的缺陷与修复（按发现顺序）

| # | 缺陷 | 修复 |
| --- | --- | --- |
| 1 | `bootstrap/models.py` 漏注册 `Workspace` 本体，`alembic check` 元数据 FK 解析失败（`NoReferencedTableError`） | 显式注册 `Workspace`；本地测试因 service 导入间接注册而未暴露，真实 PG autogenerate 只认显式注册 |
| 2 | 首次同步用 `git archive`：`.gitattributes` 的 `eol=lf` 改变 bundled 内容字节，3 项 provisioning 摘要失配；且归档不含未跟踪的 `src/opsmesh.egg-info`，12 项 bootstrap 组装测试读不到发行版本元数据 | 改打工作树 tar（保留精确字节）并包含 `src/opsmesh.egg-info` |
| 3 | 凭据 `fingerprint` 列 64 字符，实际值 `sha256:`+64 hex=71，真实 PG 插入即截断（10 项测试） | 模型与迁移 0005 同步放宽为 80（`FINGERPRINT_LENGTH`） |
| 4 | `transfer_ownership` 同表两行 UPDATE 无顺序保证，晋升先于降级时撞唯一 owner 索引 | 降级后显式 `session.flush()` 再晋升 |
| 5 | 已归档空间再归档被拒（访问控制未放行归档态），与 delete 的幂等不一致 | `allow_archived=True` + 已归档直接返回（幂等） |
| 6 | `WorkspaceScopeAdmission.admit` 需要 session，但 S5 `ScopeAdmission` 协议不传 session，文件服务调用 TypeError | 协议与默认实现增加 session 首参（调用方会话内校验，与 StorageQuotaLedger/审计准入同模式） |
| 7 | 连接测试对已分离 ORM 对象断言 `is_default`（旧值） | 改为重读数据库行 |
| 8 | 原语测试直调 `admit` 未随协议变更 | 传未绑定 `Session()`（拒绝路径不触碰会话） |
| 9 | 提交前自查发现 `bootstrap/worker.py` 实际未注册 `ModelProviderProbeWorkflow` 与 `probe_connection`（前期记录与磁盘不一致）：暂存的探测其持久执行永远不会运行 | 抽出 `feature_registrations` 注册集并补注册；新增 `tests/bootstrap/test_worker_registration.py` 锁定注册面 |
| 10 | `set_default_connection` 与 `_unset_other_defaults` 的同表两 UPDATE 被 UoW 合成一个 executemany，晋升先于降级时撞 `uq_model_provider_default`（真实 PG 偶发，最终稳定复现） | 降级改为立即执行的显式 Core UPDATE，晋升随后经 ORM 发出 |

## 8. 反向实证

对十一处不变量逐一变异，每项必须被对应真实测试抓到（脚本 `/tmp/s6-reverse.py`，
变异前 truncate，finally 还原）：

| 变异 | 结果 |
| --- | --- |
| 移除所有权转移的降级 flush | CAUGHT：`UniqueViolation on uq_workspace_owner` |
| 归档空间再归档被拒 | CAUGHT：`ConflictError: The workspace is archived` |
| 归档空间接受改名 | CAUGHT：DID NOT RAISE ConflictError |
| 移除邀请 pending 复核 | CAUGHT：DID NOT RAISE ConflictError |
| owner 行失去保护 | CAUGHT：DID NOT RAISE ConflictError |
| 撤销凭据可解析 | CAUGHT：DID NOT RAISE ForbiddenError |
| 凭据跨空间边界移除 | CAUGHT：DID NOT RAISE NotFoundError |
| 容量账户行锁移除 | CAUGHT：`['reserved','reserved'] != ['CapacityExceededError','reserved']` |
| 工作空间范围停止成员校验 | CAUGHT：DID NOT RAISE ForbiddenError |
| 项目范围停止席位校验 | CAUGHT：DID NOT RAISE ForbiddenError |
| 默认连接降级移除 | CAUGHT：`UniqueViolation on uq_model_provider_default` |

## 9. SDK 独立验证

服务器干净虚拟环境（`python3 -m venv`，仅安装由 `sdk/python` 源码构建的 wheel
及其声明依赖，无服务端源码、无 PYTHONPATH）：导入与版本一致（0.1.0rc23）；
`PlatformClient` 组合 `capabilities`/`resources`/`workspaces` 生成分组；
workspaces 子客户端（members/invitations/credentials/capacity/
model_providers/projects.members）就位；Space*/Project* 视图与 WorkspaceView
可按生成合同校验；一次 `workspaces.create` 经 MockTransport 命中
`POST /api/v1/workspaces` 且携带 `x-session-id`；未知分组与空 session 被
`ValidationError` 拒绝。冒烟退出 0。

## 10. 结论分级

- **implemented**：第 2 节全部交付物；第 3、4 节合同与事务行为。
- **verified**：第 5 节服务器真实 PG（干净库、迁移无漂移、356 过 3 跳、
  e2e 12/12、反向实证 11/11）；第 6 节本地静态门；第 9 节 SDK wheel 干净
  环境安装/导入/组合/一次生成调用。
- **unverified**：模型探测未对真实 OpenAI 端点发包（凭据不存在，传输层以
  monkeypatch 验证解密与回写）；Temporal 工作流只验证 dispatch 暂存与
  Activity 直接执行，未跑真实 Worker 重试；服务器验证用锁定版 venv，未在
  服务器执行 `uv sync`；本地跳过的 149 项不构成持久化证据，以服务器 PG
  结果为准。
- **blocked**：无。

## 11. 待关闭集成门槛（S7 及以后）

1. execution 的 `ExecutionBindings`：容量准入（reserve/release 的调用方）、
   凭据解析的 Worker 侧装配、环境显式创建/续接/释放。
2. workspaces 对 capabilities 的绑定与项目配置版本/执行组装（S9）。
3. orchestration 触发与唯一订阅；容量等待引用与 dispatch 唤醒（S7）。
4. host.jobs 委托 orchestration 返回 FlowRunRef（S10）；channels Inbox/
   Delivery 与通知分批投递。
5. operator/deploy/scripts 与 V4 的合同同步（现存脚本仍含参考实现的工程
   合同，属未提交的迁移清理，不随本步提交）。
