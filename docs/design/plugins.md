# 插件、事件与协议扩展

本文描述当前实现和公开 API。OpsMesh 采用 NoneBot2 风格的导入注册、Matcher、依赖注入和协议适配机制，并增加无 Bot 事件、平台服务依赖和 Agent 执行扩展。它是独立实现，不是 NoneBot2 插件的直接运行环境；既有插件需要迁移导入路径、类型和平台调用。

完整可运行示例见 [custom_plugin](../../examples/custom_plugin/README.md)。平台职责见 [架构](architecture.md)，HTTP 操作见 [运行指南](../runtime-api.md)，SDK 边界见 [SDK 覆盖说明](sdk-coverage.md)。

## 1. 实际目录与职责

以下只列插件机制相关的现有文件。插件使用公开入口；`internal` 保存实现。框架不依赖用户、能力、运行管理等业务模块。

```text
src/opsmesh/
├── framework/
│   ├── __init__.py             # init/get_driver、加载插件、on_* 入口
│   ├── events.py               # EventEnvelope、DispatchContext、当前事件访问
│   ├── message.py              # 事件分发、前后处理器；不是 Message 数据类
│   ├── matcher.py              # Matcher 公开入口
│   ├── params.py               # Depends、CommandArg、Arg、ArgPlainText
│   ├── rule.py                 # Rule、命令和正则条件
│   ├── permission.py           # Matcher 响应权限
│   ├── scheduling.py           # Driver监督的进程内定时器
│   ├── exception.py            # Matcher 控制流与上下文异常
│   ├── errors.py               # OpsMeshError 等系统错误
│   ├── typing.py               # OpsMeshContract 等公共类型
│   ├── plugin/
│   │   ├── __init__.py         # 插件公开入口
│   │   ├── model.py            # OpsMeshPlugin、OpsMeshPluginMetadata、PluginState
│   │   ├── manager.py          # 加载上下文、依赖关系、启停、失败回滚
│   │   ├── load.py             # load_plugin/load_plugins/require/get_plugin_config
│   │   └── on.py               # on/on_message/on_command/on_event/on_regex
│   ├── dependencies/
│   │   ├── __init__.py         # CallableDependency、DependencyScope、Depends
│   │   └── utils.py            # 注解解析
│   ├── adapters/              # Adapter/Bot/Event/Message 抽象与公开导出
│   ├── agents/                # Agent 规格、执行上下文与后端协议
│   ├── drivers/
│   │   ├── __init__.py         # 生命周期、HTTP/WS 客户端与服务端接口
│   │   ├── httpx.py            # HTTP 客户端实现
│   │   ├── websockets.py       # WS 客户端实现
│   │   ├── network.py          # OpsMeshNetworkDriver 能力组合
│   │   └── asgi.py             # 将 Driver 入站路由挂到 ASGI 应用
│   └── internal/
│       ├── event.py            # envelope 与进程内上下文
│       ├── driver/             # abstract.py、model.py、server.py
│       └── matcher/            # matcher.py、manager.py
├── dependencies.py            # 平台公开服务 provider
├── modules/
│   ├── agents/api.py           # Agent 定义目录、代码工厂协议
│   ├── capabilities/api.py     # 工具、Skill、知识等能力协议
│   ├── runtime/api.py          # AgentRuntime、SubmitRunRequest、RuntimeExtensions
│   ├── bots/api.py             # 账号、身份映射、Agent绑定与消息交付协议
│   └── scheduling/            # 平台持久任务、授权与租约调度
│       ├── api.py              # PersistentSchedule、SchedulingService
│       └── internal/service.py # PersistentScheduler
└── bootstrap/                  # 组装服务、绑定 provider、加载插件及运行编排
```

`modules/*/api.py` 是对应业务域的合同，`events.py` 是该域的业务事实，`internal/` 是私有实现。没有将所有 DTO、元数据和业务接口汇总到全局 `contracts`。平台 HTTP 路由在 `entrypoints/http/routes/`，通过公开服务访问业务。

`OpsMeshBot` 是已连接的协议实例；持久账号、外部用户映射、授权和交付幂等属于 `modules/bots`。`OpsMeshAdapter` 适配机器人协议；`AgentBackend` 适配 Agent SDK 执行，两者是不同边界。

### 内置插件的实际状态

