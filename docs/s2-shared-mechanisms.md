# S2 公共机制：实施记录

日期：2026-10-16
状态：S2 已完成（implemented + verified）。本记录是 V4 方案第 16.2 节 S2 的验收材料，不替代 V4 设计本身；只登记 S2 实际完成范围与证据。

## 1. 实施范围（V4 第 16.2 节 S2 行）

`shared`、`telemetry`、`temporal`、唯一 Outbox、codec；新配置与进程生命周期装配。共新建 `src/opsmesh` 源码 61 个文件：

| 模块 | 文件数 | 内容 |
| --- | --- | --- |
| shared | 29 | contracts/serialization/errors/files/pagination/version/mail；db 6（base/session/errors/idempotency/pagination）、http 10（errors/middleware/dependencies/pagination/responses/sse/client_ip/context/client）、redis 4（keys/client/cache）、security 5（egress/rate_limits/redaction/secrets） |
| telemetry | 7 | context/logging/metrics/tracing/exporters/instrumentation，只使用 shared 与第三方观测库 |
| temporal | 8 | codec.py；dispatch 6（contracts/models/outbox/delivery/dispatcher） |
| bootstrap | 10 | api（create_app + lifespan 逆序释放）/models/routers/resources/request_context/telemetry/temporal/worker/worker_cli |
| 根 | 3 | `__init__.py`、`main.py`（委托 `opsmesh.bootstrap.api:create_app`）、`config.py`（V4 S2 范围设置） |

`bootstrap/routers.py` 的 `register_routers` 按设计不注册特性路由；identity（S4）、capabilities/resources（S5）等后续步骤在这一点位注册。

## 2. 合同与权限落位

1. **shared 零产品依赖**：`shared` 不导入任何 `opsmesh.*` 业务模块、不读业务配置；由 import-linter 合同 `v4-shared-core-independent` 等 10 个合同固化 V4 第 12 节依赖表。
2. **唯一事务 Outbox**：`temporal_dispatches` 表（`dispatch_id` 唯一）由 `stage_execution/stage_signal/stage_dispatch` 在与业务事实同一事务内写入意图；提交后由 `TemporalOutboxDispatcher`（PG LISTEN/NOTIFY + SKIP LOCKED 领取，`_CLAIM_LEASE_SECONDS = 60`，退避上限 300s，默认并发 16）投递。同 `dispatch_id` 重复 staging 幂等；同 ID 不同命令拒绝。
3. **Payload codec**：`EncryptedPayloadCodec` 只产出 `binary/opsmesh-encrypted` 编码的 payload，metadata 带 `key_id`；拒绝解码非本 codec 的明文 payload。密钥最窄边界解密（`shared/security/secrets.py`），不进日志。
4. **进程生命周期**：`create_app(settings, *, engine=None)` 的 lifespan 按获取逆序释放（redis client 先于 database engine）；启动失败（如 redis 连接失败）时已获取资源全部释放，测试以 FakeEngine + monkeypatch 验证。
5. **不导入备份**：ruff `extend-exclude` 排除 `backup`、`backup_2`；import-linter 合同 `production-no-test-framework` 等全部 KEPT；无任何 `backup` 引用。

## 3. 相对参考源码的修正

- `shared/redis/client.py`：参考实现（backup_2）使用 `socket_connect_timeout_seconds`/`health_check_interval_seconds`，均非 redis-py 真实参数名（redis-py 静默忽略，超时与健康检查实际失效）。新实现使用真实参数 `socket_timeout`/`socket_connect_timeout`/`health_check_interval`。
- `shared/security/redaction.py`：新增 `contains_embedded_secret()`，供日志/事件输出防护复用。
- `shared/files.py`、`shared/serialization.py`、`shared/http/client.py`、`shared/http/context.py`：V4 第 9 节新文件（参考源码不存在），本次按合同新建。

## 4. 迁移与工程合同

