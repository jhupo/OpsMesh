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
