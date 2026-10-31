# SDK 能力、工作区、恢复与平台基础设施

状态核对：2026-10-30。本文描述当前实现、模块责任与仍需外部验收的边界，配合[总体架构](architecture.md)、[SDK覆盖表](sdk-coverage.md)和[文件记忆设计](memory.md)阅读。

OpenAI 后端依赖固定为 `openai-agents==0.24.0`，正式源码提交为 `17d994bd3045dae71acda676a4ef4762ed8ecd94`。普通Agent、真实SDK Runner、UnixLocal/Docker沙箱、文件记忆生成和独立子沙箱已有行为测试；另完成授权真实模型的审批重启、流式结构输出及计量验收。PostgreSQL 17.11 与 Redis 5.0.14.1 已在临时本机实例运行集成验证；这不等于多主机、高可用或生产负载验收。独立Linux主机已验证真实Docker命令隔离/取消、mTLS远程Docker、S3/PostgreSQL备份恢复与逐Run K3s Job。Claude 后端未实现，Web 前端仅预留。

## 1. 模块按所有者划分

整体采用模块化单体。公开合同与业务入口放在模块 api.py / events.py，实现放 internal。框架协议、插件系统、业务模块、SDK 适配和基础设施分别拥有明确责任。

| 模块 | 责任 |
|---|---|
| framework | Driver、Adapter、Bot、Event/Message、Matcher、插件装载、Depends 与上下文 |
| modules/accounts | 本地用户、角色、权限；不要求 OIDC 或企业 SSO 身份源 |
| modules/bots | 机器人连接配置、身份、协议与会话路由 |
| entrypoints / bootstrap | HTTP/CLI/Worker 入口、认证鉴权、服务组装、生命周期协调 |
| modules/agents | Agent 定义、不可变发布版本、工厂/配置验证 |
| modules/models | 版本化模型档案、模型参数与凭据引用 |
| modules/runtime | Run、执行尝试、历史、检查点、审批、SDK backend、恢复与取消 |
| modules/capabilities | CodeTool、脚本、Skill、MCP、知识集合的版本、解析、授权与调用 |
| modules/workspaces | 文件、manifest、快照、制品、内容对象及物理 GC |
| modules/execution | 中立执行会话、命令、超时/取消、可信本地与 Docker Provider |
| modules/memory | 文件记忆策略、namespace、来源、两阶段任务、发布与保留 |
| modules/usage | 用量事实、价格版本、成本、预算预占和结算 |
| modules/audit / infrastructure | 审计、数据库、Outbox、缓存、日志、监控、追踪与运行容量 |

插件参考 NoneBot2 的协作方式：普通 Python 模块、导入时 Matcher 注册、__plugin_meta__、require()、普通 import、Depends(callable) 与 Driver 生命周期。插件能注册工具、后端与原生资源 Provider；HTTP 只接受可序列化的配置和已部署引用。并不承诺完整兼容 NoneBot2 全部插件 API，也不允许通过 HTTP 传入 Python 函数或任意 import/eval。

## 2. SDK 能力如何映射

平台抽象采用“共同语义 + 显式能力 + 版本化扩展引用”，不假定 OpenAI 与未来 Claude SDK 的内部机制相同。

| 功能 | 当前平台合同与接线 |
|---|---|
| Agent / SandboxAgent | AgentSpecification、SandboxConfiguration；每次运行构建 SDK 图 |
| 指令和 Prompt | 静态配置、instruction_ref、prompt_ref 与部署端注册代码 |
| 模型 | ModelProfile、模型档案引用、ModelSettingsOverrides；HTTP 与 Responses WebSocket |
| 普通工具 | ToolBinding、CodeToolRegistry、ToolInvoker；schema、授权、幂等、超时 |
| 结构化输出 | JSON schema 或部署注册的 Python output_type_ref |
| Handoff | 版本目标、输入 schema、callback、input_filter、enabled 扩展 |
| Agent-as-tool | 输出提取器、可见性、结构化输入/构建器、审批和 sandbox_policy |
| 工具结束策略 | 继续、首工具结束、指定工具结束或注册 ToolsToFinalOutputFunction |
| 原生工具 | 已注册 Shell/ApplyPatch/Computer 实现；真实副作用由插件提供并受平台策略约束 |
| Hosted 工具 | WebSearch/FileSearch/CodeInterpreter/ImageGeneration、Hosted MCP、ToolSearch 映射 |
| 护栏与 hooks | 输入/输出/工具护栏、AgentHooks/RunHooks 映射与事件发布 |
| 历史与 Compaction | 平台历史存储、SDK compaction、摘要 CAS 和独立模型用量 |
| 流式响应 | SDK 流转为类型化平台事件、序号和最终结果 |
| 人工审批 | PendingToolApproval、持久检查点、决定授权、幂等与恢复 |
| 文件记忆 | 平台托管 namespace 工作流与 SDK 原生 Memory 两条显式路径 |
| 用量与 tracing | 请求级模型计量、未知用量、资源计量入口、SDK span 到平台导出 |