- 删除旧架构迁移链 `migrations/versions/0001…0035` 共 35 个文件（旧业务迁移，V4 硬性规则 4 不沿用）。
- 新建 `migrations/versions/0001_shared_mechanisms.py`（revision `0001`）：空库初始迁移，创建 `temporal_dispatches` 与 `http_creation_receipts` 两张表。
- `migrations/env.py`：target metadata 改由 `opsmesh.bootstrap.models.register_models()` 提供。
- `pyproject.toml`：ruff 增加 `extend-exclude = ["backup", "backup_2"]`；import-linter 旧合同整体替换为 V4 合同 10 个（v4-root-independence、v4-telemetry-foundation、v4-temporal-foundation、v4-config-foundation、v4-shared-core-independent、v4-pagination-no-transport、v4-shared-infrastructure-no-http、v4-shared-no-execution-sdk、v4-bootstrap-not-imported、production-no-test-framework）。
- 删除无法收集的旧测试套件 213 个文件（`ModuleNotFoundError: No module named 'opsmesh'`，源码迁出后预期状态；git 可恢复）。新建 S2 测试 6 个文件（见第 6 节）。
- `scripts/audit_app_layout.py`：按 V4 同步——BUSINESS_ROOTS 更新为 V4 十个业务根、ENTRYPOINTS 更新为 `opsmesh.main`/`opsmesh.bootstrap.worker_cli`（删除不存在的 `opsmesh.delivery`、`opsmesh.bootstrap.update_cli` 死引用）、删除 agents/workspaces.runtime 旧架构检查、superseded 目录表更新为旧根包（agents/teams/messaging/runtime/delivery/core/domains/api/observability）、包初始化文件允许仅含模块文档字符串、execution 允许依赖按 V4 12.1 表校准（identity 路由认证、governance）。
- `scripts/export_api_docs.py`：修正死引用 `build_application` → `create_app`、`storage_root` 传 str；无特性路由时 `--check` 如实报告"导出延后"而非误报（V4：实现新 API 后再从实际 routes/schema 生成接口文档，S2 无特性路由，不生成为空的旧文档树）。

## 5. 本地验证证据（Windows，2026-10-16）

| 命令 | 结果 |
| --- | --- |
| `uv run ruff check tests src/opsmesh migrations scripts` | All checks passed |
| `uv run mypy` | Success: no issues found in 61 source files |
| `uv run lint-imports --no-cache` | Contracts: 10 kept, 0 broken |
| `uv run python scripts/audit_app_layout.py --architecture-check` | Architecture verified: 61 source files, 2 unique table owners |
| `uv run python scripts/export_api_docs.py --check` | No feature routes registered; API documentation export deferred（设计内延后，非失败） |
| `uv run pytest tests -q` | 46 passed, 9 skipped |

跳过项均为环境性跳过，且在服务器真实环境执行（见第 6 节）：Windows 进程无符号链接特权跳过 `test_resolve_within_root_rejects_symlink_escape`；未配置 PG/Temporal 跳过 8 个 PG 测试与 1 个真实 Temporal 测试。

## 6. 服务器验证证据（192.168.232.129，Ubuntu，真实 PostgreSQL 16 + 真实 Temporal 1.29.1）

环境：源码上传 `/tmp/s2-verify/app`；测试库 `opsmesh_s2_test`（docker `opsmesh-postgres-1`）；验证依赖安装到 `/tmp/s2-verify/pylibs`（pytest、temporalio==1.34.0，pip 自动回溯到 Python 3.12 兼容的 nexus-rpc）；Temporal dev server（`temporal-server start`，development/sqlite）+ `default` 命名空间（CLI `operator namespace create --retention 72h`）。

