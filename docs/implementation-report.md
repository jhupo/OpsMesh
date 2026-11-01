# opsmesh 实施与验收记录

2026-11-01 架构审查整改：完成 Run/流事件索引与显式迁移、审核记忆准入、显式依赖、类型化执行资源、运行时与后台职责拆分，以及能力写入口收拢。全量回归 999 passed、32 skipped，另补充持久 Run 策略回归 1 passed，临时 PostgreSQL 10 passed；Ruff、mypy（404 源文件）、仓库检查和构建通过。本轮未部署远端，详细变更和升级限制见[架构重构记录](reviews/2026-11-01-architecture-refactor.md)。以下保留此前各轮结果。

队列批处理修正：取消固定 24 条聊天上限，当前已审核积压按估算上下文预算合并，一轮结束立即继续消费；超出预算的消息保留到下一批。相关回归 249 passed、1 skipped；服务器真实 PostgreSQL 独立收件箱的 60 条积压合并受控测试通过，QQ 连接与生产队列正常。详见 [QQ 助手设计](design/qq-assistant.md)。

2026-11-01 最新 QQ 助手增量：普通文本默认接话、稳定成员标识、同群 Session、确认投递后知识与 Memory 生成、禁用工具的独立图片分析通道，见 [QQ 助手设计与验收](design/qq-assistant.md)。全量回归 985 passed、31 skipped，Ruff、mypy（387 源码文件）、仓库检查与构建通过。已部署并确认真实 QQ 群文字投递和 Memory 生成成功；平台图片 Run 实测成功，真实群图片待用户后续验收；当前网关语音两接口返回 404，视频理解尚未接入。后文保留各轮历史记录。

本日最新增量：默认与个人模型、加密凭据、显式在线更新、Jev 仅审核/Agent 决定回复、群 Session 共享、CLI 职责拆分及项目队列备份恢复已实现；测试和真实服务结果见[补齐与架构复核](reviews/2026-11-01-platform-follow-up.md)。后文保留各轮历史验证范围。

2026-11-01 开发增量：QQ 官方 WebSocket、群内机器人身份映射、管理员撤回与主动消息；
平台可将指定群的未映射成员委派给非管理员身份。[QQ 群工作空间示例](../examples/qq_community/README.md)
通过平台创建/发布一个沙箱 Agent，绑定上游人物 Skill、Jev 工具与原生知识库。
通用审核、聊天合并、主动参与及已投递问答归档由平台对话服务协调；示例不再维护
第二个 FAQ Agent、知识检索工具或知识/记忆文件镜像。此次审查的实际接口与缺口见
[源码审查](reviews/2026-11-01-source-review.md)。此处仅记录实现范围，真实部署和端到端结果
应以本次验收记录为准；旧测试数字保留其当时范围。

目录后续修正见[全源码职责审查](design/source-layout.md)：协议现归 `infrastructure/bots`，事务消息现有真实实现 `infrastructure/messaging/outbox.py`；本文保留各历史轮次的验证记录。


更新日期：2026-10-31（Asia/Shanghai）。项目：本地开发工作副本。版本：`0.1.0.dev0`（Alpha）。本记录描述实际实现、本地与授权Linux测试环境验收；完整目标设计与当前可用功能分别维护，不能把目标文件树当作完成清单。

**本轮平台补齐：** 已完成原审查F01—F05及日常管理闭环；新增模块、执行语义和验证见[补齐记录](platform-completion.md)。下面既有SDK/环境结果仍保留原验证范围。

此前平台补齐验收：Windows/Linux各 **494 passed，22 skipped**；Ruff、mypy（323源码文件）、仓库/发行包检查和构建通过。隔离API已升级，PostgreSQL54张表、17个查询及两组管理写入闭环通过。详见[平台补齐记录](platform-completion.md)。后续代码审查修复的验证以[审查修复记录](review-fixes.md)为准。

后续机器人协议分层、钉钉/QQ 官方实现、扩展生命周期和最新回归见[机器人适配器](design/bot-adapters.md)。本轮源码更新尚未重新部署到上述测试服务器。

## 目录与职责

