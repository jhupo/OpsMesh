# opsmesh 设计与实现文档

核对日期：2026-11-01。本目录覆盖平台模块、NoneBot风格插件机制、Agent后端、能力装配、工作区/执行、文件记忆、持久化、观测与开源工程。

Python 公开入口、QQ 示例目录收敛、工具注册与 Bot 网关绑定见[应用入口重构](reviews/2026-11-01-python-application-api.md)。

QQ 助手参考项目对照、普通文本回复、稳定成员标识、群 Memory 接入及媒体能力边界见 [QQ 助手设计](design/qq-assistant.md)。

收件箱复用平台数据库、移除 OneBot/调度兼容层与框架边界清理见[源码清理记录](reviews/2026-11-01-inbox-cleanup.md)。

此前群聊执行链、默认与个人模型/加密凭据、在线更新与源码职责见[补齐与架构复核](reviews/2026-11-01-platform-follow-up.md)。早期缺项表保留作历史证据。

v0.3.1 新增独立 Linux 安装包，默认采用本地 systemd 服务，Docker 为显式可选方式；制品内容和升级边界见 [v0.3.1 发布说明](releases/v0.3.1.md)。

全源码目录责任审查、迁移说明和边界规则见[目录职责审查](design/source-layout.md)与[文件职责表](reference/source-responsibilities.md)。

机器人协议现已独立为 `infrastructure/bots/dingtalk`、`infrastructure/bots/qq`，框架提供中立合同，平台保留账号/权限/绑定/投递所有权，详见[机器人适配器](design/bot-adapters.md)。

最新代码审查 R01—R08 的修复、权限升级及恢复语义见[审查修复记录](review-fixes.md)。此前平台验收记录保留各自验证时点。

本轮已按[平台补齐记录](platform-completion.md)完成权限缺陷、调度、管理、触发器、通知及运维闭环；[原平台审查](platform-capability-audit.md)保留作历史证据。通用工作流不在本次范围。

**当前已实现框架、平台服务、HTTP/机器人入口及 OpenAI 后端的主要调用链。** 本次继续补齐此前缺少的 SDK 扩展、沙箱记忆与独立子环境、单次 Run 配置、Git/脚本工具、混合检索、机器人 WebSocket/文件、成本对账和 OTLP。真实 SDK 测试固定 `openai-agents==0.24.0`，模型响应受控；临时 PostgreSQL 17.11、Redis、本地协议服务以及 Linux UnixLocal 文件执行/恢复已验收。独立Linux环境已验证Docker、mTLS远程沙箱、S3/PostgreSQL备份恢复、真实模型Responses和逐Run K3s Job；外部机器人账号及生产多节点拓扑尚未验收。Web 仅预留，Claude 为后续适配。

| 当前实现范围 | 入口与证据 |
| --- | --- |
| Driver/Adapter/Bot/Event/Message、Matcher、Depends、上下文、导入式插件与持久调度 | [框架测试](../tests/framework/)、[机器人测试](../tests/bots/) |
| Agent版本/工厂、模型档案、OpenAI SDK运行、流、审批/检查点/恢复、会话与授权 | [SDK测试](../tests/runtime/test_openai_backend.py)、[运行时测试](../tests/runtime/test_runtime.py)、[平台测试](../tests/platform/) |
| Hosted/原生工具、动态Prompt/类型化委派扩展、工具网关、MCP、eager/lazy Skill、知识检索、工作区/快照/本地执行、两阶段文件记忆 | [资源测试](../tests/resources/test_resources.py)、[沙箱桥接测试](../tests/runtime/test_sandbox_bridge.py)、[工作区并发测试](../tests/resources/test_workspace_concurrency.py) |
| 价格/用量/预算、日志/指标/OTel、审计、事务outbox、显式schema迁移 | [用量测试](../tests/usage/test_usage.py)、[数据库测试](../tests/infrastructure/test_database.py)、[迁移说明](../migrations/README.md)、[Redis离线/显式集成测试](../tests/infrastructure/test_cache.py) |
| CLI、HTTP/SSE、单Run子进程/Docker/Kubernetes Job、S3与完整备份恢复 | [HTTP测试](../tests/http/)、[worker测试](../tests/runtime/test_run_launcher.py) |

