# 重构实施记录

本记录区分已验证的功能点和待实施方案，不以移动文件数量宣布完成。

| 阶段 | 功能点 | 状态 | 验证 |
| --- | --- | --- | --- |
| R0 | 全量结构清单与处置方案 | 已完成 | 1,029 个源文件的审查基线与处置表 |
| R1 | 显式 ORM 注册，解除数据库层反向加载业务模型 | 已完成 | 模型注册、数据库模型、健康检查和任务转移相关测试通过；新增独立进程边界检查 |
| R1 | 访问域与 HTTP 依赖边界 | 已完成 | 用户/令牌/权限迁至 `domains/access`；FastAPI 认证、平台管理员和队列依赖迁至 `api/dependencies`；认证、授权和架构门禁通过 |
| R1 | 平台控制面与外部集成归属 | 已完成 | 管理策略、发布、更新迁至 `domains/platform`；Webhook 迁至 `domains/integrations/webhooks`；模型注册、动态入口和相关导入已更新 |
| R2 | API 路由/schema 清理 | 待实施 | |
| R3 | Agent/provider/session/mailbox/tools/MCP | 待实施 | |
| R4 | 任务/Run/工作流职责 | 待实施 | |
| R5 | Workspace/team/project/archive/data lifecycle | 待实施 | |
| R6 | 容器池/后端/Worker/recovery | 待实施 | 保留既有未提交租约和队列工作 |
| R7 | 观测与成本边界 | 待实施 | |
| R8 | 入口核对、旧引用和残留清理 | 待实施 | |

原始审查文件为实施前快照，不随代码迁移覆写。`audit_app_layout.py --check` 用于检查原始快照，
重构后出现源文件差异是预期结果，不能用它代替当前阶段测试。
