# 2026-11-01 源码审查

> 历史审查记录：下文 967 项测试和“未实现”状态对应本日较早的修订。
> 后续实现、接口及职责调整以[补齐与架构复核](2026-11-01-platform-follow-up.md)为准。

本报告区分实际接口、当前限制和本轮修复。审查依据为工作区源码，不把历史设计、示例配置或已有目录当作功能已实现的证明。本节没有调用外部模型，也没有执行 Linux 安装或升级。

## 最终本地验证结果

全部本轮修复合入工作区后，主线完整本地验证已通过。以下结果覆盖模型校验、项目入口、会话审批等待、确认投递后的收尾恢复和知识写入升级保护；后文故障输出是修复前的历史复现，不代表当前仍失败。

| 检查 | 最终结果 |
| --- | --- |
| `uv run pytest` | **967 passed, 30 skipped，476.41 秒** |
| `uv run ruff check .` | 通过 |
| `uv run ruff format --check .` | 通过 |
| `uv run mypy` | **383 个源码文件通过** |
| `uv run python scripts/check_repository.py` | 文档链接、静态依赖、源码职责及包内容检查通过 |
| `uv build` | wheel 与 sdist 构建通过 |

30 项跳过测试不计为通过，也不据此声称外部 QQ、Jev、模型或 Linux 部署已完成端到端验收。平台默认模型 API、个人凭据 API 和自动在线更新仍未实现，完整本地检查通过不改变这些功能边界。

## 模型配置、凭据与默认模型

模型档案和固定版本绑定已经实现；平台默认模型、个人凭据管理尚未实现。不能把现有管理员配置接口描述成普通用户可以自行上传密钥的完整功能。

| 能力 | 实际接口与行为 | 状态 |
| --- | --- | --- |
| 创建模型档案 | `POST /api/v1/models`；`ModelManagement.create(profile, actor_id=...)` | 已实现，检查 `model:manage` |
| 更新模型档案 | `PUT /api/v1/models/{id}`，请求包含 `profile` 与 `expected_revision` | 已实现，旧版本保留，新版本递增 |
| 读取档案与版本 | `GET /api/v1/models`、`/{id}`、`/{id}/versions` | 已实现；列表要求 `model:read` 的通配授权，并非按个人所有权筛选 |
| 撤回或恢复版本 | `GET/PUT /api/v1/models/{reference}/availability` | 已实现，独立可用性版本和审计 |
| 查询引用 | `GET /api/v1/models/{reference}/references` | 已实现 |
| 平台默认模型或默认端点 | 无默认模型字段、选择逻辑或专用 API | **未实现** |
| 个人模型所有权 | `ModelProfile` 无 `owner_id`；使用角色授权控制管理和使用 | **未实现个人资源语义** |
| 用户上传或更换个人 API Key | 无 Secret 管理 API；只有 `env:NAME` 解析器 | **未实现** |
| 密钥按用户加密存储与读取授权 | 当前解析器直接读取已部署环境变量 | **未实现** |

合同见 [models/api.py](../../src/opsmesh/modules/models/api.py)，HTTP 见 [routes/models.py](../../src/opsmesh/entrypoints/http/routes/models.py)，受保护用例见 [models/management.py](../../src/opsmesh/modules/models/management.py)。`ModelProfile` 包含 `model`、`base_url`、`api`（`responses` 或 `chat_completions`）、`secret_ref`、默认模型参数和能力声明；数据库只保存密钥引用。

[AgentPublicationValidator.validate](../../src/opsmesh/application/agent_validation.py) 要求已发布的 OpenAI Agent 提供带 `@revision` 的 `model_profile_ref`。SDK 后端虽然保留没有 profile 时创建默认 `AsyncOpenAI` 的分支，但这个分支不能证明平台提供默认模型；正常发布流程会先拒绝缺失的 profile。创建一个名为 `default` 的档案也不会触发默认选择逻辑。

执行链见 [agent_context.py](../../src/opsmesh/application/agent_context.py) 和 [OpenAI 模型解析](../../src/opsmesh/modules/runtime/internal/backends/openai_agents/models.py)：运行主体需要 `model:use`，每次模型请求重新检查版本是否可用和预算；密钥由 [EnvironmentSecretResolver](../../src/opsmesh/infrastructure/secrets.py) 在执行时解析。模型档案指向的 `secret_ref` 当前没有额外的 `secret:use` 检查。档案管理允许任意合法 `env:NAME` 和 HTTP(S) 端点，因此 `model:manage` 当前必须作为可信部署管理员权限。若直接向普通用户开放这个权限，他们可以把服务器已有环境变量引用与自选模型端点组合；当前系统没有个人 Secret 所有权来限制这种组合。

