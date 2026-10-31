# OpenAI Agents SDK 能力覆盖与后端映射

核对日期：2026-10-30。本文逐项核对当前代码及测试，共 **88 项**（A14、S31、M8、C9、E8、G3、H8、O7）。目标设计中的类名和占位目录不再作为已实现证据；下面每行说明实际范围与缺口。

**当前具备可运行的文本 Agent 平台闭环，不等于完整实现 OpenAI Agents SDK。** 正式 `openai-agents==0.24.0` 已运行真实 SDK Runner 离线测试，并在授权测试环境完成真实 `gpt-6.1-sol` Responses 的工具审批、应用重启恢复、幂等、流式结构输出及用量记录。Linux Docker 29.1.3 已验证原生 Memory 两阶段生成、文件归档与清理；对应模型仍为确定性测试模型。PostgreSQL 17.11 已在临时 loopback 实例完成迁移、CAS、Outbox、容量租约和应用重启审批恢复验收；本机 MCP SSE、OneBot HTTP/主动与反向 WebSocket、OTLP HTTP collector 协议往返已经测试。Redis 已在本机 5.0.14.1 临时 loopback 实例完成原子限流和租约协议验收。第三方生产 MCP/机器人、云厂商与生产集群故障演练仍未验收；Claude Agent SDK 未实现，Web 仅预留。

状态含义：**已实现·离线验证**表示对应本地行为有测试；**部分实现**表示行内仍有缺项；**映射已实现·外部待验收**表示有真实调用代码，但没有外部系统成功运行的证据；**未实现**表示没有完整实现；**明确拒绝**表示不支持组合会报错。离线验证不自动覆盖该行提到的每个高级分支。

## 1. 版本与证据基线

- 正式可选依赖固定 `openai-agents==0.24.0`，完整传递依赖由 `uv.lock` 固定；基础框架不导入 SDK。见 [依赖声明][project]。
- 原设计参考开发源码提交 `cd26bbbc52847845fde73b9952b51e62e2bcbdc0`；正式 `v0.24.0` 对应 `17d994bd3045dae71acda676a4ef4762ed8ecd94`。两者不是同一提交，开发源码文档不能证明正式包已覆盖所有行为。[Release][release]、[源码基线][clone-project]
- [真实 SDK 离线测试][sdk-tests]实际经过 Runner、工具审批、RunState 序列化/恢复、动态指令、输出 schema、护栏、流、handoff、agent-as-tool 和请求用量钩子。UnixLocal 已通过真实文件、原生 Memory 和独立子环境验收，Docker 原生 Memory 已在真实 Linux daemon 运行。另有 [显式模型验收][live-model-tests]通过真实 Responses 服务；默认 CI 跳过这些需要外部环境或付费服务的集成用例。
- [数据库测试][database-tests]使用真实 SQLite 事务与两个数据库实例验证并发；[PostgreSQL 测试][postgres-tests]在真实 17.11 临时服务器验证并发迁移/只读校验/漂移拒绝、跨连接 CAS、事务回滚、SKIP LOCKED 与租约 fencing，以及应用重建后的审批恢复。版本迁移说明见 [migrations][migrations]。

## 2. 已存在的公开合同与所有权

| 所有者 | 当前公开合同与职责 | 实现证据 |
| --- | --- | --- |
| `framework.agents` | `AgentSpecification`、`OpsMeshAgentBackend`、`AgentExecutionContext`、`AgentBackendCapabilities`、完成/中断结果 | [公开出口][agent-api]；SDK 类型留在 OpenAI 后端 |
| `modules.agents` / `modules.models` | `AgentDefinitionCatalog`、`AgentFactoryRegistry`；模型档案和不可变版本引用 | [Agent API][catalog-api]；[模型 API][models-api] |
| `modules.capabilities` | `CapabilityCatalog`、`CodeToolRegistry`、`ToolInvoker`、`SkillCatalog`、`MCPServerCatalog`、`KnowledgeIndex`、`AgentResources` | [能力 API][capability-api]；工具、Skill、MCP 和知识工具由资源编译器绑定 |
| `modules.runtime` | Run 状态、领取、审批、检查点、SDK 模型历史及资源收尾协调 | [运行时服务][runtime-service]；[运行时测试][runtime-tests] |
| `modules.execution` | `ExecutionProvider`/`ExecutionSession`，可信本地与 Docker 命令运行 | [执行 Provider][execution]；SDK 沙箱客户端仍在 [OA 沙箱映射][oa-sandbox] |
| `modules.workspaces` | `WorkspaceService`、`WorkspaceManifest`、内容摘要、只读文件、目录、产物和快照 | [工作区服务][workspaces]；[资源测试][resource-tests] |
| `modules.memory` | namespace、读/生成策略、来源、两阶段任务、候选 CAS 发布及删除 | [记忆服务][memory-service]；[生成器][memory-worker] |
| `modules.usage` | 请求/累计用量、版本价格、Decimal 成本、预算预占/结算 | [用量服务][usage-service]；[用量测试][usage-tests] |
| bootstrap / infrastructure | 授权和资源组装、事务 outbox、迁移、日志/指标/追踪接线 | [Agent 桥接][agent-bridge]；[数据库][database] |

插件可注册工厂、代码工具、运行扩展和事件响应器；HTTP 提交的是数据与已部署引用，不是上传任意可执行 Python。当前实现是 NoneBot 风格机制，不是声称二进制兼容全部 NoneBot 插件。

## 3. agents.md 的 14 项

