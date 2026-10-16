# S3 SDK 纯合同与传输：实施记录

## 1. 实施范围（V4 第 16.2 节 S3 行）

按 S3 行交付独立 SDK `sdk/python`（发行名 `opsmesh-sdk`，导入名 `opsmesh_sdk`）：
values/errors/manifest 纯值层、扩展声明（operations/hooks/events/tools/channels）、
flow 纯值、HTTP/宿主传输合同、包构建与代码生成工具。服务端 `src/opsmesh` 尚未建立
业务模块，本步不宣称任何业务 API 或宿主服务可用。

文件与职责（对应 V4 第 10 节 SDK 职责树）：

| 文件 | 职责 | 主要入口 |
| --- | --- | --- |
| `pyproject.toml` | 发行元数据、依赖（httpx/pydantic/jsonschema）、CLI 入口、5 条 import-linter 合同 | `opsmesh-sdk` 命令 |
| `README.md` | 外部客户端/扩展作者的公开使用合同 | — |
| `codegen.toml` | OpenAPI 分组、operation_id 前缀、分页/流/共享值/错误码映射约定 | S4+ 生成输入 |
| `src/opsmesh_sdk/values.py` | 中性值类型 | `ResourceRef`/`ScopeRef`/`Cursor`/`Page`/`ContentBlock`/`Usage`/`IdempotencyKey`/`TraceContext`/`FlowRunRef` |
| `src/opsmesh_sdk/errors.py` | 类型化错误与稳定 code | `OpsMeshError` 及 11 个子类、`error_for_code()` |
| `src/opsmesh_sdk/manifest.py` | 包声明合同 | `PackageManifest`、`validate_manifest()`、`JsonSchema`、`HookStage`、`EffectKind`、`Permission` |
| `src/opsmesh_sdk/extensions/operations.py` | operation 声明与结果封套 | `declare_operation()`、`Operation`（invoke 强制封套 + 输出 schema 校验）、`OperationResult`、`cancelled_outcome()` |
| `src/opsmesh_sdk/extensions/hooks.py` | 钩子链决策合同 | `Continue`/`Patch`/`Block`、`HookInput`、`HookBinding`、`validate_decision()` |
| `src/opsmesh_sdk/extensions/events.py` | 事件封套与订阅需求 | `EventEnvelope`（`deduplication_key()`）、`EventFilter`、`EventSubscriptionNeed` |
| `src/opsmesh_sdk/extensions/tools.py` | operation→工具 schema 适配 | `tool_schema_for_operation()`、`ToolSchema`/`ToolContent` |
| `src/opsmesh_sdk/extensions/channels.py` | 厂商验签与规范消息 | `SignatureVerifier`（HMAC-SHA256 验签 + 重放窗口）、`InboundMessage`、`NormalizedMessage` |
| `src/opsmesh_sdk/transport/contracts.py` | 请求/响应/流/取消合同 | `TransportRequest`/`TransportResponse`/`StreamChunk`/`CancellationScope`/`TransportContext`/`Transport`、`require_session()`、`build_call_headers()`、`resolve_timeout()`、`send_cancellable()` |
| `src/opsmesh_sdk/transport/http.py` | HTTP 传输 | `HttpTransport`（X-Session-ID、幂等头、超时/错误映射）、`parse_error_payload()` |
| `src/opsmesh_sdk/transport/host.py` | 宿主委托传输 | `DelegationIdentity`、`HostTransport`（拒绝调用方伪造委托头） |
| `src/opsmesh_sdk/transport/streams.py` | 有界 SSE 读取 | `EventStreamReader`（游标续读、空闲上界）、`parse_sse_event()` |
| `src/opsmesh_sdk/host/state.py` | 宿主 state 客户端 | `StateStore`（get/list/set/CAS/batch/capacity）、`StateBatch` |
| `src/opsmesh_sdk/host/secrets.py` | 密钥槽读取 | `SecretReader`、`SecretSlotRef` |
| `src/opsmesh_sdk/host/models.py` | 模型/embedding 操作 | `ModelOperator`、`ModelPrompt`/`ModelResult` |
| `src/opsmesh_sdk/host/jobs.py` | 流程提交（S10 前仅合同） | `JobSubmitter`、`JobSubmission`→`FlowRunRef` |
| `src/opsmesh_sdk/host/events.py` | 自有事件发布 | `EventPublisher`、`EventDraft` |
| `src/opsmesh_sdk/host/observability.py` | 关联观测记录 | `ObservabilityReporter`、`ObservabilityRecord` |
| `src/opsmesh_sdk/context.py` | 不可变调用上下文 | `InvocationContext`/`InvocationSubject`/`PlatformFacade` |
| `src/opsmesh_sdk/client.py` | 平台客户端门面 | `PlatformClient`（`group()` 动态加载生成分组）、`client_session_context()` |
| `src/opsmesh_sdk/development/build.py` | 包构建 | `build_package()`、`export_schemas()`、`manifest_digest()` |
| `src/opsmesh_sdk/development/runner.py` | 本地合同运行器 | `ContractRunner` |
| `src/opsmesh_sdk/development/testing.py` | 合同 fixture 与可记录宿主替身 | `RecordingHostClient`、`ContractFixtures`、`hook_input()`、`invocation_context()` |
| `src/opsmesh_sdk/development/cli.py` | 作者 CLI | `main()`：validate/build/test |
| `src/opsmesh_sdk/api/__init__.py` | 生成包标记（业务分组随 S4+ 生成） | — |
| `tests/test_client_contracts.py` | 传输合同测试（httpx MockTransport/异步替身） | 21 项 |
| `tests/test_extension_contracts.py` | 扩展声明测试 | 23 项 |
| `tests/test_host_delegation.py` | 宿主委托测试 | 14 项 |

