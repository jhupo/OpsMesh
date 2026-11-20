# OpsMesh 框架架构

核对日期：2026-11-20。状态：现行架构文档。范围为 `src/opsmesh`；通过 GitNexus 定位关系，再逐条核对源码，本文不代替运行验证。网关登记、输入、分发、回复与注销的完整流程见[网关文档](gateway.md)，HTTP 能力见 [OpenAPI 文档](openapi.md)。

框架基线是 NoneBot2 v2.5.0，上游提交 `c0f9a494bcd308c2ec530ba60c167f9145d21aec`。本地保留其 Driver、导入式插件、Matcher 和依赖注入结构。**Bot → Gateway 的框架命名改造已完成**，包含来源标识、查询接口、注入上下文及连接/API 钩子名称；这是当前接口，不是待实施方案。通用 HTTP 业务适配器和任意结构化回复不在这项完成范围内。

框架把来源差异留给适配器，把业务执行统一在 Event → Matcher → 处理函数这条链上。插件通过 `on_*` 声明响应条件，通过 Depends 获取输入与依赖，通过当前 Gateway 回复。

## 1. 当前文件树与职责

以下为当前源码结构，省略无行为的包标记和类型标记文件。

```text
src/opsmesh/
├── __init__.py                  # init/run、全局 Driver 查询、公开接口导出
├── config.py                    # Env/Config、环境与初始化参数合并
├── message.py                   # 事件分发、前后处理器、Matcher 检查与执行
├── matcher.py                   # Matcher 公开导出
├── rule.py                      # 常用规则、命令前缀 Trie
├── permission.py                # 常用权限
├── params.py                    # Depends 及业务参数辅助入口
├── exception.py                 # 分发、交互、适配器异常
├── consts.py / typing.py         # 状态键与类型声明
├── compat.py / utils.py / log.py # 依赖版本适配、通用辅助、日志
├── adapters/__init__.py         # Adapter/Gateway/Event/Message 等公开导出
├── drivers/
│   ├── __init__.py              # Driver 与传输模型公开导出
│   ├── fastapi.py / quart.py    # HTTP/WebSocket 服务端驱动
│   ├── aiohttp.py / httpx.py     # HTTP 客户端等传输能力
│   └── none.py / websockets.py  # 无网络服务端的驱动、WebSocket 客户端
├── plugin/                     # 框架插件机制（单数）
│   ├── __init__.py              # 内存插件表、插件查询、配置读取
│   ├── model.py                 # Plugin 与 PluginMetadata
│   ├── config.py                # 插件与使用方配置校验、启动前适配器装配
│   ├── manager.py               # 搜索、导入管理、Finder/Loader、启停和卸载
│   ├── load.py                  # load_*、require
│   ├── unload.py                # 解析插件参数，委托所属管理器卸载
│   └── on.py / on.pyi           # on_* 注册与分组工具、类型声明
├── plugins/                    # 内置业务插件（复数）
│   ├── echo.py
│   └── single_session.py
├── dependencies/
│   ├── __init__.py              # Dependent：解析、求值与调用
│   └── utils.py                 # 依赖图相关辅助
└── internal/
    ├── adapter/
    │   ├── adapter.py           # 适配器基类、Gateway 管理、传输委托
    │   ├── gateway.py           # 来源实例、send、call_api 与调用钩子
    │   ├── event.py             # Event 抽象
    │   ├── message.py           # Message/MessageSegment 抽象
    │   └── template.py          # 消息模板
    ├── driver/
    │   ├── abstract.py          # Driver、传输 Mixin、适配器/网关登记
    │   ├── _lifespan.py         # 驱动启停回调和任务组
    │   ├── combine.py           # 组合 Driver 与传输能力
    │   └── model.py             # Request/Response/WebSocket/路由描述
    ├── matcher/
    │   ├── matcher.py           # Matcher 定义、处理器、交互与回复
    │   ├── manager.py           # 按优先级组织 Matcher
    │   └── provider.py          # Matcher 存储提供者
    └── rule.py / permission.py / params.py
                                # 规则组合、权限组合与依赖参数求值
```