| 编号 / 主题 | 当前状态、实现范围与缺口 | 代码 / 测试证据 |
| --- | --- | --- |
| A01 后续指南与能力选择 | **已实现校验·离线验证**：后端声明流、审批、恢复、结构输出、handoff、as-tool、sandbox、护栏与工具并发；版本 ModelProfile 的 features、supported_settings/unsupported_settings 约束请求，Responses/Chat 不兼容组合在解析凭据之前拒绝。不会自动发现所有供应商模型的能力。 | [校验][runtime-validation]、[模型矩阵][model-validation]、[文本扩展专项][text-feature-tests] |
| A02 Agent 基本配置 | **已实现·离线验证**：基本字段、模型引用、函数/Hosted/本地原生工具、输出类型、hooks、护栏、编排和 sandbox。平台 MCP 配置支持 strict schema 最佳努力转换、服务名开关、动态过滤及注册错误格式器，仍经 ToolInvoker 鉴权；仅指定执行类错误可转文本，授权撤销始终中止。 | [构建][oa-agent]、[MCP 配置][mcp-config]、[文本扩展专项][text-feature-tests]、[Hosted MCP][hosted-mcp-tests] |
| A03 提示词模板 | **已实现映射·离线验证**：静态 prompt、动态 prompt_ref 与版本化 PromptTemplateDefinition 三选一；插件登记远端 ID/固定版本、默认变量及允许/必填变量，运行时覆盖变量且指纹检测恢复漂移。不提供通过 HTTP 上传 Python 或自动创建供应商模板；真实远端模板服务尚待验收。 | [模板合同][prompt-templates]、[构建][oa-agent]、[文本扩展专项][text-feature-tests] |
| A04 上下文 | **已实现·离线验证**：中立执行上下文、ContextVar 绑定、SDK context wrapper；快照排除服务对象。 | [后端][oa-backend]、[检查点][oa-state]、[运行时测试][runtime-tests] |
| A05 输出类型 | **已实现·离线及模型验证**：文本、JSON Schema 及注册 Python 类型引用，Runner 测试 Pydantic/dataclass/list 输出；HTTP 只引用部署类型，不能上传 Python。真实 gpt-6.1-sol Responses 已验证流式 JSON Schema；其他模型/全部 Python 类型组合仍需按 Provider 验收。 | [输出适配][oa-output]、[扩展专项][advanced-tests]、[显式模型验收][live-model-tests] |
| A06 多 Agent 两种模式 | **已实现·离线验证**：handoff保持主循环，agent-as-tool返回父Agent；as_tool支持shared/isolated/auto环境策略，独立子环境已有真实UnixLocal验证。handoff不能在同一Runner中切换不兼容live session。 | [构建][oa-agent]、[独立子环境][isolated-tests] |
| A07 Agent 作为工具 | **已实现·离线验证**：子轮次、审批、可见性、输出提取器、注册Python输入类型/输入构建器，子RunConfig及独立sandbox。SDK嵌套审批恢复时重建执行上下文与工具图。 | [工具映射][oa-tools]、[扩展专项][advanced-tests]、[独立子环境][isolated-tests] |
| A08 Handoff | **已实现映射·离线验证**：目标版本、描述、输入schema、回调、动态可见性和注册历史过滤器；过滤模型输入可保留审计历史，其他SDK高级分支仍按需求扩展。 | [工具映射][oa-tools]、[委派扩展][oa-delegation]、[SDK测试][sdk-tests] |
| A09 动态指令 | **已实现·离线验证**：部署时注册同步/异步 callback，按 instruction_ref 获取；每 Run 新构建图并拼接授权资源指令。 | [构建][oa-agent]、[SDK 测试][sdk-tests] |
| A10 生命周期 hooks | **已实现·离线验证**：SDK 0.24.0 文本 Agent/Run 两层 start/end、模型、工具、handoff 回调；默认传事件元数据、call_id 与用量，单个 HookBinding.include_payload 可显式获取脱离 SDK 对象的输入/输出副本。沙箱 Capability 自身生命周期另由管理器处理，平台终态仍由 runtime 提交。 | [hooks][oa-hooks]、[文本扩展专项][text-feature-tests] |
| A11 Guardrails | **已实现·离线验证**：输入、输出、FunctionTool输入/输出四阶段；逐阶段tripwire、输出护栏与自定义结束策略/审批恢复均有实际Runner测试。并行输入护栏与已发送增量仍有泄露窗口，需按场景选择阻塞执行。 | [护栏][oa-guards]、[扩展专项][advanced-tests] |
| A12 克隆与复制 | **已实现**：复制版本化定义为新草稿，并重新构建 SDK 图；不复制 Session/检查点/凭据实例。没有使用 SDK 浅 clone 充当业务复制。克隆并发独立性专项测试待补。 | [目录服务][catalog-service]、[构建][oa-agent] |
| A13 强制工具与重置 | **已实现·离线验证**：授权资源准备后解析 tool_choice/stop_tools，MCP 用稳定别名 mcp:server@revision/tool，不要求预知生成名称；缺失和冲突明确拒绝。HostedMCPToolSelection 指定允许的 server_label/name；支持 reset_tool_choice、server ToolSearch/defer_loading。client ToolSearch 在当前 Runner 明确不支持。 | [选择解析][tool-selection]、[文本扩展专项][text-feature-tests]、[Hosted MCP/Search测试][hosted-mcp-tests] |
| A14 工具结束行为 | **已实现·离线验证**：继续、首工具结束、指定工具结束与注册ToolsToFinalOutputFunction；中立工具结果/结束决定映射，实际SDK验证审批恢复后结束与输出护栏。 | [扩展映射][oa-extension-mapping]、[扩展专项][advanced-tests] |

## 4. sandbox/guide.md 的 31 项