`framework/plugin/` 实现插件机制；`src/opsmesh/plugins/` 仅容纳可选扩展。此前把助手、管理、知识、调度、通知和工作流笼统规划为插件，是职责分类错误。平台的持久数据、权限与基础调用链应独立于插件加载器存在；插件使用平台公开服务增加命令、特定业务行为和可选提供商。

| 早期占位名 | 平台应拥有的职责及当前状态 | 合理的可选插件内容 |
| --- | --- | --- |
| `assistant` | `bots`拥有Bot/频道到已发布Agent版本的绑定；`sessions`和`runtime`拥有对话/执行；组装层连接默认回复链，均已实现 | 特定聊天命令、意图分流和个性化回复 |
| `agent_admin` | `agents`拥有定义、发布、版本、启停；`bots`拥有机器人绑定，均已实现 | `/agent`等聊天管理命令，仅作为同一管理服务的另一个入口 |
| `knowledge` | `capabilities`拥有知识目录、导入和检索，已实现 | 特定文档源接入或领域查询命令 |
| `scheduled_jobs` | `scheduling`拥有持久计划、执行身份、授权、租约与启停；`framework/scheduling.py`提供进程内定时器 | 日报、巡检等具体任务及TimerEvent处理器 |
| `notifications` | 平台notifications提供订阅、模板、持久收件箱、失败/审批提醒及Bot投递证据 | 邮件等新增渠道、特定业务提醒规则 |
| `workflows` | 用户确认不建设通用工作流；保留Agent委派、Run审批与恢复 | 自定义步骤类型和领域模板 |

以上6个占位包没有插件实现，源码已移除，本地可能仍有零文件目录壳。不能据此断言平台服务也未实现，也不应为补齐目录而重复实现一套服务。通知中心已由modules/notifications实现；通用工作流不在当前范围。

调度实现现已归位到 `framework/scheduling.py` 和 `modules/scheduling/`；旧调度兼容层已删除。平台启动不经 `PluginManager` 加载这两个组件，HTTP和代码插件都使用同一持久调度服务；插件可通过 `Depends(get_scheduling)` 获取它。`OpsMeshSettings.plugins` 默认为空。真正使用Loader的可运行示例位于 `examples/custom_plugin/`。

### 平台绑定与插件规则

Bot与Agent的绑定是平台配置，不写进SDK Agent对象或某个聊天插件。当前 `BotRoute` 表达 `(bot_id, channel_id) → agent_revision`：同一机器人可按私聊/群聊绑定不同Agent，一个Agent可被多个机器人使用；每条绑定固定发布版本。管理入口和插件统一调用 `BotAccounts.bind_route/list_routes/get_route/unbind_route`，由平台服务检查绑定权限、Agent可用性和执行权限并持久化。

入站处理顺序为协议验签、外部身份映射与权限检查、查找频道绑定、Matcher派发、默认Agent运行。Matcher可以通过传播控制消费特定命令；没有加载业务插件时，已配置绑定的默认Agent链仍可运行。每次提交Run重新检查主体、Agent和资源权限。解绑只改变后续入站选择，不隐式取消既有Run；Agent发布新版本也不自动修改既有绑定。

当前只支持明确频道绑定，不宣称已经支持机器人级默认Agent、优先级路由规则或按语义自动选Agent。相关功能如后续需要，应扩展平台绑定策略并独立验证。

## 2. 导入、元数据与 require

插件是可导入 Python 模块或包，使用 `load_plugin("package_name")` 或 `load_plugins(...)` 加载。应用读取 `OpsMeshSettings.plugins`，环境变量入口为 `OPSMESH_PLUGINS`。插件包须已安装或在导入路径上，框架不会运行时下载代码。

Loader 创建 `OpsMeshPlugin`，设置当前插件 `ContextVar` 和模块 `__plugin__`，随后执行模块。`on_*` 和能力登记据此记录所属插件。成功后读取、验证 `__plugin_meta__` 并设为 enabled；失败则设为 failed，逆序撤销已追踪注册，移除本次引入的对应模块，最后复位上下文。

插件应由 Loader 首次导入；先普通 import 再 load_plugin 会被拒绝。导入期只做声明，不请求模型、连接网络或修改数据库。需要 I/O 的初始化放在 `get_driver().on_startup`，释放放在 `on_shutdown`。

