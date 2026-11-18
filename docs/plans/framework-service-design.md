# OpsMesh 框架库与插件管理服务设计

**目标**：单独安装 `opsmesh` 就能开发插件或嵌入自己的应用。安装 `opsmesh-service` 后，才获得官方服务进程、插件目录和在线管理命令。QQ、钉钉、HTTP API 等协议继续由业务插件实现。普通扩展的停止与恢复必须在服务进程存活时完成。

本文同时记录已落地的源码拆包和后续目标。当前已迁移服务入口、宿主、控制、SQLite 目录及构建；显式插件装配、操作日志和 Worker 交接仍是后续设计。通道逐项审查、配置回退和版本交接的详细故障用例见[在线生命周期方案](plugin-online-lifecycle.md)。

| 阅读入口 | 可以据此完成的工作 |
|---|---|
| 第 1、2 节 | 决定包依赖，迁移源码、入口、构建和部署文件 |
| 第 3、4 节 | 实现配置读取、运行时装配、管理接口、状态转换和目录事务 |
| 第 5、6 节 | 实现隔离扩展的代码交接，明确通道与业务扩展的资源归属 |
| 第 7 节 | 分批发布，用真实服务、两版扩展以及故障注入验收 |

## 1. 谁启动谁

`opsmesh-service` 是独立发行的服务应用。操作系统启动它，它创建 Driver 并装载插件。服务自身不登记到它管理的插件目录，这样即使全部普通扩展都已停止，管理员仍然可以查询和恢复它们。其他应用可自己创建和绑定 Driver、指定插件、进入和退出生命周期，整个过程只依赖框架库。

```text
systemd / 容器 / 命令行 ──启动──> opsmesh-service
管理 CLI ──本机请求──> 已运行的 opsmesh-service ──调用──> opsmesh 的运行时 API
自建 Python 应用 ────────────────────────────────────> 同一套运行时 API
业务扩展 ──依赖──> opsmesh；按需声明依赖 channels、database 等提供方
```

| 归属 | 唯一职责 | 可独立验证的边界 |
|---|---|---|
| 框架库 `opsmesh` | Event、Message、Matcher、分发、插件声明与依赖、Driver 运行时资源归属、准入和释放 | 只安装 core wheel，无服务命令。导入框架时不启动任务、不扫描部署目录、不创建文件或监听端口；不负责安装包、代码版本切换或服务进程寿命 |
| 服务包 `opsmesh-service`，Python 包 `opsmesh.service` | 进程装配、包发现、部署策略、目录存储、本机管理、信号处理、运维结果和 Worker 交接 | 只依赖 core 即可启动空服务。管理流程不会导入某个具体业务包来判断其身份 |
| 插件发行包 | 用框架 API 声明事件处理、配置、资源和清理钩子；直接声明实际使用的其他包 | 插件开发、导入和单测无需安装服务包。插件也无需知道服务通过 SQLite 或套接字管理它 |

