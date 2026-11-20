# OpsMesh 开发与架构约定

## 先确认现状，再设计

涉及框架设计、评审或修改时，先读 [docs 索引](docs/README.md)、[框架架构](docs/framework.md)和[网关流程](docs/gateway.md)，再按任务阅读相关方案、技能和源码。说明每项能力是**已有实现、局部实现、待实现方案，还是待迁移旧代码**。设计文档、文件树和历史测试不能作为能力已经实现的证据。

框架基于 NoneBot2 v2.5.0，官方资料使用 [opsmesh-nonebot2](.agents/skills/opsmesh-nonebot2/SKILL.md) 中固定版本的本地快照。文档解释上游用法；本地源码决定当前行为。查清调用者、参数、返回值、异常、资源所有者和释放路径后再提出改动。

## 已有能力与边界

下表是源码导航，详细文件树和调用链见[框架架构](docs/framework.md)。代码变更时同步更新状态。

| 已有能力 | 源码入口 | 不能据此推断的能力 |
| --- | --- | --- |
| `init`、`run`、配置及 Driver 选择 | `src/opsmesh/__init__.py`、`config.py` | 重新调用 `init` 会重建运行实例；配置变化会更新所有已持有的对象 |
| Driver 启停钩子、任务组、适配器注册 | `src/opsmesh/internal/driver/` | 已有完整的单插件启动、停止和资源回收协议 |
| 传输、适配器、事件与消息抽象 | `src/opsmesh/drivers/`、`internal/driver/model.py`、`internal/adapter/` | 所有业务协议接入和通用回复契约均已完成；事件处理器会自动注册协议入口 |
| `on_*`、Matcher、Rule、Permission、Depends、事件分发 | `src/opsmesh/plugin/on.py`、`message.py`、`internal/matcher/`、`dependencies/` | 新增协议需要另建业务分发器；handler 的 `return` 会直接成为 HTTP 响应 |
| Plugin/PluginMetadata、内存注册、加载、启停开关、基础卸载、`require` | `src/opsmesh/plugin/` | 已有安装仓库、版本事务、完整依赖图或通用安全热替换能力 |
| 插件配置、通用依赖配置、声明式适配器装配和专属网关分发 | `src/opsmesh/plugin/config.py`、`src/opsmesh/message.py` | 重新读文件会重建资源；配置自动安装或启用依赖；分发约束等于插件沙箱或跨网关会话隔离 |
| FastAPI OpenAPI/Swagger/ReDoc 配置项 | `src/opsmesh/drivers/fastapi.py` | 已声明业务 HTTP API、鉴权、请求模型和错误响应 |

具体改造状态和协议边界以对应架构文档为准。旧 `packages/channels` 已删除；依赖旧通道抽象的示例和测试尚未迁移到独立 QQ/钉钉适配器。

## 架构原则

- **改框架代码前必须先询问用户。** 拟新增、修改、移动或删除 `src/opsmesh/` 下的代码时，先列出具体文件、原因及行为影响，得到用户对该范围的明确确认后再动手。其他任务的授权不等于框架改动授权；只读核对无需确认。
- 保留 NoneBot2 的轻量结构，优先复用已有机制。`plugin/` 是加载与注册机制，`plugins/` 是内置业务插件，不能混淆。
- Driver 负责传输和进程生命周期；Adapter 解释协议；Gateway 表示事件来源与回复能力；业务插件使用框架事件、`on_*` 和依赖注入。
- 相同业务语义应复用相同事件契约与现有分发机制，协议专属逻辑留在适配器。
- 框架维护内存插件对象和 Matcher，不承诺进程内替换框架。
- 不自行增加旧接口兼容分支和双模式入口。必要新增必须先证明现有能力的具体缺口。
- `async def`、`await` 本身不表示持久占用。设计停机/卸载时追踪实际正在执行的调用、会话、后台任务和连接，以及它们的退出与清理路径。

## 设计必须完整