工程合同同步修改（AGENTS.md：实施时核对受影响工程文件）：

- 主 `pyproject.toml`：`[tool.uv.workspace] members` 增加 `sdk/python`；mypy
  `files`/`mypy_path` 纳入 SDK；import-linter 新增合同
  `v4-server-does-not-import-sdk`（`opsmesh` 禁止导入 `opsmesh_sdk`）。
- `uv.lock`：新增 workspace 成员 `opsmesh-sdk==0.1.0rc23`。
- SDK `pyproject.toml`：`include_external_packages = true`（forbidden 合同含
  fastapi/sqlalchemy/temporalio/redis 等外部框架，必须解析外部包图）。

## 2. 合同与权限落位

- **SDK 分层**（V4 12.2）：`transport` 不导入 `api`/`client`/`context`；
  `extensions` 只依赖 values/errors/manifest；`host` 不导入 `context`/`client`；
  生成分组（`api`）不反向导入组合层；`opsmesh_sdk` 整体禁止导入 `opsmesh`、
  `backup`、`backup_2` 及服务端框架。由 5 条 SDK 合同 + 1 条服务端合同守护。
- **身份**：登录 Session 经 `X-Session-ID` 传递；`HttpTransport` 构造时拒绝空
  session；每次调用可用 context 覆盖身份头（委托场景）。
- **委托不可伪造**：`DelegationIdentity`（installation/instance/invocation/
  namespace/parent/deadline）只由宿主构造；`HostTransport` 拒绝调用方在
  `identity_headers` 中携带任一委托头（`SecurityError`）；委托流只带委托头，
  不带用户 session。
- **错误合同**：服务端错误 envelope（`error.code/message/request_id/details`）
  映射到 11 类类型化错误；5xx 归 `TransportError`（429 除外）；`request_id`
  优先取响应头、缺失时回退 envelope；`retry_after` 解析 `Retry-After`。
- **取消/超时**：`CancellationScope.ensure_active()` 在发送与流读取每行前检查；
  `httpx.TimeoutException`→`TimeoutError`，连接失败→`TransportError`；流有
  `MAX_IDLE_SECONDS=300` 空闲上界与 `MAX_CHUNK_BYTES=1MB` 行上界。
