# 插件加载与生命周期

本文记录当前实现及限制。配置和 SQLite 约定见[插件配置与本地存储](plugin-configuration-design.md)。

## 核心职责

PluginMetadata 描述作者声明，Plugin 保存运行归属，PluginManager 加载声明。每个 Driver 实例内部拥有一个 Lifespan，负责该运行时中插件资源的激活、准入、排空和释放；它不是服务进程的安装或代码版本管理器。插件通过 require、Matcher、Dependent 和 on_* 使用框架。

`Lifespan` 实现在 `plugins/activation.py`。Service 构造 Driver 并进入和退出 `driver.lifespan()`；`plugins/lifecycle.py` 在插件加载时将回调登记到所属插件，不访问 Driver。需要访问 Driver 的启动或就绪回调显式声明 `with_driver=True`，由执行器调用时传入实例。不引入 `runtime` 顶层包。

| 对象 | 责任 |
|---|---|
| PluginMetadata | 作者声明名称、说明、配置类型等 |
| Plugin | 模块、依赖、状态、配置、静态及启动期注册归属 |
| PluginManager | 发现和同步加载模块，不处理资源启停 |
| Driver.Lifespan | 当前 Driver 实例的阶段屏障、依赖顺序、资源启停、调用排空和释放 |
| plugins/registry.py | 已加载插件索引、依赖和注册撤销 |
| plugins/invocation.py | 框架调用持有及结束通知；不理解 HTTP、SDK 或业务会话 |
| opsmesh_service.catalog | 本地插件记录、SQLite 配置持久化，不处理安装包 |
| opsmesh_service.host | 服务侧配置读取、数据目录锁、记录发现及持久管理入口 |

通用 Dependent 继续只处理参数和依赖清理。插件状态检查放在 Matcher 分发、插件回调注册和通道请求边界，不能让底层依赖解析器依赖插件管理。

## 插件写法

模块导入只声明元数据、require 依赖、Matcher、路由和回调。配置相关 Agent、MCP、机器人绑定、连接池等在 on_startup 创建，on_shutdown 释放。没有资源的插件不需要额外启动函数或基类。

require 返回原生模块，不包装 Python 调用；依赖在初始化前确定，已 READY 的插件不能临时引入新依赖。直接跨插件调用的安全依赖于显式 require 边和调用方资源生命周期。框架无法拦截任意 Python 引用，也不假装提供同进程安全隔离。

## 加载和运行状态

| 操作 | 行为 |
|---|---|
| load_plugin | 导入声明，不启动网络和后台资源 |
| start_plugin | 检查 enabled、配置及依赖，启动当前已导入模块的资源 |
| stop_plugin | 关闭新调用准入，排空或取消工作，释放资源，保留模块及静态声明 |
| Host.apply_config | 保存值已单独校验后，停止并用新配置重新启动资源 |
| revoke_plugin | 撤销注册；只在加载回滚或资源关闭后使用，不等于安全代码卸载 |

LOADED → STARTING → READY → STOPPING → STOPPED。失败保持 FAILED；依赖失败可使消费者 BLOCKED。cleanup_complete 区分已清理失败和资源尚未释放的失败，后者不能再次启动。

反复启动 READY、停止已清理 STOPPED 均为幂等操作。每次启动增加内存 activation generation，用于拒绝旧队列项；不持久化，不是插件版本机制。

Service 的 `protected` 指定常驻插件，`required` 指定启动失败策略。受保护记录同时启用 `enabled`、`autoload`、`required`；Service 禁止普通管理操作停止、停用或重启受保护插件。框架只检查运行时依赖、在途调用和清理不变量。宿主整体退出与失败启动回滚仍清理常驻资源。

不提供 unload/reload 源码承诺。停用和资源重启不删除 sys.modules；替换已导入源码、第三方依赖或原生扩展需要服务进程重启，或切换到隔离 Worker。下载安装、文件删除、包版本管理和 Worker 版本交接属于 Host/Service 或部署工具，不属于 Driver.Lifespan。

## 启停顺序

宿主冷启动和单插件恢复共用激活步骤。配置在每次激活的 startup 阶段准备和校验一次。宿主在全局 startup、ready、started 屏障中推进所有插件，输入在全局就绪后开放。单插件按依赖顺序推进，on_ready 完成后进入 on_started。输入启动失败立即关闭新调用准入并回滚资源。

单插件停止顺序为：

1. 预检核心保护、活动消费者及自停调用；不默认级联关闭。
2. 设置 STOPPING，使新独立请求和 Hook 不再进入。
3. on_quiescing、on_stopping 通知入口和生产任务退出。
4. 等待在途调用和依赖清理，超时请求取消，再等待 finally。
5. 调用 on_shutdown，撤销启动期注册，标记 STOPPED。

宿主关闭先拒绝新管理操作，等待或取消已受理管理动作完成，再取得最终插件集合。先关闭所有入口并保留已有调用所需的回复连接，再按依赖逆序调用与单插件停止共用的排空、释放和启动期注册清理步骤。管理锁在宿主进入排空前释放。消费者清理失败时保留其提供方，失败记录供重试。

单插件关闭采用有界超时。asyncio 无法强制终止阻塞线程或吞掉取消的插件；最终进程退出仍由部署服务管理。生命周期回调必须可取消并自行释放创建的后台资源。

