# OpsMesh 插件架构

OpsMesh 提供事件、消息、规则、权限、依赖注入和插件宿主。功能插件通过原生 OpenAI Agents SDK 定义智能体，并按需依赖通道、执行、数据库和文件系统插件。

## 源码与发行包

```text
src/opsmesh/
├── app.py / cli.py                # 宿主与命令行
├── drivers/                       # 适配器、逻辑连接、生命周期入口
├── adapters/                      # Channel / Adapter / Event
├── dispatch/、matchers/、messages/
├── dependencies/                  # Dependent 与 Context
├── permissions/、rules/
├── plugins/                       # 加载、归属、调用持有与本地记录
└── config.py / lifecycle.py
packages/
├── channels/src/                  # opsmesh.plugins.channels
├── execution/src/                 # opsmesh.plugins.execution
├── database/src/                  # opsmesh.plugins.database
├── filesystem/src/                # opsmesh.plugins.filesystem
├── knowledge/src/                 # opsmesh.plugins.knowledge
└── cron/src/                      # opsmesh.plugins.cron
examples/
├── common/conversation_support/
├── chat/example_chat/
└── sandbox/example_sandbox/
tools/                            # 离线数据维护
```

核心和六个插件分别构建 wheel 与源码包，同一个 tag 的 Release 一并发布。Linux 框架包携带宿主运行环境，插件运行包按需部署。插件安装模块为 `opsmesh.plugins.<name>`，entry point 提供 `require("channels")` 等短名；持久记录使用完整模块路径。

## 职责与依赖

| 组件 | 负责 | 调用者负责 |
|---|---|---|
| 核心 | 插件加载、依赖、配置记录、生命周期、调用排空、事件分发 | 下载和安装包、业务数据、业务策略 |
| Driver | 适配器注册、逻辑连接索引、任务组和阶段回调 | 网络服务与协议实现 |
| channels | 认证、协议收发、事件转换、回复与平台群操作 | Agent 选择、会话、群管理策略 |
| execution | 原生 Runner、流清理、SDK 回调与 Trace | Agent/MCP/沙箱构造、Session、审批、用量存储 |
| database | PostgreSQL engine、会话工厂、事务和插件迁移 | ORM 模型、业务查询与事务边界 |
| filesystem | 插件文件空间、文件操作、ZIP 与原子发布 | 技能/工具/Agent 元数据、资源索引与安装 |

核心使用 Python 与框架依赖，SDK、SQLAlchemy、FastAPI 和机器人 SDK 由相应插件引入。execution 可独立使用，不依赖 database、filesystem 或 channels。

`require()` 确定加载依赖，返回插件模块；`Depends()` 确定一次处理的参数和资源作用域。原生类型通过正常 Python 导入使用。

## 加载、配置与保护

host.json 保存 framework、environment 和 data_dir。宿主的 SQLite 保存插件模块位置、配置和启动意图。PluginMetadata.config 声明插件自己的 Pydantic 配置类型，插件启动时通过 get_plugin_config 读取。

`load_plugin` 导入声明，`start_plugin` 创建资源，`stop_plugin` 排空并释放资源，`revoke_plugin` 在依赖和保护检查后撤销声明。停止后的静态声明保留，启动期注册随资源清理，重启资源时重新创建。

核心插件由宿主记录的 protected 标记指定，同时满足 enabled、autoload、required。普通管理操作禁止停止、停用、资源重启或撤销核心插件；宿主退出仍正常释放其资源。save_config 保存候选值，apply_config 单独执行资源重启。详见[插件配置](plugin-configuration-design.md)和[插件生命周期](plugin-lifecycle-design.md)。

源码插件是受信任的进程内 Python 代码。归属和路径约束用于可靠管理，并不隔离恶意 Python；代码和依赖更新通过宿主重启生效。

## 事件、身份与回复

