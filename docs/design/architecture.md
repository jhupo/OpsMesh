# opsmesh 架构与目录职责

本项目采用 Python `src` 布局和模块化单体。HTTP、Bot、定时事件与插件共享同一套公开业务服务；API 进程、协调 Worker 与单 Run Worker 可以独立部署。身份为本地用户/部门/角色，不引入 OIDC 或企业 SSO。Web 目录只预留，首个 Agent 后端为 OpenAI Agents SDK，Claude 后续实现相同中立合同并声明能力差异。

此文描述当前代码边界。完整实际文件清单见[源码树](../reference/source-tree.md)，SDK逐项实现和测试见[覆盖表](sdk-coverage.md)。目录不再列出尚不存在的虚构 repository/tables/http 文件，也不把每个小函数拆成一个服务。

## 项目结构

```text
opsmesh/
├── pyproject.toml / uv.lock             # 构建、依赖与工具约束
├── README.md / README.en.md             # 安装与功能入口
├── src/opsmesh/
│   ├── __init__.py / __main__.py / _version.py
│   ├── dependencies.py                 # 插件可Depends获取的公开平台服务
│   ├── framework/                      # 可复用机制，不依赖业务模块
│   │   ├── drivers/                    # 生命周期、HTTP/WS、任务与定时能力
│   │   ├── scheduling.py               # 进程内定时器，无业务存储依赖
│   │   ├── adapters/                   # Adapter/Bot/消息协议抽象
│   │   ├── agents/                     # Agent/后端/上下文/结果中立合同
│   │   ├── plugin/                     # 元数据、导入加载、require、注册归属
│   │   ├── dependencies/               # Depends解析、缓存、作用域与释放
│   │   ├── internal/                  # Matcher调用和事件上下文的私有实现
│   │   ├── matcher.py / rule.py / permission.py
│   │   ├── events.py / message.py / params.py
│   │   └── errors.py / exception.py / typing.py
│   ├── modules/                        # 业务所有权；公开api/events，私有internal
│   │   ├── resource_contracts.py        # 跨域资源引用/可用性公共值对象
│   │   ├── accounts/                   # 本地用户、部门、角色和授权
│   │   ├── agents/                     # 草稿、工厂、发布版本、克隆与启停
│   │   ├── models/                     # 模型档案、版本与凭据引用
│   │   ├── bots/                       # 机器人账号、身份映射、Agent绑定、消息投递
│   │   ├── scheduling/                 # 持久计划、Cron、补跑、并发与执行记录
│   │   ├── triggers/                   # 事件/Webhook目标、输入映射、去重与历史
│   │   ├── notifications/              # 订阅、收件箱、模板、提醒及投递状态
│   │   ├── operations/                 # Outbox运维、插件部署策略/实例同步
│   │   ├── sessions/                   # 用户可见会话、消息历史、执行占用
│   │   ├── capabilities/               # 工具/Skill/MCP/知识和授权工具网关
│   │   ├── runtime/                    # Run状态、审批、恢复、后端和模型历史
│   │   ├── execution/                  # 命令执行、环境提供商与限制
│   │   ├── workspaces/                 # 文件、Manifest、内容摘要、制品、快照
│   │   ├── memory/                     # namespace、生成任务、CAS更新与保留
│   │   ├── usage/                      # 用量事实、价格、汇率、预算、对账
│   │   ├── audit/                      # 操作证据与查询
│   │   └── configuration/              # 平台设置、运行准入策略
│   ├── bootstrap/                      # 组装根与跨域调用链
│   ├── entrypoints/                    # CLI、HTTP/SSE、Run Worker入口
│   ├── infrastructure/                 # 具体技术实现
│   │   ├── bots/                       # dingtalk、qq、onebot协议实现
│   │   ├── workers/                    # 子进程、Docker、Kubernetes Run Worker
│   │   ├── database/                   # 连接、迁移、事务、CAS记录与资源可用性存储
│   │   ├── messaging/                  # 事务Outbox、领取租约、重试与死信
│   │   ├── coordination/               # 容量准入、Redis租约与请求限速
│   │   ├── storage/                    # 本地/S3内容存储及blob备份
│   │   ├── telemetry/                  # 日志、指标与OpenTelemetry
│   │   └── secrets.py                  # 环境密钥解析
│   ├── maintenance/                   # 离线整站备份、验证与恢复
│   └── plugins/                        # 可选扩展命名空间、旧调度路径兼容导出
├── tests/                              # 模块、跨域、SDK和显式环境测试
├── examples/                           # 插件公开API示例
├── docs/                               # 设计、API、命名、运维与验证记录
├── .env.example / migrations/           # 实际环境配置示例与schema版本说明
├── deploy/                             # Docker、Compose、Kubernetes示例
├── web/                                # 门户与管理界面预留
└── .github/                            # CI、issue与PR模板
```