采用Python src布局、模块化单体与异步事件驱动。HTTP、机器人、定时任务和代码插件调用同一组业务服务。Web暂留目录，身份范围为本地账号、部门和角色。

```text
opsmesh/
├── src/opsmesh/
│   ├── framework/       # Driver、Adapter、Bot、Event/Message、Matcher、DI、插件、Agent合同
│   ├── modules/         # 17个业务域：公开api/events与私有internal
│   │   ├── accounts/        # 本地账号、部门、角色、授权
│   │   ├── agents/          # Agent定义、工厂、版本、发布
│   │   ├── models/          # 模型档案与密钥引用
│   │   ├── bots/            # 机器人账号、外部身份、Agent绑定、投递
│   │   ├── scheduling/      # 持久计划、Cron、补跑、并发与执行记录
│   │   ├── triggers/        # 事件/Webhook规则、去重及Run提交
│   │   ├── notifications/   # 订阅、收件箱、提醒与投递状态
│   │   ├── operations/      # Outbox恢复与插件部署策略
│   │   ├── sessions/        # 业务会话、历史、执行顺序
│   │   ├── capabilities/    # 工具、Skill、MCP、知识目录与网关
│   │   ├── runtime/         # Run状态机、审批、恢复、SDK后端
│   │   ├── execution/       # 本地/Docker执行提供商与资源限制
│   │   ├── workspaces/      # 工作目录、装配、制品、快照
│   │   ├── memory/          # 独立记忆空间、提取/整合任务
│   │   ├── usage/           # 用量、价格、预算、费用
│   │   ├── audit/           # 操作/安全审计
│   │   └── configuration/   # 平台配置与运行准入
│   ├── entrypoints/     # 按领域组织HTTP路由、单Run Worker
│   ├── bootstrap/       # 应用组装、授权/资源/记忆桥接、Worker协调
│   ├── infrastructure/  # SQL/迁移/Outbox、Redis、本地/S3、日志/指标/追踪
│   ├── maintenance/     # 完整离线备份、摘要验证与空目标恢复
│   ├── dependencies.py  # 插件通过Depends获取公开业务服务
├── tests/               # 框架、平台、SDK、HTTP、资源、费用、基础设施、机器人
├── docs/                # 设计、覆盖矩阵、命名、运维、开发、API说明
├── examples/            # 可运行的代码插件示例
├── migrations/          # 显式schema版本与升级说明
├── deploy/              # Dockerfile、Compose与Kubernetes API/Worker/迁移/PVC
├── web/                 # 门户/控制台预留
└── .github/             # CI与issue/PR模板
```

模块拥有自己的数据和规则，跨模块不导入`internal/`；`bootstrap/`作为组装根连接具体实现。框架不依赖业务模块，SDK原生类型与导入仅存在于对应后端。静态检查覆盖绝对导入、相对导入与成员导入。完整真实文件树见[源码树](reference/source-tree.md)。

## 命名与函数拆分

公共框架入口使用`OpsMeshApplication`、`OpsMeshSettings`、`OpsMeshDriver`、`OpsMeshAdapter`、`OpsMeshBot`、`OpsMeshEvent`、`OpsMeshMessage`、`OpsMeshMatcher`、`OpsMeshPlugin`、`OpsMeshAgentBackend`等名称。

业务类型使用完整语义，如`AgentSpecification`、`ConversationSession`、`UserPrincipal`、`ModelProfile`、`WorkspaceSnapshot`。具体实现使用`OpenAIAgentsBackend`、`AgentDefinitionCatalog`、`PersistentAgentRuntime`等名称；不机械给所有类加前缀，也不保留大量旧名字alias。规范见[命名约定](development-naming.md)。

OpenAI后端的 `backend.py` 只编排图构建、恢复、Runner执行、结果转换与清理；模型、输出、普通/Hosted/原生工具、委派、护栏、流、检查点、压缩、沙箱生命周期/子环境/记忆、追踪、错误与用量分别维护。`AgentPublicationValidator` 负责发布准备和验证，`AgentExecutionBridge` 负责执行上下文和资源，`RunResultFinalizer` 负责制品、历史和终态计量；服务构造按能力、运行时和传输分阶段。拆分以职责和资源所有权为依据，不以单个函数行数作为完成标准。