NoneBot 的 NB-CLI 把项目创建和启动放在框架外，用模板生成项目并调用目标 Python 环境。OpsMesh 可以借鉴入口归属；本项目另外要求运行中的进程能交接插件资源，所以运行时管理入口放在常驻服务。首期无需引入脚手架、模板引擎或另一套 CLI 插件系统。[NB-CLI 项目创建](https://cli.nonebot.dev/docs/guide/create-a-project/) · [项目启动实现](https://github.com/nonebot/nb-cli/blob/master/nb_cli/handlers/project.py)

## 2. 从当前文件迁到哪里

### 拆包后的目标文件树

下图展开本次拆包涉及的源码和部署入口，省略与迁移无关的文件。服务包首期有五个实现模块，命令直接指向 `opsmesh.service.cli:main`。

```text
OpsMesh/
├── pyproject.toml                       # 发行框架库 opsmesh
├── src/opsmesh/
│   ├── __init__.py                      # 供插件和嵌入方导入的公共 API
│   ├── config.py                        # Driver 接收的框架参数
│   ├── drivers/
│   │   └── base.py                      # 框架配置、任务组与生命周期入口
│   ├── plugins/
│   │   ├── activation.py                # core 的激活、排空与释放引擎
│   │   ├── model.py                     # Plugin、PluginMetadata、PluginStatus
│   │   ├── config.py                    # PluginConfiguration、配置读取与校验
│   │   ├── lifecycle.py                 # 生命周期回调声明和加载时绑定
│   │   ├── load.py                      # load_plugin、require
│   │   ├── manager.py                   # 模块导入和声明加载
│   │   ├── registry.py                  # 插件及其注册项的归属与撤销
│   │   ├── admission.py                 # 加载准入接口
│   │   ├── invocation.py                # 在途调用和资源持有
│   │   ├── hooks.py                     # 插件加载钩子
│   │   ├── on.py                        # Matcher 声明接口
│   │   └── errors.py                    # 通用插件错误
│   ├── adapters/                        # Adapter、Channel、Event 抽象
│   ├── messages/
│   ├── dispatch/
│   ├── matchers/
│   ├── dependencies/
│   ├── permissions/
│   └── rules/
├── packages/
│   ├── service/
│   │   ├── pyproject.toml               # 发行 opsmesh-service，注册 opsmesh 命令
│   │   └── src/
│   │       ├── __init__.py
│   │       ├── cli.py                   # 命令、参数、输出和退出码
│   │       ├── service.py               # 读取 host.json、目录锁、信号、进程寿命
│   │       ├── host.py                  # 装配 Driver、发现插件、执行管理操作
│   │       ├── catalog.py               # PluginRecord、SQLite、目录迁移
│   │       └── control.py               # 本机管理请求与响应
│   ├── channels/
│   │   ├── pyproject.toml               # channels 入口和内置标记
│   │   └── src/                         # 导入名 opsmesh.plugins.channels
│   │       ├── __init__.py              # 声明本插件的 PluginMetadata
│   │       ├── config.py                # 通道配置模型
│   │       ├── adapter.py               # 连接声明与归属
│   │       ├── channel.py               # 机器人连接共用逻辑
│   │       ├── lifecycle.py             # 通道资源的创建和释放
│   │       ├── events.py                # 通道共用事件
│   │       ├── callbacks/               # 回调接收、入队与分发
│   │       ├── api/                     # HTTP 请求、事件、响应流
│   │       ├── qq/                      # QQ 网关、事件转换、回复
│   │       └── dingtalk/                # 钉钉 Stream、事件转换、卡片
│   ├── cron/
│   ├── database/
│   ├── execution/
│   ├── filesystem/
│   └── knowledge/                       # 各发行包均有自己的 pyproject.toml
├── examples/groups/group_assistant/
│   └── settings.json                    # 示例扩展随包提供的初始业务配置
├── packaging/
│   ├── entrypoint.py                    # 冻结程序调用服务包 CLI
│   └── opsmesh.spec
├── deploy/
│   ├── host.example.json                # 环境、框架参数、数据目录示例
│   ├── systemd/opsmesh.service
│   └── docker/
└── scripts/
    ├── build_packages.py                # 分别校验框架、服务和插件 wheel
    └── build_release.py                  # 构建 Linux 服务发行物
```

### Worker 阶段新增位置

第 4 批实现代码热更新时，再在服务包内增加以下两个模块。宿主侧负责选择版本和转发请求，子进程入口只负责运行一个版本的扩展。

```text
packages/service/src/
├── workers.py                           # 服务侧创建和回收进程、路由切换与排空
└── worker.py                            # 子进程入口，加载指定版本并处理 IPC 调用
```

### 部署后的文件树

以下按仓库里的 Linux 部署示例展开。插件运行意图与配置修订写入服务状态库；扩展随包的配置文件仅提供初始值。

```text
/etc/opsmesh/
└── host.json                            # 服务读取的宿主配置
/var/lib/opsmesh/                        # host.json 指定的 data_dir
├── host.lock                            # 独占当前数据目录
├── state.sqlite3                        # 插件登记、部署意图和持久配置
└── control/
    └── host.sock                        # CLI 连接正在运行的服务
```

### 现有文件迁移对照

服务包已承载实际实现，`opsmesh` 命令直接指向 `opsmesh.service.cli:main`。
旧的 core 入口和存储导入路径已删除；原有 `state.sqlite3` 的位置及 v3 格式不变。
`Driver` 保留在 core 的 `drivers/base.py`；插件激活机制在 `plugins/activation.py`，钩子声明在 `plugins/lifecycle.py`。没有全局 Driver 或 `runtime` 包。

| 拆包前路径 | 已迁移位置与后续改法 |
|---|---|
| 根 `pyproject.toml`、`src/opsmesh/main.py` | 根工程只发行框架库。移除 core 的 console script 和转发入口。新增 `packages/service/pyproject.toml`，直接声明 `opsmesh = "opsmesh.service.cli:main"`。沿用已部署的可执行文件名称 |
| `bootstrap/cli.py`、`bootstrap/control.py` | 移到 `packages/service/src/{cli,control}.py`。CLI 解析参数、输出结果和退出码。control 只做本机传输、请求解码、响应编码。两个入口都把操作交给同一个 Host 方法 |
| `bootstrap/service.py`、`bootstrap/host.py` | 服务包内 `service.py` 管进程启动、数据目录独占、控制端点、信号和退出顺序；`host.py` 装配插件并执行业务无关的管理决策。Host 调 Driver 和目录存储，不再自己维护第二套生命周期。先沿用这两个模块，无需再包一层 bootstrap 或 main |
| `plugins/storage.py` | 把 `PluginConfiguration`（插件自身值和消费者提供的依赖配置）移入 core 的 `plugins/config.py`；把 `PluginRecord`、`PluginStorage`、SQLite schema 和数据迁移移入服务包 `catalog.py`。保留首期的数据库路径和数据格式 |
| `plugins/model.py`、`registry.py`、`manager.py` | Manager 的来源索引负责定位和导入；Registry 只记录已加载的插件、声明归属、依赖及撤销。Manager 不提供资源启停入口 |
| `plugins/load.py`、`plugins/manager.py` | `plugin_environment()` 显式安装 `PluginFinder`，临时持有来源和准入规则，退出时撤销本次登记。Service 和嵌入方共用该入口 |
| `drivers/base.py`、`plugins/activation.py` | Driver 持有框架配置与插件生命周期执行器，不管理协议适配器或连接。执行器维护插件阶段及资源清理。Service 构造 Driver、进入和退出其生命周期，并负责信号、包版本、持久配置及保护策略 |
| `packaging/entrypoint.py`、`opsmesh.spec`、`scripts/build_*.py`、部署文件与迁移工具 | 冻结程序直接调用服务包入口。构建区分 core、service、业务插件三类 wheel；服务 wheel 不参与业务插件枚举。发布清单记录服务和框架版本。systemd、Docker、安装脚本、离线迁移工具和测试一起切换 |

业务插件使用 `opsmesh.plugins.*`，独立发行的服务包映射到 `opsmesh.service`，不在框架 wheel 内。[当前宿主装配](../../packages/service/src/host.py) · [插件导入器](../../src/opsmesh/plugins/manager.py) · [服务目录](../../packages/service/src/catalog.py) · [wheel 构建校验](../../scripts/build_packages.py)

### 框架与服务之间的 Python 接口

这是 Service 和嵌入方使用的调用约定。构造 Driver 不会发布全局实例，声明导入无需访问 Driver。

```python
from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.plugins import load_plugin, plugin_environment

async def run_application(wait_for_exit):
    driver = Driver(Env(environment="production"), Config())
    with plugin_environment():
        plugin = load_plugin("example_echo")
        if plugin is None:
            raise RuntimeError("example_echo could not be loaded")
        async with driver.lifespan():
            await wait_for_exit()
```

`plugin_environment(admission=...)` 安装本次 finder 和准入函数；在作用域内用 `register_plugin_manager()` 预登记来源，或用 `load_plugin()` 登记并加载。一个进程只允许一个活动环境。退出时撤销本次声明并移除 finder，不卸载已导入的 Python 代码。框架负责插件登记的寿命，服务负责 `sys.path`、命名空间路径和目录存储。

| 调用方 → 被调用方 | 输入、输出与约束 |
|---|---|
| CLI → control | 传 manifest 定位的端点、动作、规范模块名和参数；返回结构化结果或操作编号。CLI 中的 `--wait` 只改变等待方式 |
| control → Host | 只读请求读同一 Host 的状态。变更交给服务持有的任务执行；处理客户端连接的协程可以先结束。控制消息不能指定任意 Python 函数执行 |
| Host → catalog | 读登记记录、条件写修订、保存操作阶段；返回已提交的记录。目录层只接受可序列化值，数据库事务中不调用插件代码 |
| Host → Driver / 框架注册 API | load 交给模块管理器；start、stop 交给 Driver；清理完成后才撤销声明。Host 解析保护与依赖冲突，Driver 在实际变更前检查运行时前置条件 |
| Driver → 插件 | 通过已有的生命周期钩子创建、排空和释放按 owner 登记的资源。业务插件在声明阶段注册处理器，在启动阶段取得网络连接，退出钩子可针对未完成的清理重试 |

## 3. 配置、元数据、状态各写一次

| 信息 | 权威来源 | 何时读取、谁使用 |
|---|---|---|
| 环境、框架参数、数据目录 | 服务读取 `host.json`，现有字段 `environment`、`framework`、`data_dir` 足够 | 服务构造 Driver 时只传必要的框架参数。插件名、账号、启停策略和版本均有自己的位置 |
| 安装身份、入口、内置标记 | 插件自己的 `pyproject.toml` 生成的发行包元数据。沿用 `opsmesh.plugins` 和 `opsmesh.builtin_plugins` 两组 entry points | 导入代码前，服务核对分发包来源、模块和所属命名空间。内置标记应引用同一受信任分发包的插件入口；重名入口或覆盖保留命名空间的制品在导入前报冲突 |
| 作者描述、配置模型、支持的适配器 | 插件模块的 `PluginMetadata`，包括 `config`、`dependency_config`、`config_file` | 加载声明后由框架解析和校验。业务插件随包提供默认配置；服务首次初始化后保存有效配置。查询未加载插件时只显示可从发行包和目录取得的字段 |
| 部署意图和配置值 | 服务目录 `state.sqlite3`，按规范模块名保存 `enabled/autoload/required/protected`、来源与配置 | 服务是运行期唯一写入方。`protected` 是部署者额外固定一个插件的策略，原有记录要求同时启用、自动加载且必需；`builtin` 是包身份，并不自动要求安装所有内置包。两者任一成立均阻止在线替换 |
| 实际运行状态 | 进程内框架对象持有 `status/generation/calls/cleanup_complete` 和人工停止标记 | 服务按请求读取并与目录合并。generation 是当前进程的激活次数。制品版本、配置修订、运行代次分别展示 |

例如，业务扩展 A 依赖 channels 并声明自己的 QQ 连接，连接参数记在 A 的依赖配置里；channels 的 `dependency_config` 定义如何校验，A 的激活/清理决定连接寿命。令牌以凭据引用传入，管理响应仅返回状态。已保存的空配置 `{}` 与从未初始化分别处理。[host 示例](../../deploy/host.example.json) · [channels 包声明](../../packages/channels/pyproject.toml) · [PluginMetadata 字段](../../src/opsmesh/plugins/model.py)

### 从一个实际插件走通配置

1. 安装包发现阶段，channels 的发行元数据在两个入口组均声明 `channels = "opsmesh.plugins.channels"`。服务在导入前就知道它受内置策略保护。普通扩展只声明普通入口。部署者选择启用哪些模块；首次部署需自启动的扩展由部署步骤显式登记 `enabled=true, autoload=true`。
2. 声明加载阶段，示例 `group_assistant` 调用 `require("channels")` 和 `require("execution")` 声明代码依赖，并在自己的 `__plugin_meta__` 声明 `config=AgentSettings, config_file="settings.json"`。channels 的元数据提供 `dependency_config=ChannelConnections`。配置文件内的 `dependencies` 字段承载给提供方的参数；运行依赖边由声明加载记录。
3. 首次激活前，服务只在配置尚未初始化时读取随包文件。文件的 `config.model` 归 group_assistant，`dependencies.channels.dingtalk.main` 归这个扩展使用的钉钉连接。两套 schema 都通过才选择该配置；后续安装新包不会覆盖运维修改。启用时由 channels 解析凭据引用并创建连接。服务查询可以暴露连接是否就绪，凭据值不得进入操作响应或日志。[插件声明](../../examples/groups/group_assistant/__init__.py) · [示例配置](../../examples/groups/group_assistant/settings.json) · [提供方声明](../../packages/channels/src/__init__.py)

### 服务目录的数据结构与提交点

首批迁移仅改变代码归属，继续读取 v3 的 `plugins(module, record, updated_at)`。后续以 `PRAGMA user_version` 管理升级。下列是目标逻辑结构，可以沿用记录的 JSON 列储存字段。

| 记录及键 | 存储内容 | 一致性要求 |
|---|---|---|
| `plugins`，键为 module | 启用/自启动/必需/部署保护、来源、执行方式、安装状态、记录修订、已选配置和已选制品 | 修订随持久变更递增。卸载保留标记。服务重启从本表恢复意图，随后观察真实运行状态 |
| `config_revisions`，键为 (module, revision) | 不可变的 config/dependencies 快照、创建时间 | 候选可独立保存。应用成功后再提交已选修订；首次迁移只为已初始化配置建快照 |
| `operations`，键为 request_id | 规范化参数摘要、目标、请求时的修订、旧/新选择、阶段、结果或错误 | 同编号同参数返回原操作，同编号不同参数返回冲突。用 request_id 同时作为公开操作编号，避免另建映射 |
| `artifacts`，键为 (module, digest)，第 4 批引入 | 版本、不可变目录、入口、Worker 协议版本和所需能力 | 同版本不同内容由摘要区分。删除前核对活动指针、待完成操作和回滚引用 |

第 2 批开放持久操作前先建 operations 表；第 3 批增加配置历史和卸载标记；第 4 批增加制品。每批迁移在独占目录锁下、单个 SQLite 事务中完成并推进 user_version。跨表的成功提交（例如选择新制品、推进修订和记录提交阶段）也使用一个事务。网络资源和进程切换在事务外执行。

## 4. 启动、停止与管理操作

**服务启动。** 读取 manifest → 独占数据目录 → 打开目录数据库 → 发现并核对已安装入口 → 创建并绑定 Driver，登记选定模块和准入规则 → 加载自启动声明及其依赖 → 执行已有的启动阶段 → 开放本机管理端点并向 systemd 报告就绪。依赖图有环或必需插件失败时报告具体路径并倒序清理；可选扩展失败时保留诊断，其余已就绪插件继续运行。所有退出路径释放目录锁并撤销此次装配的导入器、注册项和 Driver 绑定。首期保持一个进程一个活动宿主。

**服务关闭。** 收到退出信号后先停止接受新的管理变更，再等待已受理的操作到达可恢复的阶段，关闭业务准入、排空调用，按依赖逆序释放资源，最后关闭控制连接、目录库并解锁。内置插件参与整机关闭。Linux 服务以本机 Unix socket 提供管理；控制端点成功绑定后才报告就绪，绑定失败则退出并回收已启动的资源。嵌入框架的普通应用不需要这个端点。[当前服务实现](../../packages/service/src/service.py)

**建议命令。** `opsmesh serve --manifest host.json` 启动服务；`opsmesh plugin status A`、`opsmesh plugin stop A`、`opsmesh plugin start A` 等连接同一实例（实例选择来自指定 manifest 或部署默认路径）。`start` 即恢复，不再增设等价动词。下表是目标行为；当前工作树在线路径仅接通已登记扩展的 `status/load/start/stop/restart`，其余按第 7 节实施。

| 操作 | 普通扩展的可观察结果 |
|---|---|
| register / load | 登记受信任来源。load 完成声明与归属登记，start 才获取资源。登记后从未加载的扩展可查询为 `not_loaded` |
| stop / start / restart | stop 关闭该扩展的新准入，等待调用及流式回复收尾，再释放其任务、连接和本次激活的注册。成功返回 `stopped` 且清理完成。start 使用选定配置再次激活；restart 在同一次操作中先停再启。同一版本、同一服务 PID |
| enable / disable | enable 允许激活，autoload 决定下次服务启动时是否自动运行。disable 要求目标停止并持久禁用。若停止失败，返回阻挡项或未完成的清理，保留可重试意图。临时 stop 只持续到显式 start 或下次按持久策略启动 |
| save-config / apply-config | 保存候选修订。apply 先校验候选，有运行实例才需要停旧、应用并检查就绪。失败时先清理候选并恢复上一生效修订；清理失败保留失败阶段。已停止的扩展只更新下次选择的配置 |
| uninstall | 核对消费者和子插件，停用、排空、撤销路由与注册，记录卸载标记防止入口扫描把它重新登记。默认保留配置和数据。成功标准包含“已无可达的调用入口”；删除可回收的制品是后续步骤 |
| update / reinstall | 支持版本隔离的扩展在新 Worker 加载候选版本，服务完成交接。已停止的扩展校验并选择新版本后仍为停止。进程内原生扩展返回具体阻挡能力和当前支持的操作 |

**运行规则。** 对内置或部署保护目标开放查询，所有会改变它的在线请求在写库或取得资源前拒绝，包含间接依赖、子模块、同名重装和级联。依赖已就绪内置提供方的扩展可正常启停。普通扩展仍有运行消费者时，默认拒绝停用并返回依赖链；首期不自动级联。重复启动已就绪插件、停止已清理插件应返回当前结果；对从未加载的插件执行 stop 返回明确错误。排空超时或钩子失败保留未完成资源，允许重试清理，完成前禁止再次激活。依赖递归启动遇到显式停止的提供方时报告阻挡。

**管理协议。** 首期复用已有的私有 Unix socket，通过目录和端点权限限制本机访问。请求携带动作和规范模块名；变更另带请求编号，可选预期修订。查询响应分别给出包身份、部署保护、生效策略、实际状态、代次、在途数及清理结果。长操作返回操作编号及阶段；失败返回稳定错误码、阻挡模块和可否重试。比如请求停止被 B 依赖的 A，返回依赖链 B → A，A 继续接收请求。新字段随相应批次落地。

**并发与恢复。** 所有管理入口进入同一变更路径，在现有生命周期变更锁内重新检查保护、依赖和当前代次。涉及持久修改的请求使用 `request_id`、预期修订和操作记录，CLI 断线后可凭编号查询；同一编号不同参数返回冲突。写入意图、释放旧资源、选择新修订与完成分别可识别；SQLite 使用短事务，在执行插件钩子前结束事务。进程重启按已提交修订和未结束操作恢复，无法证明资源已释放时报告待处理。运行服务不可达时在线命令返回失败，客户端不自行启动第二个 Host 或离线写库。

### 运行状态与重复操作

| 源状态 → 目标状态 | 触发、成功条件与失败行为 |
|---|---|
| not_loaded → loaded | load 成功后可以查看 PluginMetadata 和依赖。导入失败撤销本次声明，目录仍可查询；失败原因随操作返回 |
| loaded / stopped → starting → ready | start 验证 enabled、依赖就绪和旧资源已释放。每次实际激活递增 generation。依赖阻挡报告 blocked 或阻挡链，钩子失败报告 failed 与清理结果 |
| ready → stopping → stopped | 停止前先核对运行消费者，再关闭准入，排空在途并释放本次激活资源。stopped 必须满足在途数为零、cleanup_complete 为真 |
| failed → stopped → ready | 清理未完先重试 stop，只执行尚未成功的清理。完成后管理员可重试 start。启动失败但已完整回滚时可直接重试 start |
| stopped → 已卸载 | 进一步撤销声明、子插件注册、别名和管理器；检查已停止但持有 Python 引用的消费者。已卸载是目录状态，不在 Driver 添加第二个枚举 |

restart 采用同一次操作中的 stop 和 start。新激活失败时报告失败阶段并允许恢复，不把已停止的旧实例报告成 ready。运行时的 ready 表示钩子完成；要求连接握手成功的 apply/update 还需核对该扩展声明的就绪条件。

### CLI、报文与维护入口

目标命令统一采用 `opsmesh <子命令> ... --manifest <path>`。默认路径由部署提供，systemd 显式指定。首期 Linux 服务使用现有私有目录和 Unix socket，每个连接一条有长度限制的 JSON 行。随新 CLI 同批切换控制协议，未知版本返回协议错误。

| 命令 | 可用时机及语义 |
|---|---|
| `serve` | 创建唯一宿主。与另一服务或已取得锁的维护命令冲突时退出 |
| `plugin list/status`、`plugin operation <id>` | 在线无副作用查询。list 返回登记项及运行摘要，status 返回单项细节，operation 返回执行阶段和结果 |
| `plugin register/load/start/stop/restart/enable/disable` | 在线管理服务已信任的普通扩展。register 引用现有制品；start 可先加载。stop/start 不改持久启用意图 |
| `plugin save-config/apply-config/update/uninstall/reinstall` | 按批次开放。更新和重装要求候选制品摘要，配置应用要求候选修订；对已停扩展尊重原停止意图 |
| `check`、`maintenance command <module:function>` | 承接部署校验与现有插件迁移命令；显式离线、独占数据目录。安装脚本、发布 smoke 和调用方同批更换命令。原 protect 及内置配置导入由显式离线维护操作承担 |

下面是目标协议的一次停止请求及终态响应示意。标识使用完整模块名。长操作可先返回 accepted，随后通过操作编号查询。CLI 退出码建议统一为 0 成功（含明确请求的异步受理）、2 参数错误、3 目标或修订冲突、4 服务不可达、1 执行失败。默认等待时只有终态成功才返回 0。

```json
{"version":2,"action":"stop","module":"example_echo","request_id":"stop-41","expected_revision":6}
```

```json
{"operation":"stop-41","phase":"done","result":{"module":"example_echo","state":"stopped","generation":3,"in_flight":0,"cleanup_complete":true,"enabled":true,"revision":6}}
```

当前代码的 4 KiB 请求上限适合启停报文。引入配置提交时由 CLI 解析本地文件后传 JSON，同时为该动作明确较大的有界报文限制并测试超限错误；不得让服务按请求中的任意路径代读配置。制品引用定位到服务验证过的目录。状态查询返回稳定错误码，例如 `protected`、`dependency_in_use`、`cleanup_incomplete`、`revision_conflict`、`unsupported_capability`，CLI 再加可读说明。

## 5. 普通扩展如何真正换代码

当前 `require()` 返回真实 Python 模块。Matcher 的函数、已导入的类、打开的流都可能持有旧对象；重跑生命周期只能重建资源。目标按扩展所需能力选执行方式，服务在登记时给出支持的操作。

| 执行方式 | 适用情况与承诺 |
|---|---|
| 宿主进程内 | 内置提供方和需要原生 FastAPI、数据库引擎等对象的扩展。普通扩展支持首次加载、在线停/启和撤销注册。逻辑卸载后同进程禁止以同名新版本重新导入；服务显示原因 |
| 独立 Worker | 面向需要在线更新、卸载后同名重装的普通扩展。不同版本用独立解释器和制品目录，共用服务提供的消息、回复和生命周期协议。首批支持通过标准事件收消息、回消息的扩展 |

1. 新版本先在独立目录验证来源、摘要、核心接口版本和所需能力；新 Worker 在未接流量时加载、注册候选处理器并回应就绪。发布清单指定可启动 Worker 的解释器；Linux 冻结版另行验证 Worker 入口和私有依赖可用。
2. 服务保留 channels 实例、监听端口、账号连接和分发入口。跨进程按调用编号传递可序列化的事件/消息、回复片段、结束和取消。流有顺序、背压和超时；HTTP 输出对象、SDK 客户端等只留在持有它们的进程。Worker 退出时撤销其注册并结束受影响的调用。
3. 以预期修订检查活动版本，切换新调用的路由。已接收的调用固定到原 Worker，排空后退出。若新旧版本需要独占同一项资源且不能共享，先停接并排空目标再交接，管理结果报告该扩展的中断窗口。
4. 提交前失败撤回候选，保留或恢复原路由；提交后按新操作回退。服务意外退出时按活动指针、交接记录和 Worker 身份对账，再开放准入。已产生外部副作用的调用禁止自动重放。涉及不可逆数据迁移时返回维护升级要求。
5. Worker 全部退出且版本无活动、回退或共享引用后，才回收服务托管的版本目录。原生对象依赖需改为可序列化能力或明确为进程内部署。Worker 路径不能直接复用同进程的 `require()` 返回跨进程对象。

## 6. 通道代码保留哪些共同逻辑

QQ 和钉钉的普通消息以及标准 HTTP 消息端点最终都形成框架可分发的事件，消息内容使用 Message；QQ、钉钉还各有通知事件。FastAPI 自定义路由可以自行处理请求。平台确认、连接握手和回复格式继续由通道插件处理。管理服务只操作资源所有者，不按平台写分支。[QQ 事件](../../packages/channels/src/qq/events.py) · [钉钉事件](../../packages/channels/src/dingtalk/events.py) · [HTTP 消息与分发](../../packages/channels/src/api/exchange.py)

以 A 使用 QQ、B 使用钉钉为例：停止 A 时只关闭 A 的准入，允许已接收的回复通过原连接发完，再清理 A 名下资源。B 能继续收发，内置 channels 的 generation 保持稳定。共用账号或监听端口要记录实际所有者；资源无法独立交接时，在变更前报告冲突。

复用已有 `CallbackChannel`、队列、去重和流式发送逻辑。公共配置、凭据解析、客户端关闭在输入和失败语义相同时合并。QQ 回复序号、钉钉卡片和 ACK、HTTP 请求输出与断连取消分别在各协议实现中测试。首批服务拆包不夹带通道去重，两个改动可单独验收。[通道去重清单与依据](plugin-online-lifecycle.md)

## 7. 实施批次与发布门槛

| 批次 | 交付内容 | 必须通过的验证 |
|---|---|---|
| 1. 框架与服务拆包 | 迁移入口、Host、SQLite 和进程控制；显式装配 finder 和插件名映射；迁清旧导入、打包脚本、systemd 启动命令、Docker、安装文档 | 干净环境只装 core：无 `opsmesh` 命令，import 前后无新增插件 finder，无目录/进程副作用。自建最小应用能注册、分发、退出后再装配。只装 service 加其核心依赖：能启动空服务。现有 v3 数据目录能读取，配置和部署保护有效。分别检查 wheel 内容和 Linux 冻结程序 |
| 2. 在线资源管理 | 保留现有在线启停能力，统一 CLI 请求、补齐在线登记及持久启用/禁用、状态和错误；将内置策略收口到服务准入与框架通用保护 | 真 Linux 服务加载 A/B，记服务 PID、内置 generation 和 B 的连续响应；stop A 时旧流收尾、新请求拒绝，start A 后只有一份连接。覆盖直接请求与间接依赖保护、并发重复请求、断线、清理失败重试、第二个宿主争锁和控制端点启动失败 |
| 3. 配置与卸载 | 增加候选/生效修订、操作记录和卸载标记；完成在线配置回退、逻辑卸载和崩溃恢复 | 用真实旧库副本校验迁移，区分默认值和空配置。注入端口占用、排空超时和各写入阶段崩溃；确认只有一个生效实例，卸载后重启也不会被重新发现 |
| 4. 版本隔离与交接 | 完成交付可运行的 Worker、版本制品布局、消息能力、回滚和垃圾回收 | 同一服务 PID 下将示例扩展 v1 更新到 v2，观察旧流用 v1 收尾、新请求命中 v2；验证候选失败、Worker 意外退出、同名重装、原生对象依赖被拒并说明原因，内置提供方与无关扩展连续可用 |

首次调整发行布局需要部署一次新的服务程序。先对目录库做一致性备份，停旧服务取得独占，安装新发行包并切换服务入口，以现有数据目录验证启动、状态查询和扩展启停。第 1 批保持 v3 格式，失败可切回旧发行物；第 3 批升级 schema 前另做快照，降级时同时恢复相应库版本。日常操作是否需要维护窗口取决于目标插件身份和运行模式。以真实扩展版本切换通过第 4 批验收，作为对外承诺支持代码热更新的条件。
