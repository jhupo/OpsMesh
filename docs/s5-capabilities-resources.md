# S5 能力目录与资源机制交付记录（2026-10-17）

分支：`refactor/s5-capabilities-resources`（基线 `73e1432c`，S4 关闭提交）。
设计依据：`docs/source-refactor-plan-v4.md` 第 16.2 节 S5 行及第 9、11、12、13、15 节；
参考源码为 `backup/opsmesh` 与 `backup_2/opsmesh`（只读，不参与运行与检查）。
本记录区分设计与实现：以下"交付物"均为本分支实际提交的代码、迁移、生成物与证据。

## 1. 范围与目标

按 V4 16.2 S5 实施两类模块，均不越界：

- `capabilities`：expert/team/mcp/skill/tool 五类能力定义、公私有、作者/时间、
  不可变版本（draft→published，`version_digest` 冻结）、包（zip intake、
  digest 核对、发布不运行代码）与 bundled 内置源 provisioning。
- `resources`：文件/目录、内容版本（CAS 写回）、授权（owner/grant）、
  存储协调（内容寻址、同事务暂存、提交后清理）与 Artifact（来源引用，
  不建第二份内容存储）。

未实施且明确不做：能力与工作空间的绑定组装、运行期 MCP 生命周期、
Artifact 的真实生产者授权（run/invocation 随 execution 到达）、编排触发、
渠道投递。这些记入文末待关闭集成门槛。

## 2. 交付物

### 源码（新增 45 个 Python 文件）

| 位置 | 内容 |
| --- | --- |
| `src/opsmesh/capabilities/`（根 8 文件） | `models`（定义/版本/包三表）、`lifecycle`（创建/更新/发布/冻结/禁用）、`packages`（zip intake、traversal 拒绝、digest）、`validation`（资源清单与流模板核对）、`provisioning`（bundled 发布索引、幂等、顺序）、`routes`/`schemas`、`__init__` |
| `src/opsmesh/capabilities/{experts,teams,mcp,skills,tools}/`（各 4 文件） | 各子类型 `service`（合同校验）、`schemas`（pydantic 合同）、`routes`（就近路由）、`__init__` |
| `src/opsmesh/resources/`（17 文件） | `storage/filesystem`（内容寻址 blob、大小上限、digest 校验读）、`storage/transactions`（session 事件：提交后删/回滚清）、`files/{models,access,content,versions,service,routes,schemas}`、`artifacts/{models,service,routes,schemas}` |
| `src/opsmesh/bootstrap/capabilities.py` | bundled provisioning 入口（CLI 子命令），不实现业务策略 |
| `migrations/versions/0004_capabilities_resources.py` | 空库链上的 S5 初始迁移：能力定义/版本/包、资源文件/版本/内容/授权/运行时策略/产物；18 个外键显式 `ondelete="RESTRICT"` |

### 修改（受影响完整调用链）

- `src/opsmesh/bootstrap/{api,cli,models,routers}.py`：注册 capabilities 与
  resources 路由、ORM 模型与 CLI provision 命令。
- `src/opsmesh/config.py`：资源存储根目录设置。
- `pyproject.toml`：新增 import-linter 合同 `v4-capabilities-boundaries`
  （capabilities 仅可用 shared/identity/governance）与 `v4-resources-boundaries`
  （resources 仅可用 shared/telemetry/temporal/governance）。
- `scripts/generate_sdk_api.py`、`sdk/python/codegen.toml`：S5 二进制通道
  （包上传/包下载/内容写回/内容读取共 10 项）与空对象 schema 默认值。
- `tests/conftest.py`、`tests/identity/test_identity_postgres.py`：
  `FEATURE_TABLES` 纳入 S5 表；identity 测试复用同一清单（消除重复
  TRUNCATE 表名单导致的外键截断错误）。

### 测试与生成物

- `tests/capabilities/`（primitives + 真实 PG）、`tests/resources/`
  （primitives + 真实 PG）：合同拒绝、不可变版本、幂等 provisioning、
  文件生命周期与 CAS、授权放行/拒绝、配额预留与释放、产物记录授权。