## 目录与实际代码一致

`framework/adapters/`直接保存`adapter.py`、`bot.py`、`event.py`和`message.py`。`framework/agents/`直接保存`specification.py`、`backend.py`、`context.py`、`state.py`、`event.py`、模型设置与工具绑定合同。二者的`__init__.py`只整理公开导出，不再把全部实现藏在平行的`internal/adapter`或`internal/agent`目录。

当前 `infrastructure/messaging/outbox.py` 是真实的可靠消息实现，由 `application/coordinator.py` 组装消费链；它替代了早期没有实现的消息占位目录。持久调度归 `modules/scheduling/`，进程内定时器归 `framework/scheduling.py`；`plugins/scheduler/` 仅兼容旧导入路径。Web 按需求预留，其他 Python 叶子包不允许只有占位说明。

本轮对全部 355 个原有 Python 源文件核对路径、符号和依赖，并调整协议、Worker、数据库、通知内部实现等职责。逐项理由见[目录职责审查](source-layout.md)，当前文件级清单见[文件职责表](../reference/source-responsibilities.md)。公开 `management.py` 是带授权的业务用例入口；它与 `internal/service.py` 的可信工作器/持久化实现具有不同调用权限，不能只为目录对称而一起隐藏。

## 所有权与公共接口

| 所有者 | 拥有的数据及规则 | 不承担的相邻职责 |
| --- | --- | --- |
| accounts | 密码/令牌、本地主体、部门/角色、资源授权 | 平台协议账号归 bots；模型secret只是引用 |
| agents | Agent 草稿、不可变发布版本、代码工厂与发布验证 | 实际调用、审批状态归 runtime |
| models | 模型配置版本、endpoint/secret_ref、模型兼容信息 | 价格和账本归 usage |
| bots | 外部账号到本地主体映射、Bot连接、频道到Agent版本的绑定、接收/发送幂等 | 协议抽象归 framework；会话持久记录归 sessions |
| scheduling | 持久计划、执行身份、授权、启停、领取租约、TimerEvent | 通用进程内定时器归 framework；具体日报/巡检规则可由插件提供 |
| triggers | 外部事件/签名Webhook规则、首次输入快照、投递去重与Run关联 | 不订阅所有内部Outbox；不做通用工作流 |
| notifications | 订阅、模板、收件箱、提醒、Bot投递及不确定结果 | Bot投递归bots、协议归infrastructure/bots；不从采样trace推导业务事实 |
| operations | 人工死信恢复、部署插件清单/策略、实例同步状态 | 不提供在线安装任意代码 |
| sessions | 用户可见消息、会话归属、单会话逻辑执行权 | SDK模型输入历史与RunState归 runtime |
| capabilities | 工具目录、解析、每次授权/校验/调用记录；Skill/Git/脚本/MCP/知识 | 文件制品归 workspaces；实际命令环境归 execution |
| runtime | Run生命周期、attempt/租约、审批/恢复、有效配置指纹、模型历史 | 不内置用户账户或HTTP路由 |
| execution | Local/Docker命令进程、超时/取消/资源政策 | 不导入OpenAI SDK；native SandboxClient由后端适配 |
| workspaces | 工作区、Manifest、只读位、blob、快照、制品与GC | 不生成Agent推理历史，也不拥有文件记忆语义 |
| memory | namespace版本、来源、两阶段任务、候选发布、更新/保留 | 使用工作区blob；原生SDK Memory由backend桥接 |
| usage | 模型/资源计量、版本价格/汇率、未知/缺价、预占/结算/对账 | 不从trace反推收费事实，不执行支付 |
| audit | 可追溯操作记录 | 调试日志和指标归telemetry |
| configuration | 准入、并发和平台配置版本 | SDK专有选项留在后端合同中 |