统一消息入口、多次回复、续接筛选和通道对话隔离由 channels 实现，具体协议见通道文档。

```text
HTTP / QQ / 钉钉
→ 通道认证与统一消息协议解析
→ Event + Message + 可信调用者 Context
→ Framework Permission / Rule / Matcher / Dependent
→ on_message / on_command 处理器
→ Matcher.send / Matcher.finish
→ 原 Channel 编码回复
```

channels 定义统一的 `POST /plugins/{plugin_id}/messages` 消息协议。调用者发送 `MessageRequest`，channels 认证身份、校验文本/附件、构造 Framework Event/Message，然后只分发已声明的 `on_message/on_command` Matcher。处理器不依赖 Python `return` 作为回复，使用 `Matcher.send()`、`Matcher.finish()`、`Matcher.pause()` 和 `Matcher.reject()`；channels 再按 HTTP、QQ 或钉钉协议编码消息。会话、知识库和计划管理通过命令或消息处理器进入，插件不为每个业务操作定义 REST 路由。协议约定见[通道与执行回调](channels-and-callbacks.md)及[多模态消息](multimodal-messages.md)。

框架仍保留通道内部健康检查和明确的非消息管理路由能力，但这类路由不承载智能体会话、知识库或 cron 业务。处理器返回值不是通用回复契约；需要给调用方返回结构化信息时，由 channels 在消息发送协议中编码，不让业务处理器返回 FastAPI Response。

HTTP 一次响应承载多条有序消息。普通 send 表示一条完整消息；模型增量通过 channels 的输出操作按 message_id 关联，HTTP SSE 展示增量，机器人默认聚合后发送。非流式回复先在依赖有效期内物化发送数据，清理成功后返回；流式持有资源直到消费和清理完成，再发送请求完成标记。finish 仅结束当前 Matcher，不直接结束 HTTP。结构化结果作为出站 data 段提供，机器人呈现同一消息的可读内容。

conversation_id 是通道对话区分符，channels 结合可信用户、连接及插件构造 Event.get_session_id()；SDK Session 的选择映射属于聊天插件。入口同时允许其已开放 Matcher 的受限临时续接，等待不跨重启保存，也不跨请求持有资源。命令兜底与普通聊天排除规则复用框架解析，未知或未授权命令不进入模型。

框架通过 Matcher.origin 保留临时 Matcher 的原始关联，并在停止和撤销时清理续接。入口复用 matcher_filter、Permission、插件注册归属与 DispatchResult.suspended/errors。HTTP/SSE 和增量编码属于 channels，SDK Session 选择与并发属于聊天插件。资源停止再启动也会清掉旧续接。

channels.UserContext 是冻结的 id、name、groups、roles，集合使用 frozenset。Context() 注入原对象；身份来自认证结果，正文无法覆盖。会话 ID、执行 ID、数据库及 MCP 客户端由调用插件单独管理。Framework permissions.User 是权限匹配器。

Driver 管理框架容器与逻辑连接；Adapter 实现协议；Channel 表示具体账号或逻辑端点。send/call 由 Channel/Adapter 提供。API、QQ 各持有自己的监听服务，钉钉持有官方 Stream 连接。

## 调用与资源生命周期

框架 plugin_call 持有调用插件及其依赖直到结果、流和依赖 finalizer 全部完成。Matcher 检查使用独立依赖作用域，未命中立即清理；命中后的资源可转交实际响应所有者。独立后台任务取得自己的调用占用。

get_current_plugin 统一解析归属，plugin_scope 为回调建立声明插件作用域。用户 Context 与插件归属各有用途。停止先关闭新工作准入，保留已有调用的发送连接；排空后按依赖逆序关闭消费者与提供方。失败保留未释放对象，后续停止可以重试。API 已受理请求的跨任务分发通过明确的准入凭据独立记账；SDK 清理超时但仍在运行的任务继续持有外层资源。完整契约见[插件生命周期](plugin-lifecycle-design.md)。