| 编号 / 主题 | 当前状态、实现范围与缺口 | 代码 / 测试证据 |
| --- | --- | --- |
| S01 组件协作 | **已实现·真实环境验证**：Agent→平台快照→SDK session→flush记忆→捕获输出→平台快照；UnixLocal 真实文件/恢复及 Linux Docker 原生 Memory 归档清理验证通过。第三方云客户端与完整生产部署仍需各自验收。 | [沙箱桥接][sandbox-bridge]、[原生来源][sandbox-source-tests]、[原生记忆][native-memory-tests] |
| S02 普通与沙箱 Agent | **已实现·本机及 Docker 验证**：sandbox=None 构建 Agent，否则 SandboxAgent；普通父 Agent 可调用独立沙箱子 Agent，父 Run 持久保存子产物。Linux Docker 原生 Memory/产物清理验证通过。 | [构建][oa-agent]、[独立子环境][isolated-tests]、[持久产物][execution-metadata-tests]、[原生记忆][native-memory-tests] |
| S03 客户端选择 | **已实现·真实环境验证**：UnixLocal、Docker、版本化 Provider 与可信注入；RemoteDockerSandboxProvider 使用允许端点、双向 TLS 秘密引用和注入 DockerClient，管理连接/临时证书生命周期。真实 Linux Docker 的 mTLS 端点已验证创建、产物、CPU/内存/PID 配置和清理；不预装 E2B/Modal 等全部云厂商。 | [公开Provider][sandbox-provider-api]、[远程Docker][remote-docker]、[远程测试][remote-docker-tests] |
| S04 七类核心对象 | **已实现合同**：Agent、Manifest、Capability、RunConfig、Runner RunState、native session_state、平台WorkspaceSnapshot分开；HTTP只接受受权的snapshot_ref/session_ref，不接受原生状态对象。 | [定义][agent-spec]、[来源测试][sandbox-source-tests]、[覆盖合同][run-overrides] |
| S05 准备流程 | **已实现·本机验证**：准备资源、构建manifest、打开session；准备失败清理。独立子Agent按tool_call_id准备新环境；SDK私有兼容点限定0.24.0并有回归。 | [Agent桥接][agent-bridge]、[生命周期][sandbox-lifecycle]、[独立子环境][isolated-tests] |
| S06 SandboxAgent 选项 | **已实现映射**：manifest、base_instructions、capabilities、run_as/cwd、创建选项与限额；RunOverrides可切换单次workspace/source，不能从HTTP修改任意宿主配置。 | [定义][agent-spec]、[构建][oa-agent]、[覆盖专项][run-overrides-tests] |
| S07 default_manifest | **已实现·本机验证**：SDK default_manifest映射；fresh/source snapshot/session恢复选择区分，恢复不重新覆盖已有文件。原生session保留身份、snapshot打开新身份已有UnixLocal测试。 | [构建][oa-agent]、[来源测试][sandbox-source-tests] |
| S08 基础与附加指令 | **已实现映射**：base_instructions 与 instructions 分字段，资源说明追加到 instructions；SDK 完整提示组装顺序没有独立快照测试。 | [构建][oa-agent] |
| S09 Capabilities | **已实现映射·本机及 Docker 验证**：Shell/Filesystem/Compaction 及 Skills/Memory 能力。原生 Memory 生成通过固定 SDK 版本管理器接入模型授权/计量，显式 flush 在捕获前完成；阶段错误不得吞掉。平台与原生双生成互斥。 | [沙箱映射][oa-sandbox]、[原生记忆][native-memory-tests] |
| S10 Manifest | **部分实现·本机验证**：文件/目录/内容摘要、workspace租约/fence和只读快照；受控Git Skill发布固定commit及文件摘要。host/cloud条目必须经已部署Provider的manifest授权策略，缺策略时拒绝。云存储写回语义仍取决于Provider。 | [工作区][workspaces]、[Git Skill测试][git-skill-tests]、[Provider测试][sandbox-provider-tests] |
| S11 文件与执行身份权限 | **部分实现·本机验证**：只读文件、Permissions/run_as映射、归档权限保留、越界/链接拒绝；UnixLocal真实文件读写通过，容器内不同用户/组与恶意代码隔离仍需验收。文件mode不等于平台RBAC。 | [生命周期][sandbox-lifecycle]、[沙箱测试][sandbox-tests]、[独立子环境][isolated-tests] |
| S12 SnapshotSpec | **部分实现·本机验证**：平台快照和受控LocalSnapshotSpec；snapshot_ref启动新环境，session_ref调用恢复路径并校验所有者/Provider配置。Remote/Noop没有通用公共合同，不把noop当备份。 | [快照][workspace-snapshots]、[来源测试][sandbox-source-tests] |
| S13 生命周期 | **已实现·本机验证**：managed session与清理引用持久化，Memory flush→产物捕获→关闭/回收；旧attempt不能清理新环境。UnixLocal已验证，真容器跨worker失联回收仍需部署演练。 | [生命周期][sandbox-lifecycle]、[原生记忆][native-memory-tests]、[沙箱测试][sandbox-tests] |
| S14 SandboxRunConfig | **已实现映射**：client/session、manifest、cwd、options、limits、受权state与snapshot来源；Provider与Run override有类型化入口，SDK所有高级组合并非全部验收。 | [沙箱映射][oa-sandbox]、[公开Provider][sandbox-provider-api]、[覆盖合同][run-overrides] |
| S15 来源优先级 | **已实现·本机验证**：受信live session注入；服务端session_ref恢复与snapshot_ref新建互斥；审批恢复使用已保存binding。未知/越权/配置不兼容来源拒绝。 | [沙箱桥接][sandbox-bridge]、[来源测试][sandbox-source-tests] |
| S16 新会话输入 | **已实现·本机验证**：新manifest、平台snapshot_ref与受控SDK快照来源；session_ref走resume且保留native身份。任意snapshot_path/session_state不向HTTP开放。 | [覆盖合同][run-overrides]、[来源测试][sandbox-source-tests] |
| S17 cwd | **已实现·离线验证**：POSIX 相对子目录校验，拒绝绝对/父目录/Windows drive；平台命令检查存在及symlink。native patch/image/shell 一致性仍需客户端验收。 | [定义][agent-spec]、[命令路径][execution-processes]、[资源测试][resource-tests] |
| S18 物化控制 | **部分实现**：并发和 archive limits 映射、输出 archive 成员/字节/路径/链接检查。没有独立物化队列及完整输入归档压力测试；限额不代表 CPU/内存隔离。 | [沙箱映射][oa-sandbox]、[沙箱桥接][sandbox-bridge]、[沙箱测试][sandbox-tests] |
| S19 完整编码场景 | **部分实现·真实 Docker 执行验证**：远程 Docker 内执行命令、超时终止、取消远程 exec、收集工作区产物并清理容器已测；真实模型+真实容器组合仍未完成端到端验收。 | [沙箱桥接][sandbox-bridge]、[Docker专项][docker-integration-tests] |
| S20 客户端切换 | **部分实现·真实 Docker 验证**：版本化 Provider 注册、DockerClient 注入、mTLS 连接、options 与 manifest 策略；RemoteDocker 已实测，跨 Provider 快照/权限/资源等价性与云厂商服务仍未验证。 | [公开Provider][sandbox-provider-api]、[Provider测试][sandbox-provider-tests]、[远程Docker][remote-docker-tests] |
| S21 单次工作区覆盖 | **已实现·运行时离线验证**：`RunOverrides`选择单次workspace，或`sandbox.snapshot_ref/session_ref`引用服务端登记的完整manifest/会话；不能同时选择。覆盖生成独立effective Agent，不修改发布版本；记录覆盖来源及发布/effective指纹，审批恢复保持冻结。HTTP不接受原生状态、宿主路径或凭据，跨主体资源授权仍由入口与bridge执行。 | [覆盖合同][run-overrides]、[专项测试][run-overrides-tests]、[沙箱桥接][sandbox-bridge] |
| S22 注入 live session | **已实现映射**：受信代码可注入native session；managed由bootstrap持有。HTTP session_ref引用已登记来源并执行所有者/配置校验，不能提交live Python对象。 | [沙箱映射][oa-sandbox]、[来源测试][sandbox-source-tests] |
| S23 session_state 恢复 | **已实现·本机验证**：服务端持久state经client.resume恢复，UnixLocal实测native身份与文件内容保持；未知/外主体引用拒绝。不承诺恢复活进程、PTY或跨Provider状态。 | [生命周期][sandbox-lifecycle]、[来源测试][sandbox-source-tests] |
| S24 快照启动 | **已实现·本机及 Docker 验证**：一致快照、目录/只读位、租约与旧 fence 拒写；UnixLocal 从平台快照打开新 native 身份，独立子 Agent 审批后重建文件环境实测。Docker 上验证真实 session 执行、归档和清理；跨主机 Docker snapshot/session 恢复语义仍需部署验收。 | [快照][workspace-snapshots]、[来源测试][sandbox-source-tests]、[独立子环境][isolated-tests]、[远程Docker][remote-docker-tests] |
| S25 Git 技能 | **已实现·本机验证**：受信仓库/完整commit/subdirectory导入，验证解析commit、大小和symlink后发布冻结摘要制品；真实临时Git仓库测试通过。外部私有Git认证部署待验收。 | [Git导入][git-skills]、[Git Skill测试][git-skill-tests] |
| S26 沙箱 Agent 作为工具 | **已实现·本机验证**：auto/shared/isolated 策略；按 tool_call_id 创建独立 session 和工作区，稳定模板支持 SDK 嵌套恢复，并发调用不混目录。父 Run 持久产物清单，审批/收尾重试按 call_id 合并。 | [独立子环境][isolated-tests]、[持久产物][execution-metadata-tests] |
| S27 本地工具与 MCP 共存 | **已实现映射·离线验证**：平台函数工具、资源编译 MCP 和 sandbox 原生能力可加入图；平台 MCP 错误处理和 Hosted MCP 是不同调用路径，stdio 在受信宿主启动，不自动复制进沙箱。实际组合流程仍需场景集成验证。 | [资源编译][resource-compiler]、[MCP][mcp]、[MCP 配置][mcp-config]、[SDK 测试][sdk-tests] |
| S28 文件记忆 | **已实现·真实环境及离线验证**：平台 namespace/两阶段任务/CAS/身份隔离、live_update 与 retention；原生 Memory 两阶段接授权、费用和严格 flush，UnixLocal 与真实 Docker 均验证生成文件在 cleanup 前保存。原生能力身份排除 Docker 连接/服务对象；禁止同一 Agent 双生成。实际付费模型的记忆内容质量与成本仍需专门验收。 | [记忆桥接][memory-bridge]、[原生记忆][native-memory-tests]、[生命周期测试][memory-lifecycle-tests] |
| S29 编排组合 | **部分实现·本机及真实 Docker 验证**：普通父 Agent→独立 SandboxAgent、子工具审批重启及并发环境隔离实测；handoff 不支持在同一 Runner 中切换不兼容 live session，跨后端委派仍拒绝。 | [构建][oa-agent]、[独立子环境][isolated-tests]、[远程Docker][remote-docker-tests] |
| S30 轮次与执行操作 | **部分实现·真实 Docker 验证**：父/子 max_turns 分开，命令另有 timeout；Docker 超时终止容器，取消终止远程 exec；预算按请求计量。更深层 sandbox/审批组合仍需单独场景覆盖。 | [定义][agent-spec]、[工具映射][oa-tools]、[用量测试][usage-tests]、[Docker专项][docker-integration-tests] |
| S31 延伸能力扩展 | **已实现合同**：RuntimeExtensions注册类型/回调，插件归属、禁用检查、失败回滚及冻结；原生工具与sandbox Provider有专用扩展入口，不依靠任意HTTP字典导入代码。 | [扩展注册][runtime-extensions]、[扩展测试][extension-tests]、[公开Provider][sandbox-provider-api] |