- `sdk/python/src/opsmesh_sdk/api/{capabilities,resources}.py` 及对应测试：
  由 OpenAPI 生成（`export_api_docs.py` → `generate_sdk_api.py`）。
- `docs/api/capabilities.md`、`docs/api/resources.md` 及 `schemas.md`、
  `README.md`、`openapi.json`：重新生成（86 接口、70 路径、75 模型）。
- `bundled/`：4 个最小内置源（expert coding、team development、skill
  workspace-guide、mcp weather）+ 发布清单；`manifest.json` 的
  `directory_digest` 为 (path, digest, size) 规范清单的 sha256。

## 3. 关键合同与角色

- **不可变版本**：draft 可改；publish 时重跑全部校验并冻结 `version_digest`；
  已发布版本不可改，更新即创建新 draft。
- **发布不运行代码**：intake 只核对声明资源/流模板是否存在且 digest 一致，
  不 import、不执行；入口发现与构建属后续扩展步骤。
- **MCP 包规则（唯一事实源）**：`mcp/service.py:carries_package()`——
  `transport == "remote"` 不得带包，local（stdio）必须带包；intake 与版本
  一致性校验共用该函数，provisioning 对 remote 条目不暂存包。
- **资源授权**：S5 只存用户范围资源；`RejectingScopeAdmission` 放行用户范围、
  拒绝工作空间等外部范围（范围授权归 workspaces，本步不伪造 workspace_id）；
  文件级 owner/grant 读、写、删除、管理四种权限。
- **CAS 写回**：写回按当前版本号比较，过期期望返回冲突，不静默覆盖。
- **配额协调**：内容上传经注入的 identity 账本预留→写 blob→记账→结算，
  全在调用方事务内；失败回滚数据库并由 session 事件删除暂存 blob。
- **Artifact**：内容与文件共享内容存储；记录授权经注入合同
  （`ArtifactRecordAuthorization`），默认实现拒绝全部来源——真实
  run/invocation 所有者随 execution 到达，本步不提前信任调用方。

## 4. 事务与外部效果

- 长操作在同一事务写事实与 Outbox 意图（既有 temporal 机制），提交后返回；
  本步不新增工作流。
- blob 生命周期：`stage_content` 在事务内登记，提交后由 session 事件删除；
  `stage_content_removal` 在删除提交后清理。回滚不留孤儿 blob。
- 跨表删除不使用 ORM 级联排序：无 relationship 的跨表 `session.delete()`
  多对象删除的 flush 顺序不是合同（最小复现证实父表先删），`delete_file`
  改用显式 Core DELETE 按 versions→grants→policy→files→contents 依赖序执行。
- `autoflush=False` 会话中，同事务 add 后未 flush 的行对后续 SELECT 不可见：
  lifecycle 包行与 artifact 内容行均在 add 后显式 flush（两处均为真实 PG
  验证暴露，见第 7 节）。

## 5. 真实环境验证（服务器）

环境：SSH `root@192.168.232.129`；PostgreSQL 容器
`opsmesh-refactor-validation-pg`（`172.18.0.2:5432`，无发布端口，容器 IP
直连），验证库 `s5_verify`；服务器 Python 3.14.4；虚拟环境
`/root/s5-venv`（按 `uv.lock` 固定 34 个包版本，SQLAlchemy 2.0.49，
`pip check` 通过）；代码 `/tmp/s5-verify/app`（本地源码 scp 同步，
MD5 逐文件核对一致）。

| 步骤 | 命令 | 退出码 | 结果 |
| --- | --- | --- | --- |
| 干净库 | `psql -c "DROP DATABASE IF EXISTS s5_verify" -c "CREATE DATABASE s5_verify"` | 0 | 重建 |
| 迁移 | `python -m alembic upgrade head` | 0 | 应用至 0004 |
| 迁移漂移 | `python -m alembic check` | 0 | No new upgrade operations detected |
| 全量测试 | `python -m pytest tests -o addopts='' -q` | 0 | **276 passed, 3 skipped** |
| 端到端 | `python /tmp/s5-e2e.py $OPSMESH_DATABASE_URL` | 0 | **7/7 PASS** |