**功能设计需要尽量完善，但禁止过度设计。** 完整是把当前需求的正常流程、失败恢复、职责边界、数据和接口说明清楚，不是增加更多模块、抽象或配置项。按需求的实际复杂度设计；优先复用已有能力，不为假设的未来需求引入通用引擎、并行机制、重复状态或空模块。每个新增字段、接口和文件都应有当前需求对应；能用现有结构完成的，不另造一套。明确不支持的边界也是完整设计的一部分，无需为了覆盖所有可能场景扩大框架。

**不能只设计一个入口、一个类或一条成功路径，就宣称架构设计完成。** 先给出完整整体设计，再按阶段实现；尚未决定的关键问题要列出、说明影响，并标记方案未闭合。现有功能的小修复按影响范围说明即可，不为简单改动制造额外架构。

涉及新功能或架构调整时，方案必须覆盖：

1. **目标与现状**：使用场景、范围、约束；逐项列出已有能力、复用方式、实际缺口和明确不做的内容。
2. **设计思想与职责**：为什么采用该结构、主要取舍、模块职责、依赖方向、对象/资源所有者，以及各组件的改动范围。
3. **完整文件树**：标明现有、修改、新增、删除、待迁移目录/文件；说明文件职责与关键接口，避免只有目录名或空类。区分现状树与目标树。
4. **端到端流程**：从启动和输入，到分发、业务执行、回复、结束清理，覆盖失败和恢复。
5. **接口与数据契约**：函数签名、配置来源与优先级、事件/消息语义、请求/响应、错误码、接口版本和依赖约束。HTTP 按[OpenAPI 约定](docs/openapi.md)说明。
6. **状态与生命周期**：内存状态与持久状态、加载/启停/卸载/升级、依赖顺序、并发管理操作、进行中请求、会话和后台资源如何处理；按场景说明哪些不适用及原因。
7. **异常与恢复**：超时、取消、部分失败、回滚、崩溃后重启、幂等和重试边界；明确操作何时算成功，哪些失败需要服务重启。
8. **运行与安全**：部署、启动、自启、日志、状态观测、权限、凭据存储、协议输入校验及服务不可用时的行为，按实际功能给出具体约束。
9. **实施与验证**：跨包影响、迁移/删除范围、阶段依赖、验收条件和后续验证方法。不能只补框架接口却遗漏调用方、文档及旧代码收尾。

设计应形成一份可以连续阅读和落实的方案，修订时同步相关章节，不在末尾反复叠加互相矛盾的补丁。完整不等于文件多：优先沿用现有文件与调用链，新增层次必须解释其必要性。

当前架构梳理阶段以设计为先；未要求实施时不顺手改源码，也不以反复运行测试代替设计核对。明确实现范围后再实施并集中做受影响验证。用户明确要求暂不测试时遵守；文档链接、引用及差异检查可以进行。

## docs 的职责与维护

| 文档 | 用途 |
| --- | --- |
| [docs/README.md](docs/README.md) | 文档导航、状态和阅读顺序 |
| [framework.md](docs/framework.md) | 现行框架架构、文件树、职责、调用链及设计思想；包含 GitNexus 核对依据 |
| [gateway.md](docs/gateway.md) | 已完成的网关改造、现有接口、登记/事件/命令/回复/注销流程及能力边界 |
| [openapi.md](docs/openapi.md) | 已有 HTTP/OpenAPI 能力、业务接口边界和完整接口契约要求 |

每份架构/接口文档标注核对日期、状态、适用范围和源码入口。已有实现与提案不得混写。关键行为只维护一个权威章节，其余文档链接引用；变更后检查引用方是否仍正确。历史 README、示例、测试或调研不能覆盖当前代码事实，也不能覆盖已确定的设计边界。

### 重构完成后的文档收尾

**重构完成必须同步更新现行架构文档，并删除已经被替代的旧重构/迁移方案；文档收尾是完成条件，不是可选的后续工作。**

