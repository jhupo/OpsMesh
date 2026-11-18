# 插件配置与本地存储

本文描述当前实现。资源生命周期见[插件生命周期](plugin-lifecycle-design.md)。

## 职责边界

| 位置 | 内容 |
|---|---|
| host.json | environment、framework、data_dir；仅框架和宿主基础配置 |
| PluginMetadata.config | 插件自己的 Pydantic 配置类型 |
| PluginMetadata.config_file | 可选的首次配置文件，相对于插件入口包目录 |
| PluginMetadata.dependency_config | 提供方定义的依赖配置类型，每个使用方分别填写 |
| state.sqlite3 | 模块位置、enabled、autoload、required、protected 和 JSON 配置值 |
| Plugin | 当前模块、运行状态、依赖、活动配置与注册归属 |
| 外部管理插件 | 安装、下载、解压、发布、删除、权限检查和管理接口 |

数据库插件服务业务 PostgreSQL，filesystem 提供普通文件操作。`opsmesh.service.catalog` 使用标准库 SQLite 保存宿主记录；core 的 `opsmesh.plugins.config` 只负责配置模型、读取和校验，不依赖 SQLite。

## 宿主配置

```json
{
  "environment": "production",
  "data_dir": "/var/lib/opsmesh",
  "framework": {"command_start": ["/"]}
}
```

相对 data_dir 按 host.json 所在目录解析。插件模块、配置和启动意图通过 CLI 或 Host 管理接口登记到 SQLite。

宿主只创建 state.sqlite3 和 host.lock。插件安装目录、临时解压目录及业务文件布局由对应插件决定，框架不强制 plugins/、staging/ 或技能工具目录。

## 存储记录

PluginRecord 定义：

| 字段 | 含义 |
|---|---|
| module | 完整 Python 模块名，唯一标识 |
| directory | 可选的绝对导入根目录；不是框架拥有的安装目录 |
| enabled | 是否允许启动和作为依赖使用，显式新登记默认 false |
| autoload | 是否在宿主启动时主动加载，默认 false |
| required | 启动失败是否使宿主启动失败，默认 false |
| protected | 核心保护，默认 false；必须同时 enabled、autoload、required |
| config | JSON 对象，由插件自己的模型校验；默认空对象 |
| dependencies | 按已声明依赖的短名或完整模块名保存的配置对象；默认空对象 |
| config_initialized | 配置是否已完成初始化；显式保存的空对象也算已初始化 |

SQLite 仅有 plugins 表：module 主键、record JSON 文本、updated_at；PRAGMA user_version 标记结构版本。版本 3 增加配置初始化标记；升级时将版本 1、2 的已有记录标记为已初始化，保留已有空配置，版本 1 同时补齐 protected=false。失败回滚，未知版本拒绝打开，损坏记录不会自动丢弃。

PluginStorage 仅负责短连接、参数化 SQL 和记录持久化。调用方通过工作线程读写；数据库事务不跨插件回调。进程独占锁属于 app 宿主，防止两个管理进程同时操作同一数据目录，不属于存储类。

删除存储记录不删除文件。仍安装的包必须保留禁用记录，否则下一次入口点发现会重新登记。管理插件处理卸载时需要负责这一规则；低层 delete 不代表安全卸载接口。

核心记录禁止通过通用更新解除保护或删除。服务停止后，CLI 的 protect 操作可提升已满足常驻条件的记录；运行宿主持有状态目录锁时，离线管理命令不能写入。普通在线接口允许保存核心配置，但资源重启通过宿主维护重启完成。

## 加载和配置读取

1. 宿主读取 SQLite 并设置登记的导入根目录。
2. 宿主准备阶段发现本地已安装的 opsmesh.plugins 入口点，缺失记录以 enabled=true、autoload=false 登记；已有禁用记录不覆盖。不导入所有发现的插件。
3. 为各记录准备 PluginManager，然后加载 enabled 且 autoload 的入口。
4. require 只解析、检查并加载依赖，不写数据库、不安装依赖。依赖必须已登记；未登记的普通源码模块由管理方显式登记。
5. load 只导入声明；start 在配置校验和资源回调前完成首次配置导入。实际加载状态每次启动重算，不把 READY 持久化。

显式加载禁用插件可取得 PluginMetadata.config，但不会启动资源，也不会允许其他插件 require 它。这仍会执行受信任的 Python 导入代码，不是沙箱或静态检查。不新增另一套元数据检查加载器。

get_plugin_config(Settings) 保留按定义模块查找归属和返回独立模型的行为。参数默认值、字段约束、资源路径解释和密钥引用均由插件定义。配置相关 Agent、连接和客户端在 on_startup 读取参数构建，不能永久缓存导入时的配置。

## 保存与应用分离

```text
save_config(module, values, dependencies=...): 校验候选值 → SQLite 提交
apply_config(module):       校验已保存值 → 依赖检查 → 停止资源 → 替换活动配置 → 重新启动
```

save_config 不修改活动配置、不重启插件。apply_config 对停止的插件只替换配置，不启动；对运行中的插件重启资源。不能单独重启仍被活动消费者依赖的提供方，管理插件需要先显式停止这些消费者。

保存失败不影响运行实例。保存成功而应用失败时，数据库保留新值，调用方会收到异常；旧活动配置是否仍在使用通过 Plugin.config_values 和状态查询，不宣称已应用成功。关闭失败不得再创建第二个实例。新配置启动失败不自动回滚。

配置值按完整对象保存，由插件模型校验。环境变量引用由插件解释；修改 systemd 的 opsmesh.env 需要重启进程。

## 管理入口

Host 连接存储与现有 PluginManager/Lifespan，提供登记、保护、配置和启停操作。部署服务从 `opsmesh.service.host.load_host()` 上下文取得 Host，再交给本机控制入口。只嵌入框架的应用可自行装配 Driver、插件及配置，不需要 Host 或 SQLite。

