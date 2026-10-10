# 后端文档

本目录只保留当前代码对应的接口、执行合同与配置说明。目录迁移计划、迁移过程报告、前端导航和旧 Worker 草案已移除；数据库迁移历史保留在 `migrations/`。

| 文档 | 范围 |
| --- | --- |
| [API 使用说明](api.md) | 认证、权限、分页、错误、Chat 接入和流式事件 |
| [完整接口参考](api/README.md) | 每个已注册接口的参数、请求、响应和权限依赖 |
| [数据模型](api/schemas.md) / [OpenAPI](openapi.json) | 字段、约束、嵌套类型和机器可读合同 |
| [对话与委派](conversations.md) | 排队、幂等、Manager、取消和重试 |
| [执行边界](backend-execution-boundary.md) | API、Worker、Runtime 与 SDK 的职责 |
| [SDK 原生执行](sdk-native-execution.md) | Session、审批恢复、MCP、记忆和压缩日志 |
| [共享 Runtime](shared-runtime-hosts.md) | 宿主、执行槽、并发、取消和生命周期 |
| [托管 MCP](managed-mcp.md) | 导入、宿主绑定、进程控制和凭据 |
| [审批配置](approval-configuration.md) | 工作空间策略、模型审核与人工批准 |
| [执行指令配置](product-prompt-configuration.md) | 用户配置的 Agent、Team 和执行输入 |
| [平台运维](platform-operations.md) | 访问上下文、恢复、审计、成本和状态历史 |
| [邀请与邮件](user-invitations.md) | SMTP、邀请令牌、激活和失败重试 |
| [服务器部署](../deploy/server/DEPLOYMENT.md) | 镜像、服务、配置、日志与升级 |
| [管理与恢复](../deploy/server/delivery-operations.md) | 安装、CLI、备份、升级及故障恢复 |
| [监控运维](../deploy/server/observability-audit-and-costs.md) | 指标、日志、追踪和可选监控服务 |
| [发布分发](../deploy/DISTRIBUTIONS.md) | 原生管理 CLI 和独立服务器包 |
| [自托管连接器](../runtime/CONNECTOR.md) | 注册、凭据和远程 Job 生命周期 |

API 参考由 `scripts/export_api_docs.py` 从实际应用生成，禁止手工增加未注册接口。使用 `--check` 验证无遗漏与文档漂移。实现说明不代表某台服务器已完成部署或外部模型验收。