- **声明合同**：manifest 校验重复 export_id、事件引用未知 operation、未声明
  权限、导出数量/ schema 体积上界；`OperationResult` 强制 1MB 上界且
  `needs_attention` 必须声明 effects；钩子补丁按阶段白名单（≤32 字段）校验；
  事件封套 `deduplication_key()` 稳定；通道验签执行重放窗口。
- **构建合同**：`build_package` 只打包 manifest、导出 schema 与声明源码，
  不执行包代码；`manifest_digest` 为安装时固定的 sha256。

## 3. 运行证据（Windows 本地，2026-10-16）

环境：Python 3.11（`uv` 管理）、依赖以 `uv.lock` 为准；无 PostgreSQL/Redis/
Temporal 参与（本步不涉及）。

| 命令 | 结果 |
| --- | --- |
| `uv run ruff check sdk/python` | All checks passed（33 个源/测试文件） |
| `uv run ruff format --check sdk/python` | 33 already formatted |
| `uv run mypy`（含 SDK，92 文件） | Success: no issues found |
| `uv run lint-imports --no-cache`（根） | Contracts: 11 kept, 0 broken |
| `uv run lint-imports --config sdk/python/pyproject.toml --no-cache` | Contracts: 5 kept, 0 broken |
| `uv run pytest sdk/python/tests` | 36 passed |
| `uv run python scripts/audit_app_layout.py --architecture-check` | Architecture verified: 62 source files |
| `uv build --package opsmesh-sdk` | wheel + sdist 构建成功 |

wheel 边界检查：top-level 仅 `opsmesh_sdk` 与 dist-info；无 `opsmesh/`、
`backup`、`opsmesh_operator` 泄漏；METADATA 含项目描述；`console_scripts`
入口 `opsmesh-sdk = opsmesh_sdk.development.cli:main` 正确；sdist 含
README.md。

CLI 端到端（fixture 包，`python -m opsmesh_sdk.development.cli`）：

- `validate`：`manifest valid: fixture-knowledge@1.0.0`，`exports: 1`；
- `build`：产物含 `manifest.json`/`schemas.json`/`package.py`，digest 稳定；
- `test`：`contracts checked`，`tool schemas: 1`。

测试覆盖的行为（正向/拒绝/边界）：请求序列化与身份/幂等头、错误 envelope
映射（409/404/429/401/5xx）、取消阻止发送、超时与连接失败分类、SSE 解析与
游标续读、流拒绝、空 session 构造期拒绝、委托头附加与伪造拒绝、委托 deadline
非法拒绝、manifest 拒绝（重复 export/未知 operation 引用/未声明权限）、
operation 输入校验与体积上界、钩子白名单、事件去重键、通道重放窗口、
JsonSchema 导出/再入往返稳定。

### 验证中发现并修复的缺陷（均在提交前关闭）

1. `transport/contracts.py`：pydantic 模型引用 `Field` 但未导入（F821）——
   与 dataclasses `field` 别名共存。
2. `transport/http.py`：`_error_for_response` 引用未导入的 `RateLimitError`
   （F821）；错误 envelope 的 `request_id` 未回退读取。
3. `transport/host.py`：`DelegationIdentity` 未校验 deadline 为正（测试
   `test_delegation_rejects_invalid_deadline` 实测失败后修复）。
4. 注入 httpx 客户端无 base_url 时相对路径失败（`unknown url type`）——
   传输自身拥有 base_url 拼接，注入客户端与自建客户端行为一致。
5. `manifest.py`：`JsonSchema` 导出使用 JSON Schema 约定的
   `additionalProperties`，再入校验被 `extra="forbid"` 拒绝——CLI `test`
   命令实测失败；改为 `AliasChoices` 双别名并补往返回归测试（修复前该测试
   失败）。
6. `client.py`：`client_session_context` 是异步生成器但未声明 `async def`；
   `context.py` 宿主分组字典改联合类型并去除错误 ignore；`host/*`、
   `extensions/*` 若干未用导入与超长行。