七类状态仍要分开：`SandboxAgent` 是定义，`Manifest` 是初始文件描述，`Capability` 是原生行为，`SandboxRunConfig` 是单次运行选择，`RunState` 是 Runner 恢复载荷，`session_state` 是客户端会话状态，平台 `WorkspaceSnapshot` 是持久文件内容。**文件快照不恢复活进程，模型Session不等于沙箱session。** 默认审批恢复可从文件快照重建；显式session_ref通过native恢复路径保留会话身份，二者有独立测试。

## 5. 模型与上下文的 17 项

| 编号 / 主题 | 当前状态、实现范围与缺口 | 代码 / 测试证据 |
| --- | --- | --- |
| M01 模型/端点 | **已实现·部分真实服务验证**：版本 ModelProfile、秘密引用、Responses/Chat Completions 客户端，每请求重新授权。真实 gpt-6.1-sol Responses 通过审批重启恢复及流式结构输出验收；其他提供商与 Chat 真实服务未在该验收中覆盖。 | [模型][oa-models]、[模型 API][models-api]、[显式模型验收][live-model-tests] |
| M02 请求参数 | **已实现·离线验证**：ModelSettings 类型验证、profile→Agent→Run 深合并与类型化 Run 覆盖；ModelProfile.features、supported_settings/unsupported_settings 明确允许/拒绝能力和字段，禁止组合在取密钥之前失败。不自动探测每个模型的能力，部署方维护版本档案。 | [模型矩阵][model-validation]、[设置合同][model-settings-merge]、[文本扩展专项][text-feature-tests]、[覆盖测试][run-overrides-tests] |
| M03 Responses 高级选项 | **已实现映射·离线验证**：SDK ModelSettings 字段映射，extra_args 不得替换输入/工具/连接或其他已映射字段；Chat 明确拒绝 Responses prompt/hosted/native sandbox/WS/compaction 及专属参数，并启用 SDK strict_feature_validation。供应商能力仍由显式 profile 约束，非所有外部模型的自动认证矩阵。 | [模型矩阵][model-validation]、[模型][oa-models]、[文本扩展专项][text-feature-tests] |
| M04 HTTP/WebSocket | **已实现映射·本机验证**：Responses/Chat HTTP及Responses WebSocket选择，实际SDK通过loopback WebSocket服务器完成response协议往返并关闭连接。真实供应商断线/复用策略仍需验收；Realtime/Voice未实现。 | [模型][oa-models]、[压缩/WS测试][compaction-tests] |
| M05 其他模型/混用 | **部分实现·离线校验及单一真实模型验证**：兼容 base_url 与后端构造时注入 Model/ModelProvider，并按声明能力拒绝不兼容混用；真实 gpt-6.1-sol Responses 已验收。没有通用第三方插件注册/自动能力探测；Claude 模型端点不等于 Claude Agent SDK 后端。 | [后端][oa-backend]、[模型][oa-models]、[模型矩阵][model-validation]、[显式模型验收][live-model-tests] |
| M06 模型超时 | **部分实现**：ModelSettings.timeout 可传入，Run deadline 与函数工具/命令 timeout 独立；模型流/非流/重试组合外部验收缺失。 | [模型][oa-models]、[运行时][runtime-service] |
| M07 模型重试 | **部分实现**：SDK设置可传结构化 retry 字段；没有版本化重试callback注册、流后重试和副作用重放策略完整实现。 | [模型][oa-models]、[定义][agent-spec] |
| M08 设置合并 | **已实现·离线验证**：模型档案→Agent→Run按字段深合并，保留retry/backoff和extra_args未覆盖字段；Run null/遗漏值继承，覆盖不得改变模型身份、凭据、工具选择。跨层非法backoff及extra_args冲突拒绝；effective配置与覆盖来源持久化，SDK Runner实测接收独立Run设置。仅根Agent应用覆盖，不隐式修改委派Agent。 | [设置合并][model-settings-merge]、[覆盖合同][run-overrides]、[专项测试][run-overrides-tests] |
| C01 本地上下文/DI | **已实现·离线验证**：Depends资源释放、事件/Matcher上下文、运行ContextVar和SDK context服务桥接。服务不会自动成为模型输入。 | [运行时测试][runtime-tests]、[DI测试][di-tests] |
| C02 能力可见性 | **已实现映射·离线验证**：绑定清单、MCP allowlist、主体范围、FunctionTool/handoff/agent-tool 动态可见性；平台 MCP tool_filter_ref 复用部署的 visibility 回调，提供 server_ref/source_name。过滤只控制模型可见性，执行仍独立鉴权，不能吞掉授权撤销错误。 | [资源编译][resource-compiler]、[MCP 配置][mcp-config]、[文本扩展专项][text-feature-tests] |
| C03 嵌套上下文 | **部分实现·本机验证**：共享调用保持主体，isolated as_tool创建独立服务上下文/沙箱/资源生命周期；稳定工具调用ID关联授权、产物和费用。没有通用切换为其他主体的NestedContextPolicy。 | [工具映射][oa-tools]、[独立子环境][isolated-tests] |
| C04 模型可见输入 | **部分实现**：instructions、输入、真实模型历史、授权知识/记忆注入；没有通用ModelInputBuilder策略和历史剪裁流水线。 | [Agent桥接][agent-bridge]、[历史][model-history] |
| C05 对话 Session | **已实现·离线验证**：自有CAS模型历史实现SDK Session，保留工具条目并独立于展示消息；压缩替换使用同一revision原子更新。PG应用持久恢复通过，未单列完整SDK+PG压缩组合验收。 | [历史][model-history]、[压缩测试][compaction-tests]、[PG测试][postgres-tests] |
| C06 sandbox Compaction | **映射已实现·外部待验收**：capabilities列表支持原生Compaction；没有实际压缩模型响应与compaction item恢复测试。 | [沙箱映射][oa-sandbox] |
| C07 会话间压缩 | **已实现·离线验证**：真实OpenAIResponsesCompactionSession与AsyncOpenAI responses.compact映射，自动/手动压缩、CAS历史替换及并发冲突拒绝。真实提供商压缩内容待验收。 | [压缩适配][oa-compaction]、[压缩/WS测试][compaction-tests] |
| C08 压缩费用 | **已实现·离线验证**：独立压缩请求走模型授权与请求级usage观察，关联同Run/模型档案，避免仅计普通Runner总量；未返回usage时保留未知状态。无独立公开批量压缩调度UI。 | [压缩适配][oa-compaction]、[压缩/WS测试][compaction-tests]、[用量服务][usage-service] |
| C09 文件长期记忆 | **已实现·离线验证**：独立namespace/制品、live_update CAS、retention、删除撤销；blob物理GC要求maintenance、最小年龄及二次引用扫描，保留运行中任务和已发布引用。S3兼容存储GC已在SeaweedFS真实服务验证，云厂商保留策略单独配置。 | [生命周期测试][memory-lifecycle-tests]、[GC测试][content-gc-tests] |