`OpsMeshPluginMetadata` 字段为 `name/description/usage/type/homepage/config/supported_adapters/extra`。`config` 是本地 Pydantic 模型，可通过 `load_plugin(..., config={...})` 提供配置，用 `get_plugin_config(Config)` 获取。元数据不是跨进程 JSON 合同；`supported_adapters` 当前用于描述，Loader 不据此替代能力校验。

`require("another_plugin")` 返回普通模块并记录依赖关系，循环依赖会报错。公开函数和对象直接由 Python 模块提供，不另设 setup、registrar 或 exports 注册中心。包版本由包管理与部署锁定解决；require 不解析版本，也不是 RPC。

通过 require 得到的普通函数不会自动检查插件禁用状态。需要受管调用边界的能力，应登记到框架或平台注册表，再通过相应接口执行。

## 3. Driver、Adapter、Bot 与定时器

`OpsMeshDriver` 管理启动、关闭、Adapter 和后台任务。`register_adapter(AdapterType)` 在启动前创建适配器并校验 `required_capabilities`；Adapter 用 `connect/disconnect` 管理 Bot。基类 Driver 不隐含网络能力。

平台使用 `OpsMeshNetworkDriver`，提供以下接口：

| 接口 | 当前行为 |
| --- | --- |
| `HTTPClient.request(HTTPRequest)` | 返回 `HTTPResponse`，HTTP 客户端随 Driver 关闭 |
| `WebSocketClient.websocket(url, headers=...)` | 异步上下文管理连接，提供 send/receive/close |
| `setup_http_server(HTTPServerSetup(...))` | 注册固定路径、方法和异步 handler |
| `setup_websocket_server(WebSocketServerSetup(...))` | handler 检查升级请求并显式 accept |
| `create_task(..., inherit_context=False)` | 记录任务错误，关闭时取消并等待清理；默认不继承当前事件上下文 |

HTTP handler 是 `async (HTTPRequest) -> HTTPResponse`；WS handler 是 `async (HTTPRequest, WebSocketServerConnection) -> None`。HTTP lifespan 在应用启动、插件加载后调用 `mount_driver_routes`，检查冲突并冻结路由。仅创建 Driver 不会启动监听端口。

路由为非根的固定绝对路径；`/api`、`/docs`、`/openapi`、`/openapi.json`、`/health`、`/metrics`、`/redoc` 保留给平台。协议签名、账号鉴别和 WS 升级鉴权由 Adapter/handler 负责，宿主 HTTP 应用负责请求体限制。挂载路由不会自动赋予平台用户身份。

失败加载会回滚所属路由；禁用会拒绝新调用，现有 WS 后续读写也检查状态。已经开始的 HTTP handler 不会被强制终止。冻结后的路由更新需要重启。

`opsmesh.framework.scheduling.Scheduler(driver)` 提供 `add_interval_job` 和 `add_date_job`。未指定 callback 时产生 TimerEvent；指定 callback 时使用事件上下文与依赖注入。同一进程同一个 interval job 不重叠执行；普通 Scheduler 不提供跨进程持久化，其 job 注册也不等同于 Loader 的通用事务回滚。需要持久绑定和多实例协调时使用平台 `modules.scheduling` 的服务。

## 4. Event、Message 与无 Bot 事件

`OpsMeshEvent` 提供 `id/type/source/occurred_at/payload`，不保证具有消息、用户或会话。缺少能力时对应访问方法抛出 `ContextUnavailableError`。`MessageEvent` 增加消息、用户和会话；`TimerEvent` 增加作业及计划时间。插件可继承事件类型，通过 `on_event(EventType)` 或类型名称匹配。

`OpsMeshMessage` 是有序 `OpsMeshMessageSegment` 集合，支持纯文本提取、拼接和 JSON 序列化；内置 text/image/file 构造器。图片和文件携带资源引用，具体协议支持、资源访问授权及发送由 Adapter/交付服务处理，不会将不支持的消息段静默当成成功。

`DispatchContext` 包含事件、可选 Bot、回复目标、可选 envelope 和进程内 values。`handle_event` 接收事件或上下文；`get_current_context/get_current_event/get_current_bot` 仅在绑定作用域内有效，无 Bot 时获取 Bot 明确报错。

