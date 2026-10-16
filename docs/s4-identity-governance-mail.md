# S4 身份、治理与基础邮件：实施记录

## 1. 实施范围（V4 第 16.2 节 S4 行）

按 S4 行交付 `identity`、`governance`、`platform.mail` 三个根模块及各自 API/SDK：
用户配额/工作根目录、受限委托及治理账本。前置 S2（公共机制）与 S3（SDK 纯合同）
均已提交；本步是首个含特性路由的步骤，`docs/openapi.json` 与 SDK 业务分组第一次
由真实路由生成。

### 1.1 源码（59 个新文件，`src/opsmesh` 下）

| 模块 | 文件 | 职责与主要入口 |
| --- | --- | --- |
| `identity`（根） | `context.py`/`errors.py`/`access.py`/`provisioning.py` | 已认证主体与 Session 标识；类型化错误；平台角色枚举与 `has_platform_role`；`AccountDefaults`/`ProvisionedAccountOpening`/`UserIdentityDirectory`/`provision_first_admin`（账号、凭据、配额账本同事务开设） |
| `identity.auth` | `models`/`passwords`/`service`/`schemas`/`routes`/`contracts`/`dependencies` | 登录 Session（随机令牌、哈希存储、到期/撤销）；Argon2 哈希；register/login/logout/sessions/change_password；`AuthService` 经注入合同开账号，失败安全事实独立事务 |
| `identity.users` | `models`/`service`/`schemas`/`routes`/`avatars.py` | 资料/状态/平台角色（最后管理员保护）、头像上传（PIL 解码后校验声明类型、缩放、体积上限） |
| `identity.quotas` | `models`/`service`/`schemas`/`routes` | Token/存储两个账本：账户行锁串行化预留、同键重放、异输入冲突、结算/释放、超限拒绝 |
| `identity.invitations` | `models`/`service`/`schemas`/`routes`/`contracts` | 邀请令牌轮换/接受/冲突、派生状态机、正文渲染；经注入合同开账号 |
| `governance.audit` | `hashing`/`models`/`service`/`schemas`/`routes`/`contracts`/`activities`/`workflow` | 哈希链（`audit_chain_head` 单行锁串行化追加）、幂等回执、分段锚点校验；`AuditVerificationWorkflow` 只编排确定性状态，DB 读写全在 Activity |
| `governance.approvals` | `models`/`service`/`schemas`/`routes`/`targets.py` | 审批单次决定：同键重放、异键冲突、过期不可决；目标解析经注入合同 |
| `governance.security_events` | `models`/`service`/`schemas`/`routes` | 登录失败等安全事实独立事务落库 |
| `platform.mail` | `models`/`service`/`schemas`/`routes`/`activities`/`workflow` | 持久发送意图（同事务暂存 Outbox）、SMTP 配置（密码加密存储）、`MailSendWorkflow` 未知效果分类（`needs_attention` 绝不重发） |
| `bootstrap` | `cli.py`（新）/`mail.py`（新）/`api.py`/`models.py`/`routers.py`/`worker.py`（改） | `init-admin` 首个管理员 CLI；邮件入口（注册账号邀请发送）；特性路由/ORM/Workflow/Activity 显式注册；`DispatchWorker` = Outbox 消费者 + 特性 Worker |

`src/opsmesh/config.py` 新增 `session_ttl_seconds`、`invitation_ttl_seconds`、
`platform_base_url`、`default_user_token_limit`、`default_user_storage_limit_bytes`。

### 1.2 API 与稳定 operation_id

42 个路由全部显式点分 ID（SDK 生成的唯一依据）：`identity.auth.*`（6）、
`identity.users.*`（8，含 `avatar.upload/read/delete` 二进制方向）、
`identity.quotas.*`（3）、`identity.invitations.*`（5）、
`governance.audit.events.record/list/read` + `verifications.create/read`、
`governance.approvals.*`（4）、`governance.security_events.*`（2）、
`platform.mail.*` + `platform.smtp.*` + `platform.build_info`（8）。

### 1.3 SDK 生成（`scripts/generate_sdk_api.py`，约 700 行，新建）

读 `docs/openapi.json` + `sdk/python/codegen.toml`（真实 TOML）生成三个分组，
勿手改：