## 6. 执行治理的 8 项

| 编号 / 主题 | 当前状态、实现范围与缺口 | 代码 / 测试证据 |
| --- | --- | --- |
| E01 本地/Windows | **已实现·离线及真实 Docker 验证**：可信本地命令跨平台运行和进程树取消；它不是 OS 沙箱。原生 UnixLocal 在 Windows 拒绝；真实 Docker 端到端完成执行、超时取消和清理。 | [执行][execution]、[进程控制][execution-processes]、[资源测试][resource-tests]、[Docker专项][docker-integration-tests] |
| E02 资源限额 | **已实现·真实 Docker 验证**：Docker 配置 CPU 0.5、内存 128 MiB、PID 32/64、network none/仅 lo、root 只读、tmpfs、drop all/no-new-privileges；端到端读取 cgroup、超时杀容器、取消远程 exec 均通过。本地 provider 对无法强制的 PID 限额明确拒绝，磁盘配额仍不宣称支持。 | [执行][execution]、[worker启动][run-launcher]、[Docker专项][docker-integration-tests]、[远程Docker][remote-docker-tests] |
| E03 远程挂载 | **受控扩展·外部待验收**：已部署 Provider 可提供真实 client 与 manifest 授权策略；默认 managed 拒绝 host/cloud 路径，不能由 HTTP 开启挂载。SeaweedFS S3 snapshot/backup/restore/GC 已在临时实例验证，未预装 S3/GCS 等全部适配和统一写回协议。 | [公开Provider][sandbox-provider-api]、[Provider测试][sandbox-provider-tests]、[存储集成][storage-integration-tests] |
| E04 外部路径/凭据 | **部分实现·离线验证**：模型/MCP/Hosted MCP秘密引用仅运行时解析，checkpoint移除Hosted MCP认证字段；host manifest须部署Provider授权，来源恢复校验主体/配置。跨云拓扑语义仍需专门Provider验收。 | [Hosted MCP测试][hosted-mcp-tests]、[Provider测试][sandbox-provider-tests]、[来源测试][sandbox-source-tests] |
| E05 live session 更新 | **部分实现**：动态Skill文件可同步至`.agents`，拒绝越界与内容冲突；没有通用WorkspaceMutationPlan/root/users/groups/mount增量编辑API。 | [生命周期][sandbox-lifecycle]、[Agent桥接][agent-bridge] |
| E06 eager/lazy Skill | **已实现·离线验证**：发布摘要制品、元数据索引、eager装配、lazy load/read工具，逐次鉴权；SDK原生Skills source/lazy所有组合未验收。 | [Skill][skills]、[资源编译][resource-compiler]、[资源测试][resource-tests] |
| E07 SDK桥接 | **已实现·离线及真实 Docker 验证**：execution 保持 SDK 无关；native 构造/编码留 OA 后端，bootstrap 桥接快照；公开版本化 Provider factory 接真实 SDK client/options/manifest 授权，RemoteDocker mTLS 已验收。各云 Provider 仍需独立实现和验收。 | [执行][execution]、[公开Provider][sandbox-provider-api]、[Provider测试][sandbox-provider-tests]、[远程Docker][remote-docker-tests] |
| E08 脚本/MCP位置 | **已实现·本机验证**：stdio、Streamable HTTP、SSE，真实本地stdio/SSE往返；版本脚本工具冻结制品，按锁定依赖创建隔离Python环境并经网关执行，离线wheel/超时/篡改测试通过。WebSocket MCP未实现，远端服务认证待部署验收。 | [MCP][mcp]、[检索/MCP测试][retrieval-mcp-tests]、[脚本测试][script-tool-tests] |

创建 Agent 只保存版本引用。Skill 在发布时固定内容摘要，执行准备/按需加载时进入选定 workspace。MCP远端服务保存配置与secret引用，不能复制“远端MCP”进目录；stdio程序需要在声明的可信执行位置部署。SDK内置沙箱命令与平台工具网关是不同调用通路，不能声称原生Shell/Hosted工具都自动经过ToolInvoker。

## 7. 护栏、审批与恢复的 11 项