| 验证项 | 命令/方式 | 结果 |
| --- | --- | --- |
| 空库初始迁移 | `python -m alembic upgrade head`（OPSMESH_DATABASE_URL 指向 opsmesh_s2_test） | Running upgrade -> 0001, shared mechanisms |
| 全量测试（PG + Temporal） | `OPSMESH_TEST_DATABASE_URL=... OPSMESH_TEST_TEMPORAL_ADDRESS=127.0.0.1:7233 python -m pytest tests` | **56 passed**（连续三次：55→55→56，第三次含新增真实 Temporal 测试），0 failed, 0 skipped |
| 符号链接逃逸（Linux 真实执行） | `test_resolve_within_root_rejects_symlink_escape` | passed（本地因 Windows 特权跳过，服务器补验） |
| Outbox 回滚零投递 | `test_staged_dispatch_is_invisible_after_rollback` | passed |
| 同 dispatch_id 幂等 / 异命令拒绝 | `test_repeated_staging_of_same_dispatch_is_idempotent`、`test_staging_same_id_with_different_command_is_rejected` | passed |
| publisher 标记 / 失败退避 | `test_publisher_marks_published_rows`、`test_failed_delivery_schedules_backoff` | passed |
| 并发领取独占 | `test_concurrent_publishers_claim_exclusively`（BlockingClient + asyncio.gather） | passed（恰好一个 publisher 交付） |
| codec 往返 / 拒绝明文 / 无明文泄漏 | `tests/temporal/test_payload_codec.py` 3 项 | passed |
| **真实 Temporal 加密 history + 重放** | `tests/temporal/test_payload_codec_replay.py`（opt-in，`OPSMESH_TEST_TEMPORAL_ADDRESS`） | passed：携带 secret 的 workflow 经 codec 往返执行成功；原始 history 序列化字节不含明文 secret 且含 `binary/opsmesh-encrypted` 标记；`Replayer` 重放无失败 |

## 7. S2 验收标准核对（V4 第 16.2 节）

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| shared 零业务导入 | 通过 | import-linter 10 合同 KEPT；audit_app_layout 无违规 |
| 真实 PG 回滚零投递 | 通过 | 第 6 节服务器测试 |
| 并发领取幂等 | 通过 | 第 6 节服务器测试（独占断言 + 恰好一次交付） |
| Temporal codec/重放正确 | 通过 | 第 6 节真实 Temporal 服务器：往返执行、history 无明文、Replayer 重放无失败 |
| 密钥不进日志/明文 history | 通过 | codec 单测（输出不含明文）+ 真实 history 字节断言；SecretCipher 单测（未知 key 拒绝、旧 key 解密、redaction） |
| 启动失败按逆序释放 | 通过 | `tests/bootstrap/test_api_assembly.py`（FakeEngine + monkeypatch：启动失败释放引擎；lifespan 状态设置与关闭释放） |
| 不导入备份 | 通过 | ruff exclude + import-linter + 无引用 |

## 8. 已知项与后续

- `export_api_docs.py` 文档生成延后至首个含特性路由的步骤（S4 identity）：V4 要求从实际 routes/schema 生成，S2 无特性路由。
- 旧测试套件 213 个文件已删除（git 可恢复）；新测试按 S2 范围重建，后续步骤各自扩充。
- 工作区中 `AGENTS.md`、`README.md`、`deploy/*`、`.agents/skills/*` 的改动为先于 S2 存在的未提交变更，不属于 S2 提交范围。
- 服务器 `/tmp/s2-verify` 为验证用临时环境（temporal dev server、pylibs、测试库 `opsmesh_s2_test`），不参与运行部署；`opsmesh_s2_test` 库与 temporal 进程可在后续步骤复用。
- 待关闭集成门槛：S3 SDK 纯合同起按第 16.2/16.3 节逐项验收；S2 不替后续步骤背书。真实 Temporal 沙箱/取消/恢复与官方 Agents 集成在 S7（execution）按第 16.4 节登记。

## 9. 评审修复（提交 `9e000211` 的 8 项问题）

来源：对 `9e000211` 的代码评审，结论"基础函数已经写出，但装配和失败场景尚未闭环"。8 项全部修复，本地与服务器真实 PG/Redis/Temporal 复验通过。以下按问题逐条登记。

### 9.1 [P1] Docker 启动入口无法启动（无参工厂）

- **问题**：`create_app(settings)` 为必填参数，Uvicorn `--factory opsmesh.main:create_app` 无参调用报 `missing 1 required positional argument: 'settings'`。
- **修复**：[`src/opsmesh/bootstrap/api.py`](src/opsmesh/bootstrap/api.py) 改为 `create_app(settings: Settings | None = None)`，`None` 时 `get_settings()` 读取进程配置；显式传参的测试与脚本行为不变。
- **验证**：`uvicorn --factory target opsmesh.main:create_app resolved: OpsMesh` 真实启动成功；`tests/bootstrap/test_api_assembly.py::test_create_app_reads_the_process_configuration` 覆盖无参工厂。

### 9.2 [P1] HTTP 安全配置没有接入应用