本轮没有扩充 Secret 存储、增加默认模型 API 或改变权限模型。上述缺项应由平台模型和凭据模块完成，不能靠业务示例保存明文密钥来绕过。

### 本轮修复：模型校验只有一个实现

原 HTTP 层和目录内部各有 `validate_profile`，HTTP 副本遗漏 URL fragment 检查，而目录内部会拒绝，已出现规则漂移。现在将原内部完整规则提升为 [models/validation.py](../../src/opsmesh/modules/models/validation.py) 中的 `validate_model_profile`，通过 `models.api` 公开导出；HTTP 创建/更新、受保护管理服务和目录创建/更新共用该实现。HTTP 不导入内部实现。

共用规则拒绝 URL 中的用户名、密码、query、fragment、非 HTTP(S) 端点，拒绝非 `env:` 凭据引用，以及配置中嵌套的凭据字段。它仍允许由可信管理员部署的本地 HTTP 模型服务，不声称实现端点 allowlist 或个人密钥管理。

[跨入口回归](../../tests/http/test_model_profile_validation.py) 验证 HTTP、管理服务、目录和公开校验函数拒绝同一组错误配置，失败后不生成档案、不增加版本、不在错误正文返回测试密钥；同时验证合法端点和引用可以创建、更新并保留历史。

修复阶段的定向验证：`tests/http/test_model_profile_validation.py`、`tests/resources/test_resource_management.py`、`tests/platform/test_services.py` 共 **9 passed**；涉及文件 Ruff、格式检查及 7 个模型/HTTP 源文件 mypy 通过。这些测试也已纳入上述最终全量结果，没有新增联网测试。

## Agent 绑定与能力管理接口

一个 Agent 可以直接绑定一个人设 Skill、一个业务 Jev 工具和一个平台知识集合。无需为知识检索另建 Agent 或业务搜索工具。以下路径均以 `/api/v1` 为前缀。

| 操作 | 接口 | 主要实现 |
| --- | --- | --- |
| 创建、编辑、发布 Agent | `POST /agents`、`PUT /agents/{id}`、`POST /agents/{id}/publish` | [agents.py](../../src/opsmesh/entrypoints/http/routes/agents.py)、[AgentSpecification](../../src/opsmesh/framework/agents/specification.py) |
| 绑定模型、技能、工具、知识 | 在 Agent 规格中提交 `model_profile_ref`、`skill_refs`、`tools`、`knowledge_collections`、`workspace_id`，再发布版本 | [agent_validation.py](../../src/opsmesh/application/agent_validation.py) |
| 注册已部署工具及查询、调用 | `POST /tools`、`GET /tools`、`GET /tools/{ref}`、`POST /tools/{ref}/invoke` | [capabilities.py](../../src/opsmesh/entrypoints/http/routes/capabilities.py) |
| 发布工作区技能 | `POST /skills`，含 `name`、`version`、`workspace_id`、`directory` | 同上；目录必须位于已授权工作区内 |
| 查询及物化技能 | `GET /skills`、`GET /skills/{ref}`、`POST /skills/{ref}/load` | [SkillManagement](../../src/opsmesh/modules/capabilities/skill_management.py) |
| 固定 Git 来源技能、脚本工具 | `POST /skill-imports/git`、`POST /scripts`、`GET /scripts`、`GET /scripts/{ref}` | [resource_sources.py](../../src/opsmesh/entrypoints/http/routes/resource_sources.py) |
| 工具/技能/MCP/脚本版本和可用性 | `GET /capability-versions/{kind}`、`GET/PUT /capability-versions/{kind}/{ref}/availability`、`GET .../{ref}/references` | [capability_versions.py](../../src/opsmesh/entrypoints/http/routes/capability_versions.py) |
| 知识集合与文档管理 | `POST/GET /knowledge/collections`、`GET/PUT/DELETE /knowledge/collections/{id}`；文档增删改查、文本导入、重建与任务查询 | [knowledge.py](../../src/opsmesh/entrypoints/http/routes/knowledge.py) |
| 知识检索 | `GET /knowledge/{collection}/search`；Agent 绑定后自动获得 `search_knowledge` | [resource_compiler.py](../../src/opsmesh/modules/capabilities/internal/resource_compiler.py) |

`POST /tools` 注册的是工具定义。Python `code_ref` 必须已由可信扩展注册，HTTP 不能上传任意 Python 回调；脚本工具通过单独的脚本发布和隔离执行机制管理。Agent 发布会验证工具回调是否存在、绑定 schema 是否与目录一致，以及 Skill/MCP 引用是否存在；运行时另外检查调用主体的工具和能力权限。

技能绑定需要工作区，运行主体需要工作区写权限及 `capability:use` 对 `skill:<ref>` 的授权。`skill_loading=eager` 表示提前物化文件，不等同于把人设正文自动注入系统指令；编译器提供 `load_skill`、`read_skill` 和技能目录说明。Agent 的指令需要明确要求使用人设技能，或使用已部署的动态指令机制。

