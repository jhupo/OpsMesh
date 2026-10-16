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