Botless 事件可以执行规则、依赖和处理器。必需的 Bot 依赖缺失时 Matcher 被跳过；`finish()` 可正常结束，但 `finish("text")` 或 send 需要回复 Bot，否则抛出 `ReplyUnavailableError`。定时任务和后台事件需要主动发送时应显式调用消息交付服务。

`EventEnvelope.from_event(...)` 提供 schema version、事件身份、来源、时间、关联/因果 ID、主体、Session、Run、trace context 和 JSON payload。`decode(EventType)` 验证版本及 payload 身份。Bot、服务实例、闭包、SDK 对象和 ContextVar 不可随 envelope 跨进程，接收端重新绑定依赖。业务模块的持久事件仍各有合同，不能由 envelope 的存在推断所有模块使用同一种队列消息。

## 5. Matcher、Depends 与上下文

当前入口为 `on/on_message/on_command/on_event/on_regex`，实现在 `plugin/on.py`，由 framework 重导出。它们返回 OpsMeshMatcher；`@matcher.handle()` 注册处理器。Rule 和 Permission 支持 `&`、`|`、`~`，条件也通过依赖注入求值。

派发按 priority 数值从小到大执行，同优先级并发。`block=True` 或 stop_propagation 只阻止后续优先级，不撤销已开始的同级处理器。每次调用有独立 state；同一 Matcher 的同一会话串行处理。Matcher 响应权限不能替代 Agent、工具或工作区的资源授权。

finish 结束当前链；pause 从下一处理器等待后续消息；reject 等待后重试当前处理器；got 通过 state 收集参数。交互需要稳定 Session；等待状态当前在进程内并有超时，不是持久会话或 Run checkpoint，不能承诺重启恢复和多 Worker 自动共享。

`Depends(provider)` 接收普通函数，不要求先注册 Provider。支持默认参数与 Annotated 写法、嵌套依赖、注解解析、循环检查和 `use_cache=False`。CommandArg、Arg、ArgPlainText 提供交互参数便捷入口。

一次事件的 DependencyScope 共享结果缓存与资源退出栈，各 Matcher fork 自己的 state。相同 provider 的并发请求等待同一求值任务；需要独立可变资源时使用 use_cache=False。这不是应用级单例，也没有额外 App/Plugin/Run DI 作用域声明系统。

异步 provider 被等待，同步 provider 在线程执行；同步或异步 yield provider 由退出栈释放。事件结束或异常离开作用域时执行清理，取消 asyncio task 无法撤销同步代码的外部副作用。`DispatchContext.values` 是事件内共享对象，并发修改需自行协调。

事件、Matcher 和 Run 上下文分别通过 ContextVar token 复位。`get_run_context()` 返回 AgentExecutionContext，仅在 Agent 执行作用域有效；导入期、普通事件 handler 或裸工具调用不会自动获得它。Run 本地上下文也不等于模型消息历史。

## 6. 可运行示例与能力登记

在仓库根目录执行：

```bash
uv run python examples/custom_plugin/demo.py
```

程序创建临时数据库、加载插件、派发 Botless 的 `/hello Ada`、释放 yield 依赖并调用 Agent 工厂。输出包括：

```text
Hello [example-event], Ada!
Matched handlers: 1
Factory produced: demo-assistant@1 (not published or executed)
```

它不发布 Agent、不调用模型、不发送机器人消息。完整实现见 [插件模块](../../examples/custom_plugin/opsmesh_example_plugin/__init__.py) 和 [demo.py](../../examples/custom_plugin/demo.py)。下列是实际处理器摘录，导入、元数据和 provider 定义均在该模块中：

```python
hello = on_command("hello", priority=10, block=True)


@hello.handle()
async def greet(
    context: DispatchContext[OpsMeshEvent],
    argument: Annotated[OpsMeshMessage, CommandArg()],
    prefix: Annotated[str, Depends(greeting_prefix)],
) -> None:
    name = argument.extract_plain_text().strip() or "world"
    reply = f"{prefix}, {name}!"
    context.values["example_reply"] = reply
    if context.bot is not None:
        await hello.finish(reply)
    await hello.finish()
```

greeting_prefix 继续依赖带 try/finally 的异步 yield provider event_label。同一模块还实际登记了以下声明，函数和配置类定义见完整源码：

