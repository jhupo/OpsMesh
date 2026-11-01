# 群聊链路、模型管理与在线更新复核

本次按真实调用链修正前一轮设计。源码边界、HTTP、SQLite、备份恢复和受控下载测试与真实服务验收分别记录，测试替身不代表外部模型或机器人已验收。

## 群聊执行链

```text
机器人事件 → 持久入队 → Jev 违规审核 → 每群合并已通过的文本
                                          ↓
                                 单个聊天 Agent / 群 Session
                                          ↓
                                Agent 决定回复或保持安静
                                          ↓
                          输出审核 → 平台投递 → 知识库幂等记录
```

Jev 不再判断是否值得回复，没有回复概率阈值。普通群消息无需 @，通过审核的文本批次全部进入 Agent。审核未知、附件无法提取文本等情况进入检查状态，不能假装模型已经阅读附件。撤回受平台权限及结果确认约束，未知结果不盲目重试。

Agent 使用 `ConversationResponse(respond, text)` 表达回复或安静。输出 schema 和返回协议属于应用 AGENTS.md，人设 Skill 保持上游原文。Jev 同一实例既注册为 Agent 函数工具，也执行入站与输出审核；知识检索由平台根据 `knowledge_collections` 自动装配。没有第二个 FAQ Agent，也没有工作区知识库镜像。

同一机器人、群、运行主体和已发布 Agent 版本共享 Session，普通聊天和主动发言共用历史；不会按群成员或发言目的再建会话。不同群隔离，新 Agent 版本属于新的会话代际，旧历史保留。当前批次执行期间的新消息等待下一批，每个 Run 仍有独立幂等键。

## 默认与个人模型

所有路径以 `/api/v1` 为前缀。

| 功能 | 接口 | 行为 |
| --- | --- | --- |
| 平台共享模型 | `POST /models`、`PUT /models/{id}` | 可信管理员配置模型、base URL、环境密钥引用，保留版本 |
| 平台默认模型 | `GET/PUT /models/settings/default` | `{reference, expected_revision}`，引用固定 `@revision` |
| 个人模型 | `GET/POST/PUT /me/models` | 自动绑定当前用户，修改使用 CAS，不读取服务器环境密钥 |
| 个人默认模型 | `GET/PUT /me/models/default` | 优先于平台默认，清空后回退 |
| 个人凭据 | `POST/GET /me/model-credentials` | 接收 Secret，仅响应元数据和固定版本 ref |
| 凭据轮换 | `PUT /me/model-credentials/{id}` | `{secret, expected_revision}`，旧引用保留原值 |
| 凭据启停 | `PUT /me/model-credentials/{id}/availability` | 禁用阻止所有版本的后续请求 |
| 模型版本与可用性 | `/models/{id}/versions`、`/{reference}/availability` | 管理服务校验所有者；全局列表不泄露个人模型 |

Agent 显式指定模型时优先使用；未指定的 OpenAI Agent 发布时选择“个人默认 → 平台默认”，并写入固定模型版本。更改默认值不会改变已发布 Agent；没有可用默认则发布失败。草稿仍可不指定模型。

默认设置不自动授予共享模型的 `model:use` 权限；发布主体和实际运行主体仍须拥有相应授权。

个人凭据使用 Fernet 加密进入数据库。部署密钥位于数据目录 `secrets/model-credentials.key`，Linux 权限 0600；解密时密钥缺失会拒绝运行，不自动生成新密钥替代。数据库、审计、HTTP 不返回密钥明文，管理员也没有读取其他用户个人密钥的 API。

个人模型不可使用服务器 `env:` 引用或其他用户的凭据。发布和每次模型请求重新检查所有权、模型可用性和凭据启停。共享机器人使用部署管理员的共享模型，避免借用某个用户的私人凭据。

个人端点要求 HTTPS、443 端口和部署批准的确切主机名。默认允许 `api.openai.com`，管理员可用 `OPSMESH_PERSONAL_MODEL_ALLOWED_HOSTS=api.openai.com,dash.ovload.com` 扩充。名单不支持通配符；允许一个主机意味着允许服务器访问该主机，应由部署管理员维护。共享模型仍可配置可信内网 HTTP 服务。

使用顺序：创建凭据取得 `ref`，创建个人模型并绑定该引用，然后设置个人默认或在 Agent 中指定 `id@revision`。修改端点或绑定新凭据时产生新模型版本，再发布 Agent。轮换密钥不会静默改变已发布版本。

## 在线更新

```sh
opsmesh update check
opsmesh update download --release-version 0.3.1 --directory ./updates
sudo opsmesh update install --instance opsmesh --mode local
```

检查和下载不构造应用、不连接数据库。安装只允许 Linux root 维护终端，目标必须已有安装；拒绝相对 CLI 或目标实例的降级。更新器读取目标实例版本，安装器在实例锁内再次比较，防止检查后被其他升级改变。