| 编号 / 主题 | 当前状态、实现范围与缺口 | 代码 / 测试证据 |
| --- | --- | --- |
| G01 输入/输出护栏 | **已实现·离线验证**：阻塞/并行输入配置与输出护栏映射，实际Runner验证各阶段tripwire及审批恢复后的自定义结束输出校验。并行输入和已发送增量不可保证零泄露。 | [护栏][oa-guards]、[扩展专项][advanced-tests] |
| G02 工具护栏 | **已实现·离线验证**：FunctionTool 输入/输出与 pre_approval_tool_input_guardrails 独立配置；真实 Runner 验证等待审批前检查、拒绝不产生审批、批准后执行前重新检查。max_function_tool_concurrency 映射并验证并行调用上限。Hosted/handoff/沙箱原生工具不自动套用函数工具护栏。 | [工具映射][oa-tools]、[Agent构建][oa-agent]、[文本扩展专项][text-feature-tests] |
| G03 被拒输出 | **部分实现**：tripwire转统一错误/blocked，安全消息不保存原异常；原生历史对拒绝输出的完整持久/重放语义以及已发送增量不可撤回，需专项测试。 | [错误映射][oa-errors]、[运行时][runtime-service] |
| H01 审批要求 | **已实现·离线验证**：FunctionTool/as_tool、独立Shell/ApplyPatch及Hosted MCP审批；中断持久化并恢复SDK审批，工具执行授权仍独立进行。 | [原生工具][native-tool-tests]、[Hosted MCP][hosted-mcp-tests]、[SDK测试][sdk-tests] |
| H02 审批决定 | **已实现·离线验证**：主体与revision检查，服务端可信状态approve/reject，重复/过期/越权拒绝；客户端不上传RunState。 | [运行时测试][runtime-tests]、[HTTP测试][http-tests] |
| H03 部分/自动审批 | **已实现·离线验证**：部分批准后剩余项继续等待；Hosted MCP可使用部署注册策略回调，server_label进入待审身份。未把单次决定自动扩大为全局永久授权。 | [扩展专项][advanced-tests]、[Hosted MCP][hosted-mcp-tests]、[检查点][oa-state] |
| H04 中断/恢复 | **已实现·离线及真实模型验证**：SDK to_state/from_json 恢复工具审批，流消费至终点，使用原 Agent 图；真实 Responses 已验证工具待审→应用关闭/重建→批准继续，工具副作用只执行一次。暂停不是完成。 | [检查点][oa-state]、[SDK测试][sdk-tests]、[显式模型验收][live-model-tests] |
| H05 版本/完整性 | **部分实现·离线验证**：SDK版本、Agent图指纹、状态摘要、attempt领取/fencing；未独立冻结所有外部模型/凭据/容器镜像/资源provider语义版本。 | [检查点][oa-state]、[运行时测试][runtime-tests] |
| H06 嵌套审批 | **已实现·离线及本机验证**：子Agent内部工具暂停→序列化→新后端/worker恢复；独立UnixLocal子环境保存/重建并继续，父Run产物清单持久化。 | [扩展专项][advanced-tests]、[独立子环境][isolated-tests]、[持久产物][execution-metadata-tests] |
| H07 会话/Worker并发 | **已实现·本地验证**：Session占用、attempt fencing、finalizer恢复、跨worker CAS backend capacity与续租；等待审批释放capacity。上限约束后端执行，不是已启动进程总数；真实多主机/PG故障演练待做。 | [运行时测试][runtime-tests]、[admission测试][admission-tests]、[资源scope][run-resources] |
| H08 错误/取消 | **部分实现·离线验证**：统一错误分phase/provider/retryable/retry-after，已映射401/403/404/429/5xx、连接、timeout、护栏及轮次限制；取消/进程树终止有测试。未穷举全部第三方提供商错误。 | [错误映射][oa-errors]、[提供商错误测试][provider-error-tests]、[运行时测试][runtime-tests] |

## 8. 观测与计费的 7 项

| 编号 / 主题 | 当前状态、实现范围与缺口 | 代码 / 测试证据 |
| --- | --- | --- |
| O01 SDK→OTel | **已实现·本机验证**：SDK processor保留父子span与Run关联；HTTP→持久trace carrier→worker/context传播；实际OTLP HTTP loopback collector解析protobuf验证父链。生产collector部署待验收。 | [追踪桥接][oa-tracing]、[OTLP测试][telemetry-export-tests]、[追踪测试][tracing-tests] |
| O02 导出/敏感数据 | **已实现·本机验证**：替换SDK默认processor、禁敏感payload、属性白名单、本地JSONL及OTLP配置/秘密引用；loopback验证认证header和无用户payload。生产脱敏配置需部署审阅。 | [追踪桥接][oa-tracing]、[OTLP测试][telemetry-export-tests] |
| O03 生命周期 | **已实现·本机验证**：OTel有界flush/shutdown；runtime排队/审批等待/提交到终态时长接线。每进程累计指标以UUID覆盖保存共享DB快照，5秒周期及退出flush；HTTP汇总历史counter/hist，gauge仅选live且近期观测。真实worker退出后HTTP读取和重复flush不累加已测；DB指标I/O有2秒deadline。硬终止可能丢最后未flush观测，历史快照暂无自动压缩/清理，生产collector背压仍待演练。 | [OTLP测试][telemetry-export-tests]、[跨进程指标][metric-store-tests]、[执行指标][execution-metadata-tests] |
| O04 请求用量 | **已实现·离线及真实模型验证**：按 request scope 配对开始/完成，保存输入/输出/cache/reasoning 及数字 raw usage；缺 response_id 有稳定本次请求身份，失败前成功请求不重复标未知，并发同 Agent 子调用隔离。真实 Responses 流/审批恢复均已记录用量；未知结果不会伪造零用量。 | [用量映射][oa-usage]、[用量观察测试][usage-observation-tests]、[显式模型验收][live-model-tests] |
| O05 去重/累计 | **已实现·离线验证**：response_id重放去重，缺ID的不同请求分开，累计差额避免父/子双计；SDK恢复用量及并发request scope有测试。无法对无供应商ID且结果未知的任意外部重放承诺exactly-once。 | [用量观察测试][usage-observation-tests]、[用量测试][usage-tests] |
| O06 价格/成本/预算 | **已实现·离线验证**：模型token及ResourceUsageFact计价、版本价格/FX、Decimal成本、预算CAS预占/结算、补价reprice和供应商账单行对账。缺价/用量/汇率保持pending；Hosted观察按实际call计量，不推断未知资源量。真实供应商账单与云资源表尚待接入。 | [账本API][usage-api]、[资源账单测试][resource-billing-tests]、[用量观察测试][usage-observation-tests] |
| O07 持久事实/Outbox | **已实现·本地验证**：记录+事件同事务，租约/重试/死信；真实PG17.11实测回滚、SKIP LOCKED、旧owner/token拒绝；Redis5临时实例实测限流Lua和token所有权。多主机/集群故障未验收；投递at-least-once，外部副作用需幂等。 | [数据库测试][database-tests]、[PostgreSQL测试][postgres-tests]、[Redis测试][cache-tests] |

