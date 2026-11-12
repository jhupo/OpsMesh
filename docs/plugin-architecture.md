# OpsMesh 插件架构

本文定义当前插件职责与公开契约。目录以源码为准，设计条目不代表外部服务已经联调成功。通道协议细节见 [通道与执行回调](channels-and-callbacks.md)，数据库转换见 [数据库迁移](database-migrations.md)，安装见 [Linux 部署](linux-deployment.md)。

## 1. 结构与依赖

```text
src/opsmesh/                       # 核心发行包，直接就是框架
├── app.py / cli.py                # 加载宿主清单与启动
├── drivers/                       # 配置、适配器索引、连接、生命周期
├── adapters/                      # Channel / Adapter / Event
├── dependencies/                  # Dependent 与上下文注入
├── dispatch/、matchers/、messages/
├── permissions/、rules/
├── plugins/                       # 加载机制与可扩展插件命名空间
└── config.py / lifecycle.py
packages/
├── channels/src/                  # 安装为 opsmesh.plugins.channels
├── execution/src/                 # 安装为 opsmesh.plugins.execution
├── filesystem/src/                # 安装为 opsmesh.plugins.filesystem
└── database/src/                  # 安装为 opsmesh.plugins.database
examples/
├── common/conversation_support/
├── chat/example_chat/
└── sandbox/example_sandbox/
```

| 部分 | 负责 | 不负责 |
|---|---|---|
| Framework | 加载和依赖关系、注册归属、生命周期、Event/Message、Matcher、Rule、Permission、Dependent、Context | SDK 执行、数据库表、模型配置、业务身份系统 |
| Framework Driver | 基础配置、任务组、适配器注册、逻辑连接、生命周期及连接回调 | 网络实例、路由、ASGI、HTTP 客户端 |
| channels | 协议自有监听与连接、请求与流收发、认证、协议解析、用户上下文、路由声明、消息转换与回复 | 智能体构造、会话 CRUD、模型调用 |
| execution | 已构造 Agent 的原生执行、完整流作用域、回调、Trace 导出 | Agent 注册中心、持久调用表、Usage 账本、业务状态机、会话索引 |
| filesystem | 普通目录、读写、受限路径、ZIP 校验与解压、导出和新目录发布 | SQLite、安装记录、技能/工具模型、元数据数据库、Python 加载 |
| database | PostgreSQL engine、session factory、显式事务、独立迁移 | 执行历史、会话模型和任何业务表定义 |
| 功能插件 | 构造 Agent/工具/MCP/技能，公开入口，持有会话和资源 | 改写 Framework/SDK 的基础机制 |

宿主创建通用 `Driver`，当前实例的绑定和获取集中在 `opsmesh/drivers/base.py`，通过 `opsmesh.drivers` 导出 `set_driver/get_driver/is_initialized`。删除独立的 `access.py`、`runtime.py`，不保留兼容入口。`register_adapter`、`get_adapter`、`get_channel` 和生命周期回调都属于 Driver。

Driver 是框架容器；Adapter 是协议实现；Channel 是插件声明的具体账号或逻辑连接。Driver 保存唯一的适配器注册表和已连接通道索引，Adapter.channels 是按所属适配器过滤的只读快照。查询通道必须提供连接 ID，不隐式选择第一条连接。`send/call` 留在 Channel/Adapter，不再加一层 Driver 转发。

框架不持有网络服务。API 和 QQ 分别持有自己的 FastAPI 实例与监听器，钉钉持有官方 Stream SDK 连接；channels 的生命周期协调器只调用协议模块的启停方法，不分配、注入或保存网络实例。

通用启动顺序为 `on_startup → on_ready → on_started`；channels 在 `on_startup` 准备适配器和路由，在所有插件就绪后通过 `on_started` 开放监听。关闭顺序为 `on_quiescing → on_stopping → on_shutdown`，各关闭阶段内部逆序执行；channels 在第一阶段关闭监听、排空 HTTP 与机器人消息，消费者随后停止，最后释放资源。框架只执行阶段回调，不判断网络协议。

