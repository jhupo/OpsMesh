# SDK 与插件责任边界

以 [插件架构](../../docs/plugin-architecture.md) 为准。核对锁文件和当前安装 SDK 的公开签名；不要根据旧设计恢复 modules/capabilities/IAM/Jobs、Usage 账本或能力版本中心。

| 原生能力 | 使用者 | 补充职责 |
|---|---|---|
| Agent、SandboxAgent、ModelSettings、模型客户端 | 智能体插件 | 从插件配置构造、持有和关闭资源 |
| function_tool、Tool、Guardrails | 工具/智能体插件 | 参数说明、权限与实际执行位置 |
| Skill、Skills、Manifest | 智能体插件 | 普通技能目录的加载与工作区挂载 |
| MCPServer | 创建它的插件 | 凭据、连接及调用作用域 |
| Runner、RunConfig | execution | 原生执行、流取消清理和插件回调 |
| RunResult、RunResultStreaming、Usage | 调用者 | 直接读取原生结果，不镜像或重新累计落库 |
| RunState、interruptions、approve/reject | 功能插件 | 审批决定和状态持有 |
| Session、SQLiteSession、SQLAlchemySession | 会话所属插件 | 选择后端、会话归属和并发策略 |
| RunHooks、AgentHooks | execution 与调用插件 | 组合原生语义；结束观察独立于业务结果 |
| Trace、Span、TracingProcessor | execution | 安全导出、TraceContext 属性和有界刷新 |
| 原生测试模型与沙箱测试支持 | 相关插件测试 | 受影响语义验证，不替代真实联调 |

Framework 不依赖 SDK。UserContext 只携带 id/name/groups/roles；TraceContext 的 user_id/session_id/tags 是独立观测数据。filesystem 无业务索引和 SQLite，仅提供文件能力。database 为按需使用的 PostgreSQL 插件，execution 不依赖它。

插件用 require 建立依赖及归属，调用期间用 Depends 管理资源。on_run_end(result,error,trace_id) 观察失败不覆盖已有结果；before_run 与原生 SDK hooks 的阻断行为必须区别对待。