## 9. 高级能力与尚未覆盖的边界

已提供类型化WebSearch/FileSearch/CodeInterpreter/ImageGeneration/HostedMCP/ToolSearch绑定；Hosted调用在供应商端执行，不能宣称每次都经过本地ToolInvoker。平台执行准入、资源权限、MCP认证与审批、实际call计量和版本资源价格分别处理；真实供应商仍需验收。独立Shell/ApplyPatch/Computer通过部署注册的中立执行接口映射实际SDK工具，Computer安全检查必须显式配置。

注册Python输出类型、ToolsToFinalOutputFunction、as_tool自定义输入/输出、动态Prompt、handoff过滤/可见性、Responses WebSocket和会话压缩已实现。真实SDK离线或本机协议测试分别见上表，不能继续归为未实现。知识检索还具备可替换EmbeddingProvider、持久向量缓存及lexical/vector/hybrid模式；默认本地哈希向量不等于商用语义embedding。[检索测试][retrieval-mcp-tests]

仍有明确边界：

- Realtime/Voice、Claude Agent SDK后端与跨SDK检查点迁移未实现；兼容模型端点不能代替后端适配。
- 当前 SDK 标准 Runner 不执行 client ToolSearch，平台只开放 server 模式；动态生成工具可在授权发现后按版本别名解析 tool_choice/stop_tools，仍没有开发时完整枚举动态工具目录的静态 API。
- 不提供不受限宿主路径/凭据注入；云Provider可扩展但不等于所有云挂载、资源配额和写回协议已验收。跨Provider原生状态、PTY/活进程恢复不作保证。
- 同一Runner的handoff无法切换不兼容live sandbox；需要独立资源时使用as_tool隔离策略。外部模型/真实Docker/云Provider的全链路行为仍需部署验收。
- 高级模型字段的逐Provider兼容矩阵、版本化retry策略callback及复杂流后重试不完整；未知外部结果不会自动盲重放。
- SDK 新增版本的专用 hooks 需随版本复核；本版本 Agent/Run hook 可选择显式传中立化 payload，但不覆盖沙箱内部所有回调。已发送模型增量无法撤回。
- 原生Memory生成与平台managed任务均已实现，但同一Agent不能同时启用两种生成。blob GC、retention和live_update已有本地实现；S3回收已验证；云厂商版本保留/生命周期策略不由平台代设。

Web UI仍只预留；管理与运行通过公开服务、CLI、HTTP/SSE和机器人插件提供。

## 10. 下一轮验收优先级

1. PostgreSQL17.11、Redis5、OTLP/OneBot/MCP/Responses WS的本机协议与UnixLocal真实文件环境已验证；继续进行多主机、连接中断、集群故障与生产认证演练。
2. Docker CPU/内存/PID/网络/只读根/timeout/取消及归档已在独立Linux主机验证；仍须按生产节点内核、存储与网络策略重复部署验收。
3. 真实模型→工具→审批→worker恢复→子环境产物→原生Memory→模型/资源费用全链路；离线模型测试证明协议与控制流，不证明供应商服务结果。
4. 部署真实云Provider、远端Git/MCP/机器人与OTel collector，核对配额、凭据轮换、可观测性背压和供应商账单；保留已实现行为与未完成外部验收的区分。

## 代码与测试索引

[project]: ../../pyproject.toml
[agent-api]: ../../src/opsmesh/framework/agents/__init__.py
[agent-spec]: ../../src/opsmesh/framework/agents/specification.py
[mcp-config]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/mcp_config.py
[tool-selection]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/tool_selection.py
[prompt-templates]: ../../src/opsmesh/modules/runtime/api.py
[model-validation]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/model_validation.py
[remote-docker]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/sandbox_remote_docker.py
[text-feature-tests]: ../../tests/runtime/test_text_runner_features.py
[remote-docker-tests]: ../../tests/runtime/test_remote_docker.py
[docker-integration-tests]: ../../tests/resources/test_docker_integration.py
[storage-integration-tests]: ../../tests/infrastructure/test_s3_integration.py
[live-model-tests]: ../../tests/platform/test_live_model.py
[catalog-api]: ../../src/opsmesh/modules/agents/api.py
[catalog-service]: ../../src/opsmesh/modules/agents/internal/catalog.py
[models-api]: ../../src/opsmesh/modules/models/api.py
[capability-api]: ../../src/opsmesh/modules/capabilities/api.py
[runtime-api]: ../../src/opsmesh/modules/runtime/api.py
[runtime-service]: ../../src/opsmesh/modules/runtime/internal/service.py
[runtime-validation]: ../../src/opsmesh/modules/runtime/internal/validation.py
[runtime-extensions]: ../../src/opsmesh/modules/runtime/internal/extensions.py
[model-history]: ../../src/opsmesh/modules/runtime/internal/history.py
[oa-agent]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/agent_builder.py
[oa-backend]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/backend.py
[oa-delegation]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/delegation.py
[oa-hosted]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/hosted_tools.py
[hosted-tests]: ../../tests/runtime/test_hosted_tools.py
[cache-tests]: ../../tests/infrastructure/test_cache.py
[memory-bridge]: ../../src/opsmesh/application/memory_bridge.py
[sandbox-configuration-tests]: ../../tests/runtime/test_sandbox_configuration.py
[workspace-concurrency-tests]: ../../tests/resources/test_workspace_concurrency.py
[admission-tests]: ../../tests/infrastructure/test_admission.py
[run-resources]: ../../src/opsmesh/application/run_resources.py
[coordinator]: ../../src/opsmesh/application/coordinator.py
[oa-tools]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/tools.py
[oa-output]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/output.py
[oa-models]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/models.py
[oa-sandbox]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/sandbox.py
[sandbox-lifecycle]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/sandbox_lifecycle.py
[oa-hooks]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/hooks.py
[oa-guards]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/guardrails.py
[oa-state]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/state.py
[oa-errors]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/errors.py
[oa-usage]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/usage.py
[oa-tracing]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/tracing.py
[oa-config]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/config.py
[agent-bridge]: ../../src/opsmesh/application/agent_bridge.py
[sandbox-bridge]: ../../src/opsmesh/application/sandbox_bridge.py
[run-launcher]: ../../src/opsmesh/infrastructure/run_workers/launcher.py
[telemetry]: ../../src/opsmesh/infrastructure/telemetry/tracing.py
[resource-compiler]: ../../src/opsmesh/modules/capabilities/internal/resource_compiler.py
[tool-invoker]: ../../src/opsmesh/modules/capabilities/internal/tool_invoker.py
[skills]: ../../src/opsmesh/modules/capabilities/internal/skills.py
[mcp]: ../../src/opsmesh/modules/capabilities/internal/mcp.py
[workspaces]: ../../src/opsmesh/modules/workspaces/internal/service.py
[workspace-snapshots]: ../../src/opsmesh/modules/workspaces/internal/snapshots.py
[execution]: ../../src/opsmesh/modules/execution/internal/service.py
[execution-processes]: ../../src/opsmesh/modules/execution/internal/processes.py
[memory-service]: ../../src/opsmesh/modules/memory/internal/service.py
[memory-worker]: ../../src/opsmesh/modules/memory/internal/generation.py
[usage-service]: ../../src/opsmesh/modules/usage/internal/service.py
[database]: ../../src/opsmesh/infrastructure/database/connection.py
[migrations]: ../../migrations/0001-initial.md
[sdk-tests]: ../../tests/runtime/test_openai_backend.py
[runtime-tests]: ../../tests/runtime/test_runtime.py
[sandbox-tests]: ../../tests/runtime/test_sandbox_bridge.py
[launcher-tests]: ../../tests/runtime/test_run_launcher.py
[tracing-tests]: ../../tests/runtime/test_tracing.py
[resource-tests]: ../../tests/resources/test_resources.py
[usage-tests]: ../../tests/usage/test_usage.py
[database-tests]: ../../tests/infrastructure/test_database.py
[http-tests]: ../../tests/http/test_api.py
[plugin-tests]: ../../tests/framework/test_plugins_driver.py
[di-tests]: ../../tests/framework/test_dependencies.py