资源启动失败时先关闭新调用准入，再执行清理；包括已经进入 READY、但 on_started 尚未成功的情况。Matcher 的依赖清理与准入持有分别结束，只有清理完整返回才释放占用。排空通过调用任务的取消作用域请求取消，使清理能在屏蔽区内完成；重复排空不反复以 Task.cancel 打断清理。外部代码再次直接取消任务仍可能中断 finalizer，此时保留占用并让停止报告失败，不把已终止的任务等同于已释放的资源；无法完成的清理由宿主进程退出处理。

管理操作由 Driver 的运行时任务组持有并串行执行，Host 只负责把服务侧请求协调到这个运行时。管理请求取消不会直接留下半次启停；禁止生命周期回调嵌套等待管理，也禁止处理器等待停止自身或自己的依赖。

## 调用与注册归属

| 内容 | 生命周期 |
|---|---|
| 导入期 Matcher、路由、Hook 和生命周期声明 | 保留到撤销声明；停止时关闭调用准入 |
| 启动期创建的绑定与动态注册 | 本次 start 到 stop，再次启动重新创建 |
| 框架事件调用及 yield 依赖 | 持有到 AsyncExitStack 关闭 |
| API 流式响应 | 持有到流结束或取消清理，不能在 handler 返回时释放 |
| 插件自己启动的任务、线程和连接 | 插件通过停止/关闭回调结束，框架不自动发现 |

在途调用同时持有声明的提供方，停止后仍可完成已持有的依赖；新的独立调用会被拒绝。Matcher 检查拥有独立 stack/cache，未命中立即释放，命中则延续到处理和响应结束。框架与 SDK 回调在声明插件的 plugin_scope 中运行并清理依赖。

后台执行通过 plugin_call 取得独立占用；同一已受理任务可嵌套续借。create_task 继承 ContextVar 不代表继承占用，父作用域结束后不能继续使用过期资源。

API 已准入请求通过明确的调用记录将准入传给 Matcher；框架只允许来源仍有效且目标属于原依赖闭包的续接，取得独立记录并追踪实际任务。已经取得的记录保持到清理完毕，即使父调用已结束。排空检查持续读取当前调用记录，不能遗漏停止期间建立的续接。

取消作用域只关联当前正在执行或清理的调用任务，不扩大续接权限。显式续接仍要求有效来源和已准入的依赖子集；复制 ContextVar 或新建任务不会自动获得续接资格。框架发起的取消在清理后继续向调用者传播，不转换成正常返回。

execution 复用 plugin_call。原生 Agent.as_tool/handoff 等由 SDK 在外层执行范围内完成；独立任务再次调用 execution.run/stream 必须重新通过准入，不能根据继承的 ContextVar 自动授权。

流清理预算耗尽后，仍未结束的 SDK 任务继续持有调用资源。停止报告排空失败并保留提供方，不会提前关闭它们使用的 MCP、沙箱或模型客户端。强制终止不响应取消的工作由部署服务的进程退出期限处理。

## 通道责任

channels 自己管理 API HTTP 监听、QQ Gateway、钉钉连接和有界队列。框架不认识协议类型、Token、消息签名、模型、Agent 或 Session。

消费者启动时按自己的依赖配置声明具名连接。Driver 缓存协议适配器，适配器按消费者和连接名保存已准备的连接，Driver 的在线索引只登记已上线的连接。停止一个消费者会排空并释放它拥有的连接，其他消费者的连接继续服务。

机器人队列并发、QQ Gateway 参数和 HTTP 监听配置均属于具体连接。向允许重启的消费者应用依赖配置时，会重新构造该消费者的连接。channels 模块和适配器实例随宿主运行。内置保护与扩展在线管理的拟议改造见[扩展插件在线管理方案与内置 channels 架构审查](plans/plugin-online-lifecycle.md)。

停止输入后，队列先排空已接收的工作，超时则请求取消工作任务并释放剩余队列项。队列项持有接收时的调用准入和运行代数，跨代的事件或已失去回复资源的事件会在分发前丢弃。队列保存在内存中。

API 路由随所属插件 READY 状态开放，关闭期间返回不可用。流和附件等资源由通道现有响应清理机制管理。管理页面、控制 API 和业务会话操作都不是框架的新职责。

## 源码位置

```text
src/opsmesh/
├── drivers/
│   ├── base.py             # 具体 Driver、适配器和连接索引
│   └── lifecycle.py        # core 生命周期引擎与插件生命周期声明
├── plugins/
│   ├── model.py
│   ├── manager.py
│   ├── load.py
│   ├── config.py
│   ├── invocation.py
│   └── registry.py
├── dispatch/
└── dependencies/           # 保持通用参数与清理职责
packages/service/src/opsmesh_service/
├── host.py                 # 宿主装配与管理
└── catalog.py              # 插件目录及 SQLite
packages/channels/src/
├── lifecycle.py            # 按消费插件注册框架生命周期回调
├── adapter.py              # 已准备连接与操作准入
├── api/                    # HTTP 请求、响应和流
├── qq/                     # QQ 连接和队列处理
└── dingtalk/               # Stream 连接和队列处理
```

验证只运行受影响的宿主、生命周期、分发、通道、示例及发布契约测试，不触发全量业务或真实模型测试。
