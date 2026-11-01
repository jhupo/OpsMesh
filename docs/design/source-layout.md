# 源码目录职责审查与调整

审查日期：2026-10-31。范围为调整前 `src/opsmesh` 下全部 **355 个 Python 文件**；依据文件声明、类/函数、导入关系和调用位置建立清单，再重点检查职责混合的实现。调整后为 **363 个 Python 文件**。这是目录和依赖边界审查，不等于对所有行为重新证明正确。

原目录并非所有文件都归类合适。根层 `adapters` 单独成层，与项目已有的 `framework / modules / infrastructure / bootstrap / entrypoints` 划分不一致；另外还有 Worker 执行实现进入组装层、数据库协作文件分散、通知持久化暴露在模块公开层、命令行入口分散、资源合同混在 SQL 实现中等问题。本轮已调整实际源码、调用方、测试、入口配置和文档。

完整文件职责、迁移来源、主要符号见[文件职责表](../reference/source-responsibilities.md)；真实文件树见[源码树](../reference/source-tree.md)。文件级表由 AST 清单生成，其描述辅助定位职责，不把静态扫描当作逐行行为审计。

## 一、归类原则

| 位置 | 应有内容 | 不应放入 |
| --- | --- | --- |
| `framework` | Driver、Adapter/Bot、Agent 后端端口、统一 Event/Message、Matcher、Depends、上下文、插件加载与注册归属 | 用户账户、SQL 业务表、具体钉钉/QQ 协议、应用组装 |
| `modules/<domain>` | 有业务所有权的合同、授权用例、状态与内部实现 | 其他模块的私有实现、HTTP 路由、具体机器人协议 |
| `infrastructure` | 数据库、持久消息、协调、存储、观测、机器人协议、Run Worker 进程/集群实现 | 入口参数解析、应用创建、平台用户到 Bot 的授权政策 |
| `bootstrap` | 构造服务、绑定回调、应用生命周期、跨域调用链和消费链的组装 | 厂商协议处理、Kubernetes REST/Job 实现、通用 SQL 机制 |
| `entrypoints` | CLI 参数解析、HTTP/SSE 路由、独立 Worker 入口 | 复制业务状态机、直接访问其他模块内部持久化对象 |
| `maintenance` | 离线整站备份、校验、恢复及相应文件/数据库操作 | 在线业务插件、CLI 参数解析 |
| `plugins` | 可选插件，消费框架和公开平台服务 | 必须依赖插件启用才能工作的账号、绑定、调度、审批、成本功能 |

顶层只保留版本、包入口、`python -m opsmesh` 入口和面向插件的 `dependencies.py`。共享资源治理值对象放在 `modules/resource_contracts.py`，不在框架中引入平台资源启停语义。部署配置仍在 `bootstrap/settings.py`；`modules/configuration` 拥有的是可持久化、可授权的业务配置。

## 二、实际调整

| 调整前 | 当前归属 | 原因 |
| --- | --- | --- |
| `adapters/{dingtalk,qq,onebot}` | `infrastructure/bots/{dingtalk,qq,onebot}` | 这里是外部协议的具体实现，不是框架抽象或平台账号管理 |
| `framework/internal/driver/{abstract,model,server}.py` | `framework/drivers/{base,models,server}.py` | Driver 的合同、生命周期和网络机制聚合在同一功能目录；公开导入仍是 `opsmesh.framework.drivers` |
| `bootstrap/run_launcher.py` | `infrastructure/run_workers/launcher.py` + `bootstrap/workers.py` | 前者执行进程/容器并管理生命周期；后者只绑定部署配置、构造实例 |
| `bootstrap/kubernetes_workers/*` | `infrastructure/run_workers/kubernetes/*` | Kubernetes API、Job 规格、观测和生命周期属于具体执行实现 |
| `bootstrap/worker_status.py` | `infrastructure/run_workers/status.py` | Worker 观测合同由执行设施拥有，HTTP 对外诊断仍转换成受限返回对象 |
| `infrastructure/database.py` | `infrastructure/database/connection.py` + 包公开导出 | 连接、元数据、显式 schema 生命周期有明确归属；`opsmesh.infrastructure.database` 入口保持稳定 |
| `infrastructure/{records,migrations,sql_transactions}.py` | `infrastructure/database/{records,migrations,transactions}.py` | 与数据库连接共同构成 SQL/CAS/事务实现 |
| `infrastructure/outbox.py` | `infrastructure/messaging/outbox.py` | 真实持久消息实现，包括领取租约、重试、死信；此目录已不是占位包 |
| `infrastructure/{cache,admission}.py` | `infrastructure/coordination/{redis,admission}.py` | Redis 租约/请求限速与执行槽位的原子协调；业务准入政策仍归 configuration |
| `infrastructure/resource_versions.py` | `modules/resource_contracts.py` + `infrastructure/database/availability.py` | 资源引用/可用性合同与 CAS 存储分离 |
| `modules/notifications/{service,delivery_state}.py` | `modules/notifications/internal/*` | 可信事件入库、投递和 CAS 状态属于内部实现；公开管理用例留在 management |
| `modules/runtime/management_base.py` | `modules/runtime/internal/management_base.py` | 授权基类只服务于本模块实现，不作为独立公开入口 |
| `cli.py`、`maintenance/commands.py` | `entrypoints/cli/{main,maintenance}.py` | 参数解析、终端输出归命令行入口；整站备份的执行逻辑保留在 maintenance |