## 已落地的主要能力

| 范围 | 当前实现 |
| --- | --- |
| NoneBot风格框架 | Driver生命周期/HTTP/WS/定时任务；Adapter/Bot/Event/Message；Matcher优先级、传播、多轮；Depends/Annotated/缓存/yield/上下文；插件元数据、require、归属、失败回滚和禁用 |
| 插件扩展 | Agent工厂、后端、代码工具、动态指令/Prompt、类型化输入输出、工具结束策略、护栏/钩子、native工具ports、sandbox provider、embedding和网络入口；HTTP仅引用已部署能力 |
| 用户与管理 | 本地密码/令牌、部门/角色/资源授权；Agent草稿、不可变版本、克隆/启停、模型档案与环境密钥引用；无企业SSO依赖 |
| OpenAI SDK | 真实0.24.0 Runner、流/HTTP/WebSocket、Pydantic/dataclass/列表输出、handoff/as-tool、自定义输入/结束、四阶段护栏、hooks、Hosted MCP审批、原生Shell/ApplyPatch/Computer、会话压缩与计量 |
| Run可靠性 | CAS/attempt/租约/容量/会话占用、审批与重启恢复、取消与过期恢复、收尾屏障；发布配置与Run覆盖分离、有效配置指纹、子产物持久化、并发失败清理 |
| 资源与工具 | 授权/Schema/幂等工具网关；Git固定commit Skill、eager/lazy装配；不可变脚本/wheel、隔离venv和环境执行；MCP stdio/HTTP/SSE；向量/词法/RRF混合检索 |
| 环境与文件 | Local/Docker命令Provider，独立Python/Docker/Kubernetes Job Run Worker；SDK原生沙箱、公开provider、cwd/manifest/限制、snapshot/session恢复、独立子环境和制品所有权、清理重试 |
| 文件记忆 | namespace、来源与两阶段任务；原生Memory逐请求计量/限时生成/捕获前flush；live update/CAS/幂等；审批恢复回调稳定；保留策略与维护GC |
| 持久化 | SQLite/PostgreSQL、显式迁移/结构漂移拒绝、事务Outbox/租约领取/重试/死信；Redis原子限流和租约；workspace持久锁/fence |
| 观测与审计 | JSON日志、固定低基数计数/仪表/时长直方图、跨进程DB指标快照、W3C跨HTTP/Outbox/Run、OTLP批处理/有界关闭、独立操作审计 |
| 成本 | Token/reasoning/Provider用量、Hosted及计算/存储资源事实、版本单价/FX、缺价/未知、幂等补价、预算预占结算、供应商账单行对账；不会用trace代替计费 |
| 接入与工程 | CLI init/migrate/check/serve/worker/storage-gc/backup/backup-verify/restore，HTTP/SSE；OneBot签名HTTP、主动/反向WS、重连、身份/会话路由、NapCat文件；持久调度；开源文档、CI、构建和部署示例 |

### 工作目录和文件装配

Agent绑定工作区与资源引用，Run开始时按权限解析版本和装配文件。Skill文件按策略写入工作区；lazy Skill在加载后同步至活跃沙箱。普通代码工具和远程MCP是注册能力与连接配置，不在创建Agent时盲目复制服务代码、密钥或整个Python运行环境。工作区内容、Run检查点、沙箱会话状态和Snapshot分别保存。

WorkspaceService使用持久租约、递增fence和文件操作期间的数据库锁，保证通过该API的并发修改与快照一致性。可信宿主代码直接写磁盘不受这个API锁保护；隔离执行仍需独立工作区和提供商约束。

## 实际验收

基线：OpenAI Agents SDK0.24.0、uv0.13.5；Windows Python3.13.7；独立Ubuntu26.04测试服务器Python3.14.4、Docker29.1.3。默认测试不发付费请求；真实模型测试使用用户授权gateway与gpt-6.1-sol。