对外导出层与 `internal/` 是已有的结构分工，不需要为“统一网关”另建事件运行层。`plugin/` 管加载与注册机制，`plugins/` 放内置插件。

## 2. 启动与传输装配

源码：[初始化入口](../src/opsmesh/__init__.py)、[Driver](../src/opsmesh/internal/driver/abstract.py)、[Lifespan](../src/opsmesh/internal/driver/_lifespan.py)。

```text
启动程序
  → opsmesh.init(**config)
      → Env / Config
      → 解析 Driver 组合表达式
      → 创建全局 Driver（已有时不会重建）
  → load_plugin / load_from_toml 等 → 启用本次运行的插件
  → opsmesh.run(...)
      → 校验插件文件、依赖连接和环境凭据
      → 按协议名注册适配器（不建立连接）
      → Driver.run(...)
      → 所选驱动启动生命周期、传输服务
      → 启动回调和后台任务
      → 关闭回调、任务组结束
```

`init` 负责配置和装配，不会因为选择 HTTP Driver 就自动生成业务 API。`run` 先完成声明式装配，再把运行参数交给所选 Driver。普通适配器仍可由宿主调用 `driver.register_adapter`，但不能和声明式适配器重复注册同一协议。Driver 提供整个实例的启停回调和任务组。

以 FastAPI 为例，`_lifespan_manager` 驱动 startup/shutdown，`run` 委托 Uvicorn。适配器可以注册 HTTP/WebSocket 回调、使用客户端能力、维护 Gateway；具体支持哪些传输取决于所选 Driver/Mixin。框架不会因新增一个消息来源而必须新增一套 `init`。

## 3. 插件导入、注册与 require

源码：[load.py](../src/opsmesh/plugin/load.py)、[manager.py](../src/opsmesh/plugin/manager.py)、[内存登记](../src/opsmesh/plugin/__init__.py)、[Plugin 模型](../src/opsmesh/plugin/model.py)。

```text
load_plugin(module_path)
  → PluginManager：声明模块 / 目录、检查名称冲突
  → importlib.import_module(...)
      → PluginFinder / PluginLoader
      → _new_plugin、module.__plugin__、设置 _current_plugin
      → 执行插件模块
          → on_* 创建并登记 Matcher
          → require(...) 确保依赖插件已加载，返回真实模块
      → 重置导入上下文，读取 __plugin_meta__
  → 返回 Plugin；加载失败按实现记录错误并返回 None
```

`PluginMetadata` 描述名称、介绍、用法、自身配置模型、配置文件、依赖配置模型、适配器声明及支持适配器等。`Plugin` 记录模块、管理器、导入期间登记的 Matcher、父子插件及元数据。父子插件关系不是完整的跨插件依赖图。

`require` 查询已加载插件，必要时借助对应管理器加载，失败抛出 `RuntimeError`，成功返回 `plugin.module`。普通函数调用仍是进程内 Python 调用，不经过 socket、代理或 RPC。应在直接导入未声明的外部插件前先使用 `require`，遵循[上游跨插件访问约定](../.agents/skills/opsmesh-nonebot2/references/website/docs/advanced/requiring.md)。

导入失败时 `_revert_plugin` 只撤回插件表及父子关系等登记，**不是导入副作用事务**。`Plugin.enabled` 默认 `False`；`PluginManager.enable_plugin()` / `disable_plugin()` 控制所属 Matcher 是否接收新事件。顶层 `unload_plugin()` 委托所属管理器；已停用且通过保护、子插件和模块归属检查后，管理器移除所属 Matcher、插件登记和对应模块缓存，不删除制品。`PluginMetadata.protected=True` 禁止由管理器停用或卸载。框架不会自动结束已有执行、释放外部资源或清空其他插件通过 `require()` 保留的引用。

## 4. 事件注册与实际执行顺序

源码：[on.py](../src/opsmesh/plugin/on.py)、[message.py](../src/opsmesh/message.py)、[Matcher](../src/opsmesh/internal/matcher/matcher.py)、[Rule](../src/opsmesh/rule.py)。