## 原生执行与观测

execution.run 返回原生 RunResult；stream 是异步上下文管理器，提供 RunResultStreaming，消费和清理在同一作用域。调用方直接读取 final_output、interruptions 和 context_wrapper.usage。Agent、SandboxAgent、Session、RunState、RunConfig、原生 hooks 均由调用方提供。

| 回调 | 语义 |
|---|---|
| before_run | 执行前检查，异常阻断 |
| on_agent_start/end、on_llm_start/end、on_tool_start/end、on_handoff | 对应原生 SDK hook，保留异常行为 |
| on_run_end | 结束观察，普通失败记录日志；调用取消仍传播 |

参与回调的是调用插件及其 require 闭包。回调和依赖 finalizer 在各自声明插件的作用域执行；已受理运行在 STOPPING 中仍收到通知。Matcher 提供执行归属，后台调用显式指定 plugin_id。

Trace 支持 disabled、local、langfuse。TraceContext 的 user_id、session_id 和 tags 用于观测关联；diagnostics() 返回目的地和有界本地摘要。敏感内容采集由插件配置限制，导出失败不重跑 Agent。详见[Langfuse 追踪](langfuse-tracing.md)。

Langfuse 诊断另含实际 exporter 批次结果、成功/失败计数和最近导出时间；排队不记作导出成功。关闭超时或等待者取消时保留资源与同一个清理任务，后续停止继续等待；只有清理成功才清空引用。后台同步函数不可强制杀死，框架等待其线程退出后再释放依赖，取消或超时结果随后传播。

## 数据与文件

数据库迁移通过 PluginMetadata.extra["database"] = DatabaseSchema(directory, head) 声明。database 在加载时收集声明，在启动前检查版本；每个插件使用独立版本表，运维命令在事务与 advisory lock 内执行升级。ORM 模型和原生 MetaData 由调用方持有。每次资源启动读取当前配置并新建 engine/sessionmaker。详见[数据库迁移](database-migrations.md)。

filesystem 在一个配置根下分配 data、cache、config，各独立插件使用完整规范模块名作为一个目录段，子插件与父插件平级。普通文件可直接供 SDK LocalFile/LocalDir 使用。路径、链接和 ZIP 检查限制文件逃逸；写入使用原子替换，publish_directory 向不存在的目标发布。

技能和工具索引、包来源、启用状态及业务授权属于功能插件。原始技能目录与沙箱可写工作区分开，工作区改变不自动回写资源。

knowledge 依赖 database 和 filesystem，拥有知识库、原文、分块和索引租约；编码使用固定 BGE 模型目录，检索使用 pgvector。调用插件选择 KnowledgeBase 句柄，将其原生 tools 交给 Agent。cron 仅依赖 database，保存计划定义并用原生 APScheduler 计算触发时间，到期后分发给声明插件的 Matcher；它不选择 Agent，也不生成用户或会话。两者均无默认网络监听，公开对象与示例见各自包说明。

## Agent、MCP 与会话

智能体插件直接定义 SDK Agent/SandboxAgent、工具、Manifest 和技能。SDK 工具需要框架依赖时，调用方显式使用 Dependent 并负责其清理。MCP 使用原生服务器类型，拥有它的插件负责凭据、连接和关闭。

Session 可使用 SDK SQLAlchemySession；会话索引、用户归属和并发策略由功能插件补充。SDK interruptions/RunState/approve/reject 负责工具审批状态，业务插件决定授权及保存方式。重启重新构造对象，会话历史的持久性由选择的 Session 决定。

## 验证

定向验证覆盖：调用排空、取消、失败重试、核心保护、响应/流清理、回调归属、原生 SDK 审批、数据库迁移、文件边界和协议映射。确定性模型与模拟协议测试独立于真实 PostgreSQL、机器人、模型供应商、Langfuse、沙箱和 Linux 发行包验收。