```python
get_agent_factories().register(
    name="example.assistant",
    version="1",
    config_schema=AssistantFactoryConfiguration,
    factory=build_assistant,
)
get_code_tools().register("example:uppercase@1", uppercase)
get_runtime_extensions().register_hook("example.observe@1", observe_run)
get_runtime_extensions().register_guardrail("example.input_limit@1", limit_input)
```

工厂校验 Pydantic 配置并返回 AgentSpecification，不自动发布。代码工具也需在能力目录登记 ToolDefinition 并由 Agent 引用，实际调用继续授权。示例工具和 hook 用 get_run_context 获取当前 Run；未绑定时明确失败。

## 7. 平台服务与 Agent 执行扩展

`opsmesh.dependencies` 的普通 provider 可直接调用或放入 Depends；其合同归对应模块 api.py。插件不应导入其他模块 internal。

| provider | 当前用途 |
| --- | --- |
| get_agent_factories / get_agents | 代码工厂注册、异步 build；Agent 定义与版本管理 |
| get_code_tools / get_capabilities / get_tool_invoker | 代码函数登记、持久能力声明、受管理调用 |
| get_runtime_extensions / get_runtime | 执行回调登记；AgentRuntime 与 SubmitRunRequest |
| get_skills / get_script_tools / get_knowledge / get_mcp_catalog | Skill、脚本制品、知识与 MCP 服务 |
| get_accounts / get_sessions / get_models | 本地身份授权、会话与模型配置 |
| get_bots / get_message_delivery | Bot 账号、身份路由与消息交付 |
| get_workspaces / get_memory / get_usage | 工作区、长期记忆、成本 |
| get_configuration / get_audit | 配置与审计 |
| get_run_context | 当前 AgentExecutionContext，非全局服务 |

RuntimeExtensions 支持 register_backend、register_guardrail、register_hook、register_instruction、register_prompt、register_visibility、register_handoff_filter、register_output_extractor，以及结构输出、工具完成、Agent 输入、MCP 审批、shell/patch/computer 扩展。准确签名见 [runtime/api.py](../../src/opsmesh/modules/runtime/api.py)。

`register_backend(name, factory)` 的工厂返回实现 `opsmesh.framework.agents.AgentBackend` 的对象。当前内置 SDK 后端是 OpenAI Agents；未来 Claude 后端需要实际实现协议与能力验证。SDK 专属沙箱扩展通过 get_sandbox_providers 和 `modules/runtime/openai_extensions.py` 显式提供，不混入 Bot Adapter。

代码插件是受信任部署代码，可以计算定义、编写 handler/provider、连接协议及登记回调。HTTP 只引用已部署的 code_ref、扩展和后端并提交可校验声明，不接收任意 Python 导入路径或函数。因此插件扩展范围高于 HTTP 配置。获得服务对象不意味着获得管理员身份；调用底层服务仍须满足其授权前置条件。

`resource:` 是平台资源编译器的保留代码引用前缀。插件使用普通 register；register_runtime/unregister_runtime 用于受控生成闭包与清理，不是客户端绕过冻结的入口。

## 8. 生命周期、禁用与部署边界

平台先组装并绑定 provider；启动时检查数据库版本、加载插件、执行 Driver startup，再冻结工厂、代码工具、执行扩展和知识 provider 声明；HTTP 随后挂载并冻结路由。数据库初始化由单独初始化流程负责。

插件状态为 loading/enabled/disabled/failed。`opsmesh.framework.plugin.manager.disable(name, cascade=False)` 在存在启用依赖方时拒绝禁用；显式 cascade 才递归处理。enable 要求依赖已启用。这是进程内状态，不是自动广播的集群插件开关。

Matcher、事件前后处理器、Driver hook、Adapter 注册、入站路由及平台工厂/代码工具/执行扩展登记，通过所属插件 own(cleanup) 回滚。仅撤销登记过的资源，不撤销任意 Python、文件、数据库或网络副作用。

禁用后新 Matcher 和受管能力调用检查所属插件；工厂、代码工具和执行扩展的缓存包装回调也检查。Driver 保留启动时纳入管理的 shutdown 清理机会。禁用不强杀已运行代码、不自动卸载模块或断开所有自定义 Adapter，外部任务须有自己的生命周期。

