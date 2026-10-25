# OpsMesh

基于 OpenAI Agents Python SDK 的插件应用宿主，负责身份、发布发现、私有目录、持久任务、定时触发和消息网关。Agent、工具调用、模型选择、Session、MCP、技能和业务编排由插件直接使用原生 SDK 实现。

[![Static checks](https://github.com/jhupo/OpsMesh/actions/workflows/ci.yml/badge.svg)](https://github.com/jhupo/OpsMesh/actions/workflows/ci.yml) [![Security](https://github.com/jhupo/OpsMesh/actions/workflows/security.yml/badge.svg)](https://github.com/jhupo/OpsMesh/actions/workflows/security.yml)

独立插件 SDK 位于 [`sdk` 分支](https://github.com/jhupo/OpsMesh/tree/sdk)，也随宿主 Release 提供独立 wheel 和源码包。

## 安装与启动

生产部署使用 Linux 独立可执行包，支持 x86_64、aarch64，包含 systemd 安装和可选自动更新；部署主机不需要 Python 或项目源码。具体命令见 [部署说明](deploy/README.md)。Tag 发布同时提供 Linux 可执行压缩包、源码 tar.gz/ZIP、安装脚本、宿主与 SDK wheel/sdist、校验清单、SBOM、安全报告和构建来源证明，以及 GHCR 的 amd64/arm64 镜像。版本号必须与宿主及 SDK 元数据一致。当前仓库配置不代表 Release 已实际发布，见 [发布页](https://github.com/jhupo/OpsMesh/releases)。

以下为源码开发环境：

需要 Python 3.12+、uv 和 PostgreSQL。插件 SDK 单独支持 Python 3.11.8+。

```sh
uv sync --frozen --extra dev
uv run opsmesh keygen
```

按 `.env.example` 创建本机 `.env`，配置**全新 PostgreSQL 数据库**和上一步的加密密钥。然后执行：

```sh
uv run opsmesh migrate
uv run opsmesh run
```

`run` 启动 API、任务、调度和网关四个独立进程，默认监听 `0.0.0.0:8000`。API 首次启动创建超级管理员：可用 `OPSMESH_ADMIN_USERNAME`、`OPSMESH_ADMIN_PASSWORD` 设置，缺少的值自动生成，写入数据目录下的 `initial-admin-用户ID.json`（Linux 权限 `0600`），日志仅显示文件位置。读取后修改初始密码并删除该凭据文件。已有管理员时不覆盖账密。

任务 Worker 未配置并发数时，启动时按可用 CPU、内存及容器 cgroup v2 限额自动估算并记录结果；设置 `OPSMESH_WORKER_CONCURRENCY` 可覆盖。每个 Session 仍串行，不同 Session 可并发，调用用户的并发额度独立生效。计算规则及部署约束见 [运行合同](docs/runtime.md)。

通过 `POST /v1/auth/login` 提交 `username` 和 `password` 获取 24 小时令牌，请求使用 `Authorization: Bearer <token>`。本机管理员可使用 `opsmesh issue-token <user_id>` 恢复访问，再调用密码接口；修改密码撤销已有令牌。密钥与数据库、数据目录一起备份，丢失密钥无法解密已有记录。

也可以分别管理各进程，API 不持有通道长连接：

```sh
uv run opsmesh-worker tasks
uv run opsmesh-worker scheduler
uv run opsmesh-worker gateway
```

只启动 API 使用 `uv run opsmesh serve`。发行版统一使用 `opsmesh worker tasks|scheduler|gateway` 启动各 Worker。

启动后的交互文档为 `http://127.0.0.1:8000/docs`，实际 HTTP 合同来自 `/openapi.json`。`/health` 只表示 API 进程存活，不表示数据库、Worker 或第三方服务可用。

Docker 配置见 [deploy/README.md](deploy/README.md)，实际检查记录与联调范围见 [校验边界](docs/verification.md)。

## 第一条调用

1. 管理员打包并安装可信插件：`uv run opsmesh-plugin package examples/native_agent native-agent.zip`，通过 `POST /v1/plugins` 上传 multipart 文件字段 `content`。
2. 在环境变量设置 `OPSMESH_MODEL_API_KEY`、`OPSMESH_MODEL_NAME`、`OPSMESH_MODEL_BASE_URL` 作为框架默认模型；或通过 `POST /v1/connections` 登记自己的模型配置，填写 `name`、`api_key`、`model_name`、`base_url`、`api_mode`、`reasoning_effort`、`is_default`。思考等级仅支持 `low`、`medium`、`high`、`xhigh`，默认 `medium`。用户连接默认只允许 `api.openai.com`，其他域名由管理员通过 `OPSMESH_ALLOWED_MODEL_HOSTS` 启用。
3. 调用 `POST /v1/agents`，填写安装 ID、`entry: "chat"`。示例的 `config` 可为空以使用默认模型，也可显式指定 `connection_id`、`model`。需要文件目录时，先创建自己的 workspace，再填写 `workspace_id`。
4. 调用 `POST /v1/tasks`，提交 `agent_id`、`input` 和稳定的 `idempotency_key`，通过 `GET /v1/tasks/{id}` 查看状态与结果。
5. 示例里的 `confirm_text` 使用原生 SDK 人工审批。查看 `GET /v1/interactions`，以布尔值提交 `POST /v1/interactions/{id}/respond`，Worker 从保存的 RunState 继续执行。

没有模型连接和调用权限时不能运行模型。示例不会使用假结果，也不会在安装时调用模型。

模型配置支持 `GET /v1/connections`、`GET/PATCH/DELETE /v1/connections/{id}`。Key 不在响应中返回；删除后后续调用拒绝使用该连接。`await context.resources.model()` 的选择顺序为显式连接、Agent 所有者的个人默认连接、环境变量默认连接，继续保留原调用者的 Token 计量。思考等级通过原生 ModelSettings 传入，插件的显式设置可覆盖默认值。

本机也可导入插件 ZIP：`uv run opsmesh plugins import native-agent.zip --owner-id <user-id>`。省略 owner 时使用已有管理员，导入命令不执行插件入口。独立 SDK 仍只提供插件开发和打包能力。

## 身份与目录

`UserIdentity` 只有用户 ID、平台角色和组 ID。HTTP 根据令牌构造；网关根据管理员维护的外部身份绑定构造。`metadata` 是业务输入，不能覆盖身份或平台权限。

同组成员可以发现和使用彼此的 Agent 实例，**不能读取配置、浏览或编辑对方的 workspace**。任务输入、结果和人工交互按调用者归属隔离。管理员具有管理访问权。

Agent 实例可关联一个自己的 workspace，也可以没有。`resources` 提供统一的目录列表、文件下载、写入覆盖、删除和创建目录接口；参数为 `kind=workspace|sandbox`、`root_id` 和相对路径 `path`。工作区通过 Workspace ID 定位，沙箱通过 Session ID 定位，不接收服务器绝对路径。插件使用 `context.resources.workspace` 操作分配的目录，用 `artifact(...)` 将选定文件复制成调用者可下载的结果附件。

Agent 配置使用创建、查询和修改接口管理，通过 catalog 发布供其他用户使用；不提供 Agent ZIP 导入导出。插件代码仍通过插件 ZIP 安装。

发布 Agent 时会固化入口、配置和依赖，发布版本**不挂载发布者的私有 workspace**。宿主提供隔离、加密的原生 SDK Session；插件也可自行选择其他原生实现并承担对应的隔离与保留职责。

## 会话与沙箱

`context.session.history` 默认是原生 `EncryptedSession(SQLiteSession(...))`，直接支持 `get_items`、`add_items`、`pop_item`、`clear_session`，传入 `Runner.run(..., session=context.session.history)` 即可使用。清空历史不删除沙箱、文件或任务。保留期由 `OPSMESH_SESSION_TTL_SECONDS` 配置，默认一年。

HTTP 任务不传 `session_id` 时创建会话，后续提交同一 ID 继续对话。也可先调用 `POST /v1/agents/{id}/sessions`。QQ/钉钉自动按 Agent、通道、私聊/群聊类型和会话 ID 绑定；私聊还包含用户 ID。相同群的用户共享该群对话历史，私聊与群聊、不同群之间各自隔离，工作区管理权限仍归所有者。

每个会话同时只有一轮任务执行；等待人工输入仍占用该会话。会话进入后续轮次后不能恢复更早轮次的检查点。`POST /v1/sessions/{id}/close` 关闭空闲会话且保留数据，通道下次消息创建新会话。

沙箱插件通过 `async with context.session.sandbox(client, options, manifest=...) as sandbox` 获得原生 `SandboxRunConfig`。默认初始化 `/workspace/input`、`output` 和 `.opsmesh/{session,skills,cache}`；使用 native Manifest 扩展初始化内容。宿主保存加密会话状态和本地快照，后续重连相同后端；Manifest 只初始化新沙箱，不重置恢复中的文件。

公共文件路由直接操作所选工作区；容器沙箱通过插件注册的原生客户端访问原沙箱，不建立宿主文件副本。所有者可管理沙箱工作目录下的文件和目录，`.opsmesh` 保留给内部设施。文件操作期间占用会话，避免与 Agent 执行冲突。异常时不能确认沙箱状态会保留 `sandbox_dirty`，阻止新任务静默复用；有可恢复状态时可恢复原任务，否则应显式关闭会话。示例见 `examples/sandbox_agent`，公共文件接口和目录约束见 [会话说明](docs/sessions.md)。

## 发布与依赖

`POST /v1/catalog` 发布 `plugin`、`agent`、`tool`、`mcp` 或 `skill`，指定 `private`、`group` 或 `public` 可见性。发布不可覆盖；新版本创建新记录，撤回会阻止后续使用。

`GET /v1/catalog` 可按 `kind`、`name`、`owner_id`、`version` 筛选。消费者取得发布 ID 后，用别名引用具体版本：

```json
{
  "dependencies": {
    "identity": {"kind": "tool", "id": "发布 ID", "version": "1.0.0"},
    "analyst": {"kind": "agent", "id": "发布 ID", "version": "1.0.0"}
  }
}
```

插件清单的依赖与实例依赖合并，禁止覆盖同名声明。运行时使用 `context.dependencies` 取原生工具、MCP 连接或技能路径，或调用已发布的 Agent。直接调用已发布 Agent 使用 `POST /v1/catalog/{publication_id}/tasks`。依赖图限制为 12 层、100 个发布版本。

发布 `plugin` 表示允许消费者使用该安装包创建实例；工具、MCP、技能分别发布其注册项。发布资源不安装任意 Python 依赖，也不让普通用户上传和执行 Python 包。代码安装只对管理员开放。

## 任务与恢复

任务保存调用输入、调用来源、入口配置、插件安装 ID、依赖版本和执行尝试。Worker 通过 PostgreSQL 锁领取任务，续租并处理取消；进程失联后的任务变为 `interrupted`，不自动重跑。

插件可显式保存检查点，或返回 `Suspended` 进入 `waiting_input`。状态与通知 Outbox 在同一数据库事务内保存。响应只接受指定用户，绑定当前检查点；超时、重复响应、取消和过期决定均有状态检查。

`POST /v1/tasks/{id}/resume` 只恢复有检查点的失败、中断或超时任务。恢复使用固定插件和原 SDK 版本，并重建当前身份与资源连接。原生 RunState 表示 SDK 支持的恢复边界，不能恢复任意 Python 调用栈或保证外部副作用自动幂等。业务工具应使用自己的幂等键或效果回执。

子 Agent 调用保留原调用者并生成独立任务，稳定调用键可复用已成功的结果。子任务进入人工输入或嵌套等待时，父任务必须已经保存检查点；宿主持久化父子关系，子任务完成后自动排队恢复父任务，插件从检查点继续并按原调用键读取结果。

`POST /v1/schedules` 支持一次性、固定秒间隔和五字段 cron，包含时区及首次时间。cron 的首次时间是开始边界，按表达式取第一个匹配时刻。默认跳过重叠任务，不补发停机期间每一个错过的时刻；每次触发使用计划 ID 与计划时间去重。

## 额度与可观测性

原调用者承担模型 Token，目录所有者承担自己的存储。`resources.model` 与 `resources.model_provider` 在每次模型请求前通过供应商原生输入计数接口预留“输入计数 + 输出上限”，并按供应商返回的实际 usage 结算。无法提供原生输入计数的模型会明确拒绝托管计量，不使用本地估算或静默降级。原生 MultiProvider 使用登记的 `prefix/model`；连接使用 Responses 计数能力。

预留包含本次请求的输入和输出预算，实际 usage 仍以供应商返回值为准。网络异常、取消或缺失 usage 时保留预留，管理员根据供应商账单通过结算接口核对，不能直接记成零。插件直接使用自己的模型客户端时不在托管计量范围内。

文件接口和附件导出在用户额度锁下检查目录大小，执行结束也会扫描目录。插件直接写盘和沙箱文件系统的硬限制需要部署侧文件系统配额或沙箱配额；进程内路径检查不能限制可信 Python 代码的全部文件操作。数据库记录及服务日志不计入个人目录额度。

统一 JSON 日志包含任务、尝试、用户、插件、Trace 和 Span 标识。每个进程使用独立轮转文件，避免多进程争用一个轮转文件；生产可收集 stdout。SDK 默认 OpenAI Trace 导出器被本地处理器替换，只记录标识与生命周期。修改请求的审计记录与数据库修改一起提交；不记录请求正文和凭据。

## 网关

内置 QQ 官方机器人和钉钉 Stream 适配器，两者直接继承 OpsMesh 自有 `Adapter` 基类。QQ 直接调用腾讯官方 `qq-botpy==1.2.1`；钉钉内部调用官方 `dingtalk-stream 0.24.4b1`。QQ 支持 C2C、群、频道和频道私信、官方 SDK 的消息与固定原生 API 操作；钉钉支持消息、事件、卡片/Graph 回调、主动消息、媒体上传与固定的卡片操作目录。两者实现 capabilities/run/send/call 四项接口；Worker 管理运行与取消，传输类型不进入插件 SDK。设计边界见 [适配层设计](docs/gateway-architecture.md)。具体可用性取决于应用权限。配置、回执与调用示例见 [网关说明](docs/gateway.md)。

管理员创建 Channel 后，为每个外部用户绑定平台用户；未绑定消息与非消息事件只记录，不执行任务。外部群主或管理员角色不会转换成平台角色。管理员操作通过持久队列执行，插件可通过 `context.channel.reply(...)` 回复当前对话，管理员身份还可调用 `context.channel.request(...)`。

QQ 与钉钉都直接使用各自官方 SDK；OpsMesh 只负责宿主 Adapter 边界、事件持久化和出站回执。分层与扩展方法见 [网关架构](docs/gateway-architecture.md)。

入站消息落库去重，出站使用持久 Outbox。送达结果不确定时标记 `uncertain`，只允许显式重试，不能承诺外部消息恰好发送一次。QQ 协议的后台事件分发也不能提供应用落库之前崩溃时的零丢失保证。

群聊人工交互只发送提醒，内容在指定用户的交互收件箱。钉钉使用有有效期的 session webhook，过期后保留待核对记录，不静默改用其他身份或通道发送。

## 目录

```text
src/opsmesh/
  agents/            # 实例配置、可见性、固定执行快照
  bootstrap/         # 应用装配、ASGI、CLI、三类 Worker 入口
  catalog/           # 发布、检索、版本依赖与运行时访问
  gateway/           # 通道配置、身份绑定、收发记录、连接 Worker
    adapters/
      base.py        # 唯一 Adapter ABC：capabilities/run/send/call
      qq/            # 单个 QQAdapter、宿主消息与 API 边界
      dingtalk/      # 单个 DingTalkAdapter、官方 Stream、消息和 OpenAPI
  infra/             # PostgreSQL、加密、版本化迁移
  plugins/           # ZIP 安装、注册表、版本加载
  quotas/            # 用户额度、模型连接、预留与结算
  resources/         # 私有 workspace、文件和结果附件
  sessions/          # 会话隔离、原生历史、沙箱状态和生命周期
  shared/            # 错误、HTTP 依赖、日志、Trace、审计
  tasks/             # 持久任务、调度、检查点、人工交互、执行器
    runtime/         # 执行生命周期、租约、调用上下文和子任务
  users/             # 平台身份、组、认证与令牌
examples/            # 原生 Agent、可发布贡献、插件内组合
deploy/              # Linux systemd、安装、更新与可执行包容器
scripts/             # 可执行包构建入口
.github/workflows/   # 静态检查、安全扫描、双架构构建、Python 包、镜像与 Release 发布
.agents/skills/      # OpenAI、QQ、钉钉集成维护技能
docs/                # 插件与运行合同
```

HTTP 模块均使用 `routes.py`；请求及响应在 `schemas.py`；数据库实体在 `models.py`，业务规则在 `service.py` 或明确职责的运行时模块。

任务执行内部模块收在 `tasks/runtime/`，管理接口和定时调度留在 `tasks/`。其他模块按领域组织：Session 路径及沙箱初始化布局在 `sessions/layout.py`，原生会话生命周期在 `sessions/runtime.py`；网关连接在 `gateway/worker.py`，出站入队与发送在 `gateway/delivery.py`。具体职责树见 [运行合同](docs/runtime.md)。

业务错误统一在 `shared/errors.py` 定义，携带稳定 `code`、`message`、`status_code` 与公开 `details`；HTTP 错误响应为 `{code, message, details}`。首次管理员初始化在 `bootstrap/initialization.py`，完整 ORM 注册在 `bootstrap/schema.py`；`infra/database.py` 只负责数据库基础设施，不反向导入业务模型。

开发参考：[插件合同](docs/plugins.md)、[运行与恢复](docs/runtime.md)、[校验边界](docs/verification.md)。

## SSE 流式事件

创建任务后，用相同身份订阅 `GET /v1/tasks/{task_id}/events`：

```sh
curl -N -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8000/v1/tasks/$TASK_ID/events"
# 断线后，从已处理的最后事件 ID 继续：
curl -N -H "Authorization: Bearer $TOKEN" -H 'Last-Event-ID: 12' \
  "http://127.0.0.1:8000/v1/tasks/$TASK_ID/events"
```

连接先返回当前 `task.snapshot`，再按 ID 顺序回放加密持久日志中的 `task.status`、`task.result`、`plugin.*` 事件。也可使用 `?after=12`；请求头优先，超前或非法游标在建立流之前拒绝。任务终止或等待人工输入时发送 `stream.end` 并关闭连接；提交审批或恢复后重新连接。客户端收到结束事件后应停止自动重连。SDK 保活心跳默认 15 秒；代理应关闭缓冲并放宽读取超时。

插件通过 `await context.events.emit("progress", {"step": 1})` 发布 JSON 数据，客户端收到 `plugin.progress`。`examples/native_agent` 直接消费原生 `Runner.run_streamed()`，将文本增量发布为 `plugin.text.delta`，不替换 SDK 的执行器。事件每条最多 64 KiB，每任务插件事件受 10,000 条事件序列与 8 MB 日志预算约束，超额明确失败。

SSE 按任务调用者/管理员权限访问，并在每次读取时重新验证令牌和身份；撤销访问后发送 `stream.error` 结束。每轮读取使用短事务，不占用跨连接数据库事务。事件 ID 可用于去重，插件事件与状态事件一起保留；模型供应商是否逐字输出取决于插件与原生模型实现。

## 独立 SDK

插件开发和打包使用独立 SDK，文档见 [SDK 分支](https://github.com/jhupo/OpsMesh/tree/sdk)。Python 3.11.8+ 可用于 SDK 打包和类型检查；运行插件的 OpsMesh 宿主需要 Python 3.12+。生产部署请安装与宿主版本一致的 SDK wheel：

```sh
pip install ./opsmesh_sdk-0.2.0-py3-none-any.whl
```

需要从当前源码生成 SDK 时：

```sh
uv run --extra build python scripts/release_assets.py sdk-source --output ../opsmesh-sdk
uv build ../opsmesh-sdk --out-dir ../opsmesh-sdk-dist
# 对已经检出的独立 SDK 工程重新生成：
uv run --extra build python scripts/generate_sdk.py ../opsmesh-sdk
```

Release 会同时提供 SDK wheel、源码包和校验清单。目前未配置 PyPI 发布，请使用对应 Release 中的文件并按发布说明校验 SHA-256。

## 参与与许可

开发规范见 [AGENTS.md](AGENTS.md)，贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，漏洞披露见 [SECURITY.md](SECURITY.md)，变更见 [CHANGELOG.md](CHANGELOG.md)。

OpsMesh 与插件 SDK 使用 **MIT**，见 [LICENSE](LICENSE)。发行包内第三方依赖保留各自版权与许可声明。