每个业务模块以 `api.py` 暴露服务 Protocol、请求与返回合同，`events.py` 暴露业务事实。大合同可再按职责拆为 `resource_contracts.py`、`overrides.py`、`extension_contracts.py` 等，并从公开入口重导出。`api.py` 是Python调用边界，不是Web路由文件。HTTP薄路由统一放在 `entrypoints/http/routes`，按业务域分文件，只做输入转换、入口鉴权和服务调用。

业务数据表由模块实例化并登记到数据库；当前通用存储使用版本化JSON聚合、CAS和事务Outbox，未假装每个实体都有专用ORM表。基础设施提供连接与原子操作，不决定业务状态机。生产大量查询应按实际负载增加索引/分页，而不是在目录中预先制造空仓储层。

`bootstrap` 是组装根例外：允许导入各模块具体实现，构造服务并绑定授权、模型、资源、记忆、用量与观测回调。业务模块之间只使用对方公开API；框架不能反向依赖模块；第三方SDK导入只允许对应backend。`scripts/check_repository.py` 检查这些静态边界。

同一检查也禁止平台模块、基础设施、组装根和HTTP入口静态导入`opsmesh.plugins`中的可选业务代码。应用可以按配置调用框架的`load_plugin`，插件再通过公开服务和Depends使用平台能力；基础服务启动不依赖某个插件的导入副作用。

## NoneBot 风格插件机制

平台能力通过模块公开服务提供，启用与否不取决于是否加载某个聊天插件。Agent管理、Bot绑定、会话、知识目录、持久调度、运行审批和消息投递属于平台；插件可以为同一服务提供命令、业务规则或扩展提供商。通知订阅、模板及提醒归notifications；触发器归triggers，部署策略与死信恢复归operations。通用工作流经确认不纳入范围。

复用的是 NoneBot2 的设计模式：导入即声明 Matcher/扩展，`on_message`/`on_command` 创建响应器，`Rule` 与 `Permission` 决定接收，Handler 使用 `Depends` 或类型参数注入，ContextVar 表示当前事件/响应器/执行上下文。`OpsMeshPluginMetadata` 描述插件；`require` 显式保证依赖加载，然后普通Python import获取公开服务。插件加载器追踪声明归属，失败时撤销注册，禁用后阻止后续调用。没有另造与这套声明体系竞争的万能 registrar。

Driver提供网络与生命周期，Adapter解析平台协议，Bot表示具体连接账号；统一Event/Message保留平台扩展信息。Matcher负责优先级、传播控制、多轮等待与依赖释放，不拥有持久Run状态。原生控制流 `finish/pause/reject` 不作为业务错误写入Run失败。

代码插件可以注册后端、模型/Agent工厂、指令、Prompt、类型化输入输出、工具结束策略、护栏、钩子、Computer/Shell/Patch提供商、沙箱provider、embedding和网络入口，并通过普通Provider使用平台服务。HTTP只能引用已部署代码和受限配置，不能上传任意Python或原生沙箱状态。扩展性来自这些有归属、可校验的接口，不能用任意字典透传冒充支持全部SDK。

详见[插件设计](plugins.md)与[插件示例](../../examples/custom_plugin/README.md)。此框架独立实现，不承诺现有NoneBot插件直接兼容。

## 后端文件职责

`modules/runtime/internal/backends/openai_agents/backend.py` 仅编排构建、恢复、Runner调用、结果转换及清理。其余按变化原因拆分：

| 文件组 | 职责 |
| --- | --- |
| agent_builder / models / config / output | 图构建、模型设置、RunConfig、输出schema/类型 |
| tools / delegation / extension_mapping | 函数工具、as-tool/handoff、公开扩展到SDK桥接 |
| hosted_tools / hosted_mcp / native_tools / computer | 供应商工具与本地执行接口、审批、凭据恢复 |
| hooks / guardrails / streaming | 回调、四阶段护栏、流事件转换 |
| state / serialization / compaction | 安全检查点、版本兼容、序列化与会话压缩 |
| sandbox / sandbox_lifecycle / sandbox_docker | 原生client/session创建、限制、快照和释放 |
| sandbox_delegation / sandbox_memory | 独立子环境的恢复图、生成计量与捕获前flush |
| usage / tracing / errors | 请求计量与资源观察、SDK→OTel、统一安全错误 |