失败插件同进程重试被拒绝。冻结后的代码更新、失败重试和可靠升级需要重启；当前不承诺热卸载、运行时安装包或集群代码发布。网关、后台 Worker、隔离 Run Worker 必须安装同版插件与一致配置，分别导入以重建函数；Docker Run Worker 镜像也必须含插件。数据库名称引用不会运输 Python 闭包。

## 9. 验证与固定版本参考

现有 [test_example_plugin.py](../../tests/framework/test_example_plugin.py) 实际加载示例并执行 handler、yield 清理、工厂、工具及 hook 上下文。可运行 `uv run pytest tests/framework/test_example_plugin.py -q`。其他机制的行为证据见 [依赖测试](../../tests/framework/test_dependencies.py)、[事件与 Matcher](../../tests/framework/test_events_matchers.py)、[加载与 Driver](../../tests/framework/test_plugins_driver.py)、[平台登记归属](../../tests/framework/test_plugin_platform_ownership.py)、[网络 Driver](../../tests/framework/test_network_driver.py)、[服务端路由](../../tests/framework/test_server_driver.py)、[HTTP 挂载](../../tests/http/test_driver_ingress.py) 和 [持久调度](../../tests/framework/test_persistent_scheduler.py)。这些测试不调用付费模型或真实外部机器人。

NoneBot2 参考固定为提交 [e52d819fefceadea1fccc773a15f0bc205aff069](https://github.com/nonebot/nonebot2/tree/e52d819fefceadea1fccc773a15f0bc205aff069)：

- [plugin/model.py](https://github.com/nonebot/nonebot2/blob/e52d819fefceadea1fccc773a15f0bc205aff069/nonebot/plugin/model.py) 区分运行时插件与元数据；[manager.py](https://github.com/nonebot/nonebot2/blob/e52d819fefceadea1fccc773a15f0bc205aff069/nonebot/plugin/manager.py) 在执行模块时绑定当前插件。
- [load.py](https://github.com/nonebot/nonebot2/blob/e52d819fefceadea1fccc773a15f0bc205aff069/nonebot/plugin/load.py) 的 require 返回模块；[on.py](https://github.com/nonebot/nonebot2/blob/e52d819fefceadea1fccc773a15f0bc205aff069/nonebot/plugin/on.py) 创建并登记 Matcher。
- [dependencies](https://github.com/nonebot/nonebot2/blob/e52d819fefceadea1fccc773a15f0bc205aff069/nonebot/dependencies/__init__.py) 与 [params](https://github.com/nonebot/nonebot2/blob/e52d819fefceadea1fccc773a15f0bc205aff069/nonebot/internal/params.py) 是函数依赖、缓存和参数机制参考。
- [message.py](https://github.com/nonebot/nonebot2/blob/e52d819fefceadea1fccc773a15f0bc205aff069/nonebot/message.py) 负责分发，[internal/adapter](https://github.com/nonebot/nonebot2/tree/e52d819fefceadea1fccc773a15f0bc205aff069/nonebot/internal/adapter) 放协议对象，[Driver 抽象](https://github.com/nonebot/nonebot2/blob/e52d819fefceadea1fccc773a15f0bc205aff069/nonebot/internal/driver/abstract.py) 区分生命周期和网络能力。

这些是架构参考，不意味着实现了上游全部接口。无 Bot 事件、Agent 上下文、平台业务合同和 Worker 部署边界是 OpsMesh 的具体实现。许可见 [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md)：当前未 vendoring NoneBot2；今后若复制或修改源码须保留原版权与 [MIT 许可](https://github.com/nonebot/nonebot2/blob/e52d819fefceadea1fccc773a15f0bc205aff069/LICENSE)，记录来源版本和修改范围。


## 受保护的平台依赖

管理Provider要求调用者身份，例如 `await get_agents().create(specification, actor_id=principal_id)`。从DispatchContext解析已经验证的principal，不接受消息正文自报的管理员ID。HTTP与插件命令共用管理服务。

新增 `get_triggers()`、`get_notifications()`、`get_outbox_management()`、`get_plugin_management()`。资源Provider返回Skill/MCP/Script管理门面；embedding代码注册使用`get_embedding_registry()`。代码扩展口保留部署级能力，普通管理Provider不公开计量写入、worker执行或通知投递循环。行为及边界见[平台补齐记录](../platform-completion.md)。