## 4. 状态

- **implemented**：`sdk/python` 全部 33 个源/测试文件、pyproject/codegen.toml/
  README.md；主工程 workspace 成员、mypy 纳入、服务端禁导入 SDK 合同。
- **verified**：ruff/format/mypy strict/两份 import-linter/pytest 36 项/
  布局审计/wheel 与 sdist 构建及边界检查/CLI validate-build-test 端到端。
- **unverified**：
  - wheel 在干净 venv（无 opsmesh）实际安装导入未执行（本轮跳过）；已用
    wheel 内容边界、METADATA 依赖与禁导入合同间接证明无服务端依赖。
  - 业务 API 分组客户端（`api/*`）未生成，随 S4–S13 对应服务端交付；本步
    不宣称任何业务 API 可用（V4 16.3）。
  - 宿主客户端（`host/*`）服务端实现随 S8 验收；`host.jobs` 随 S10 与唯一
    流程引擎一起实现，S10 前不得宣称可用。
  - 真实 HTTP/宿主传输的产品行为（真实服务端、Session、取消、游标续读）
    随 S7 集成验收。

## 5. 评审修复（提交 `c4123305` 的 8 项问题 + README 不一致）

对 `c4123305` 的正式评审指出 3 项 P1 与 5 项 P2 安全/合同缺口，全部在本轮
关闭；每项修复均以“恢复缺陷逻辑→对应测试必须失败”反向实证。

### 5.1 [P1] 流式委托绕过身份伪造检查

`HostTransport.stream()` 原先直接进入 `EventStreamReader`，后者让
`context.identity_headers` 覆盖宿主身份，伪造 `INVOCATION_HEADER` 的流请求
可以成功。修复：新增 `transport/contracts.py::build_call_headers()` 统一
send/stream 的头装配——调用方 `request.headers` 与 `context.identity_headers`
均不得覆盖传输声明的受保护头（http：`X-Session-ID`，可由 context 替换；
host：全部 7 个委托头，一律不可覆盖），违规抛 `SecurityError`；
`EventStreamReader` 增加 `protected_headers`/`overridable_headers` 参数，
由所属传输注入。反向实证：移除 host reader 的 protected 后
`test_delegation_stream_rejects_a_forged_invocation_header` 失败
（DID NOT RAISE SecurityError）。

### 5.2 [P1] `SignatureVerifier` 实际没有验证签名

`InboundMessage` 无签名字段，`verify()` 丢弃 secret 只查时间窗，任意新鲜
未签名消息返回 True。修复：`InboundMessage` 增加 `signature` 字段；
`verify(message, *, secret, now_unix)` 实现 HMAC-SHA256：
`HMAC(secret, delivered_at_unix || "." || raw_body)` 十六进制，常数时间
比较；未知算法在构造时 fail closed（`SUPPORTED_SIGNATURE_ALGORITHMS`）。
厂商特定方案（如 ed25519）随 S11 真实协议扩展本合同，不静默跳过验签。
反向实证：移除密码学校验后，篡改 body/伪造签名/错误 secret 三个测试失败。

### 5.3 [P1] deadline 没有真正传递到 HTTP 超时

`CancellationScope` 只保存 deadline 不计算剩余时间；HTTP 用请求自身
timeout，SSE 用固定 300s。修复：

- `CancellationScope` 以 `time.monotonic()` 记录起点，新增
  `remaining_seconds()`/`expired`；`ensure_active()` 在过期时抛
  `TimeoutError`；deadline 非正数构造期拒绝。
- 新增 `contracts.py::resolve_timeout()`（http/host 共用，消除两处重复
  实现）：有效超时 = min(请求 timeout, 剩余 deadline)。
- 新增 `contracts.py::send_cancellable()`：请求与取消 future 竞速，
  `cancel()` 立即中断在途请求（含 SSE 逐行读取）。
- SSE 空闲上界取 min(300s, 剩余 deadline)；read 超时且 deadline 已过期时
  映射为 `TimeoutError` 而非 `TransportError`。