删除 `HostDriver`、`get_app/get_asgi`、`driver.http` 和网络 Mixin；无兼容别名。插件通过生命周期装饰器获取通用启动能力，HTTP 类型直接使用 FastAPI/Starlette；删除独立的 `http/` 实现。框架 `Adapter` 只保留逻辑连接、调用和运行上下文，不再代理 HTTP/WebSocket 注册或出站请求。

`framework/` 已删除，公共入口直接是 `opsmesh`。核心的 `opsmesh.plugins` 保存加载机制；官方插件在独立发行包中扩展它的子模块，核心包不包含这些实现。源码使用扁平的 `packages/<name>/src/`，setuptools 将其映射到安装模块路径。标准 `opsmesh.plugins` entry point 提供短名，因此使用 `require("channels")`、`require("execution")` 即可；宿主清单和配置命名空间使用完整路径。加载器先匹配宿主声明，未声明的模块仍受准入策略限制。短名冲突会明确报错，不猜测选择哪个包。

核心与四个插件分别构建 wheel 和源码包，同一个版本 tag 的 Release 一并发布。Linux 宿主包只包含核心及其运行环境，插件作为独立附件按需部署。默认宿主清单不启用任何插件，安装器不执行插件迁移或安装模型依赖。

功能插件按需 `require()`；不是所有插件都依赖 database 或 filesystem。execution 无 database、channels、filesystem 依赖。Framework 不导入 SDK；SDK 类型直接出现在需要它的插件接口中，不建立镜像模型和多层装配函数。

## 2. 插件加载与配置

宿主读取入口模块、插件路径、启停状态及插件配置，配置由所属插件校验。`require("channels")` 等声明并加载依赖，不要求在清单中重复列出默认配置的依赖。需要覆盖配置的依赖可以使用 `autoload: false` 条目；`config_namespace` 缺省为模块名。显式禁用及来源校验仍生效。宿主不扫描加载所有已安装插件，也不把清单当作依赖白名单。Framework 按依赖顺序加载、启动，消费者不可在依赖失败时接单。声明通过框架登记归属，加载失败或撤销时移除；启动资源通过生命周期回调释放。

宿主清单的 `framework` 字段使用框架 Config 校验，只包含命令前缀、分隔符、交互等待超时等基础行为。每个 `plugins[].config` 由对应插件的配置类定义和校验，配置快照通过插件管理器归属到 Plugin。框架 Config 不再保存 `plugin_configs`，配置访问也不再回退到全局配置。

`/etc/opsmesh/host.json` 是宿主启动配置；`/etc/opsmesh/opsmesh.env` 仅是可选的 systemd 环境变量来源。框架不读取环境文件，也不定义模型、数据库或通道凭据。没有插件环境变量时不需要这个文件，安装器不会默认创建它。

插件通过 `execution = require("execution")` 等句柄调用接口或声明回调。`require` 确定加载依赖；`Depends` 决定一次调用的参数和资源作用域，两者不能互相替代。类型及原生 SDK 导入仍是正常 Python 导入，不要求把每个类型都包装成依赖函数。

官方插件导入模块、配置命名空间与运行 ID 使用 `opsmesh.plugins.channels`、`opsmesh.plugins.execution`、`opsmesh.plugins.filesystem`、`opsmesh.plugins.database`；展示名称可以简写，不能把展示名称当作加载地址。业务插件 ID 及已有数据库历史归属不因发行包拆分自动改名。

配置由所属插件读取与校验。宿主不识别 QQ、钉钉、模型或数据库业务字段。channels 只有一个插件入口；API 配置存在即开启，机器人连接由消费插件声明。不要再登记三个通道子插件，不提供手动 route activation 流程。

重启按原配置和持久文件重新加载插件及原生对象。旧执行和内存审批不自动恢复；历史存储是否持久由调用插件选择的 SDK Session 决定。源码插件是受信任的进程内代码，框架归属和文件路径限制不构成恶意 Python 隔离。

## 3. 统一入口、身份与响应

