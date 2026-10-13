# 后端文档

[Temporal 与原生 SDK 架构](architecture.md) 统一维护当前执行边界、用户配置与框架职责、入口/回复插件、沙箱方式和验证记录；代码实现、验证和服务器部署分别记录。

[Python 命名与模块职责](naming.md) 说明类、文件、执行环境、查询和写入入口的命名约定。

## API 与产品配置

| 文档 | 范围 |
| --- | --- |
| [API 使用说明](api.md) | 认证、权限、分页、错误、Chat 和 SSE |
| [完整接口参考](api/README.md) | 从当前已注册路由生成的参数和响应 |
| [数据模型](api/schemas.md) / [OpenAPI](openapi.json) | 约束、嵌套类型与机器合同 |
| [对话与原生路由](conversations.md) | Turn 排队、幂等、路由、回答所有权与取消 |
| [记忆与知识工具](agent-tools.md) | 受权 CRUD、检索、版本、SDK 本地记忆 |
| [Runtime](api/runtimes.md) / [MCP](api/capabilities.md) | 原生沙箱配置、MCP 与产品生命周期 |
| [审批配置](approval-configuration.md) | 生效策略、模型审核与人工决策 |
| [执行指令配置](product-prompt-configuration.md) | 用户配置的 Agent、Team 与输入 |
| [平台运维](platform-operations.md) | 权限上下文、治理、诊断和成本 |
| [执行诊断](observability.md) | 脱敏证据、事件、结构化日志与 tracing |
| [邀请与邮件](user-invitations.md) | SMTP、邀请、激活和失败重试 |

## 安装与运维

| 文档 | 范围 |
| --- | --- |
| [部署](../deploy/server/DEPLOYMENT.md) | Temporal、API/Worker、原生沙箱和配置 |
| [管理与恢复](../deploy/server/delivery-operations.md) | CLI、备份和升级 |
| [监控](../deploy/server/observability-audit-and-costs.md) | 指标、日志、追踪与可选监控 |
| [发布](../deploy/DISTRIBUTIONS.md) | CLI、独立服务器包和镜像 |
| [验证与交付](verification.md) | PR 受影响流程、发布全套、真实 PG/Temporal 和原生沙箱 |

API 参考通过 `scripts/export_api_docs.py` 生成，`--check` 检查漂移。禁止手写不存在的路由；实现说明不等于生产部署或外部模型验收。