- **问题**：文档开关、CORS、限流配置未实际生效——关闭文档后 `/docs`、`/openapi.json` 仍 200；每窗口 1 次请求连续三次仍全 200；允许来源的 CORS 预检 405。
- **修复**：`create_app` 用 `app_settings.enable_api_docs` 控制 `docs_url`/`redoc_url`/`openapi_url`（None 即不注册路由）；按 V4 网关限流策略新增 [`src/opsmesh/bootstrap/http_policy.py`](src/opsmesh/bootstrap/http_policy.py)——按 `api_prefix` 首段分类：`auth`→authentication（fail_closed）、`platform`→platform（fail_closed）、其余 api（fail_closed=False）；中间件注册顺序为"后注册在最外层"：SecurityHeaders → RateLimit → RequestContext → CORS，保证 CORS 预检在网关内层得到应答、`enabled`/`window_seconds` 来自配置。
- **验证**：服务器真实 Redis 场景复验——关闭文档返回统一 404 envelope；CORS 预检 200；每窗口 1 次请求第二次即 429 `rate_limited`；`test_api_applies_the_configured_docs_cors_and_rate_limit_settings`、`test_gateway_policy_classifies_paths_by_scope` 覆盖。

### 9.3 [P1] Outbox 可能永久丢失取消命令

- **问题**：Workflow 不存在被当作取消成功，START 与 CANCEL 并发投递时取消先到会被标记完成，Workflow 随后仍启动。
- **修复**：[`src/opsmesh/temporal/dispatch/outbox.py`](src/opsmesh/temporal/dispatch/outbox.py) 引入兄弟命令感知的因果序：`DispatchOutcome` 增加 `DEFERRED`/`SUPERSEDED`。START 投递前检查同 `workflow_id` 是否已有**已发布** CANCEL（有则 SUPERSEDED，记录完成但不启动）；CANCEL 投递前检查是否还有**未发布** START（有则 DEFERRED，`available_at` 按指数退避重排、`last_error_code=awaiting_sibling_command`，等待 START 先投递）。`delivery.py` 对已结束 Workflow 的取消仍按成功处理（取消已终结对象是幂等空操作），"尚未启动"由兄弟检查覆盖。投递失败退避上限常量收敛为 `_MAX_DEFERRAL_SECONDS = 30`。
- **验证**：服务器真实 PG 确定性测试（按操作类型精确控制 `available_at`）：`test_cancel_arriving_before_start_defers_until_the_start_is_delivered`（取消 DEFERRED 且代码为 `awaiting_sibling_command`；释放 START 后投递；释放 CANCEL 后取消运行中的 workflow）、`test_start_is_never_delivered_after_its_cancel_was_published`。

### 9.4 [P2] Signal 重投缺少端到端去重合同

- **问题**：未把稳定 `dispatch_id` 传给接收方，服务端接受 Signal 后响应丢失，Outbox 重试导致两次接收。
- **修复**：[`src/opsmesh/temporal/dispatch/delivery.py`](src/opsmesh/temporal/dispatch/delivery.py) 将 `dispatch_id` 作为 Signal 首参数：`args=(str(request.dispatch_id), *request.signal_args)`；[`contracts.py`](src/opsmesh/temporal/dispatch/contracts.py) `DispatchRequest` docstring 明确合同——`dispatch_id` 是稳定投递身份，Signal 首参数即该身份，接收方按 Temporal 消息传递要求对 at-least-once 重投做去重（恰好一次处理）。
- **验证**：`test_signal_delivery_carries_the_stable_dispatch_identity` 断言首参数为 `str(request.dispatch_id)`。接收方去重规则属于使用 Signal 的 Workflow 合同，随 S7 execution 具体 workflow 落地时逐项验收。

### 9.5 [P2] API 遥测与统一日志没有装配

- **问题**：`configure_api_telemetry()` 已定义但应用入口未调用；Worker 的初始化不能覆盖独立 API 进程。
- **修复**：lifespan 启动时 `configure_logging(...)` 按 `log_level`/`log_format`/`log_queue_capacity`/`service_name`/`environment` 初始化统一日志；`create_app` 中调用 `configure_api_telemetry(app, app_settings)`；关闭时 `flush_process_telemetry()` + `shutdown_logging()`，与引擎/Redis 释放共同构成逆序。
- **验证**：服务器真实 Redis 起完整应用——API 进程 `trace.get_tracer_provider()` 非 Proxy（真实 TracerProvider）、root logger 挂 QueueHandler（统一有界队列）；输出可见结构化 JSON 请求日志（`request_id`、`trace_id`、`http_status`、`duration_ms`）；`test_api_process_installs_telemetry_and_unified_logging` 覆盖。