e2e 七项：bundled provisioning 幂等（第二次 published 0/skipped 4）、用户
发布 skill 包并取回一致字节、文件上传/grant/CAS 写回/冲突、被授权者读得、
陌生人被拒、artifact 记录与验纹读回、traversal 包/工作空间范围/摘要不符
三类拒绝、删除文件清 blob 且 artifact 内容保留。

## 6. 本地静态门（最终状态）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| Ruff | `ruff check src tests sdk/python scripts` | All checks passed |
| Mypy strict | `mypy` | Success：204 个源码文件 |
| 导入合同 | `lint-imports --no-cache` | 21 kept，0 broken |
| 架构审计 | `audit_app_layout.py --architecture-check` | 169 个源码文件、29 个唯一表所有者 |
| 接口生成 | `export_api_docs.py --check` | 8 个生成文件一致 |
| SDK 生成 | `generate_sdk_api.py --check` | 5 个生成文件一致 |
| 测试 | `pytest tests sdk/python/tests` | 387 项，0 失败/0 错误，105 跳过（未配 PG） |

## 7. 真实环境暴露的缺陷与修复（按发现顺序）

| # | 缺陷 | 修复 |
| --- | --- | --- |
| 1 | 测试期望 `InvalidArgumentError`，实际 pydantic `ValidationError`（约定：仅 `validated_configuration` 映射域错误） | 测试改期望 ValidationError |
| 2 | teams 环检测 `ConflictError` 未被 pydantic 包装 | 转 `ValueError` 统一包装 |
| 3 | 下载头断言引号写法错误 | 精确相等断言 |
| 4 | SDK 生成客户端参数名为线名 `type`/`source`；二进制 mock 缺 content-type（传输层约定：JSON 解值、其余 bytes） | 测试按生成合同修正 |
| 5 | `created_by_user_id` schema 非空但 system 定义为 None，运行时会 500 | schema 改 `UUID \| None` 并重生成 |
| 6 | 迁移 0004 的 18 个外键缺 `ondelete="RESTRICT"`，`alembic check` 漂移 | 批量补齐 |
| 7 | identity 测试 TRUNCATE 名单缺 S5 表，外键截断失败 | 复用 `tests.conftest.FEATURE_TABLES` |
| 8 | `RejectingScopeAdmission` 连用户范围也拒绝 | 用户范围放行、外部范围拒绝 |
| 9 | `UnsafePackageError(ValueError)` 非域错误，HTTP 层无法映射 | 继承 `InvalidArgumentError` |
| 10 | provisioning digest 不符：`_store_package_row` add 后未 flush，同事务 publish 查不到包行 | 补 `session.flush()` |
| 11 | `delete_file` 多对象 ORM 删除被 UoW 排成父表先删，FK 违反 | 显式 Core DELETE 依赖序 |
| 12 | `record_artifact` 内容行 add 后未 flush，FK 插入序错误 | 补 `session.flush()` |
| 13 | bundled SKILL.md 的 YAML description 值含 `": "` 未加引号，ScannerError | 加引号并重算 manifest digest |
| 14 | provisioning 对 remote MCP 传包被 intake 拒绝 | `carries_package()` 唯一规则源；provisioning 对 remote 不暂存包 |

## 8. 反向实证

对三处修复逐一变异，每项必须被对应真实测试抓到（脚本
`/tmp/s5-reverse.py`，变异前 truncate，finally 还原，MD5 复核一致）：

| 变异 | 结果 |
| --- | --- |
| 移除 lifecycle 同事务 flush | CAUGHT：`ConflictError: The draft package index no longer matches its digest` |
| 移除 `delete_file` 的 versions 删除 | CAUGHT：`ForeignKeyViolation on resource_file_versions` |
| remote MCP 改为带包 | CAUGHT：`InvalidArgumentError: A remote MCP template carries no package` |

## 9. SDK 独立验证

`pip wheel` 构建 `opsmesh_sdk-0.1.0rc23-py3-none-any.whl`，在服务器全新
虚拟环境仅安装该 wheel（连带 httpx 0.28.1 / jsonschema 4.26.0 /
pydantic 2.14.1，无服务端源码、无 PYTHONPATH）：导入与版本一致；
`PlatformClient` 组合 `capabilities`/`resources` 生成分组；未知分组与空
session 被 `ValidationError` 拒绝；传输可关闭。冒烟退出 0。