[advanced-tests]: ../../tests/runtime/test_advanced_extensions.py
[native-tool-tests]: ../../tests/runtime/test_native_tools.py
[hosted-mcp-tests]: ../../tests/runtime/test_hosted_mcp.py
[isolated-tests]: ../../tests/runtime/test_isolated_sandbox.py
[execution-metadata-tests]: ../../tests/runtime/test_execution_metadata.py
[oa-extension-mapping]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/extension_mapping.py
[sandbox-source-tests]: ../../tests/runtime/test_sandbox_sources.py
[native-memory-tests]: ../../tests/runtime/test_sandbox_memory.py
[sandbox-provider-api]: ../../src/opsmesh/modules/runtime/openai_extensions.py
[sandbox-provider-tests]: ../../tests/runtime/test_sandbox_providers.py
[git-skills]: ../../src/opsmesh/modules/capabilities/internal/git_skills.py
[git-skill-tests]: ../../tests/resources/test_git_skills.py
[memory-lifecycle-tests]: ../../tests/resources/test_memory_lifecycle.py
[extension-tests]: ../../tests/runtime/test_extensions.py
[compaction-tests]: ../../tests/runtime/test_compaction_and_websocket.py
[oa-compaction]: ../../src/opsmesh/modules/runtime/internal/backends/openai_agents/compaction.py
[content-gc-tests]: ../../tests/resources/test_content_gc.py
[retrieval-mcp-tests]: ../../tests/resources/test_retrieval_mcp.py
[script-tool-tests]: ../../tests/resources/test_script_tools.py
[provider-error-tests]: ../../tests/runtime/test_provider_errors.py
[telemetry-export-tests]: ../../tests/infrastructure/test_telemetry_export.py
[usage-observation-tests]: ../../tests/runtime/test_usage_observation.py
[resource-billing-tests]: ../../tests/usage/test_resource_billing.py
[usage-api]: ../../src/opsmesh/modules/usage/api.py
[metric-store-tests]: ../../tests/infrastructure/test_metric_store.py

## 官方资料

此前审阅的官方资料保留如下。它们描述SDK能力；本页实现状态以项目代码和测试为准。

[agents]: https://openai.github.io/openai-agents-python/zh/agents/
[sandbox-guide]: https://openai.github.io/openai-agents-python/zh/sandbox/guide/
[sandbox-clients]: https://openai.github.io/openai-agents-python/zh/sandbox/clients/
[models]: https://openai.github.io/openai-agents-python/zh/models/
[context]: https://openai.github.io/openai-agents-python/zh/context/
[guardrails]: https://openai.github.io/openai-agents-python/zh/guardrails/
[hitl]: https://openai.github.io/openai-agents-python/zh/human_in_the_loop/
[tracing]: https://openai.github.io/openai-agents-python/zh/tracing/
[usage]: https://openai.github.io/openai-agents-python/zh/usage/
[memory]: https://openai.github.io/openai-agents-python/zh/sandbox/memory/
[sessions]: https://openai.github.io/openai-agents-python/zh/sessions/
[tools]: https://openai.github.io/openai-agents-python/zh/tools/
[handoffs]: https://openai.github.io/openai-agents-python/zh/handoffs/
[exceptions]: https://openai.github.io/openai-agents-python/ref/exceptions/
[run-overrides]: ../../src/opsmesh/modules/runtime/overrides.py
[run-overrides-tests]: ../../tests/runtime/test_run_overrides.py
[model-settings-merge]: ../../src/opsmesh/framework/agents/model_settings.py
[postgres-tests]: ../../tests/infrastructure/test_postgresql.py
[release]: https://github.com/openai/openai-agents-python/releases/tag/v0.24.0
[pypi]: https://pypi.org/project/openai-agents/0.24.0/
[release-project]: https://github.com/openai/openai-agents-python/blob/v0.24.0/pyproject.toml
[clone-project]: https://github.com/openai/openai-agents-python/blob/cd26bbbc52847845fde73b9952b51e62e2bcbdc0/pyproject.toml
[agent-source]: https://github.com/openai/openai-agents-python/blob/cd26bbbc52847845fde73b9952b51e62e2bcbdc0/src/agents/agent.py#L580
[sandbox-agent-source]: https://github.com/openai/openai-agents-python/blob/cd26bbbc52847845fde73b9952b51e62e2bcbdc0/src/agents/sandbox/sandbox_agent.py
[compaction-source]: https://github.com/openai/openai-agents-python/blob/cd26bbbc52847845fde73b9952b51e62e2bcbdc0/src/agents/sandbox/capabilities/compaction.py
[model-settings-source]: https://github.com/openai/openai-agents-python/blob/cd26bbbc52847845fde73b9952b51e62e2bcbdc0/src/agents/model_settings.py

官方站点会随开发持续更新；需要复核本文阅读版本时，可对照固定提交中的 [agents.md](https://github.com/openai/openai-agents-python/blob/cd26bbbc52847845fde73b9952b51e62e2bcbdc0/docs/zh/agents.md) 与 [sandbox/guide.md](https://github.com/openai/openai-agents-python/blob/cd26bbbc52847845fde73b9952b51e62e2bcbdc0/docs/zh/sandbox/guide.md)。以上来源支持 SDK 行为；opsmesh 的合同名、模块分工和验收条件是本项目设计。