修正了已实现模块的 `implementation pending` 和 `Reserved implementation namespace` 旧说明。没有增加未来功能占位包，也没有为了目录对称给每个小函数制造一层包。

## 三、机器人链路

```text
framework/adapters/                 协议抽象、连接注册、中立消息与事件
        ↑ 实现接口
infrastructure/bots/
    dingtalk/                      Stream、OpenAPI、平台消息转换
    qq/                            官方签名 Webhook、鉴权、OpenAPI、消息转换
    onebot/                        已有兼容协议实现
        ↑ 仅由组装层选择和注入
bootstrap/bot_adapters.py
        ↓ 注入框架注册表
modules/bots/                      账号、外部身份映射、Agent 绑定、授权、幂等投递
        ↑ 调用公开用例
entrypoints/http/routes/bots.py    HTTP 入口
dependencies.py                   插件 Depends 入口
```

具体协议不能导入 `modules/bots`。平台 Bot 服务只依赖 `OpsMeshBotAdapter`、`BotAdapterRegistry` 等框架合同，因此插件仍可注册新协议。没有把 Agent 绑定或持久调度搬进插件，也没有改变本轮之前确定的 QQ 官方 API 接入方式。

## 四、为什么这些文件保留原位

| 范围 | 保留理由 |
| --- | --- |
| `framework/agents/*` | Agent 定义、上下文、后端端口和事件是跨 SDK 合同；不会因接入新的 SDK 改成厂商目录 |
| `modules/runtime/internal/backends/openai_agents/*` | 具体 SDK 类型、工具映射、Sandbox、Memory、追踪桥接和 SDK 用量观察由 runtime 拥有；保持一个私有后端边界比散入多个 infrastructure 子包更容易隔离厂商差异 |
| `framework/plugin/*`、`framework/dependencies/*` | 保留 NoneBot 风格的导入加载、`require`、`on_*`、注册归属、`Depends` 和作用域，没有另建一套插件注册系统 |
| `framework/matcher.py`、`framework/events.py` 与其 `internal` 实现 | 是明确的公开门面和私有执行机制，不是两个重复的业务所有者 |
| `modules/*/management.py` | 面向 HTTP 和插件的带授权用例；不能把公开用例全部隐藏，再让插件越过 internal 边界 |
| `modules/accounts/internal/management*.py` | accounts 已在 `api.py` 定义管理 Protocol，因此实现可保持私有；这与其他域直接发布管理门面不冲突 |
| `modules/operations/{plugins,outbox}.py` | 平台的插件部署政策和死信运维用例，不属于可选业务插件或通用消息存储 |
| `modules/execution/internal/*` | 命令执行环境的业务接口及资源政策，与启动整个 Run Worker 的进程/集群机制不同 |
| `modules/capabilities/internal/*` | Skill、MCP、脚本、知识目录及工具网关属于能力域；其 provider 实现由域管理，不要求所有第三方实现都集中到 infrastructure |
| `bootstrap/*_bridge.py`、`run_resources.py`、`run_usage.py`、`coordinator.py` | 连接多个域的应用调用链/生命周期绑定，当前模块化单体明确允许在组装层；不创建独立业务表或再造领域状态机 |
| `maintenance/*` | 离线恢复会组装受限应用，涉及数据库、workspace 和 blob，是独立维护操作；不塞进仅提供单存储机制的 infrastructure/storage |
| `infrastructure/secrets.py` | 目前只有一种环境密钥解析机制，独立文件职责单一，不为未来 KMS 创建空包 |
| `plugins/scheduler/*` | 旧公开导入路径的兼容门面；实际持久调度只在 `modules/scheduling`，不重复实现 |