```text
API / QQ / 钉钉
→ 验证 API 凭据、QQ 签名或钉钉 Stream 连接身份
→ Event + Message + UserContext
→ Framework Permission / Rule / Matcher / Dependent
→ 功能插件处理函数
→ execution.run / stream（需要模型时）
→ 功能插件决定输出
→ 原 Channel 编码并发送
```

`channels.UserContext` 是冻结对象，只含 `id`、`name`、`groups`、`roles`，集合为 `frozenset`。名称缺失时使用身份 ID。身份由可信入口建立，不能用请求正文覆盖。处理函数通过 `Annotated[channels.UserContext, Context()]` 取得同一对象，原生 SDK 工具通过 `wrapper.context` 访问。

运行 ID、会话 ID、数据库句柄、MCP 连接不塞进 UserContext。Framework 的 `permissions.User` 是权限匹配器，不重定义为用户资料类。会话 ID 属于功能插件的接口参数；传给 Langfuse 的用户/会话关联使用单独的 TraceContext。

API 将请求转换成通道定义的 HTTPEvent（继承框架 Event）和 Message，不定义 HTTPBody 依赖。路由处理函数声明参数和返回类型，业务插件控制返回内容；通道控制 HTTP 状态编码和发送。路由只匹配声明的 Matcher，消息路由可选择自己的命令 Matcher，仍只分发一次。

## 4. execution

### 原生执行

`run(agent, message, ...)` 返回 SDK 原生 RunResult；`stream(...)` 是异步上下文管理器，在作用域内提供原生 RunResultStreaming。调用插件直接读取 `final_output`、`interruptions`、`context_wrapper.usage` 等字段。不重建 Result、UsageTotals、CallRecord 或持久执行状态。

Agent、SandboxAgent、Session、RunState、RunConfig、原生 hooks 均由调用者提供。execution 不查找或构造智能体，不实现 AgentLoop，不自动重试整轮调用，不保存审批决定。流提前退出、异常或取消时取消未完成 SDK 工作并等待其清理；正常完成后才结束回调和追踪作用域。

必要的本进程在途任务跟踪只用于停止接单、关闭等待和取消，不形成业务查询 API 或新的执行状态机。

### 回调与归属

| 接口 | 语义 |
|---|---|
| `before_run` | SDK 调用前校验；异常阻断执行 |
| `on_agent_start/end`、`on_llm_start/end`、`on_tool_start/end`、`on_handoff` | 对应 SDK hook 的插件声明；保留原生异常语义 |
| `on_run_end(result, error, trace_id)` | 结束后的观察通知；通知失败被记录，不覆盖已有结果或原异常 |

回调经 Framework Dependent 注入参数，不建立第二次 Event 分发。当前 Matcher 决定调用插件；后台调用以 `plugin_id=` 显式指定归属。当前 Matcher 与显式归属矛盾时拒绝执行。调用插件及其 `require()` 依赖链中的回调可参与，互不相关插件不接收广播；每个声明只执行一次。

原生 SDK hooks、AgentHooks 和 Guardrails 保留既有行为，不能笼统吞掉它们的异常。只有明确的结束观察通知不影响已得到的业务结果。回调依赖覆盖整次调用和流清理，结束时释放。

### Trace 与 Langfuse

execution 启动时配置一次导出目标，支持禁用、本地诊断或 Langfuse。不要每次请求改全局 processor，不安装重复导出链。敏感内容采集由 tracing 配置控制，凭据不进入 trace。

具体导出链路和 SDK 审批恢复的追踪兼容边界见 [Langfuse 追踪](langfuse-tracing.md)。

调用者传 `trace_context=TraceContext(user_id=..., session_id=..., tags=...)`。这属于观测属性，不改变模型上下文或 SDK Session；同一对话每轮使用同一个稳定 session_id。属性作用域覆盖 run 或整个 stream，不能在创建流后立刻退出。

Users/Sessions 由上报属性形成；Prompts/Skills 由功能插件选择并加载，必要时关联版本和技能加载事件。execution 不变成 Prompt/Skill 管理中心。Playground 的模型连接配置在 Langfuse 中独立管理。

Langfuse 导出或结束观察失败不能重跑 Agent、覆盖成功结果。恢复观测服务应先检查 Web、Worker、队列和 ClickHouse，不能把页面无数据等同于 SDK 没有调用。Langfuse 不可用与本地执行失败是不同故障。