Hosted 工具的实际执行在模型提供商侧；映射、授权及审计不意味着每次内部操作经过本地 ToolInvoker。Hosted MCP 的认证与审批有专门桥接，检查点剥离凭据，恢复重新取得授权配置。真实供应商能力仍需要对应账号与服务验收。

AgentBackend 适配执行循环和恢复，模型 Provider 适配请求，ExecutionProvider 适配运行环境。用 OpenAI Agents SDK 调用另一厂商模型，不等于已增加该厂商 Agent SDK 后端。[Agents](https://openai.github.io/openai-agents-python/zh/agents/)、[Models](https://openai.github.io/openai-agents-python/zh/models/)、[Tools](https://openai.github.io/openai-agents-python/zh/tools/)

## 3. 七个沙箱对象保持独立

| SDK 对象 | 平台职责 | 不应混同 |
|---|---|---|
| SandboxAgent | 行为定义、Capability、默认 manifest | 每个 Run 的可变资源实例 |
| Manifest | 初始文件、仓库、挂载、环境与权限计划 | ToolManifest / 可调用工具清单 |
| Capability | 指令、工具、文件和行为的原生扩展 | 仅一个工具函数 |
| SandboxRunConfig | client、session 来源、cwd、snapshot 与单次选项 | 已发布 Agent 的唯一状态 |
| RunState | Runner 进度、审批项、适用的沙箱状态 | 文件快照 |
| session_state | 恢复沙箱资源身份和提供方状态 | 自动恢复模型循环 |
| snapshot | 用已保存文件初始化新会话 | 恢复原 Runner/会话身份 |

SDK Session 是交互历史接口，也不是上表任何一种文件资源。[Sandbox 概念](https://openai.github.io/openai-agents-python/zh/sandbox/guide/)

平台提供的恢复路径：

- 普通新运行：从指定 workspace 与 Agent manifest 建立新会话。
- snapshot_ref：选择服务端 WorkspaceSnapshot，将内容装入新的 native session，身份发生变化。
- session_ref：引用已持久的 run 资源绑定，校验主体、provider、options、resume_state 与快照，然后调用 client.resume 并恢复权威文件内容。
- RunCheckpoint：恢复 Runner 审批和执行图；工作区桥接同时恢复其资源。子 Agent 的 pending tool 图也重建并校验。
- 进程内 live session：由 bootstrap 管理生命周期并注入 SDK，Runner 借用它，不负责平台最终清理。

session_ref 与 snapshot_ref 互斥。HTTP 只接收注册引用，不接收任意原生 state、宿主路径或客户端自报权限。源工作区在提交和执行时授权；session_ref 还要求同主体且 provider/options 匹配。服务端保存的 session_state 也不能自行授权宿主挂载。

SDK 自身的状态优先级和平台对外 API 不完全相同：SDK 可以按 live session、RunState、显式 session_state、新会话解析；平台在可信组装点限制组合。原生 options 通常用于创建，不能作为恢复时绕过资源策略的入口。[正式会话选择实现](https://github.com/openai/openai-agents-python/blob/17d994bd3045dae71acda676a4ef4762ed8ecd94/src/agents/sandbox/runtime_session_manager.py)

## 4. 工作目录、资源和单次覆盖

发布 Agent 时配置 workspace_id、SandboxConfiguration.cwd、manifest、provider/options 与 Capability。cwd 必须是工作区内的 POSIX 相对路径。新 Run 的公开 RunOverrides 可选择 workspace_id，或 sandbox.snapshot_ref / session_ref，以及类型化 model_settings；它不会修改发布版本。

例如，下面是 SandboxConfiguration 的 Docker options 内容，资源限制实际下发给 Docker API：

```json
{
  "image": "python:3.13-slim",
  "network_mode": "none",
  "resource_limits": {
    "cpus": 1.5,
    "memory_bytes": 268435456,
    "pids_limit": 128
  }
}
```

原生 SDK Docker 适配在容器 create 时传 nano_cpus / mem_limit / memswap_limit / pids_limit，并读取 HostConfig 验证；恢复已有容器时继续验证。提供方未落实配置会报 RESOURCE_POLICY_MISMATCH，创建后验证失败会删除未启动容器。未知资源参数明确拒绝。磁盘配额、完整域名出口白名单、idle TTL 不在当前内置实现范围。

中立 ExecutionSpec 的 cpu / memory_mb / network 属于命令执行 Provider 的合同，和原生 SDK Docker options 分开映射。可信本地执行明确要求 trusted_local，拒绝不能保证的隔离/配额；它不是容器。archive_limits 限制归档提取，manifest/file concurrency 限制装配并发，都不是 CPU 或内存配额。

### Provider 与挂载扩展

[SDK-free 公开扩展合同](../../src/opsmesh/modules/runtime/openai_extensions.py)定义 SandboxProviderRegistry、OpenAISandboxProviderFactory 和 OpenAISandboxProviderBinding。部署插件以版本引用注册异步工厂，返回真实 native client/options；不安装 OpenAI extra 时核心公开合同仍可导入。

可选 validate_manifest 在创建前验证远端/宿主挂载、主体和凭据策略。没有这个可信验证器时仅允许受控 inline file / directory / Git manifest。工厂负责验证并落实其 provider 选项；client 负责创建、恢复、序列化和删除真实资源。挂载恢复须从当前可信配置重新绑定授权，不能依赖已剥离的持久凭据。

这是真实 Provider 注册与执行接口，并不代表已经内置或验收 Modal/E2B 等所有云服务。默认环境仍为 UnixLocal/Docker；云账号、网络、镜像与挂载语义需对应插件部署测试。

## 5. Worker 与 Sandbox 的执行位置

| 执行层 | 实际内容 | 所有者 |
|---|---|---|
| API/Bot/Matcher | 接收事件、授权、提交 Run、响应与推送 | framework / entrypoints |
| Backend Worker | Python Runner、模型请求、普通 Python 工具、stdio MCP 所在进程 | runtime / bootstrap |
| Native SandboxSession | Shell、文件工具、工作区和原生 Memory 阶段二 | OpenAI backend / SandboxWorkspaceBridge |
| 独立外部工具服务 | Remote MCP、知识服务、Hosted 工具 | 远端提供者与 capability 配置 |

SandboxAgent 不会自动把整个 Runner、普通 Python 工具或 stdio MCP 移入沙箱。整段 Worker 放到容器与 SDK 单独创建工具沙箱是两个部署决定。当前真实子进程 worker 已测试；Docker worker 构建参数并不等于当前机器已运行容器。

## 6. Skill、脚本、MCP 与知识资源装配

创建 Agent 保存版本引用和声明，不把所有可执行资源复制到永久 Agent 目录。实际运行先解析授权能力，再向工作区装配需要的不可变文件。

| 资源 | 保存内容 | 运行时处理 |
|---|---|---|
| Skill | SKILL.md 元数据、文件清单、摘要、版本 | eager 物化或 lazy 索引；授权加载后同步 native sandbox |
| 脚本工具 | 不可变制品、入口、schema、执行要求 | ToolInvoker 授权，按配置的执行位置调用 |
| CodeTool | 部署代码注册和版本定义 | 不复制 Python 函数到工作区，调用受注册实现 |
| stdio MCP | 命令、参数、环境引用与明确执行位置 | 在指定 Worker/进程侧启动；不是复制“一个 MCP”目录 |
| Remote MCP | URL、传输、认证引用、允许工具 | 通过连接器访问远端服务，不复制服务端代码 |
| Hosted MCP | 提供商侧 server 配置与审批/认证桥接 | 凭据仅在授权请求期间组装，持久检查点不存秘密 |
| 知识集合 | 文档、索引与访问范围 | 当前内置字词检索，查询工具走统一授权路径 |

Skill 路径穿越、摘要不一致、覆盖只读资源和未授权加载会拒绝。SDK 自己的 Skills Capability 还可改变 manifest、指令与工具；平台 SkillCatalog 与 SDK 原生 lazy loading 是两条不同的装配机制，配置时避免重复装入两份。[Skills](https://openai.github.io/openai-agents-python/zh/sandbox/skills/)、[MCP](https://openai.github.io/openai-agents-python/zh/mcp/)

WorkspaceService 的 write/materialize/restore/delete/snapshot 使用持久 lease 和 fence，跨服务实例串行一致；文件替换发生在持有 fence 的阶段。直接访问宿主目录的可信程序仍能绕过这些 API，因此它不是任意宿主进程的安全边界。

## 7. 独立子 Agent 环境与审批恢复

AgentToolBinding.sandbox_policy 提供 auto/shared/isolated：auto 在环境不同的时候选择独立沙箱，isolated 强制分配，shared 要求配置兼容。每次子工具调用按根 Run、Agent 版本与 SDK tool_call_id 生成稳定资源绑定，同一工具两次并发调用不会共享同一可变 SDK Agent/session。

独立环境的生命周期为：授权子工作区 → 创建/恢复 → 为该调用构建真实 Agent → 执行或等待审批 → flush 记忆与捕获文件 → 释放。父 Run metadata 保存 delegated_artifacts，并取得输出 workspace 的访问归属。

审批恢复需要静态可恢复工具图与新的真实 session-bound 工具映射。平台在恢复时重建子 RunState，保留审批决定，随后重新绑定 native 工具；不会用只有名字的成功 stub 继续执行。真实 UnixLocal 测试覆盖子 Agent 写文件、审批暂停、数据库/桥接/backend 重建、继续读文件与父 Agent 完成，并覆盖两次并发调用文件隔离。

Handoff 沿同一 Runner 上下文执行，当前要求共享兼容的 sandbox/workspace。不同环境的 Handoff 明确拒绝；独立环境委托使用 as_tool，不能把两种语义等同。

## 8. 文件记忆与资源收尾

平台托管 Memory 默认由 modules/memory 拥有 namespace、来源、两阶段生成、候选 CAS 和 live update；原生 Memory 属于 Sandbox Capability 和 workspace 文件。两者均支持，但同一 Agent 禁止同时开启两套生成。

原生生成在每次内部模型请求前授权、按阶段独立计量，阶段二使用真实 Shell/文件工具并保留 run_as。SDK 内部默认可能吞掉部分生成异常，平台的精确版本适配将其变成失败；archive 之前 flush，成功后保存完整文件与生成 receipt，再清理 session/client。模型失败用量为 unknown/unavailable，不是假零费用。[记忆专项](memory.md)

managed live_update 采用 ACL、幂等键、CAS 与原子发布事件；原生 live update 是沙箱内文件修改。generate=None 仅关闭结束生成，仍可能允许读取侧更新；只读还需 read.live_update=false 与实际文件权限。

物理对象 GC 默认 dry_run，实际删除要求维护窗口、全库引用扫描、对象 lease 与二次确认。数据库中仍被快照、任务、审计或 Outbox 引用的摘要不会回收。云对象生命周期和备份删除仍由部署方负责。

## 9. 上下文、护栏、错误与恢复

AgentExecutionContext 持有主体、run/session ID、授权工具入口和运行 services。SDK local context 不会自动进入模型；只有明确指令、输入、历史和工具结果会进入。ContextVar 只处理当前任务内绑定，后台任务、跨进程恢复必须重建上下文，不能持久化连接池或客户端对象。[Context](https://openai.github.io/openai-agents-python/zh/context/)

输入护栏作用于当前链的起始 Agent，输出护栏检查最终输出。要求模型开始前阻断时使用阻塞输入护栏。工具护栏有适用范围，不能宣称自动覆盖 Hosted 内部动作、handoff 和所有 native Shell 操作。[Guardrails](https://openai.github.io/openai-agents-python/zh/guardrails/)

统一错误为 OpsMeshError / ErrorInfo：code、message、retryable、details。SDK 边界映射配置、资源、模型、护栏、工具、超时与恢复错误；错误输出经过受控处理，不把秘密或完整上游响应直接暴露。非幂等工具失败不能通过重跑整个 Run 无条件重试。

RunCheckpoint 保存 SDK 版本、Agent 图指纹、状态摘要与 run/session 关联，执行尝试有 fencing。审批只接收主体、决定与 expected_revision，服务端加载原生 RunState。跨后端状态不可互换，也不宣称所有外部镜像、秘密版本和资源内容完全可复现。[Human-in-the-loop](https://openai.github.io/openai-agents-python/zh/human_in_the_loop/)

## 10. PostgreSQL、Redis、事件与可观测性

SQLAlchemy 当前使用模块拥有的泛型版本记录，显式版本化 migrate/verify_schema；CLI init/migrate 建表，serve/worker 只检查，不在启动时默默更改 schema。CAS 更新与 Outbox 事件同事务。真实 PostgreSQL 17.11 已验收迁移互斥、schema 漂移拒绝、跨连接 CAS、Outbox 回滚/行锁、租约 fencing、全局 capacity 和完整应用审批重启；网络故障与高可用尚需验收。

Outbox 提供 lease claim、owner 检查、指数重试、死信和重投，交付语义为 at-least-once。多个 worker 不并发持有同一有效租约；外部副作用仍需业务幂等。Redis 辅助原子限流和租约，不是业务唯一账本。真实 Redis 临时实例验证了 Lua 比较 token、续租、限流与旧 owner 保护，未做集群网络分区测试。

运行容量采用共享数据库 CAS 租约，续租失败取消执行任务，避免将每进程 semaphore 误称为全局限额。断网进程的外部副作用仍受实际执行环境和远端幂等约束。

当前观测包含 JSON 日志、持久审计、Prometheus 指标、SDK span 桥接、本地 OTel JSONL 导出与受控敏感字段。模型请求、工具、队列、恢复与原生资源收尾关联 run/session/attempt；远端 collector、长时压力和跨环境传播需要部署验收。[Tracing](https://openai.github.io/openai-agents-python/zh/tracing/)

Usage 按请求 ID 幂等，保留 input/output/cache/reasoning 及提供方数值字段；父累计与子请求禁止双记。只有没有请求级记录时才采用最终累计 fallback。缺价为 unpriced，缺用量为 unavailable。资源计量支持明确单位/数量，Hosted 调用数不等同供应商的最终账单，也不能把运行墙钟时间当作 CPU 秒。

费用使用 Decimal、币种和版本价格；预算按 principal+currency CAS 预占，记录费用后结算释放剩余 hold，避免双算。未知或币种未决的费用保持 pending；没有 FX、完整供应商账单对账或部门预算体系。在途模型请求可能超估算，事后 token 计量不是供应商绝不超支的强保证。[Usage](https://openai.github.io/openai-agents-python/zh/usage/)

## 11. 验证与扩展边界

- [原生 Memory](../../tests/runtime/test_sandbox_memory.py)、[独立子沙箱](../../tests/runtime/test_isolated_sandbox.py)、[快照/会话来源](../../tests/runtime/test_sandbox_sources.py)：真实 SDK 0.24 + UnixLocal，真实 Shell/文件操作，离线模型。
- [Provider](../../tests/runtime/test_sandbox_providers.py)、[Docker 资源控制](../../tests/runtime/test_sandbox_docker_limits.py)：受控 client 验证授权顺序、options 传递、恢复政策和限额拒绝；不是云服务/容器真实验收。
- [资源测试](../../tests/resources/test_resources.py)、[工作区并发](../../tests/resources/test_workspace_concurrency.py)、[记忆保留](../../tests/resources/test_memory_lifecycle.py)、[物理 GC](../../tests/resources/test_content_gc.py)：真实本地存储和数据库。
- SDK 高级工具、WebSocket 与 compaction 的细分证据见[覆盖表](sdk-coverage.md)。没有将离线供应商替身测试表述为真实商业服务验收。

Claude Agent SDK、实时语音、Voice Pipeline、实验性 Codex SDK 集成、云对象存储与所有云沙箱不在当前完整交付范围。新增后端复用平台合同，但必须分别适配工具、恢复、审批和资源语义，不能仅更换类名宣称兼容。