| 生成文件 | 操作数 | 子客户端 |
| --- | --- | --- |
| `sdk/python/src/opsmesh_sdk/api/identity.py` | 23 | auth/users/quotas/invitations |
| `sdk/python/src/opsmesh_sdk/api/governance.py` | 11 | audit.events/audit.verifications/approvals/security_events |
| `sdk/python/src/opsmesh_sdk/api/platform.py` | 8 | mail/smtp/build |

DTO 经拓扑排序（pydantic 类创建时解析注解）；子客户端树 children 先于 parent；
请求体可选字段省略而非发送显式 `null`（`model_dump(mode="json",
exclude_none=True)`，有 required 可空字段的模型改用显式 `exclude={...}`）；
`--check` 模式防漂移。传输层新增二进制通道（`TransportRequest.content`/
`decode_answer` 按 media type 分流 JSON/bytes，`+json` 后缀纳入 JSON），
头像上传/读取登记在 codegen.toml `[[binary]]`。

### 1.4 测试

本地纯逻辑：`tests/governance/test_audit_hashing.py`（canonical 编码、digest
稳定性、篡改/乱序/缺口/锚点）、`test_audit_schemas.py`（凭据键筛查、64 字段/
16KB 上限）、`tests/identity/test_identity_primitives.py`（密码边界/畸形哈希
fail-closed、邀请令牌摘要/状态机/正文渲染）、`test_avatar_normalization.py`、
`tests/bootstrap/test_first_administrator_cli.py`（决策表/parser/stdin 密码）。

PG 门控（服务器运行）：`tests/identity/test_identity_postgres.py`（注册/登录/
认证、失败事实独立事务、到期/撤销/改密吊销、禁用账户、最后管理员、配额预留/
重放/超限/结算、邀请轮换/接受/冲突、分页）、
`tests/governance/test_governance_postgres.py`（链链接/篡改检测/扩展追加幂等/
异输入冲突/缺调用范围拒绝/验证 run 暂存 Outbox/审批单次决定+同键重放+异键冲突/
过期不可决/收件箱隔离/链头推进）、
`tests/platform/test_mail_postgres.py`（暂存执行/同键同内容重放/异内容冲突/
未准入来源拒绝/每日上限/claim lease/未知效果核对不重发/取消语义/workflow 取消
分类/SMTP 密码加密往返）。

SDK：`test_identity_client.py`/`test_governance_client.py`/
`test_platform_client.py`（httpx MockTransport + PlatformClient）与
`test_client_contracts.py` 扩展（错误别名/二进制/双载荷拒绝）。

## 2. 合同与权限落位

- **依赖方向**（import-linter 19 条合同，本步新增 8 条）：
  `v4-identity-boundaries`（identity 只用 shared/telemetry/temporal/governance）、
  `v4-governance-boundaries`（governance 禁导入 identity——占位依赖 +
  bootstrap 注入）、`v4-platform-boundaries`、`v4-identity-auth-isolated`、
  `v4-identity-users-service-no-auth`、`v4-identity-quota-ledger-isolated`、
  `v4-identity-invitations-no-auth`、`v4-platform-mail-service-no-identity`。
- **平台角色即平台角色**：`users.platform_roles` 是用户行上的 JSONB 列，只经
  users 服务变更（最后管理员保护 + 审计追加）；identity 无任何空间/项目导入
  （合同守护），平台角色在结构上不能变成空间权限。空间/项目角色归 S6
  workspaces，本步不出现 `workspace_id` 授权判断。
- **Session**：登录返回随机 Session，普通客户端经 `X-Session-ID` 访问；
  令牌只存哈希；到期/撤销/改密吊销在认证路径逐次校验（真实 PG 实测即时拒绝）；
  不新增第二种用户 Token 登录机制。
- **审计读写分离**：`governance.audit.events.record` 的 actor 来自宿主委托
  （installation/instance/invocation  scope，缺一即 `InvalidArgumentError`），
  绝不接收用户 Session；`events.list/read` 与 `verifications.*` 是平台读路径。
  同一 `(scope, key)` 重放返回原回执，异输入 `ConflictError`；链头行锁使并发
  追加串行化。
- **审批单次决定**：决定写在同一账户行锁内；同键重放返回原决定，异键冲突；
  过期审批不可决；决定唯一由 governance 作出。