### 9.6 [P2] API 数据库配置被硬编码覆盖

- **问题**：lifespan 固定 `pool_size=5, max_overflow=5`，配置 1/0 实际仍 5/5，语句超时等参数未传入。
- **修复**：[`src/opsmesh/bootstrap/resources.py`](src/opsmesh/bootstrap/resources.py) `build_database_resources(settings)` 统一装配——`pool_size`/`max_overflow`/`pool_timeout_seconds`/`pool_recycle_seconds`/`statement_timeout_ms` 全部来自配置；sessionmaker `info` 注入 dispatch cipher 与 trace context 捕获。api.py lifespan 改为调用该函数，删除硬编码。
- **验证**：`test_api_database_uses_the_configured_pool_and_session_resources` 以 spy + `database_pool_snapshot` 断言配置 3/1 时实际池为 3/1。

### 9.7 [P2] 缓存失效可能被正在加载的旧结果覆盖

- **问题**：`get_or_set` 发布时只检查加载锁，加载旧数据期间发生更新和 `delete()` 后旧结果仍重新写入缓存。
- **修复**：[`src/opsmesh/shared/redis/cache.py`](src/opsmesh/shared/redis/cache.py) 发布脚本增加失效代次守卫（三 KEY：锁/存储/代次键）：代次不匹配返回 2，`get_or_set` 对 2 只为本调用返回加载值、不持久化；`delete()` 先 `incr` 代次键再删存储键，保证并发加载者无法发布删除前旧值；[`keys.py`](src/opsmesh/shared/redis/keys.py) 新增 `cache_generation`。
- **验证**：`tests/shared/test_cache_invalidation.py` 5 项（fakeredis 确定性：`test_invalidation_during_load_never_publishes_the_stale_value` 等；真实 Redis opt-in）；服务器真实 Redis 全过。

### 9.8 [P2] 框架产生的 404/405 不符合统一错误合同

- **问题**：只注册 FastAPI `HTTPException`，遗漏 Starlette 基类；未匹配路由返回 `{"detail":"Not Found"}`，缺 `error.code`/`request_id`。
- **修复**：[`src/opsmesh/shared/http/errors.py`](src/opsmesh/shared/http/errors.py) 注册 `starlette.exceptions.HTTPException`（FastAPI 子类继承它，一个处理器同时覆盖两者）；`_code_for_status` 增加 `HTTP_405_METHOD_NOT_ALLOWED → method_not_allowed`。
- **验证**：`test_unmatched_route_returns_the_unified_error_envelope`（含 `request_id`）、`test_method_not_allowed_uses_the_unified_envelope`；服务器真实应用访问不存在路由返回统一 envelope。

### 9.9 修复后验证证据

| 环境 | 命令 | 结果 |
| --- | --- | --- |
| 本地 Windows | `uv run ruff check tests src/opsmesh migrations scripts` | All checks passed |
| 本地 Windows | `uv run mypy` | Success: no issues found in 62 source files |
| 本地 Windows | `uv run lint-imports --no-cache` | Contracts: 10 kept, 0 broken |
| 本地 Windows | `uv run python scripts/audit_app_layout.py --architecture-check` | Architecture verified: 62 source files |
| 本地 Windows | `uv run pytest tests -q` | 58 passed, 13 skipped（跳过项均为 Windows 环境性/PG/Temporal 未配置，服务器补验） |
| 服务器（真实 PG 16 + Redis + Temporal 1.29.1） | `OPSMESH_TEST_DATABASE_URL=... OPSMESH_TEST_TEMPORAL_ADDRESS=127.0.0.1:7233 python -m pytest tests` | **72 passed, 0 failed, 0 skipped**（bootstrap 12、cache 5、security 20、shared_mechanisms 24、dispatch outbox 11 含 3 项新增因果序测试、codec 3、replay 1、真实 Redis 缓存测试） |
| 服务器（无参工厂实证） | `uvicorn --factory target opsmesh.main:create_app` | `resolved: OpsMesh`，启动成功 |
| 服务器（评审场景复验，真实 Redis） | 完整应用逐项访问 | 关闭文档→统一 404 envelope；未匹配路由带 `request_id`；CORS 预检 200；每窗口 1 次第二次 429 `rate_limited`；API 进程真实 tracer provider + 统一有界日志队列，输出含 `request_id`/`trace_id` 的结构化请求日志 |