平台生成的 `search_knowledge` 只允许查询此 Agent 绑定的集合，每次调用以运行主体检查 `capability:use` 对 `knowledge:read:<collection>` 的授权。文档写入通过公开 `KnowledgeManagement` 或 HTTP 文档接口，检查 `knowledge:write:<collection>`。当前自动生成的是检索工具，没有自动问答整理和写入工具。知识数据由平台数据库保存，不要求业务工作区再维护一份知识目录。默认 embedding 仍是确定性 hash 实现，不能描述为已部署预训练语义向量模型。

会话处理链另外调用公开知识接口，幂等保存**已审核、已确认送达的原始问答记录**，包含来源事件、原始问题和实际发送的答案，并标记 `record_type=raw_conversation`、`authoritative=false`。这项捕获没有提炼、去重归纳或核验成权威 FAQ，不是自动 FAQ 整理，也不创建第二个 Agent、独立 MemoryNamespace 或工作区知识镜像。保存失败按既有记录重试或留待处理，不能把未送达、未通过审核的回答作为已完成知识记录。

额外边界：当前发布校验没有检查 `knowledge_collections` 对应集合是否存在或发布者是否能读；运行查询会检查实际主体权限。HTTP 沙箱配置只允许受限 Docker 配置，拒绝原生 state、host manifest、任意 provider options 等；更详细的部署资源配置通过可信部署用例提供。以上属于现有边界，不应以示例绕过为完整管理 API。

## 设置与 Linux 升级的实际边界

[OpsMeshSettings](../../src/opsmesh/bootstrap/settings.py) 从进程环境的 `OPSMESH_<FIELD>` 读取部署设置，包含数据库、Redis、Worker、追踪、存储、脚本执行和记忆生成专用模型。它没有平台默认模型字段，也不会因向配置表写入同名键就热更新当前进程环境。

`GET /api/v1/configuration`、`GET/PUT /api/v1/configuration/{key}` 是带授权、审计和版本检查的持久配置接口，见 [management.py](../../src/opsmesh/entrypoints/http/routes/management.py) 和 [ConfigurationManagement](../../src/opsmesh/modules/configuration/management.py)。当前明确消费的内置类型配置是运行准入 `admission`；其他任意键仅保存为数据。不能把这个通用接口称为已经支持默认模型设置、凭据录入或全部部署参数在线修改。

Linux 安装和显式升级已实现，见 [install.sh](../../deploy/install.sh)、[common.sh](../../deploy/install/common.sh)、[local.sh](../../deploy/install/local.sh)、[docker.sh](../../deploy/install/docker.sh) 和 [部署说明](../../deploy/linux.md)：

- 必须先取得并解压新的 Linux 发布包，再执行 `sudo bash install.sh --upgrade`；默认本地 systemd，Docker 显式指定 `--mode docker`。
- 安装包校验 `VERSION`、唯一 wheel 和 `SHA256SUMS`。本地模式复用可被服务账户访问的 Python 3.11–3.14；否则在线下载受管理 Python 3.13，随后按锁文件安装依赖。安装包不携带 Python 环境。
- 新发布目录及环境准备完成后停止应用，执行登记的迁移和 schema 检查，切换 `current` 并按选项启动、检查就绪。旧发布目录、配置和数据保留。
- `--no-start` 与升级可组合，升级后保持应用停止；重复安装相同制品不会重建已有数据。更换部署模式需使用独立实例。

**未实现** GitHub 最新版本查询、通过 HTTP 或 CLI 一键下载应用更新、无人值守在线更新任务、自动备份与失败自动回滚。安装器中的联网下载主要服务于 uv、Python 和依赖准备，不是 OpsMesh 自更新。`opsmesh migrate` 只应用已登记数据库迁移，`opsmesh check` 当前只校验数据库 schema；保留旧代码目录也不等于数据库能自动降级。升级前的备份、失败后的恢复与兼容性判断仍需要显式操作。

本次对升级仅做静态调用链审查，没有在此工作区复跑 Linux 安装验收；已有安装测试脚本的存在不代表本次已经完成外部环境验证。

## 本地机器人项目入口审查与修复

旧 `bootstrap/projects.py`（现已删除） 是本地声明到平台公开服务的组装入口；旧 `entrypoints/cli/projects.py`（现已删除） 负责 `validate/apply/serve`，不包含特定 QQ 社区或 Jev 业务规则。平台的 Agent、模型、Bot 和知识集合仍由各自服务持有状态。

本轮发现并修复了以下有实际触发条件的问题：

