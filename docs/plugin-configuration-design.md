# 插件配置与本地存储

本文描述当前实现。资源生命周期见[插件生命周期](plugin-lifecycle-design.md)。

## 职责边界

| 位置 | 内容 |
|---|---|
| host.json | environment、framework、data_dir；仅框架和宿主基础配置 |
| PluginMetadata.config | 插件作者提供的 Pydantic 配置类型，继续使用现有 Metadata |
| state.sqlite3 | 模块位置、enabled、autoload、required、protected 和 JSON 配置值 |
| Plugin | 当前模块、运行状态、依赖、活动配置与注册归属 |
| 外部管理插件 | 安装、下载、解压、发布、删除、权限检查和管理接口 |

数据库插件服务业务 PostgreSQL，filesystem 提供普通文件操作，宿主存储直接使用标准库 SQLite。

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

SQLite 仅有 plugins 表：module 主键、record JSON 文本、updated_at；PRAGMA user_version 标记结构版本。版本 2 增加核心保护，版本 1 记录在事务中补齐 protected=false。失败回滚，未知版本拒绝打开，损坏记录不会自动丢弃。

PluginStorage 仅负责短连接、参数化 SQL 和记录持久化。调用方通过工作线程读写；数据库事务不跨插件回调。进程独占锁属于 app 宿主，防止两个管理进程同时操作同一数据目录，不属于存储类。

删除存储记录不删除文件。仍安装的包必须保留禁用记录，否则下一次入口点发现会重新登记。管理插件处理卸载时需要负责这一规则；低层 delete 不代表安全卸载接口。

核心记录禁止通过通用更新解除保护或删除。服务停止后，CLI 的 protect 操作可提升已满足常驻条件的记录；运行宿主持有状态目录锁时，离线管理命令不能写入。普通在线接口允许保存核心配置，但资源重启通过宿主维护重启完成。

## 加载和配置读取

1. 宿主读取 SQLite 并设置登记的导入根目录。
2. 宿主准备阶段发现本地已安装的 opsmesh.plugins 入口点，缺失记录以 enabled=true、autoload=false 登记；已有禁用记录不覆盖。不导入所有发现的插件。
3. 为各记录准备 PluginManager，然后加载 enabled 且 autoload 的入口。
4. require 只解析、检查并加载依赖，不写数据库、不安装依赖。依赖必须已登记；未登记的普通源码模块由管理方显式登记。
5. load 只导入声明；start 校验配置并运行资源回调。实际加载状态每次启动重算，不把 READY 持久化。

显式加载禁用插件可取得 PluginMetadata.config，但不会启动资源，也不会允许其他插件 require 它。这仍会执行受信任的 Python 导入代码，不是沙箱或静态检查。不新增另一套元数据检查加载器。

get_plugin_config(Settings) 保留按定义模块查找归属和返回独立模型的行为。参数默认值、字段约束、资源路径解释和密钥引用均由插件定义。配置相关 Agent、连接和客户端在 on_startup 读取参数构建，不能永久缓存导入时的配置。

## 保存与应用分离

```text
save_config(module, values): 校验候选值 → SQLite 提交
apply_config(module):       校验已保存值 → 依赖检查 → 停止资源 → 替换活动配置 → 重新启动
```

save_config 不修改活动配置、不重启插件。apply_config 对停止的插件只替换配置，不启动；对运行中的插件重启资源。不能单独重启仍被活动消费者依赖的提供方，管理插件需要先显式停止这些消费者。

保存失败不影响运行实例。保存成功而应用失败时，数据库保留新值，调用方会收到异常；旧活动配置是否仍在使用通过 Plugin.config_values 和状态查询，不宣称已应用成功。关闭失败不得再创建第二个实例。新配置启动失败不自动回滚。

配置值按完整对象保存，由插件模型校验。环境变量引用由插件解释；修改 systemd 的 opsmesh.env 需要重启进程。

## 管理入口

Host 连接存储与现有 PluginManager/Lifespan，提供 register、save_config、apply_config、enable、disable。管理插件通过 get_host() 获取它，并负责自己的认证授权。

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

## 验证范围

单元验证覆盖配置字段校验、SQLite 重新打开、禁用依赖不导入、配置保存与应用分离、声明加载不启动资源、持久启停、并发数据目录锁、核心保护、资源重启与调用排空。Linux 二进制安装和真实服务发布需单独验收。