第 9.3、9.4、9.7 的复现此前仅用模拟依赖；本次已在服务器用真实 PostgreSQL/Redis/Temporal 补齐对应验证。8 项问题状态：implemented + verified（9.4 的接收方去重规则属后续 S7 workflow 合同，已按第 16.2 节登记为待关闭项）。

## 10. 第二轮评审修复（提交 `15bdd5f5` 的 5 项问题）

来源：对 `15bdd5f5` 的代码评审，结论集中在"决策未原子化、装配顺序与清理覆盖不全、回收策略缺失、测试自身可被规避"。5 项全部修复，本地与服务器真实 PostgreSQL/Redis/Temporal 复验通过。

### 10.1 [P1] START/CANCEL 并发窗口（决策原子化）

- **问题**：`_deliver_command` 用独立事务查询兄弟命令，检查与标记 `published_at` 之间有窗口。取消查询时 START 尚未提交，随后 START 提交并投递，两边都通过检查——取消标记完成后 Workflow 仍然启动（交错模拟已复现）。
- **修复**：[`outbox.py`](src/opsmesh/temporal/dispatch/outbox.py) 将"兄弟检查 + 决策持久化"合并到同一事务，事务持有 PostgreSQL 事务级 advisory lock（`pg_advisory_xact_lock(hashtextextended(workflow_id, 0))`），同一 Workflow 的 START/CANCEL 因此串行化，先提交的决策必然被后决策方观察到：START 见已决策 CANCEL → SUPERSEDED（标记终态不启动）；CANCEL 见未决策 START → DEFERRED。决策事务内即把本行 `published_at` 置位（决策先于网络调用持久化），Temporal 投递在锁外进行（网络 I/O 不持 DB 锁）；投递失败时 `_record_failure` 清回 `published_at`，命令回到未决策态按新同胞状态重判。SIGNAL 无因果序合同，仍直接投递。`_has_command` 改为决策事务内的 `_has_sibling(session, ...)`。
- **验证**：真实 PG 测试 `test_cancel_decided_before_its_start_is_staged_supersedes_the_start`（取消投递期间才提交 START，START 随后被 SUPERSEDED，`client.calls == []`）、`test_concurrent_start_and_cancel_settle_without_a_running_workflow`（两个 publisher 并发，终态两行均有决策且 Workflow 不停留运行）；`test_failed_delivery_schedules_backoff` 增加 `published_at is None` 断言（失败释放决策）。

### 10.2 [P2] OTLP 日志导出生效

- **问题**：`configure_logging()` 在 lifespan 中清空 root handlers，把创建应用时已挂载的 OTLP `LoggingHandler` 一并移除——stdout 有日志、OTLP 零条（内存导出器复现）。
- **修复**：[`api.py`](src/opsmesh/bootstrap/api.py) 将 `configure_logging(...)` 移到 `create_app` 主体，且先于 `configure_api_telemetry(app, app_settings)`：统一日志先重置 root handlers，OTLP 处理器随后挂载，两者并存。
- **验证**：`test_api_log_export_handler_survives_the_unified_logging_setup`（create_app 后 root logger 同时存在 QueueHandler 与 OTLP LoggingHandler）；服务器内存导出器实证：应用装配后发出的日志记录被 OTLP 导出器收到（1 条，payload 匹配）。

### 10.3 [P2] 数据库初始化失败泄漏日志监听线程

- **问题**：`build_database_resources` 在清理 try/finally 之前，日志已启动时数据库构建失败，`shutdown_logging()` 不执行，监听线程残留（已复现）。
- **修复**：lifespan 重构为"日志配置之后的一切获取都在 try 内"：外层 finally 固定执行 `flush_process_telemetry()` + `shutdown_logging()`；内层 finally 维持逆序释放（redis client → database engine），`database` 预声明为 `None` 并按实际构建情况释放。
- **验证**：`test_database_startup_failure_stops_the_logging_listener`（数据库构建失败时 spy 记录到 `shutdown_logging` 调用）；服务器实证：失败后 `telemetry.logging._listener is None`，监听线程已停止。