- YAML 中 `${GROUP_ID}` 作为 `group_member_openids` 的字典键时，原解析器只展开值，导致配置绑定保留字面占位符。现同时展开键和值，拒绝展开后碰撞的键、非字符串键、缺失工作区和递归/过深结构；`env:NAME` 凭据引用仍不读取真实密钥。
- `validate` 原先只确认对象创建成功，未比较传入 Agent 与项目资源；现调用统一的声明校验，并明确输出只验证本地声明，没有验证平台发布、部署回调和外部提供商可用性。
- `apply/serve` 原先直接调用 `app.initialize()`，会在管理员认证前或普通启动时隐式创建/迁移表。现只调用 `verify_schema()`；首次管理员初始化与迁移仍通过明确的 `opsmesh init`、`opsmesh migrate` 完成。
- 管理员认证现在先于策略客户端的启动。应用停止位于策略资源上下文内部，异常启动时也先停止消费者、关闭应用，再释放模型审查客户端等调用方资源。
- `serve` 对保存的单个绑定检查 Bot、群、Agent 标识、工作区和知识集合，拒绝使用其他项目资源的配置；此检查不替代运行时资源授权。
- `apply` 在修改任何项目资源前取得同一个 `consumer.lock`；正在运行的消费者、已有 `claimed/running` 队列批次或待完成的 `conversation-capture:` 知识写入记录会导致 `CONVERSATION_BINDING_BUSY`。锁覆盖发布和配置文件的原子替换，失败时关闭临时队列连接并释放锁。未完成批次和知识写入须先在原绑定下排空或恢复，不能先修改 Agent/角色后再启动旧任务。公开部署用例仍要求调用方在消费者启动前使用；此锁保护属于本地命令入口。

[项目入口回归](../../tests/application/test_conversation_reconfiguration.py) 在修复阶段 **22 passed**，验证真实 SQLite 未完成批次及 Run 引用在拒绝更新后仍存在、待完成知识写入阻止绑定升级、排空后可以发布、真实文件锁冲突及失败释放、配置替换失败保留旧文件、无隐式迁移、无认证前客户端启动和异常关闭顺序。这些回归已纳入最终 **967 passed** 的完整本地结果；不代表 QQ、Jev 或模型端到端验收。

最终复核曾复现：所有消息批次都已 `replied`，但还有独立知识写入任务时，原 CLI 预检允许 `apply`；随后服务启动发现新 Agent 绑定与待写入记录原绑定不同而拒绝启动。CLI 已补充上述知识写入预检，与服务启动保护一致，避免更新后才发现无法用旧权限继续收尾。

## 会话恢复问题的历史复现与已完成修复

本轮原始入口和初始实现曾在独立故障场景复核中触发下列两项 P2，不能把修复前的状态记为通过。两项现已修复，跨层回归和最终完整本地测试均通过。以下失败状态及数字仅保留为历史问题与修复依据，不是最终验证结果；外部环境验收仍须单独记录。

| 问题 | 修复前的实际复现 | 修复归属与当前状态 |
| --- | --- | --- |
| P2：待审批 Run 导致批次永久失败 | [operations.py](../../src/opsmesh/application/conversations/operations.py) 返回 `CONVERSATION_RUN_WAITING_APPROVAL` 后，原 [coordinator.py](../../src/opsmesh/application/conversations/coordinator.py) 将批次标为 `failed`。真实 SQLite 队列保留 Run ID，但默认待处理查询返回空；下一轮不再读取该 Run，外部审批完成也不能继续回复。CLI 还会误认为没有活跃批次而允许更换绑定。 | 会话实现负责人已修复：审批等待保留原批次/Run，持久化下一次检查时间并有限退避，完成审批后继续同一运行。跨协调器、工作器和真实队列回归通过，并纳入最终完整本地测试。 |
| P2：已确认发送后的本地收尾失败无法恢复 | [chat.py](../../src/opsmesh/application/conversations/chat.py) 已取得发送成功 receipt 后，注入一次本地 capture 写入 `OSError`。原协调器将批次标为 `failed`，结果为 `confirmed_sends=1`、`capture_count=0`；恢复存储后再次执行协调器仍不处理此批次。原有直接重复调用工作器的测试没有经过这个失败状态转换。 | 会话实现负责人已修复：已确认投递后的本地提交失败标为可恢复 `CONVERSATION_COMMIT_PENDING`，保留批次并使用既有发送幂等标识恢复收尾；`DELIVERY_UNKNOWN` 仍禁止自动重放。[跨层故障回归](../../tests/application/test_conversation_workers.py) 通过，并纳入最终完整本地测试。 |

上述复现仅使用临时 SQLite 与受控平台操作，没有重新调用模型或向机器人发送消息。待写入知识记录阻断升级的 CLI 修复由入口审查负责人完成，22 项定向测试已通过；审批及提交恢复由会话实现负责人独立修改，避免并行编辑同一状态机。