管理员 HTTP 为 `GET /operations/updates?version=...` 和 `POST /operations/updates/download`，后者接收 `{version}` 并暂存校验包。HTTP 不取得 root 权限；完整安装由 CLI 复用既有 `install.sh --upgrade`，Docker 需显式 `--mode docker`。本次是显式在线升级，不包含无人值守、自动回滚或定时更新任务。

版本来自固定 OpsMesh GitHub Releases 稳定发布，优先使用资源 SHA-256 digest，否则读取同一发布 checksum；字段依据 [GitHub Releases API](https://docs.github.com/en/rest/releases/releases)。下载和重定向均限制 HTTPS、已知 GitHub 主机与大小。拒绝路径穿越、符号/硬链接、特殊文件、重复项和未列入清单的文件；校验失败不执行安装器，不覆盖原有已验证包。

升级保留配置、数据与旧发布目录，不等于数据库可自动降级。升级前应先做离线备份。

## 文件职责与精简

| 位置 | 职责 |
| --- | --- |
| `entrypoints/cli/projects.py` | 参数、密码输入、本地 validate、转交执行，无队列编排 |
| `bootstrap/project_runtime.py` | 项目生命周期组装和 apply/serve，先停止应用再关闭策略资源 |
| `bootstrap/projects.py` | 本地 YAML 声明到公开平台管理接口的部署入口 |
| `application/deployment.py` | 通用资源发布与工作区模板更新 |
| `application/conversations/` | 中立审核、合批、Run、主动发言、投递、知识记录协调 |
| `application/conversations/reconfiguration.py` | 消费者和未收尾任务阻止修改绑定 |
| `modules/bots/conversations/` | SQLite 队列与进程锁，不声称分布式队列 |
| `modules/models/` | 档案、默认值、凭据版本、所有权与授权 |
| `infrastructure/secrets.py` | 部署密钥与加密、环境解析，无社区规则 |
| `maintenance/` | 离线备份、恢复和发布更新 |
| `examples/qq_community/` | 项目声明、人设 Skill、Jev 工具；平台不导入示例 |

全源码扫描覆盖 391 个 Python 文件；AST 检测未发现至少 25 行、至少 6 条语句的重复函数体。静态依赖检查禁止 framework 依赖平台模块、跨模块 internal 引用和核心导入业务插件。该扫描不证明没有语义重复。

保留队列锁、Run 幂等、审批等待、确认投递后收尾和知识重试，这些有故障回归支撑。本轮没有增加另一套插件注册器、配置数据库、工作流引擎或社区业务插件。

## 完整备份

离线备份覆盖数据库、工作区、blob、个人凭据部署密钥、项目绑定和群聊 SQLite 队列。备份持有每个队列消费者锁；消费者仍运行时明确拒绝。SQLite 备份 API 包含已提交 WAL，不复制瞬时锁文件。恢复保留待处理批次、Run 引用及知识捕获状态，启动时将队列路径定位到恢复后的数据根目录。

备份仍要求停止全部写入者、Worker、消费者和 GC，验证版本/插件及空恢复目标。包含解密密钥的备份须按机密保存；外部环境变量与第三方账号配置仍需另行备份。

## 本次验证结果

- 全量回归：976 passed、30 skipped，514.55 秒。随后补充两项端点/安装边界回归，并对最后改动运行模型、发布更新及安装专项：55 passed。最终共收集 1008 项测试；没有把跳过的外部测试算作通过。
- Ruff、545 文件格式检查、mypy 391 源文件、仓库边界与文档链接检查通过。wheel、sdist、Linux 安装包构建通过。
- 测试服务器完成离线备份后升级，systemd 服务 active，QQ WebSocket connected。群绑定为 `community-a1636c2719f6580c@3`，输出 schema 为 `respond/text`；平台默认为 `qq-community-model@1`，模型设置和个人接口 HTTP 200。
- 真实 Jev 审核返回 ok/allow。真实 gpt-6.1-sol 诊断 Run `run_04b1e0f72b74088b4434560343db26b0369a4474` 完成，返回 respond=true、144 字正文，3 次模型请求共 21955 tokens；执行了 load_skill、read_skill、search_knowledge、jev_group_review。诊断结果未发送到群，不能据此声称本轮已观察到新的真人群消息端到端回复。
- 服务器通过 GitHub 在线检查得到稳定版本 0.3.1；当前开发版 0.3.2.dev0 较新，正确返回 available=false。没有为测试而降级线上实例，也没有发布新 GitHub Release。
- 当前代码共 669 个非忽略文件已同步到 `D:\opsmesh` 并逐文件校验，保留本地 Git、环境和运行数据。

外部账号权限、附件内容审核、多节点故障和长期运行不由上述结果代替。审查、接口与部署证据分别限定其实际范围。