- **邮件未知效果**：`MailTransportError(outcome_unknown=True)` →
  `needs_attention` 等待运营核对，绝不自动重发；已知拒绝 → non_retryable →
  intent 落 `failed`；claim lease 300s 界定崩溃窗口；取消在途投递不谎称
  cancelled。
- **凭据边界**：SMTP 密码与邮件正文加密存储（`SecretCipher`），只在发送
  Activity 的最窄边界解密；Workflow 参数/历史由 S2 加密 codec 保护（e2e 在
  加密历史下跑通）；密钥不进日志/事件/响应。
- **SDK 错误合同**：服务端稳定码归并到类型化错误（`invalid_credentials` →
  `AuthenticationError`、`validation_error` → `ValidationError`、
  `quota_exceeded` → `RateLimitError` 等 19 别名，codegen.toml `[errors]`
  全量登记）。

## 3. 相对参考源码的修正

- 账号开设从"注册即写全部表"改为 `provisioning` 的注入合同：
  `ProvisionedAccountOpening`（auth 用）、`UserIdentityDirectory`、邀请接受
  经同一合同，避免 auth/users/invitations 三处重复开设逻辑与循环导入。
- 配额从"用时查一次"改为双账本行锁模型：预留持上限、结算销差异，并发无法超订
  （参考实现无行锁，靠应用层检查）。
- 审计从"best-effort 记录"改为哈希链 + 幂等回执 + 单行锁；追加、查询、验证
  三路径分离，验证是 Temporal Workflow（只编排，DB 在 Activity）。
- 邮件从"发送即忘"改为持久意图 + 未知效果分类；参考实现的同步发送、无核对
  路径整体删除。
- 邀请令牌从"存明文"改为只存摘要，接受/轮换/冲突走派生状态机。
- 平台角色枚举替代参考实现的字符串角色集合；最后管理员保护在服务层强制。

## 4. 迁移与工程合同

- `migrations/versions/0002_identity_governance_mail.py`：17 张特性表 +
  `audit_chain_head` 种子行（`op.execute(sa.text(...).bindparams(...))`，
  `sa.func.now()` 不能作绑定参数）；5 个 JSONB 列用 `postgresql.JSONB()`
  （`sa.JSON()` 会被 `alembic check` 判类型漂移）。0001 保留为公共机制基线，
  本迁移 revises 0001；不继承历史业务迁移链。
- `pyproject.toml`：新增 `email-validator` 依赖（`EmailStr`）、
  `opsmesh-cli` 入口、8 条 import-linter 合同；`uv.lock` 同步
  （email-validator 2.3.0 + dnspython）。
- `tests/conftest.py`：`FEATURE_TABLES`（17 张）+ `truncate_feature_tables`
  （连同 `temporal_dispatches`/`http_creation_receipts`）。
- `scripts/export_api_docs.py`：首次真实导出 6 个文件；修复生成文档引用不存在
  `docs/api.md` 的死链，替换为自包含的错误与追踪说明。
- `sdk/python/codegen.toml`：从 markdown 风格文档改写为真实 TOML（openapi/
  clients×9/pagination/streaming/shared_values/errors/[[binary]]）。
- `AGENTS.md` 验证命令块增加 `uv run python scripts/generate_sdk_api.py
  --check`（该文件本次改动与既有未提交的 V4 重写混在一起，随 AGENTS.md 整体
  提交时一并落库，本提交不含）。

## 5. 本地验证证据（Windows，2026-10-16）

| 命令 | 结果 |
| --- | --- |
| `uv run ruff check src tests migrations scripts` | All checks passed |
| `uv run mypy` | Success: no issues found in 156 source files |
| `uv run lint-imports --no-cache` | Contracts: 19 kept, 0 broken |
| `uv run python scripts/audit_app_layout.py --architecture-check` | Architecture verified: 123 source files, 19 unique table owners |
| `uv run python scripts/export_api_docs.py --check` | API documentation verified: 6 generated files |
| `uv run python scripts/generate_sdk_api.py --check` | SDK generation verified: 3 generated files |
| `uv run pytest tests -q`（无 PG，门控跳过） | 退出码 0 |
| SDK 独立检查（`sdk/python`） | ruff/mypy/lint-imports(5 kept)/pytest 均通过（S3 轮次证据，本步未改传输层合同语义之外的部分后复跑） |