## 5. database

插件通过 `PluginMetadata.extra["database"] = DatabaseSchema(metadata, directory, head)` 声明原生 SQLAlchemy metadata 和 Alembic 目录。database 消费框架 `on_plugin_loaded`，负责登记回滚和启动前版本检查，业务插件不显式登记模型或迁移。

`DatabaseAccess()` 注入 Database，可并发共享的是 engine/factory，不是 AsyncSession。并行依赖分别打开 session/transaction；一个原子事务里的操作顺序执行。database 不为 execution 创建表。

每个稳定插件 ID 使用独立版本表；不同插件不共享 Alembic revision 图。版本变更由运维命令执行，启动只校验。已有共享 `alembic_version` 必须通过显式完整来源清单转换；停用插件仍需保留历史迁移来源。旧 execution 数据不会自动删除。完整转换和数据保护流程见 [数据库迁移](database-migrations.md)。

## 6. filesystem

部署配置一个根目录。filesystem 在该根下划分插件的 data/cache/config 普通目录，保留真实文件内容，SDK LocalFile/LocalDir 可直接使用。目录归属从框架加载上下文或调用插件解析，不能由请求指定任意插件身份。

公共操作为路径获取、读写、删除、解压、导出及 `publish_directory`。路径应始终处于受控插件根内；初始化时检查配置根和子根，后续拒绝路径穿越、链接逃逸及受保护根删除。ZIP 限制压缩大小、解压大小、文件数量、深度和重复路径。

文件写入采用临时文件后原子替换。`publish_directory` 只向不存在的目标发布，不覆盖旧目录；目标已存在必须报冲突，不能以删旧目录再 rename 冒充原子更新。ZIP 导出快照由返回的句柄负责临时文件清理。

技能名称、说明、安装 ID、启用状态及资源引用由功能插件保存；filesystem 没有 SQLite 索引或安装业务状态机。源技能目录与沙箱可变工作区分离，工作区修改不自动回写技能包。

## 7. 原生 Agent、MCP 与会话

智能体插件通过原生 SDK 定义 Agent/SandboxAgent、工具、Manifest、Skill/Skills 和模型配置。SDK Skill 从普通目录/文件条目构造，ZIP 先校验解压。工具按原生 function_tool/Tool 定义；SDK 不会自动解析 Framework Depends，需要 DI 的工具显式使用 Dependent 并关闭依赖作用域。

MCP 使用 SDK 原生服务器类型；创建它的插件负责连接、凭据和关闭，并在调用作用域结束前保持可用。不同用户的凭据不能靠修改同一个共享 MCP/模型对象隔离。

Session 由调用插件提供，可用 SDK SQLAlchemySession；会话列表、所属用户和并发策略是业务插件补充，不能重写 SDK 消息历史存储。审批使用 SDK interruptions/RunState/approve/reject；聊天交互可通过 on_command，但 Framework pause/got 不代替 SDK 运行状态。

示例中会话索引、清空历史、删除会话、重置工作区是不同操作。UnixLocalSandbox 的本地目录不代表操作系统隔离；工作区持久化不代表中断执行可恢复，也不代表可自动跨机器共享。

## 8. 文件职责与验收

根 `__init__.py` 保留插件元数据、依赖声明、必要声明导入和公共导出。实际实现按功能职责放置：通道的认证/协议/出站客户端/流资源，execution 的调用/回调/追踪，database 的连接/声明/迁移，filesystem 的路径/文件/压缩包。下划线表示内部实现，不要求机械套 `_internal` 目录，也不为一行转发建立类。

验证只覆盖受影响路径：原生 Runner 正常/异常/审批/流取消，回调错误语义与依赖归属，Langfuse 属性关联和导出故障，数据库独立历史与显式转换，文件边界和发布冲突，通道单次分发及断连清理。区分确定性测试、真实 PostgreSQL、模型供应商、机器人、MCP、沙箱、冻结可执行包以及服务器验收，不用“单测通过”替代外部联调。