- 取消等待改为“注册式 future”（`register_cancellation`），不再在 scope 上
  保存跨事件循环的 `asyncio.Event`——评审前发现的既有用法（同一
  TransportContext 跨两个 `asyncio.run`）会直接崩溃。
  反向实证：`resolve_timeout` 恢复缺陷后客户端/宿主两个 deadline 测试均
  失败；`_idle_bound` 恢复缺陷后 SSE deadline 测试失败。

### 5.4 [P2] Operation 输出 schema 和结果大小限制没有在执行入口生效

`invoke()` 只校验输入，裸返回整数也成功。修复：`invoke()` 强制 handler
返回 `OperationResult` 封套（否则 `ValidationError`），并校验
`result.value` against 声明的 `output_schema`；体积上界与
needs_attention/effects 一致性由封套构造保证。`JsonSchema` 空声明
（无 properties 且无 required）释义为“不约束”，未声明 schema 的
operation 不再拒绝一切参数/输出。同步更新 `ContractRunner`（解包 value）、
CLI fixture handler 与测试。反向实证：移除封套/输出校验后两个测试失败。

### 5.5 [P2] Patch 字段数量限制无效

Pydantic 模型上的 `__post_init__` 永不执行，33 个字段仍通过。修复：改用
`field_validator("fields")` 强制 ≤ `MAX_PATCH_FIELDS`。反向实证：恢复
“不检查”后测试 DID NOT RAISE。

### 5.6 [P2] `TransportRequest.headers` 是公开字段但没有发送

三种传输均未合并 `request.headers`，自定义头静默丢失。修复：
`build_call_headers()` 以“调用方头 → 传输身份 → context 身份 → trace”
顺序合并；受保护身份头由调用方提供即 `SecurityError`（见 5.1）。
反向实证：去掉合并后 http send/http stream/host stream 三个自定义头
断言失败。

### 5.7 [P2] `parent_invocation_id` 保存但没有传输

修复：新增 `X-OpsMesh-Parent-Invocation-Id` 委托头并纳入
`_FORBIDDEN_CALLER_HEADERS`；根调用不带头。测试覆盖父子链与缺失两种
情形。反向实证：移除父头后父子链测试失败。

### 5.8 [P2] HostTransport 没有响应体大小限制

修复：`HostTransport._to_response` 与 http 传输共用
`MAX_RESPONSE_BYTES`（8MB）上界，超限抛 `TransportError`。反向实证：
移除检查后大小测试失败（错误信息不含 size bound）。

### 5.9 README CLI 示例与实际参数不一致

`validate`/`test` 实际接收 manifest 文件路径，`build` 需要 `--target`；
README 示例改为一致并补充说明。

### 5.10 修复后验证证据（Windows 本地，2026-10-16）

| 命令 | 结果 |
| --- | --- |
| `uv run ruff check sdk/python` | All checks passed |
| `uv run ruff format --check sdk/python` | 33 already formatted |
| `uv run mypy`（含 SDK，92 文件） | Success: no issues found |
| `uv run lint-imports --no-cache`（根） | Contracts: 11 kept, 0 broken |
| `uv run lint-imports --config sdk/python/pyproject.toml --no-cache` | Contracts: 5 kept, 0 broken |
| `uv run pytest sdk/python/tests` | 58 passed |
| `uv run python scripts/audit_app_layout.py --architecture-check` | Architecture verified: 62 source files |
| `uv build --package opsmesh-sdk` | wheel + sdist 构建成功 |
| CLI validate/test/build（fixture 包） | 全部通过 |

新增测试：流伪造拒绝（http/host）、send 伪造请求头拒绝、父调用链、
host 响应大小、host/客户端 deadline 边界、请求超时、取消中断在途请求
（send 与 stall 流）、SSE deadline、自定义头发送、封套拒绝、输出 schema
拒绝、空声明不约束、Patch 上限、验签正向/篡改/错密/重放/未知算法。