## 6. 服务器验证证据（192.168.232.129，真实 PostgreSQL + 真实 Temporal 1.29.1）

环境：验证库 PG 容器 `opsmesh-refactor-validation-pg`
（`postgresql+psycopg://validation:***@172.18.0.2:5432/opsmesh_s4_test`）；
Temporal dev server 127.0.0.1:7233；Python 3.13 精简 venv（按 src/tests 实际
导入面装配，`PYTHONPATH=src` + `--no-deps -e .` 注册发行元数据 +
`fakeredis[lua]`）。

| 步骤 | 命令/场景 | 结果 |
| --- | --- | --- |
| 迁移 | `alembic downgrade 0001` → `upgrade head` → `alembic check` | 成功；`No new upgrade operations detected`；20 张表（含 alembic_version） |
| S4 identity | `pytest tests/identity -q` | 38 passed |
| S4 governance | `pytest tests/governance -q` | 44 passed |
| S4 platform | `pytest tests/platform -q` | 15 passed |
| 全量 | `pytest tests -q`（含 S2 机制/Temporal/API 装配） | 183 passed, 2 skipped（真实 Redis opt-in 项，S2 轮次已验证） |
| e2e 审计 | 3 事件 → `start_verification(1..3)` 暂存 Outbox → 真实 dispatcher + feature worker 投递 | workflow `succeeded`；DB 事实 `status=succeeded, checked_count=3` |
| e2e 邮件 | SMTP 指向 127.0.0.1:1（拒绝端口）→ 注册意图 → 真实投递 | Activity 记录 `failed/ConnectionRefusedError/outcome_unknown=false`；workflow 以 non_retryable `ApplicationError` 失败（`WorkflowFailureError`），无重试风暴 |
| e2e 凭据 | 上述两个 workflow 的 history 经 S2 加密 codec | 加密历史下投递、解密、结果返回全部正确 |
| CLI | `opsmesh-cli init-admin` 四态 | 空库创建 exit 0；同管理员重跑 exit 0（already exists）；异邮箱冲突 exit 1；弱密码 exit 2；持久事实：唯一 active 管理员 `["admin","member"]` |

e2e 由真实 `TemporalOutboxDispatcher`（PG LISTEN/NOTIFY + 恢复扫描）发布暂存
意图，由 `build_feature_worker` 注册的 Workflow/Activity 执行；脚本先终止上一
轮崩溃残留的 RUNNING workflow 保证确定性。

## 7. S4 验收标准核对（V4 第 16.2 节）

| 验收项 | 证据 |
| --- | --- |
| Session 到期/撤销即时生效 | 真实 PG：到期、注销撤销、改密吊销全部 Session、禁用账户均即时拒绝（identity 38 项） |
| 平台角色不能伪造成空间权限 | identity 零空间导入（合同）；platform_roles 仅 users 服务可变更 + 最后管理员保护 + 审计；无任何 workspace 授权判断存在于本步 |
| 真实 PG 审计并发/幂等/哈希链 | 链头行锁串行化追加；幂等回执同键重放/异输入冲突；篡改/乱序/缺口/锚点校验；验证 run 同事务暂存 Outbox（governance 44 项 + e2e succeeded） |
| 审批单次决定正确 | 单次决定、同键重放、异键冲突、过期不可决（governance 套件） |
| 邀请模板/来源/发送幂等与未知效果可核对 | 邀请轮换/接受/冲突；邮件同键同内容重放、异内容冲突、未准入来源拒绝、每日上限、claim lease、未知效果 needs_attention 不重发、取消语义（platform 15 项 + e2e failed 事实） |
| SDK 审计写读权限独立 | `audit.events.record`（委托 scope，缺 scope 服务端拒绝）与 `events.list/read`、`verifications.*`（平台读）在服务端是两个依赖；SDK 分组同构暴露 |
| 无凭据外泄 | SMTP 密码/正文加密存储、发送边界外不解密；workflow 历史加密（e2e 实证）；遥测脱敏（S2） |

## 8. 延后登记（unverified / 待生产者步骤）

- **governance 事实模块**：policies（风险策略）、reviews（治理评审）、costs
  （成本）、alerts（告警）的生产者分别属 S7（执行用量）、S8（扩展效果）、
  S12（业务包）。本步只交付其消费方必需的审计/审批/安全事件账本与 API；
  V4 第 11 节这四个模块的实现与验收随对应生产者步骤关闭，不在 S4 宣称。