目录按代码的所有者、调用边界和实际职责划分，不机械规定“所有具体实现都在 infrastructure”或“所有管理类都在 internal”。业务模块可以有私有技术实现；框架的 HTTPX/WebSocket Driver 也仍是框架网络能力的实现。

## 五、Worker 和数据库的依赖处理

`RunWorkerLauncher` 接收 `WorkerLaunchConfiguration`，包括工作目录、数据库地址、Kubernetes 配置和生成子进程配置的回调。`bootstrap/workers.py` 负责从 `OpsMeshSettings` 绑定这些参数。Worker 实现不导入 bootstrap；环境变量白名单、容器路径重映射、PostgreSQL 集群要求、进程回收和日志限制保留原行为。

数据库包维持 `AsyncDatabase / StoredRecord / VersionedRecordStore` 的公开入口。Outbox 使用 SQL 事务帮助函数；数据库在构造实例时装配 Outbox，避免包初始化产生循环。新增独立 Python 进程导入回归，覆盖先导入消息设施、Worker 配置、CLI 再导入应用的顺序。

## 六、边界检查和验证

`scripts/check_repository.py` 现在检查：

- Python 源码必须属于已声明的架构层，根层只允许明确公开入口。
- framework 不导入平台业务、基础设施或应用组装。
- 具体机器人协议不导入平台业务；平台模块不导入具体机器人或 Run Worker 实现。
- framework/modules/infrastructure 不反向导入 bootstrap、entrypoints、maintenance。
- 不跨模块导入 internal；公开 api 不导入自身实现；平台核心不依赖可选 plugins。
- OpenAI Agents SDK 导入仅出现在 runtime 的私有 OpenAI 后端。
- 不发布只有占位说明的 Python 叶子包，文档链接指向实际文件。

静态检查解析绝对导入、相对导入和 `from package import member`。动态导入、函数回调与运行时语义仍需要代码审查和行为测试。

本轮实际验证：

| 检查 | 结果 |
| --- | --- |
| 完整默认测试集 | **645 passed, 26 skipped**；363.94 秒 |
| 临时 PostgreSQL 集成 | **12 passed**；24.84 秒，使用隔离的本地临时数据库 |
| Ruff / 格式 | 通过；488 个 Python 文件格式检查通过 |
| mypy | **363 个源码文件**通过 |
| 文档链接、静态依赖、根层归属、空包 | 通过 |
| wheel / sdist | 构建成功；源码包 Markdown 链接通过 |
| wheel 独立导入 | 363 个源码文件，CLI/数据库消息/Worker/QQ/钉钉/应用导入成功；没有旧协议包或字节码缓存 |

默认跳过的是显式环境集成，其中 PostgreSQL 的 12 项已单独启用执行。此次没有调用真实 QQ/钉钉账号、付费模型或生产服务；没有更新远端测试服务器。后续清理已删除旧缓存、废弃目录壳、未加载的 TOML 设计配置和空迁移目录占位文件；Web 预留目录、有效包入口和类型标记保留。

## 七、导入迁移

稳定入口继续使用 `opsmesh.framework.adapters`、`opsmesh.framework.agents`、`opsmesh.framework.drivers`、`opsmesh.dependencies` 和 `opsmesh.infrastructure.database`。CLI 命令 `opsmesh` 与 `python -m opsmesh` 保留。

直接使用旧具体实现路径的部署代码需要更新，例如 `opsmesh.adapters.qq` 改为 `opsmesh.infrastructure.bots.qq`。直接构造旧 Launcher 的代码改用 `bootstrap.workers.create_run_worker_launcher(runtime, settings, ...)`，或自行提供 `WorkerLaunchConfiguration` 构造基础设施 Launcher。本项目仍为 Alpha；本轮没有保留已经职责错位的整套旧实现导入别名。