| 检查 | 实际结果 |
| --- | --- |
| 平台/插件职责修正后的Windows完整pytest | 389通过、22跳过，207.63秒；含Bot绑定管理、无插件调用链、调度迁移与导入边界回归 |
| 前一基线Linux完整pytest | 374通过、22跳过，87.48秒；本次绑定/调度改动尚未重新部署到该环境 |
| 静态检查 | Ruff/格式、Mypy strict 260源码文件、模块边界、包内容及文档检查通过 |
| 真实模型 | 两条平台集成通过：工具审批→关闭/重建应用→恢复→只执行一次；流式结构化输出/事件/历史/rollout/usage。未配置单价，成本为unpriced |
| Linux SDK沙箱 | UnixLocal、Docker原生Memory、子沙箱/来源组合11通过；修复live model包装器被SDK身份序列化遍历的问题 |
| Docker命令执行 | 真实2项通过：CPU/内存/PID cgroup值、network=none、只读根、持久工作文件、timeout/取消停止容器并清理 |
| 远程Docker | mTLS真实端点与资源限制、文件归档通过；首次6项通过，后增加失败清理的离线回归 |
| S3 | SeaweedFS4.51真实服务+PostgreSQL16：并发不可变写入、快照、备份/恢复、GC通过 |
| 完整平台恢复 | 原生pg_dump/pg_restore加S3+活动工作目录恢复到新数据库/新目录/新前缀；账号登录、只读文件、空目录、快照/blob及非空目标拒绝通过 |
| Kubernetes | K3s1.34.1真实Pod：双协调器、完成、跨Pod审批、新Job恢复、取消、缺镜像deadline失败、日志回收与禁止读Secret，综合用例77.02秒通过 |
| HTTP与Redis7 | 独立API健康/就绪/登录/模型/Agent/授权metrics与79条OpenAPI路径；100管理请求并发10完成于1.30秒；Redis40次争抢准确放行7次、过期owner无法续租/删除新租约 |
| 既有协议/数据库 | PostgreSQL17.11迁移/CAS/Outbox等8项已验；本机MCP/OneBot/Responses WS/OTLP真实协议服务器回归保留 |

HTTP小规模请求检查不代表容量基准；单VM上的K3s不能证明跨主机高可用。标准Dockerfile构建的系统软件源下载在测试机停滞，实际Job验收使用已锁定依赖的Python3.14验证镜像；没有把它描述为标准发布镜像已通过。待部署网络稳定后仍需标准镜像构建和供应链审核。

### 目录清理

`framework/adapters/`直接包含Adapter/Bot/Event/Message；`framework/agents/`直接包含定义、执行接口、上下文、事件、模型设置和工具绑定。`infrastructure/messaging`等8个只有说明字符串的占位包源码已删除，持久Outbox和scheduler实现保留。自动审批检查拒绝删除旧目录壳，未继续清理；目录可能仍可见，但不再包含/打包虚构模块。`scripts/check_repository.py`拒绝新的空叶子包，`scripts/generate_source_tree.py`依据文件重新生成目录文档。

### 平台与插件职责修正

机器人/频道到Agent发布版本的绑定由`modules/bots`拥有。服务补齐查询、列表、解绑，并统一校验管理权限、Agent版本/启用状态和绑定者执行权限；原HTTP专有版本校验下沉到服务。入站仍检查消息发送者权限；解绑不取消已经提交的Run，并发新绑定不会被旧解绑删除。

进程内定时器移到`framework/scheduling.py`，持久任务移到`modules/scheduling/api.py`与`internal/service.py`；旧调度兼容导出已删除。`Depends(get_bots)`和`Depends(get_scheduling)`提供同一平台服务。新增测试实际验证`plugins=()`时Bot与定时事件都能创建并执行Agent Run，而非仅断言接口存在。仓库规则禁止核心模块、入口与组装根静态依赖业务插件。

### 核心回归证据

