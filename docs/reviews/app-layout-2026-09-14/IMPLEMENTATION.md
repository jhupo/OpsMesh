# 重构实施记录

本记录区分已验证的功能点和待实施方案，不以移动文件数量宣布完成。

| 阶段 | 功能点 | 状态 | 验证 |
| --- | --- | --- | --- |
| R0 | 全量结构清单与处置方案 | 已完成 | 1,029 个源文件的审查基线与处置表 |
| R1 | 显式 ORM 注册，解除数据库层反向加载业务模型 | 已完成 | 模型注册、数据库模型、健康检查和任务转移相关测试通过；新增独立进程边界检查 |
| R1 | 访问域与 HTTP 依赖边界 | 已完成 | 用户/令牌/权限迁至 `domains/access`；FastAPI 认证、平台管理员和队列依赖迁至 `api/dependencies`；认证、授权和架构门禁通过 |
| R1 | 平台控制面与外部集成归属 | 已完成 | 管理策略、发布、更新迁至 `domains/platform`；Webhook 迁至 `domains/integrations/webhooks`；模型注册、动态入口和相关导入已更新 |
| R2 | API 路由/schema 清理 | 进行中 | 已删除转发文件、tasks 聚合文件、公共模型和通知转发文件；混合 schema 不再导出领域合同，管理路由也不再使用只改类名的空继承包装，路由、测试和 E2E 脚本直接引用所属模块。任务转移、通知租户隔离与脱敏、任务领域视图、管理队列/Worker/Runtime 三个功能场景通过；剩余路由归属和 OpenAPI 对照仍待完成 |
| R3 | Agent/provider/session/mailbox/tools/MCP | 进行中 | Mailbox 命令、校验、读取、收件箱和汇总已分别收敛到 `domains/agents/messages/service.py` 与 `queries.py`，删除 5 个唯一调用的 Mixin 碎片；工具组合模块已按功能收敛为 `tools/{files,mailbox,memory,normalization,events,service}.py`；Provider catalog 的 capability/metadata/model_api/policy/view 已扁平到 `domains/agents/providers` 并删除空 catalog 包；持久会话已迁移到 `domains/agents/sessions`，ORM、仓储、管理和视图的导入已全部更新，旧 `runtime/sessions` 空包已清理。MCP 的 `catalog/execution/transport` 是实际协议与生命周期边界，按最终方案保留，不做机械合并；Mailbox、工具、Provider、会话相关场景通过，MCP 相关场景继续核验 |
| R4 | 任务/Run/工作流职责 | 待实施 | |
| R5 | Workspace/team/project/archive/data lifecycle | 进行中 | Workspace 原 `domains` 子包已按方案归并为 `extensions`，保留 `api/routes/workspace/domains.py` 的 HTTP 路径与领域语义；模型注册、Worker 计划、知识库文档和路由导入已全部切换，旧 `workspace/domains` 包已清理。团队、项目、归档、数据生命周期仍待继续核对 |
| R6 | 容器池/后端/Worker/recovery | 待实施 | 保留既有未提交租约和队列工作 |
| R7 | 观测与成本边界 | 待实施 | |
| R8 | 入口核对、旧引用和残留清理 | 待实施 | |

原始审查文件为实施前快照，不随代码迁移覆写。`audit_app_layout.py --check` 用于检查原始快照，
重构后出现源文件差异是预期结果，不能用它代替当前阶段测试。