- **空间/项目范围授权**：S4 全部授权是平台级或委托级；workspace/project
  范围校验随 S6 接入真实成员后验收（V4 16.2 S6 行）。
- **真实 SMTP 服务器发送成功路径**：e2e 只验证拒绝端口（已知失败）与加密
  往返；成功投递需真实 SMTP 凭据，标 unverified（基础设施/凭据不可用）。
- **真实 Redis**：缓存失效的 opt-in 测试在 S2 轮次已验证；本步未改缓存代码，
  精简 venv 无 Redis，2 项 skip。
- **API 进程级集成**（uvicorn 起服务跑真实 HTTP）：S4 经应用装配单测
  （`tests/bootstrap/test_api_assembly.py` 23 项，含路由注册/错误 envelope/
  CORS/限流）覆盖；端到端 HTTP 客户端行为随 S7 集成验收。

## 9. 验证中发现并修复的缺陷（均在提交前关闭）

### 9.1 [真 bug] 扩展审计追加在 `autoflush=False` 下读到不到本事务早前的回执

`record_extension_event` 先 SELECT 回执再追加；应用会话工厂
（`bootstrap/resources.py`）与测试均 `autoflush=False`，同一事务内第二次调用
看不到第一次 `session.add` 的回执 → 误判为新追加 → 重复事件 + 回执插入
`event_id=None`（`event.id` 在 flush 前为 None）→ PG `IntegrityError`。
服务器真实 PG 实测复现（`test_an_extension_append_is_idempotent_per_invocation`、
`test_a_reused_audit_key_with_another_input_conflicts` 失败）。修复：查找前与
追加后各一次显式 `session.flush()`（与 `reserve_tokens` 既有 flush 一致）。
反向实证：移除任一处 flush 后对应测试失败。

### 9.2 [真 bug] 畸形存储哈希导致崩溃而非 fail-closed

`passwords.py` 只捕获 `ValueError`，pwdlib 的 `UnknownHashError` 不继承
`ValueError`，损坏哈希行会让认证路径抛未处理异常。修复：
`except (PwdlibError, ValueError)`。测试：畸形哈希认证被拒（`AuthenticationError`）。

### 9.3 [真 bug] 头像声明类型与解码格式不匹配未检出

`avatars.py` docstring 承诺校验声明 content_type 与实际解码格式一致，实现
缺失。修复：`_DECLARED_FORMATS` 映射，解码后校验 `image.format`。

### 9.4 [迁移] 种子行绑定参数与 JSONB 类型漂移

`audit_chain_head` 种子行用 `sa.func.now()` 作绑定参数（真实 PG 报
`cannot adapt type 'now'`）→ 改 `op.execute(sa.text(...).bindparams(...))`；
5 个 JSONB 列写成 `sa.JSON()`（`alembic check` 报类型漂移）→ 改
`postgresql.JSONB()`。修复后 downgrade/upgrade/check 全干净。

### 9.5 [测试] 两处 identity 测试自身错误

改密吊销测试在同一 session 内 `authenticate`（autobegin）后又 `begin()` →
`InvalidRequestError`，改用新会话块登录；配额超限测试超额数值写成 400
（限额 1000、已预留 400 时不应拒绝）→ 改 700（400+700>1000 必须拒绝）。
两处均为测试缺陷，服务逻辑经复核无需变更。

## 10. 状态

- **implemented**：identity/governance/platform.mail 共 59 个源文件、
  bootstrap 装配与 CLI、迁移 0002、42 个稳定 operation_id、SDK 生成器与
  三分组（42 操作）、8 条新依赖合同、S4 全部本地与 PG 门控测试。
- **verified**：本地 ruff/mypy strict(156)/19 条 import 合同/架构审计(123
  文件)/文档导出/SDK 生成 check/本地单测；服务器真实 PG 迁移（downgrade/
  upgrade/check 干净）+ 全量 183 passed + 真实 Temporal 双 workflow e2e +
  CLI 四态退出码。
- **unverified**：第 8 节各项（真实 SMTP 成功路径、真实 Redis opt-in、
  workspace 范围授权、API 进程级 HTTP 集成、governance 事实四模块）。
- **blocked**：无。