### 10.4 [P2] 代次键永久累积

- **问题**：`delete()` 每次 `incr` 代次键但无过期，目标缓存不存在也留下永久键。
- **修复**：[`cache.py`](src/opsmesh/shared/redis/cache.py) 新增原子 Lua 脚本 `_INVALIDATE_SCRIPT`：递增（或初始化）代次键、设置 TTL、删除存储键一步完成。TTL 取 `DEFAULT_GENERATION_TTL_SECONDS = 86_400`（24h）：必须长于任何加载租约（默认 30s），否则过期代次读作 '0' 会放行旧值发布；加载租约本身由锁检查兜底（锁过期则发布失败），24h 覆盖全部合理场景且有界。
- **验证**：`test_delete_bounds_the_generation_key_with_a_ttl`（fakeredis：TTL 在 `(0, 86400]`）；真实 Redis 实证：delete 不存在的键后代次键 TTL 为 86400s，且加载期间 delete 的守卫仍然拦阻旧值发布。

### 10.5 [P2] 真实 Redis 测试无法证明旧值未重新发布

- **问题**：`test_invalidation_guard_holds_against_real_redis` 在 finally 中先删除目标缓存再断言，即使旧值被错误写入断言也通过（内存中移除守卫仍通过）。
- **修复**：调整为"先断言、后清理"：join 加载线程后立即断言旧值未持久化与代次键 TTL 有界，全部通过后才 `delete(key)` 清理并关闭连接。
- **验证**：服务器真实 Redis 下该测试通过；反向实证：故意把发布脚本替换为无代次检查的版本后，交错场景中旧值确实重新持久化（证明修复后的测试能抓住该回归）。

### 10.6 第二轮修复验证证据

| 环境 | 命令 | 结果 |
| --- | --- | --- |
| 本地 Windows | `uv run ruff check tests src/opsmesh migrations scripts` | All checks passed |
| 本地 Windows | `uv run mypy` | Success: no issues found in 62 source files |
| 本地 Windows | `uv run lint-imports --no-cache` | Contracts: 10 kept, 0 broken |
| 本地 Windows | `uv run python scripts/audit_app_layout.py --architecture-check` | Architecture verified: 62 source files |
| 本地 Windows | `uv run pytest tests -q` | 63 passed, 16 skipped（跳过项均为 Windows 环境性/PG/Temporal/Redis 未配置） |
| 服务器（真实 PG 16 + Redis + Temporal 1.29.1，host 网络容器） | `python -m pytest tests`（OPSMESH_TEST_DATABASE_URL/TEMPORAL_ADDRESS/REDIS_URL） | **79 passed, 0 failed, 0 skipped**（dispatch outbox 13 项含 2 项新原子性测试、bootstrap 14、cache 6 含真实 Redis、security 20、shared_mechanisms 24、codec 3、replay 1） |
| 服务器（评审场景复验） | `python /verify/s2_review_scenarios.py` | OTLP 导出收到记录；数据库失败后 listener 停止（`_listener is None`）；真实 Redis 代次键 TTL 86400s 且守卫有效；移除守卫后旧值持久化（反向证明测试有效）；START/CANCEL 原子性由真实 PG 测试覆盖 |

5 项问题状态：implemented + verified。第二轮复验使用服务器真实 PostgreSQL/Redis/Temporal（host 网络容器 `opsmesh-refactor-test:initial`，依赖版本与本地 uv.lock 一致：temporalio 1.34.0、opentelemetry-sdk 1.44.0、fakeredis 2.35.1 + lupa）。

## 11. 第三轮评审修复（提交 `c3f2f7c8` 的 3 项问题）

来源：对 `c3f2f7c8` 的代码评审。3 项全部修复，本地与服务器真实 PostgreSQL/Redis 复验通过。

### 11.1 [P1] 投递中断永久丢失命令