## 10. 结论分级

- **implemented**：第 2 节全部交付物；第 3、4 节合同与事务行为。
- **verified**：第 5 节服务器真实 PG（干净库、迁移无漂移、276 过 3 跳、
  e2e 7/7）；第 6 节本地静态门；第 8 节反向实证；第 9 节 SDK wheel 干净
  环境安装/导入/组合。
- **unverified**：本步不涉及 Temporal 工作流、SMTP、模型与机器人，未做
  相应真实环境验收；服务器验证用锁定版 venv，未在服务器执行 `uv sync`；
  bundled 仅 4 个最小内置源，完整内置能力源与扩展源包未交付；SQLite、
  mock 与本地跳过的 105 项不构成持久化证据，以服务器 PG 结果为准。
- **blocked**：无。

## 11. 待关闭集成门槛（S6 及以后）

1. workspaces 对 capabilities 的绑定与配置组装（能力安装 ≠ 替用户绑定）。
2. execution 的 `ExecutionBindings`：Artifact 记录的真实生产者授权、
   MCP 生命周期、环境显式创建/续接/释放。
3. orchestration 触发与唯一订阅；channels Inbox/Delivery 对通用流程的调用。
4. bundled 完整内置源（知识库、防护、Jev 判断、编码工具等归对应包）与
   扩展源包；admin 安装/替换/卸载可执行包。
5. operator/deploy/scripts 与 V4 的合同同步（现存脚本仍含参考实现的工程
   合同，属未提交的迁移清理，不随本步提交）。

## 12. Review 整改（2026-10-17 晚，对应 `docs/s5-code-review.md` 三个 P1）

Review 判定 S5 为 partial/blocked：七类能力只接通五类、CAS/版本号分配未在
PostgreSQL 下串行化、`output_declarations` 无路径与结构校验。以下整改不改变
第 3、4 节已记录合同，只补齐缺口并按同规格重新验证。

### 12.1 七类能力接通（channel/extension）

- 按 V4 第 9 节职责树，channel 与 extension **不设类型子包**：两类合同的
  pydantic 模型与 `validated_channel_configuration`/
  `validated_extension_configuration` 并入根 `schemas.py`，包接收
  （`channel_intake_package`/`extension_intake_package`）与版本校验
  （`validate_channel_version`/`validate_extension_version`）并入根
  `validation.py`，六条类型路由（create/update_draft/package.attach）并入
  根 `routes.py`；其余五类保持子包。`lifecycle` 的
  `CONFIGURED_TYPES`、`_CONFIGURATION_VALIDATORS`、`_PACKAGE_INTAKE`、
  `_VERSION_VALIDATORS` 注册满七类；`bootstrap/routers.py` 不需要新增注册。
- channel 配置合同：`protocol` 为小写厂商标识；`signature_algorithm` 仅
  `hmac-sha256`（镜像 SDK `SUPPORTED_SIGNATURE_ALGORITHMS`）；operations
  必须含 receive/send、entrypoint 为 `module:function`；intake 校验
  entrypoint 的模块是包内声明资源；版本必须带包（适配器代码随包发布）。
- extension 配置合同：operations/hooks/events 声明镜像 SDK
  `PackageManifest.validate_declarations`（export_id 全局唯一、event 必须
  引用已声明 operation、operation permissions ⊆ 声明 permissions、总数
  ≤256）；effects/stages/failure_policy 枚举镜像 SDK 纯合同；intake 核对
  声明资源；版本必须带包。
- provisioning 无需改动：channel/extension 走通用 manifest.json 路径。
- `sdk/python/codegen.toml` 增加 2 条二进制 attach 条目，OpenAPI/SDK
  重新生成（`channels`/`extensions` 分组与配置模型进入生成客户端）。
