# 文件记忆设计与实现

状态核对：2026-10-30。平台托管生成与 OpenAI SDK 原生 Memory 生成均已有实现；两条路径使用不同的发布机制，必须明确选择。数据库 Session 历史、文件记忆、向量知识库与 Runner 检查点分别管理。

依赖固定为 `openai-agents==0.24.0`，对应源码提交 `17d994bd3045dae71acda676a4ef4762ed8ecd94`。原生适配核查并使用正式安装包，涉及 SDK 内部管理器的兼容代码会检查精确版本；升级必须重新验收。依据为[官方 Memory 文档](https://openai.github.io/openai-agents-python/zh/sandbox/memory/)及[该版本 Capability](https://github.com/openai/openai-agents-python/blob/17d994bd3045dae71acda676a4ef4762ed8ecd94/src/agents/sandbox/capabilities/memory.py)。

| 范围 | 当前实现与证据 | 实际边界 |
|---|---|---|
| 平台记忆 | namespace ACL、来源、两阶段任务、候选 CAS 发布、live update | 产物为 summary/index JSON；不自动生成 SDK 全套目录 |
| 原生 Memory | 授权模型包装、独立用量、严格失败、关闭前生成与文件捕获 | SDK 0.24 专用；原生文件发布不等于平台 namespace CAS 发布 |
| 运行中更新 | `MemoryService.update`、revision CAS、幂等键、授权读写工具 | 只在 policy.live_update=true 时开放；没有单独内容审批工作流 |
| 保留策略 | 来源和终态任务 TTL，保护活动任务输入，清理阶段记录 | 显式 `prune`；没有自动备份删除、单来源撤回或完整版本浏览 |
| 物理回收 | 本地对象 GC、全库引用扫描、对象 lease、二次检查、dry run | 真正删除需停止写入并显式 maintenance；被历史/审计/Outbox 引用的内容保留 |
| 验证 | 真实 SQLite、真实 SDK Runner + UnixLocal 文件生成、离线模型响应 | Memory模型响应受控；Linux真实Docker两阶段生成、捕获与清理已通过 |

相关代码：[公开合同](../../src/opsmesh/modules/memory/api.py)、[服务](../../src/opsmesh/modules/memory/internal/service.py)、[原生适配](../../src/opsmesh/modules/runtime/internal/backends/openai_agents/sandbox_memory.py)。

## 1. 所有者与数据边界

| 对象 | 所有者 | 含义 |
|---|---|---|
| 业务 Session | modules/sessions | 用户、Bot、会话成员、Agent 绑定 |
| 模型交互历史与 RunCheckpoint | modules/runtime | 模型条目、工具结果、执行进度、待审批项 |
| MemoryNamespace / Policy / Source / Job | modules/memory | 权限、来源、生成任务、版本发布、保留策略 |
| 内容对象与 WorkspaceSnapshot | modules/workspaces | 来源正文、候选 JSON、文件内容及快照 |
| 模型调用与成本 | modules/runtime + modules/usage | 模型执行、请求用量、价格、预算与结算 |
| 原生 Memory 文件生命周期 | OpenAI backend + bootstrap | SDK capability、session、flush、文件归档与清理 |
| 检索能力 | modules/capabilities | 文档检索；独立于记忆生成和发布 |

恢复聊天历史不会自动恢复文件记忆；恢复文件目录也不能恢复审批进度。SDK Memory 是 SandboxAgent 的 Capability，不是 SDK Session 数据库。

平台实现目录：

```text
modules/memory/
├── api.py                 # namespace/policy/source/job/candidate 与服务合同
├── events.py              # 生成请求、版本发布事件
└── internal/
    ├── service.py         # ACL、来源、读取、live update、删除和查询
    ├── requests.py        # 输入集合与任务事件同事务提交
    ├── generation.py      # 两阶段执行、租约、阶段保存与 CAS 发布
    └── retention.py       # TTL 清理与活动输入保护
```

工作流通过公开 WorkspaceService 和注入的提取/整合 callback 工作。`application/memory_bridge.py` 将 callback 接入平台 Run，`application/memory_tools.py` 为 Agent 绑定读写工具。runtime 不重复拥有一套记忆任务仓储。

## 2. 平台托管生成

推荐默认使用平台托管模式：AgentSpecification.memory_namespace_id 选择命名空间，memory_generate 控制来源提交。MemoryPolicy 默认 read=true、generate=true、live_update=false；生成模型由 extract_model_profile / consolidate_model_profile 选择版本化模型档案，缺少配置明确失败。

流程如下：

1. 授权来源提交，实参 principal_id 覆盖客户端提供的身份；正文按内容摘要持久化。
2. `request_generation` 持久化来源集合、策略、基线和任务，并在同一事务写生成请求事件。
3. worker 以数据库租约/CAS 领取任务。对每个来源执行提取，验证来源 ID、空结果规则并保存阶段产物。
4. 整合已保存的提取结果及上一版记忆，生成 summary/index 候选；候选校验通过后保存为不可变内容对象。
5. 用 namespace expected_revision 发布候选。过期候选不能覆盖新发布版本，失败保留上一版内容。
6. 任务与阶段结果支持重试复用。模型调用仍可能因外部请求结果未知而重新收费；系统不承诺供应商请求 exactly-once。

两个阶段通过普通平台 Agent Run 执行，沿用授权、预算、审计、取消和用量接线；不是调用 SDK 私有后台任务。生成模型缓存身份包含 namespace、主体、模型档案、来源内容和指令摘要，避免不同项目的同名来源串用。

[生成工作流](../../src/opsmesh/modules/memory/internal/generation.py)与[平台桥接测试](../../tests/platform/test_memory_bridge.py)覆盖两个 namespace、四个独立受计量 Run；这些模型响应为受控离线实现。

### 运行中更新

当 policy.live_update=true，`update(namespace_id, candidate, principal_id, expected_revision, idempotency_key)` 直接发布已校验的新内容：

- 只允许 owner/contributor；reader 无权写入。
- 同主体、同幂等键、同内容可以重复提交；同键不同内容冲突。
- 先保存候选，再 CAS 更新 namespace、memory_revision、发布事件；并发写者只能有一个成功。
- 不接收调用方伪造的 usage 作为计费事实。
- Agent 的读写工具随运行资源 scope 注册/释放，运行结束后撤销闭包。

这是平台共享记忆的显式更新接口，和原生 Memory 直接改沙箱文件是两种机制。单条来源遗忘后的自动重建、历史版本浏览、内容审批属于后续扩展。

## 3. SDK 原生 Memory

| SDK 配置 | 含义 | 平台接入 |
|---|---|---|
| Memory() / generate={} | 读取并在关闭阶段生成 | 支持；必须有受管理 session、版本模型档案与授权/用量 callback |
| generate=None | 关闭结束阶段生成 | 支持；读取默认仍可能 live update，不能等同只读 |
| read=None | 关闭读取，保留生成 | 支持；生成按独立阶段计量 |
| read.live_update=false | 不提供运行中改写指引 | 支持；真正写限制仍需文件权限/提供方隔离 |
| read=None 且 generate=None | 无有效能力 | SDK 拒绝 |

Memory 读取要求 Shell；live update 还要求 Filesystem。摘要注入有 SDK 内部限制，不能自行虚构一个平台字段宣称改变它。普通 Agent 的本地 context 不会自动传入 SDK 内部生成 Runner。[正式配置源码](https://github.com/openai/openai-agents-python/blob/17d994bd3045dae71acda676a4ef4762ed8ecd94/src/agents/sandbox/config.py)

原生生成适配提供以下闭环：

1. `prepare_capabilities` 为两个阶段构造独立的 MeteredMemoryModel。模型字段使用版本档案引用，缺省继承业务 Agent 的 model_profile_ref。
2. 每次内部模型请求前调用平台 authorize_model_request；结束记录独立 call_id、profile、purpose=memory_generation、stage、token/cache/reasoning 和数值用量。调用失败记录 usage_available=false，不记作免费。
3. 适配器沿用 SDK 来源与存储格式，但将其默认“记录日志后继续”的生成失败改为显式失败。生成使用 memory_timeout_seconds 限时；阶段二 max_turns 缺省取 Agent max_turns。
4. 阶段二确实运行 SandboxAgent Shell/文件工具，并继承配置的 sandbox.run_as。它使用同一受管理沙箱；不会自动继承业务 Agent 的所有护栏、工具回调或额外策略。
5. `ManagedSandboxSession.archive` 先等待所有原生记忆 flush，成功后再捕获工作区。随后关闭 session、删除底层环境、关闭模型客户端。
6. 成功的捕获 metadata 包含 memory_generations 状态与用量关联。失败向平台最终收尾传播；已记录费用仍保留，不能把业务模型返回视为全部资源结算已经完成。

该适配精确依赖 SDK 0.24 的内存管理器和布局注册接口，没有全局 monkeypatch Runner。重复成功 flush 不重复生成；多个 Agent 共享同一 session 和布局时生成配置必须一致，不同布局不得有父子目录重叠。

原生生成与同一 Agent 的 platform memory_generate 互斥，避免一次业务输出生成两遍。generate=None 的读取配置可以与平台生成共存。原生生成内容属于 workspace，不会自动发布为 MemoryNamespace 或共享 Skill。

### 原生布局与多轮标识

SDK 默认来源目录为 sessions，记忆目录为 memories，均相对 workspace root，不相对工具 cwd。可以通过 layout.sessions_dir / memories_dir 明确选择独立目录：

```text
<workspace>/
├── sessions/<rollout-id>.jsonl
└── memories/
    ├── memory_summary.md
    ├── MEMORY.md
    ├── raw_memories.md
    ├── phase_two_selection.json
    ├── raw_memories/<rollout-id>.md
    ├── rollout_summaries/<rollout-id>_<slug>.md
    └── skills/
```

SDK 的来源 ID 优先关联 server conversation / SDK Session / RunConfig.group_id，最后回退到 sandbox session ID；平台传递业务 session 关联。来源文件可累积多个调用片段，记忆不是模型历史的无损替代品。[来源保存实现](https://github.com/openai/openai-agents-python/blob/17d994bd3045dae71acda676a4ef4762ed8ecd94/src/agents/sandbox/memory/rollouts.py)

快照应包含完整来源、原始记忆和选择记录，不能只保存 MEMORY.md。生成的 skills 文件仍是工作区产物；注册为平台 Skill 需单独验证、版本发布和授权。

## 4. 保留、删除与物理 GC

MemoryPolicy 提供 source_retention_seconds 与 job_retention_seconds，默认不清理。owner 显式调用 prune：过期终态且无有效租约的任务可删除；queued/活动任务及其来源保留；任务删除后清理对应提取/候选记录。

生成任务选择来源与保留清理使用同一 namespace 数据库行锁；SQLite 使用串行写事务，PostgreSQL 使用行锁。选择输入、创建任务、Outbox 写入同事务提交，避免清理与任务提交之间出现来源悬空。不同任务仍可同时计算，在 namespace 发布时竞争 CAS；这不等同 namespace 永久单写队列。

forget 先 tombstone namespace 撤销读写/发布可见性，再删除相关来源及阶段记录。它不是所有副本即时物理擦除：发布事件、历史任务、审计、快照或其他命名空间可能仍引用内容。备份删除和单来源撤回重建尚无完整实现。

WorkspaceService.collect_garbage 默认 dry_run，扫描本地内容对象，跳过被数据库所有已注册 JSON 记录及 Outbox 引用的摘要。实际删除必须显式 maintenance=true 并停止 worker/写服务；宽限期、对象 lease 和二次引用扫描是附加保护，不能代替维护窗口。回收不删除仍被引用的对象，也不处理外部云存储备份。

[保留与并发测试](../../tests/resources/test_memory_lifecycle.py)、[GC 测试](../../tests/resources/test_content_gc.py)覆盖活动输入、候选发布、跨实例提交竞争及引用二次检查。

## 5. 成本、权限与恢复边界

平台托管阶段走独立受计量 Run；原生阶段通过模型包装调用相同计费观察入口。两者都可能额外消耗模型额度，不能并入业务请求后假定免费。缺少价格保留 unpriced，缺少用量保留 unavailable，预算保持待结算；预算当前按 principal/currency，不提供独立 namespace 或部门预算目录。

原生 Memory 的阶段二有 Shell/文件写能力，调用者必须明确接受其沙箱权限。Shell 工具并不自动逐条经过本地 ToolInvoker，读取提示也不是文件 ACL。可信 UnixLocal 是真实宿主进程执行，不能称为容器隔离。

平台托管任务可恢复已持久化阶段。原生关闭阶段使用活跃沙箱与 SDK 内部管理器；成功快照及用量事实可保留，worker 在未捕获前丢失时仍可能丢失文件修改或发生重新生成。没有对任意中途 native Memory 任务提供跨进程 exactly-once 恢复保证。

## 6. 已运行验证与尚需外部验收

- [原生 Memory 测试](../../tests/runtime/test_sandbox_memory.py)：授权、失败未知用量、阶段错误、flush 顺序；WSL UnixLocal 使用真实 SDK Runner 与真实 Shell 写出 memory_summary.md / phase_two_selection.json，模型为离线响应。
- [独立子环境测试](../../tests/runtime/test_isolated_sandbox.py)：真实子沙箱并发隔离、审批中断后重建服务和恢复文件。
- [快照与会话测试](../../tests/runtime/test_sandbox_sources.py)：snapshot_ref 新建身份，session_ref 恢复身份，文件保留，跨主体拒绝。
- PostgreSQL 17.11 已有基础设施及应用审批重启集成验证；记忆专项的多主机故障、网络分区和大规模负载仍需单独验收。
- Linux Docker29.1.3已验证原生Memory文件生成、捕获和清理，并修复计量包装模型的SDK身份序列化问题。远程mTLS Docker和S3/PostgreSQL恢复也已验收。特定云厂商、真实Memory供应商计费和跨节点故障仍须按部署验证。