注册阶段：`on_message/on_request/on_notice/on_metaevent` 选择事件类别；`on_command` 在消息处理上组合命令规则；`on_type` 组合 Python 类型规则。它们最终通过 `on → Matcher.new` 创建 Matcher 类并按优先级登记。`MatcherSource` 记录插件和模块来源，导入期间 `store_matcher` 还会写入 `Plugin.matcher`。

执行阶段：

```text
适配器构造 Gateway + Event 后主动调用 handle_event
  → 创建 state、dependency_cache、AsyncExitStack
  → event_preprocessors
  → TrieRule.get_value（消息命令前缀解析）
  → 共享 Gateway 按现有规则分发；专属 Gateway 仅匹配归属插件
  → 按优先级从小到大处理 Matcher
      → 同优先级在任务组中并发检查/执行
      → _check_matcher：过期检查、权限与规则（两者均先检查类别）
      → _run_matcher：临时 Matcher 移除、创建本次实例
      → run_preprocessors
      → Matcher.run → simple_run → 依赖求值 → handlers
      → run_postprocessors
      → block / StopPropagation 影响后续优先级
  → event_postprocessors
  → 退出 AsyncExitStack，释放依赖资源
  → 返回 None
```

这是正常执行的骨架，早退、规则不满足、异常和取消会改变路径。`run_preprocessor` 位于权限/规则检查之后，不能直接作为“停止插件后任何插件代码都不再执行”的闸门；postprocessor 也不能视作所有失败路径都保证执行的 `finally`。同优先级并发不能靠 `block` 保证一个 HTTP 请求只有一个处理器回复。

分发使用 `run_coro_with_shield`。因此未来 HTTP 超时和插件停止必须核对取消如何穿过保护层及资源释放，不应仅在最外层套超时就宣称执行已经结束。以上均为现有路径分析，不表示已完成热生命周期设计。

## 5. 依赖、会话与回复

源码：[Dependent](../src/opsmesh/dependencies/__init__.py)、[参数求值](../src/opsmesh/internal/params.py)、[Matcher](../src/opsmesh/internal/matcher/matcher.py)、[Gateway](../src/opsmesh/internal/adapter/gateway.py)。

依赖注入解析处理函数和依赖参数，将 Gateway、Event、state 等上下文交给函数；依赖缓存按单次事件分发共享。生成器依赖的上下文进入当前事件的 `AsyncExitStack`，在其退出时释放。这个请求级资源作用域不等于插件的整个存活周期。

`pause/reject` 通过控制流异常创建后续临时 Matcher，保存处理进度、状态、来源和到期时间。一次 handler 调用结束后仍可能存在等待后续事件的会话，所以检查“活动协程已经结束”不足以证明插件可以卸载。`Matcher.destroy` 只从注册表移除该类，不会自动终止已运行实例或释放插件资源。

回复链是 `Matcher.send → 当前 Gateway.send(event, message)`；`Matcher.finish` 可先发送，再抛出 `FinishedException` 结束当前 Matcher。普通 handler 的 `return` 不会被 `handle_event` 聚合成响应对象。`Gateway.call_api` 调用适配器 `_call_api` 并经过调用前后钩子，是协议能力入口，不是通用插件函数调用框架。