- 2026-10-17 夜修正：初版曾为两类各建 `capabilities/channels/`、
  `capabilities/extensions/` 子包，不在 V4 第 9 节树内（第 1681 行明确
  第七类 extension 的合同归根 `schemas.py`/`validation.py`），且未按
  AGENTS.md 规则 3 先同步方案。已按树整改：合同并入根模块、删除子包；
  OpenAPI 路径/operationId/字段结构与 SDK 生成客户端逐字节不变
  （`generate_sdk_api.py --check` 通过），仅两条 schema 描述文案随根模块
  文档串变详细及 `capabilities.md` 中六条路由的实现路径引用更新。

### 12.2 CAS 与版本号分配串行化

- capabilities：`_authorized_definition` 与 `publish_version` 对定义行
  `SELECT ... FOR UPDATE`；`create_definition` 的 (type,key) 竞争经
  IntegrityError→ConflictError 映射（唯一索引是合同）。
- resources：`_lock_file_row` 单锁点；`write_back` 在 CAS 检查**之前**加锁，
  `upload_version` 在版本分配之前加锁，`delete_file` 在授权后加锁。写路径
  只有"文件行/定义行"一个锁点，无嵌套第二锁，无死锁环。
- 并发测试全部用 holder 事务 + `wait_for_lock_waits` 强制窗口（不用时序
  竞赛）：并发 create 同 key 一成一败、并发 publish 一成一败、并发
  update_draft 均成且只有一个 draft、并发 write_back 同期望一成一败、
  并发 upload 版本递增。

### 12.3 runtime-policy 输出声明校验

- schema：`OutputDeclaration`（`extra=forbid`、path 1..1000、
  media_type ≤255、description ≤2000）；`RuntimePolicyRequest`/`View` 改
  `list[OutputDeclaration]`。
- 服务层（唯一门）：`_validated_output_declarations` 对每个声明重跑
  pydantic 校验与 `validate_relative_path`，并执行
  `_reject_declared_file_collision`——声明路径解析到文件所属目录后若被他
  人文件占位即 ConflictError，防止经本文件策略的写回绕过他人文件授权。
  路由层 schema 之外不依赖，直接调用 service 的内部路径同样被拒绝。

### 12.4 重新验证证据

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 服务器全量 | `pytest tests -o addopts='' -q`（干净库 + alembic upgrade head/check 后） | **304 passed, 3 skipped** |
| 端到端 | `python /tmp/s5-e2e.py` | **7/7 PASS** |
| 反向实证 | `/tmp/s5-review-reverse.py`（7 项变异，truncate 前置、finally 还原、MD5 复核） | **7/7 CAUGHT** |
| SDK wheel | 干净 venv 仅装 wheel 后冒烟（含 channels/extensions 分组、OutputDeclaration） | OK |
| 本地静态门 | ruff / mypy（204 文件）/ lint-imports（21 kept）/ audit（169 文件）/ export check（8）/ SDK gen check（5）/ pytest（307 + SDK 110） | 全部通过 |

> 2026-10-17 夜按 V4 第 9 节树整改 channel/extension 归属后复跑：受影响测试
> 92 passed、反向实证 7/7 CAUGHT、服务器全量 + e2e 7/7（`ALL_S5_SERVER_CHECKS_DONE`）、
> 六个同步文件 MD5 与服务端一致；SDK 生成客户端逐字节不变。

反向实证 7 项：channel 类型未注册、write_back 行锁移除、publish 定义行锁
移除、输出声明路径校验移除、输出声明跨文件校验移除、create 冲突映射移除、
`_authorized_definition` 行锁移除。一项说明：移除 `upload_version` 文件行锁
的变异未被测试抓到——`reserve_storage` 的账户行锁在版本分配前已串行化；
保留显式文件行锁不依赖账本锁的副作用，该变异不计入缺陷复现。

### 12.5 结论分级（整改后）

- **implemented**：七类能力的配置合同/包接收/版本校验/路由；CAS 与版本号
  分配的行锁串行化；输出声明 schema + 服务层路径/跨文件校验。
- **verified**：12.4 全部证据（服务器真实 PG 含 5 个确定性并发测试、7/7
  反向实证、SDK 干净环境）。
- **unverified**：同第 10 节（Temporal/SMTP/模型/机器人非本步范围）。
- **blocked**：S5 范围内无；第 11 节集成门槛不变。