register 只登记已存在模块，不复制文件或执行代码。enable 保存 enabled=true、autoload=true；运行中同时启动。disable 预检消费者后保存 enabled=false，再停止资源。临时 start_plugin/stop_plugin 不修改持久值。

管理动作串行执行；资源操作交由宿主生命周期监督。回调中禁止嵌套等待管理操作，处理器不能等待停止自身或所依赖插件。客户端断开不直接中断半次启停。

本地 CLI 用于服务停止期间的基础登记与配置，不提供包安装：

```bash
opsmesh --manifest /etc/opsmesh/host.json --plugin-action list
opsmesh --manifest /etc/opsmesh/host.json --plugin-action register \
  --module example_chat --directory /opt/opsmesh-examples/chat
opsmesh --manifest /etc/opsmesh/host.json --plugin-action configure \
  --module example_chat --config /etc/opsmesh/example-chat.json
opsmesh --manifest /etc/opsmesh/host.json --plugin-action enable --module example_chat
opsmesh --manifest /etc/opsmesh/host.json --plugin-action disable --module example_chat
opsmesh --manifest /etc/opsmesh/host.json --plugin-action register \
  --module example_core --enabled --autoload --required --protected
```

JSON 文件是 CLI 的一次性输入，保存后不会被运行过程读取。依赖有必填参数时先配置依赖。冻结宿主的外部运行包需要登记其绝对 site 导入根；后续入口点发现由宿主准备阶段完成。服务运行时独占锁阻止离线 CLI 修改，在线管理 API 由管理插件实现。

## 插件携带的配置文件

插件在 `PluginMetadata` 声明 `config_file="settings.json"`，仍用 `config=Settings`
声明自身字段。文件格式与下方 CLI 配置格式一致，包含 `config` 和 `dependencies`。
文件名不固定，可以放在入口包内的任意子目录；单文件插件按入口文件所在目录定位。
包安装管理方负责解压，保留文件相对位置，框架不读取或安装 ZIP。

新登记记录未提供配置时，第一次启动（包括间接 require 的插件）或 `--check` 会读取
声明的文件，校验自身和依赖参数后保存 SQLite，再供启动回调使用。未声明文件则校验
当前空配置，按模型默认值运行；必填字段缺失时失败。声明文件不存在、JSON 错误或
参数不合法时不保存初始化标记，也不启动资源，可通过管理接口修正后重新启用。

已有初始化记录不再读取文件，即使文件被修改或删除，也不会覆盖在线保存的值。
显式传入 `config={}` 或 `dependencies={}` 也表示已经配置，不按对象是否为空猜测。
`register --config`、`save_config` 和 `configure` 都保存已初始化的配置。

显式重新导入使用 `Host.import_config(module)` 或服务停止时执行：

```bash
opsmesh --manifest /etc/opsmesh/host.json --plugin-action import-config --module example_chat
```

重新导入校验成功后只替换保存值；在线调用者再使用 `apply_config` 才重建运行资源。
导入失败保留原配置。文件路径拒绝绝对路径、上级目录跳转和指向包外的符号链接。
配置里存密钥环境变量引用，不存明文密钥。该初始化流程由标准 Host 管理；直接嵌入
`load_plugin(config=...)` 的应用仍负责提供配置，不会隐式创建宿主数据库。

## 验证范围

单元验证覆盖配置字段校验、SQLite 重新打开、禁用依赖不导入、配置保存与应用分离、声明加载不启动资源、持久启停、并发数据目录锁、核心保护、资源重启与调用排空。Linux 二进制安装和真实服务发布需单独验收。


## 使用方的依赖配置

CLI 的 `--config` 文件统一使用以下结构。`register --config` 可以同时导入初始值；
`configure --config` 替换这两部分完整配置，不合并旧字段。原来直接放置插件参数的
文件需把参数放入 `config`，不保留两种格式的猜测分支。

```json
{
  "config": {"model": "your-model-name"},
  "dependencies": {
    "channels": {
      "dingtalk": {
        "main": {
          "client_id": "configured-client-id",
          "client_secret_ref": "DINGTALK_CLIENT_SECRET"
        }
      }
    }
  }
}
```

使用方代码仍通过 `require("channels")` 声明依赖，不需要在 Settings 中重复定义
机器人参数，也不调用连接创建函数。框架校验配置键属于已经声明的依赖；配置不会
触发任意模块导入。短名与完整模块名不能重复配置同一依赖。

提供方通过 `PluginMetadata.dependency_config` 声明模型，用
`get_dependency_config(Model)` 读取当前使用方的独立副本；需要检查指定使用方时可以
显式传入 Plugin。提供方自己的 `get_plugin_config` 仍读取自己的全局配置，
两种配置不会相互覆盖。channels 复用插件加载通知登记启动回调，在每次使用方启动时
读取当前配置，按声明创建机器人；仅使用的协议会进行具体配置校验。

`Host.save_config(module, values, dependencies=...)` 一起校验并保存。省略这个关键字
表示只更新插件自己的参数，传入 `{}` 表示清除依赖配置。保存不改变当前运行资源，
`apply_config` 或重新启用时替换活动值并重建连接。群组和智能体之间的选择写在插件
的 `on_message/on_command` 与 Rule 中，配置中没有 Agent 路由表。

依赖参数在保存、`--check` 和资源启动前校验，声明加载时只登记启动回调。
这样已有错误配置的插件仍可加载并修正参数，但不能启动机器人连接。密钥环境变量
在连接准备时检查，日志只说明缺失的引用名称。同一机器人账号不能由两个使用方
重复创建；连接随所属插件停止而释放，不影响其他插件持有的连接。