- [Bot绑定服务](../tests/bots/test_bindings.py)、[绑定HTTP管理](../tests/http/test_bot_bindings.py)、[无业务插件的Bot到Agent调用链](../tests/platform/test_bot_bindings.py)、[无业务插件的持久任务及DI](../tests/platform/test_scheduling.py)。
- [SDK与审批](../tests/runtime/test_openai_backend.py)、[类型化扩展](../tests/runtime/test_advanced_extensions.py)、[原生工具](../tests/runtime/test_native_tools.py)、[Hosted MCP](../tests/runtime/test_hosted_mcp.py)、[压缩与WebSocket](../tests/runtime/test_compaction_and_websocket.py)。
- [单次覆盖](../tests/runtime/test_run_overrides.py)、[HTTP覆盖授权](../tests/http/test_run_overrides.py)、[子产物与指标](../tests/runtime/test_execution_metadata.py)、[请求计量](../tests/runtime/test_usage_observation.py)。
- [原生记忆](../tests/runtime/test_sandbox_memory.py)、[独立子沙箱](../tests/runtime/test_isolated_sandbox.py)、[来源授权](../tests/runtime/test_sandbox_sources.py)、[记忆审批恢复](../tests/platform/test_live_memory_resume.py)。
- [Git Skill](../tests/resources/test_git_skills.py)、[脚本工具](../tests/resources/test_script_tools.py)、[混合检索/MCP](../tests/resources/test_retrieval_mcp.py)、[机器人协议](../tests/adapters/test_qq_gateway.py)。
- [PostgreSQL](../tests/infrastructure/test_postgresql.py)、[Redis](../tests/infrastructure/test_cache.py)、[OTLP](../tests/infrastructure/test_telemetry_export.py)、[资源费用/FX/对账](../tests/usage/test_resource_billing.py)、[内容GC](../tests/resources/test_content_gc.py)。

本次修复了真实组合场景中的问题：SDK start/end hook在不同Task中导致重复unknown计量；记忆工具attempt身份变化破坏检查点恢复；补价提前释放仍有未知成本的预算；子Agent产物只留在内存；并发子调用失败后资源未排空；独立Worker退出丢指标；traceparent变化破坏业务幂等。对应测试验证实际Runner或真实进程/数据库行为，不只检查接口存在。

## 实现范围与环境边界

- 早期6个插件占位名混淆了平台能力和可选命令。Agent管理、Bot/频道绑定、会话运行、知识检索和持久调度已有平台服务；插件仅增加命令、领域规则和提供商。通用通知中心和工作流仍缺少平台实现。持久调度现归`modules/scheduling`，旧调度兼容导出已删除。详见[平台与插件职责](design/plugins.md#内置插件的实际状态)。
- Web按要求仅预留；Claude、Realtime/Voice、跨SDK原生状态迁移不在本次实现。完整88项逐条边界见[覆盖表](design/sdk-coverage.md)。
- 实际支持本地/S3存储、RemoteDocker mTLS和逐Run Kubernetes Job；不会把通用Provider注册称作每个云厂商都已适配。
- 已验证真实模型的两条核心流程。随后网关返回余额不足，没有继续发收费请求；Hosted/远程Prompt模板/真实Memory费用等供应商分支仍需对应额度和资源验收。
- 未向真实外部机器人账号发送消息，未发布Git远程仓库/PyPI。测试服务器上有独立HTTP部署，原已有服务保留。
- 单机验证不承诺跨节点容灾、Redis Cluster/Sentinel、长期负载和RTO/RPO。默认hash embedding不是训练语义模型。
- 未提供供应商单价时保留unpriced；外部副作用和消息投递不宣称恰好一次。
- 两次目录清理命令被自动审批检查以`blocked by policy`拒绝。逐文件迁移与占位源码删除成功，旧空目录壳保留；此前停止的PostgreSQL调试临时目录也保留。

## 启动与继续开发

```powershell
# 在本版仓库根目录执行
uv sync --locked --dev --extra openai --extra s3
uv run --extra openai opsmesh init
uv run --extra openai opsmesh serve
```

`init`交互设置本地管理员密码。打开`http://127.0.0.1:8000/docs`；按[HTTP使用指南](runtime-api.md)登录、配置模型和能力、发布Agent、创建会话并提交Run。模型密钥通过环境引用提供；`.env`需显式用`uv run --env-file .env`加载。

工程包含MIT许可证、贡献/安全/支持/行为规范、Git与编辑器规范、锁文件、类型声明、pre-commit、CI、模板、部署和示例。下一步按[当前状态与路线](roadmap.md)实施外部验收及扩展；保留已验证的模块边界和命名规则。