1. 按实际源码把最终设计思想、职责边界、文件树、接口、完整流程、生命周期与限制写入对应正式文档，删除“准备改为”“计划新增”等已过期描述。
2. 删除旧方案前检查其中尚未实现的独立事项：仍需要的内容移入对应功能的有效设计并标明待实现，已放弃的设计不带入现行架构；不得将未完成内容一并标为完成。
3. 正式文档接管有效内容后，删除旧重构文档。不要通过重命名或保留“旧版/最终版/新版”多份方案继续维护同一事实；演进历史需要时查 Git。
4. 同步 docs 索引、根 README、AGENTS、技能入口及其他引用，搜索被删除的路径与失效章节锚点，确保没有悬空链接。
5. 完成报告说明本次实际落地范围、正式文档位置及剩余独立事项。只完成局部时标明局部，不把其他包迁移或 HTTP 接入一并宣称完成。

## 接口文档约定

新增或变更接口时，同步说明输入、输出、认证、错误、超时、取消和版本兼容。按接口实际类型选择文档形式；HTTP 契约使用 [OpenAPI 约定](docs/openapi.md)。实现状态和示例必须一致，不能将设计中的接口描述为已提供。

## 使用 GitNexus 梳理代码

架构梳理、调用链分析和影响评估使用 `gitnexus-exploring` 等对应技能。先确认仓库，再查索引新鲜度，不能只看提交号：本仓库未提交的恢复/改名也会使图谱失效。

优先使用可用的 GitNexus MCP；不可用时使用仓库 runner，在仓库根目录执行：

```powershell
node .gitnexus/run.cjs list
node .gitnexus/run.cjs status
# 仅在索引过期时刷新；避免自动覆盖项目文档和技能
node .gitnexus/run.cjs analyze --index-only
node .gitnexus/run.cjs query handle_event -r OpsMesh -l 3
node .gitnexus/run.cjs context handle_event -r OpsMesh -f src/opsmesh/message.py -l 8
node .gitnexus/run.cjs trace handle_event check_and_run_matcher -r OpsMesh --from-file src/opsmesh/message.py --to-file src/opsmesh/message.py
```

按 **概念检索 → 符号上下文 → 调用路径 → 源码核对** 组织调查。核对动态导入、装饰器、回调、异步任务、异常和返回路径；静态图谱没有边不代表运行时没有调用。记录仓库、索引时间/状态、关键符号与文件，并将稳定结论写入现行架构文档。不要把整份索引、查询日志或数据库放入架构说明。

工具缺失、索引刷新失败或路径无法解析时如实记录限制，继续可完成的源码分析，不编造 GitNexus 结果。不得拿旧图谱中的已删除模块作为当前架构依据。

## 按任务读取技能

| 技能 | 说明 | 何时使用 |
|---|---|---|
| [opsmesh-nonebot2](.agents/skills/opsmesh-nonebot2/SKILL.md) | NoneBot2 官方文档快照与源码入口 | 涉及 Driver、Adapter、事件、Matcher、依赖注入或框架插件加载时 |
| [opsmesh-agents-integration](.agents/skills/opsmesh-agents-integration/SKILL.md) | 原生 Agent、插件边界与上下文 | 设计或修改智能体插件、模型接入及 SDK 依赖时 |
| [opsmesh-agents-runtime](.agents/skills/opsmesh-agents-runtime/SKILL.md) | Runner、流式、Session 和原生审批状态 | 设计或修改 execution 接入及调用生命周期时 |
| [opsmesh-agents-tools](.agents/skills/opsmesh-agents-tools/SKILL.md) | 原生工具、技能引用、Guardrails 和 MCP | 编写工具插件、接入技能或 MCP 时 |
| [opsmesh-agents-sandbox](.agents/skills/opsmesh-agents-sandbox/SKILL.md) | SandboxAgent、文件来源和资源生命周期 | 设计或修改沙箱与技能挂载时 |
| [opsmesh-agents-verification](.agents/skills/opsmesh-agents-verification/SKILL.md) | Usage、Trace 与受影响测试 | 修改观测、统计或 SDK 兼容性时 |