当前 `Event` 仍要求实现用户、会话、消息、是否指向网关等方法；当前 `Matcher.send` 的契约仍以字符串、Message、MessageSegment、MessageTemplate 为主。Gateway 改名没有自动使任意请求数据、JSON 回复和无聊天信息事件都获得完整统一语义；这些是独立的待实现事项，HTTP 接入所需边界见 [OpenAPI 文档](openapi.md#尚待落实的接入契约)。

## 6. 插件配置与声明式连接

源码：[config.py](../src/opsmesh/config.py)、[`get_plugin_config`](../src/opsmesh/plugin/__init__.py)、[FastAPI Driver](../src/opsmesh/drivers/fastapi.py)。

未声明 `config_file` 的插件仍通过 `get_plugin_config(Model)` 读取框架全局配置：配置来自初始化参数、环境变量、dotenv 和默认值，显式初始化参数优先。每次调用得到新模型。

业务插件声明 `PluginMetadata(name="Report", description="…", usage="…", config=ReportConfig, config_file="settings.json")` 后，框架会在 `opsmesh.run()` 时从启用插件的入口目录读取 UTF-8 JSON。文件须位于插件目录内。例如：

~~~json
{
  "config": {"model": "your-model-name"},
  "dependencies": {
    "dingtalk": {
      "client_id": "configured-client-id",
      "client_secret_ref": "DINGTALK_CLIENT_SECRET"
    }
  }
}
~~~

- `config` 只交给本插件声明的模型，不与全局环境配置合并；省略时按空对象及模型默认值校验。在插件自己的模块或子模块调用 `get_plugin_config(ReportConfig)` 时，会重新读该插件的 JSON 并校验，返回新的模型。即使不同插件使用同一个模型类也不会串用。缺失或无效文件会报 `ValueError`，传入非声明模型会报 `TypeError`。
- `dependencies.<provider-id>` 直接是当前使用方为提供方填写的对象，不再套 `main` 或连接名。提供方须已安装、加载、启用，并声明自己的 `dependency_config` 模型，不要求它是适配器。框架在 Driver 启动前用这个模型校验每对「提供方、使用方」的配置；JSON 不安装或启用包，也不替代 `require()` 或 wheel 的 `Requires-Dist`。
- 已启用的提供方可以通过 `from opsmesh.plugin import get_plugin_dependency_configs`，调用 `get_plugin_dependency_configs(Options)` 重新读取已启用插件的配置文件，校验其中指向自己的条目，返回 `{使用方插件 ID: Options}`。没有声明方时返回空字典。调用者不是已启用的插件时报 `RuntimeError`，传错模型报 `TypeError`，读文件或校验失败报 `ValueError`。这是同步文件读取，不提供超时或取消；建议提供方在启动时读取并自行决定如何持有和释放资源，不在每条消息上反复扫描。模块归属用来定位配置，不是同进程沙箱。
- 提供方另外声明 `adapter` 时，同步类方法 `prepare_settings(driver, config, bindings) -> dict` 将自身配置（没有模型时为 `None`）与按使用方插件 ID 索引的配置副本转换为构造参数，不建立连接。每种协议注册一个 Adapter。QQ、钉钉当前每个使用方可指定一个账号，可按 `adapter.connections[plugin_id]` 查找对应专属 Gateway；宿主全局账号仍是共享 Gateway。事件范围见[网关流程](gateway.md#4-输入匹配与回复)。
- 无效文件、未启用的提供方、账号冲突或缺失凭据会在 Driver 启动前报错；依赖配置错误以 `<consumer>.dependencies.<provider>` 定位。启动装配只使用临时数据，不把配置存进 `Plugin` 或框架缓存。改动文件后，只有下一次显式读取才会取得新值；已创建的资源不会自动重建，管理服务中更新适配器账号需重启。旧命名连接格式不兼容；远端鉴权仍在真实连接时完成。

源码：[启动校验](../src/opsmesh/plugin/config.py)、[QQ 装配](../packages/qq/src/adapter.py)、[钉钉装配](../packages/dingtalk/src/adapter.py)。

### HTTP 文档

HTTP Driver 已有 `HTTPServerSetup` 和 `Request → Response` 回调。FastAPI 文档开关已有，但通用回调没有业务 body/response 模型，不能从业务 Matcher 自动得到完整 schema。传输、业务事件和 OpenAPI 的具体边界见 [HTTP 与 OpenAPI](openapi.md)。

## 7. 从现有结构归纳的设计思想

这些结论是依据当前实现的架构归纳，不是宣称上游支持全部 OpsMesh 目标：

| 思想 | 源码体现 | 后续设计的约束 |
| --- | --- | --- |
| 协议与业务分离 | Driver 传输，Adapter 转换，Matcher 执行业务 | 业务插件不注册协议路由，不因来源增加重复处理器体系 |
| 通过导入和声明注册 | Import Hooks、`on_*`、PluginMetadata、`require` | 优先复用加载器和元数据，不新增强制 PluginSpec |
| 注册表留在内存 | 插件表、MatcherManager、Gateway 表 | 记录限于当前进程 |
| 用上下文管理一次执行 | ContextVar、Depends、AsyncExitStack | 不用全局变量保存请求回复状态；仍需区分插件级资源 |
| 直接组合普通 Python 能力 | `require` 返回模块，规则/权限/依赖可组合 | 不为单进程跨插件访问增加 RPC 或代理层 |
| 明确扩展边界 | Driver/Mixin、Adapter/Gateway、公开导出 | 在实际缺口处增补，避免重建分发、配置或依赖注入 |

框架已有插件状态闸门和基础卸载，没有通用的资源排空与清理协议。

## 8. GitNexus 调查依据与限制

2026-11-20 的配置改动以本地源码和测试为依据；GitNexus 现有索引已过期，未将其作为本次证据。以下保留网关改造时的取证记录。

本次仓库为 `OpsMesh`，GitNexus CLI 版本 1.6.12。旧索引虽然提交号相同，但工作区内容已经变化，不能用于恢复后的框架。使用 `analyze --index-only` 完成刷新，未自动生成 AGENTS 或技能。

- 索引完成时间：2026-11-19 13:29:01（Asia/Shanghai；UTC 05:29:01）。
- Git HEAD：`a093d153710730c1bceebaf7044254b773e62866`；索引包含当时未提交的源码，不能仅凭 HEAD 复现工作区。
- 索引规模：368 个文件、6,122 个节点、13,144 条边、372 条流程；本次未启用 PDG。数量仅记录此次快照，不是功能覆盖率。
- 下表查询在刷新后进行；本文及导航文档随后写入。后续代码有变动必须重新检查新鲜度，不能把这里的时间当成永久有效证明。

| 查询对象 | 图谱结果与源码核对结论 |
| --- | --- |
| `init`，`src/opsmesh/__init__.py` | 定位 Driver 组合解析；源码确认仅在全局实例不存在时创建 |
| `handle_event`，`src/opsmesh/message.py` | 找到前后处理器、TrieRule、`check_and_run_matcher` 调用；trace 确认后者的一跳关系 |
| `require`，`src/opsmesh/plugin/load.py` | 找到插件查询和加载分支；源码确认返回真实模块，没有完整依赖状态图 |
| `on_command`，`src/opsmesh/plugin/on.py` | 与同文件的分组方法同名，按 UID 消歧；源码确认复用 `on_message` 与命令规则，图谱的同名 `command` 边不能直接当作准确调用目标 |
| `PluginLoader.exec_module`，`src/opsmesh/plugin/manager.py` | 找到 `_new_plugin/_revert_plugin`；图谱提示接收者类型不明导致部分调用点缺失，源码补查导入上下文 |
| `Matcher.send`，`src/opsmesh/internal/matcher/matcher.py` | 能定位定义但部分调用边缺失；源码确认通过 ContextVar 取得 Gateway/Event 后调用 `gateway.send` |
| `Driver._handle_http`，`src/opsmesh/drivers/fastapi.py` | 找到 `_handle` 和 `HTTPServerSetup.handle_func` 的连接；源码确认这里处理传输响应而非业务事件 |

可复现的基本查询命令见 [AGENTS.md](../AGENTS.md#使用-gitnexus-梳理代码)。同名符号应附加文件路径；同文件还有同名成员时按返回的 UID 消歧。

此处记录的是历史索引快照，可能命中待迁移通道，不能把搜索排序当作架构依据。分析器也报告部分候选调用和流程超过预算而省略；动态导入、ContextVar、回调与任务调度不能仅靠静态边还原。图谱中的空 incoming/outgoing 不代表没有运行时调用。本次未运行框架测试，结论是源码与静态关系核对，而非整体可运行性或热升级安全性的证明。