对外稳定名称使用 `OpsMeshApplication`、`OpsMeshDriver`、`OpsMeshAgentBackend` 等；业务对象保持完整语义如 `AgentSpecification`、`WorkspaceSnapshot`，实现如 `OpenAIAgentsBackend`、`PersistentAgentRuntime`。不机械给所有类加前缀。文件名由包上下文提供含义；函数依职责拆分，不为行数生成无意义包装。详见[命名规范](../development-naming.md)。

## 执行与状态关系

1. Gateway或Bot适配得到输入，accounts解析主体并授权，sessions确认归属。
2. application解析发布Agent，合并类型化RunOverrides并校验工作区/来源权限；runtime持久化有效配置、原版本指纹和幂等身份。
3. Run领取attempt、会话占用和容量令牌；准备模型、预算、ToolManifest、资源及执行环境。每次模型/工具调用仍重新授权。
4. 每Run构建SDK图与执行上下文，HTTP增量转换为持久Run事件；工具返回值交给Runner，不通过事件再次执行工具。
5. 中断先保存原生安全检查点、批准项、沙箱快照与子产物，再提交waiting_approval。完成先保存final_outcome和产物，再提交completed；收尾重试不重跑模型。
6. 保存模型历史/会话展示消息、用量事实与Outbox；释放资源。未知外部副作用保留不确定状态，不自动重放。

`AgentBackend`、模型请求Provider、执行环境Provider是不同变化轴。后端选择其他厂商模型不等于适配其Agent SDK。共享的是不可变定义和服务，SDK Agent及可变上下文按Run构建，子沙箱按稳定tool_call_id管理。

`ToolManifest`描述可调用能力；`WorkspaceManifest`描述文件；SDK Capability可以增加工具、指令与生命周期行为。业务Session、模型历史、RunCheckpoint、沙箱session_state、WorkspaceSnapshot和MemoryNamespace版本独立保存。snapshot启动新环境，session_ref恢复已登记会话；文件快照不等于恢复OS进程或PTY。

## 存储、可靠性与运维

PostgreSQL/SQLite保存事实，Redis承担限流/租约等协调，blob和工作目录保存文件。活动工作区使用本地/共享POSIX目录；不可变blob支持本地与S3兼容存储。S3不替代活动目录的POSIX语义。Workspace API使用持久租约/fence；直接写磁盘的受信插件不会自动受到该锁约束。GC在维护模式二次扫描所有持久引用，先预览后删除无引用内容。

Outbox原子提交事实并租约领取，重试/死信支持恢复；模型请求、工具调用、预算和消息发送有各自幂等边界。外部副作用不能宣称恰好一次。错误出口统一 `OpsMeshError/ErrorInfo`，保留phase/provider/retry hints并隐藏原始供应商正文。

日志、Prometheus指标、OTel追踪、审计和收费分别负责不同证据。W3C上下文跨HTTP/Outbox/Run传递，OTLP队列与关闭等待有界；trace失败不改变业务事实。用量支持请求级未知费用、资源价格、显式汇率、补价和账单差异；预算预占不保证供应商实际花费永不超过估算。

Compose和Kubernetes部署位于deploy。Linux测试服务器已验证Docker命令隔离、SDK原生/远程mTLS沙箱、S3/PostgreSQL备份恢复、真实模型Responses和逐Run Kubernetes Job。外部机器人账号、供应商账单与多节点高可用仍须按部署验收。详细操作见[运维](../operations.md)，完整结果见[实施记录](../implementation-report.md)。

## 机器人协议实现

`framework/adapters` 提供 `OpsMeshBotAdapter`、中立配置与注册表；`infrastructure/bots/*` 实现厂商协议；`modules/bots` 只管理平台账号、身份映射、Agent绑定和投递状态。平台与框架不能导入具体适配器，具体适配器不能导入业务模块；Bootstrap 负责组合。详见[实际文件职责、配置和扩展合同](bot-adapters.md)。
