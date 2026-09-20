# OpsMesh 文档索引

OpsMesh 是一个后端优先的企业级 Agent 控制平面。当前源码已经包含 Workspace 隔离、访问
控制、Agent SDK 适配、任务与团队编排、能力/MCP、隔离运行时、Worker、审计、成本和发布
运维能力。远程插件生命周期和隔离进程托管已进入后端，真实插件容器与渠道仍待验收；前端画布未实现。

2026-09-20：SDK 0.5.0 与钉钉插件 0.3.0 已发布，平台依赖固定发布 wheel 的 SHA-256。
平台 `v0.1.0rc18` 已通过完整发布门禁以及 Compose/systemd 托管交付验收；`v0.1.0rc19`
发布线统一通过无需登录的 GitHub Release API 定位资产，并支持 CDN 下载重试和断点续传。
插件目录默认只同步信息，不安装插件。发布与部署证据见[插件服务接入](plugin-service-integration.md)。

## 当前实现

- [多用户资源权限](multi-user-authorization.md)：默认私有、用户授权、执行身份、渠道绑定、会话隔离、预算及恢复演练边界（2026-09-20）。

- [系统架构](architecture.md)：控制面、执行面、状态面和证据面的总览。
- [Backend 服务架构](backend-service-architecture.md)：API、领域、runtime、worker 与基础设施依赖方向。
- [Agent Runtime 架构](agent-runtime-architecture.md)：OpenAI/Claude SDK、沙箱合同、运行时池、项目 I/O 和执行证据。
- [Agent Runtime Contract](agent-runtime-contract.md)：Provider-neutral 请求、结果、事件、会话和审批边界。
- [能力与 Runtime](capabilities-and-runtime.md)：工具、技能、资源、MCP、权限和运行时关联。
- [Runtime 执行模式](runtime-execution-modes.md)：`none`、`isolated`、`pooled`、`persistent` 的授权与生命周期。
- [Backend Runtime Control Plane](backend-runtime-control-plane.md)：Docker、runtime space、命令、文件和清理。
- [Cloud Control Plane 与 Runtime Spaces](cloud-control-plane-and-runtime-spaces.md)：平台管理员、Worker fleet、配额和运行空间。
- [Workspace 数据管理](workspace-data-management.md)：项目文件、artifact、导入导出、备份和恢复。
- [Isolation 与 Security](isolation-and-security.md)：租户、凭据、网络、文件和执行隔离。
- [Threat Model](threat-model.md)：威胁、拒绝路径和必须保留的安全证据。
- [Domain Model](domain-model.md)：Workspace、Agent、Team、Task、Run、Capability 和 Runtime 实体。
- [API Design](api-design.md)：workspace-scoped API、错误 envelope 和分页契约。
- [Self-hosted Runtimes](self-hosted-runtimes.md)：用户自有机器的注册、心跳、任务和 artifact 回传。
- [Self-hosted Connector](self-hosted-connector.md)：官方 MCP SDK connector、恢复和安全合同。
- [Observability、Audit 与 Cost](observability-audit-and-costs.md)：日志、指标、链路、审计完整性、成本账本和工作区治理告警（P1-8，2026-09-17）。
- [Open-source SDK Strategy](open-source-sdk-strategy.md)：官方 SDK 采用边界和禁止重复造轮子的规则。
- [Adapter Boundaries](adapter-boundaries.md)：MCP、Provider health 和 runtime backend registry 的 adapter 合同。
- [Shared Domain Services](shared-domain-services.md)：跨领域服务的 owner 与复用规则。
- [MCP 日志分析员工流程示例](examples/mcp-log-analysis-workflow.md)：工具自动发现、员工/专家角色、结构化编排、审批与记忆。
- [自动化与远程插件](automation-and-extension-contracts.md)：结构化输入输出、SDK 实时订阅与工具状态、消息续接、人工控制、签名通知和插件生命周期（2026-09-18）。
- [插件分发与安装](plugin-distribution.md)：工作区批准的固定目录、后台校验下载、权限配置预览、显式安装与撤回（2026-09-18）。
- [插件平台服务与钉钉接入](plugin-service-integration.md)：安装凭据、用户授权、媒体附件、群回复、卡片审批与隔离进程托管；本地验证和真实渠道待验收项分别列明（2026-09-19）。

## 架构与质量

- [GitHub 安全与协作自动化](github-automation.md)：PR 提交、CodeQL、依赖检查、Dependabot 和标签规则（2026-09-20）。
- [Architecture Gates](architecture-gates.md)：Import Linter、依赖方向、API/Domain/Infrastructure 边界。
- [代码组织与架构边界](code-organization-audit.md)：当前目录、唯一 owner、拆分规则、重构结论和结构证据。
- [Decisions](decisions.md)：稳定的产品与架构决策。

## 发布与生产化

- [许可证与分发边界](licensing.md)：平台 LGPL-3.0-only、独立插件 SDK Apache-2.0，以及历史 MIT 声明保留规则（2026-09-18）。

- [Platform Productionization Plan](platform-productionization-plan.md)：当前平台收口、可靠性和运维阶段。
- [Release Delivery Plan](release-delivery-plan.md)：tag 门禁、构建、签名、发布和公共下载验证。
- [Standalone Distributions](standalone-distributions.md)：CLI 与 server bundle 的构建和验证。
- [Backend Deployment](backend-deployment.md)：Compose/systemd 部署、更新、回滚和恢复；镜像构建集中在 `deploy/images`，开发配置在 `deploy/local`，生产配置在 `deploy/server`（2026-09-20）。
- [Delivery Operations](delivery-operations.md)：发布后交付、托管安装和运维操作。

## 文档规则

- `backend/app` 的目录、owner、拆分与依赖规则只在
  [代码组织与架构边界](code-organization-audit.md) 中维护；`docs/reviews/app-layout-2026-09-14`
  只保存机器生成证据，不再保存第二套重构方案或实施记录。
- 当前实现文档必须标注状态和日期，不能把历史 release 或旧分支结果描述为当前状态。
- 设计/计划文档只保留仍然影响源码边界的内容；完成后的阶段清单和临时发布记录应删除，
  不与当前架构重复维护。
- 源码目录、import owner 和删除路径以 `scripts/audit_app_layout.py` 以及架构测试为准。
- 远程插件绑定已有能力，不在 API/Worker 动态导入第三方代码；托管插件进程仍须通过 Runtime 隔离边界。

2026-09-18：自动化、画布配置合同和远程插件生命周期已接入现有后端；
[独立 SDK 仓库](https://github.com/jhupo/opsmesh-plugin-center) 提供连接器合同与签名能力，
本仓库已删除内置副本。外部插件贡献与发布合同见其
[架构文档](https://github.com/jhupo/opsmesh-plugin-center/blob/master/docs/architecture.md)。
平台已有受控 HTTPS 目录和描述文件下载，详见 [分发合同](plugin-distribution.md)。
当前不包含钉钉适配器、Web 页面、独立公共目录运营仓库或自动部署外部插件服务。