下列设计文档中有些仍描述目标架构，**实现状态以SDK覆盖表、路线和当前代码/测试为准**；目标目录树中的文件名不能作为某项功能存在的证据。

平台能力独立于业务插件：Agent管理、Bot/频道绑定、会话运行、知识检索、持久任务由模块服务提供。插件扩展命令、领域规则和提供商；`modules/scheduling`已接管原调度实现，旧 plugins 调度兼容导出已删除。通知中心已归平台模块；通用工作流明确排除，详细分类见[平台与插件职责](design/plugins.md#内置插件的实际状态)。

文件记忆包含平台显式提取/整合任务及 SDK 原生 Memory 生成；二者均经过模型授权与计量。原生生成在产物捕获前显式 flush，实时更新有 namespace CAS 与幂等控制。namespace 版本、会话历史、Run 检查点、沙箱 session 与工作区 snapshot 分别管理。

| 文档 | 内容与阅读定位 |
| --- | --- |
| [架构重构记录](reviews/2026-11-01-architecture-refactor.md) | 索引查询、类型化上下文、显式依赖、后台分工、能力入口与审核记忆边界 |
| [本轮平台补齐](platform-completion.md) | 审查逐项闭环、模块边界、执行语义及验证 |
| [资源治理](design/resource-governance.md) | 模型/能力撤回、知识任务、快照保留和记忆ACL |
| [平台能力与缺口审查](platform-capability-audit.md) | 已复现缺陷、定时触发完整性、平台管理缺项和补齐顺序 |
| [测试环境位置](testing-environment.md) | 独立服务器部署、模型档案、凭据引用与实际证据 |
| [实施验收记录](implementation-report.md) | 实际目录、命名拆分、已实现能力、本地验证和限制 |
| [总体方案](design/overview.md) | 平台目标、模块关系、请求链、状态与部署边界 |
| [目录与模块边界](design/architecture.md) | 目标文件树、模块所有权、公开接口及依赖约束 |
| [SDK逐项覆盖](design/sdk-coverage.md) | 88项逐条对照当前实现、离线测试、外部待验收与不支持项；区分实现、协议测试、真实环境与上游限制 |
| [SDK、工作区与资源](design/sdk-and-resources.md) | 七个沙箱对象、装配、模型、审批、错误与观测的设计语义 |
| [插件与事件框架](design/plugins.md) | NoneBot风格Driver/Adapter/Bot/Matcher/Depends/元数据；扩展目标与机制 |
| [记忆专项](design/memory.md) | 独立于Session的文件记忆、namespace、生成任务、预算、发布和清理设计 |
| [数据与协议](design/data-and-protocols.md) | 实体、持久化、HTTP/事件/错误合同及一致性目标 |
| [当前状态与路线](roadmap.md) | 已实现范围、正确性补齐、外部验收及后续开发 |
| [开发说明](development.md) | 安装、检查、锁文件、测试、导入规则和构建 |
| [Linux 安装与服务管理](../deploy/linux.md) | wheel与依赖清单构成的独立安装包、默认本地systemd、可选Docker、配置与升级 |
| [初始化交付记录](initialization-report.md) | 初次开源脚手架建立时的历史记录，不代表当前实现仍停留在该阶段 |
| [运维说明](operations.md) | 进程、数据库、恢复、指标、追踪、成本与部署边界 |
| [ADR 0001](adr/0001-modular-monolith.md) | 模块化单体与业务所有权 |
| [ADR 0002](adr/0002-sdk-boundaries.md) | 后端边界、能力声明、版本与原生扩展 |
| [ADR 0003](adr/0003-memory-ownership.md) | 平台拥有默认记忆生成任务；runtime只协调 |

建议先读当前状态和SDK覆盖，再读目标方案/模块边界及专项设计。开发时选定可交付行为，同步修改公共合同、实现、不支持路径、测试和文档。

参考源码基线：NoneBot2 `e52d819fefceadea1fccc773a15f0bc205aff069`；OpenAI开发源码`cd26bbbc52847845fde73b9952b51e62e2bcbdc0`。正式安装依赖锁定`openai-agents==0.24.0`，开发源码与正式发行并非同一提交。框架是独立实现的NoneBot风格设计，不宣称对全部现有NoneBot插件直接兼容。