- **问题**：决策事务在调用 Temporal 前就提交 `published_at`。进程退出或任务取消时命令实际未投递，但后续领取只查 `published_at IS NULL`，租约过期也无法恢复——命令永久丢失。
- **修复**：[`outbox.py`](src/opsmesh/temporal/dispatch/outbox.py) 拆分语义：`published_at` 只在投递成功确认后由 `_record_published` 置位；决策事务（持 per-workflow advisory lock）只做兄弟检查与 claim 校验，不再提前写任何状态。命令在确认前保持 `published_at IS NULL`，进程退出/取消后租约过期即可被重新领取、重新决策并重投。SUPERSEDED 是唯一在决策事务内置位的终态（无需投递）。`_record_failure` 不再需要清理预置位。
- **验证**：真实 PG 测试 `test_interrupted_delivery_stays_claimable_and_is_retried`（`start_workflow` 抛 `CancelledError` 模拟中断：行保持 `published_at IS NULL`；租约过期释放后重新领取并投递成功）。

### 11.2 [P1] 启动与取消竞态仍然存在

- **问题**：START 提前标记后，启动 RPC 尚未生效时 CANCEL 查询不到 Workflow 并幂等“完成”，START 随后成功——取消已完成、Workflow 却在运行（模拟复现）。
- **修复**：同一拆分顺带闭合此窗口——CANCEL 的 DEFER 条件是“START 未确认”（`published_at IS NULL`），而 `published_at` 现在只在启动成功后置位，因此 START 投递在途期间 CANCEL 一律等待，绝不会对“尚不存在的 Workflow”完成取消；START 确认后 CANCEL 才投递，取消的是真正运行中的 Workflow。CANCEL 的 SUPERSEDED 侧不变（仅对已确认取消的 START 生效）。
- **验证**：真实 PG 测试 `test_cancel_defers_while_its_start_delivery_is_in_flight`（START RPC 阻塞在途期间并发 CANCEL：取消 DEFERRED、`last_error_code=awaiting_sibling_command`、未调用取消；START 确认后取消到达运行中的 Workflow）。

### 11.3 [P2] 缓存失效标记过期后重复

- **问题**：代次键 TTL 过期后重新从 `1` 计数。旧 loader 持旧 `1`，标记过期、再次 delete 又生成 `1`，旧 loader 通过检查写回旧值（fakeredis 复现）。
- **修复**：[`cache.py`](src/opsmesh/shared/redis/cache.py) 代次值改为随机 token：`_INVALIDATE_SCRIPT` 直接 `set` 一个 `uuid4().hex`（不再 incr），随机 token 永不过期重复；发布脚本代次缺省值同步改为 `''`。TTL 保留仅为有界回收，正确性不再依赖代次键存活时长。
- **验证**：fakeredis 测试 `test_expired_generation_does_not_unblock_a_stale_loader`（delete → 删除代次键模拟过期回收 → 再次 delete：旧 loader 被拦阻，旧值不持久化）；真实 Redis 测试增加断言：连续两次 delete 生成的代次 token 不同。

### 11.4 第三轮修复验证证据

| 环境 | 命令 | 结果 |
| --- | --- | --- |
| 本地 Windows | `uv run ruff check tests src/opsmesh migrations scripts` | All checks passed |
| 本地 Windows | `uv run mypy` | Success: no issues found in 62 source files |
| 本地 Windows | `uv run lint-imports --no-cache` | Contracts: 10 kept, 0 broken |
| 本地 Windows | `uv run python scripts/audit_app_layout.py --architecture-check` | Architecture verified: 62 source files |
| 本地 Windows | `uv run pytest tests -q` | 64 passed, 18 skipped（跳过项均为 Windows 环境性/PG/Redis/Temporal 未配置） |
| 服务器（真实 PG 16 + Redis + Temporal 1.29.1） | `python -m pytest tests`（host 网络容器，OPSMESH_TEST_DATABASE_URL/TEMPORAL_ADDRESS/REDIS_URL） | **82 passed, 0 failed, 0 skipped**（dispatch outbox 15 项含 2 项新回归测试、cache 7 项含真实 Redis、bootstrap 14、security 20、shared_mechanisms 24、codec 3、replay 1） |

3 项问题状态：implemented + verified。上轮已确认修正项（OTLP 日志挂载、数据库初始化失败后的日志清理、测试断言被清理掩盖）随本轮套件回归通过，未回退。
