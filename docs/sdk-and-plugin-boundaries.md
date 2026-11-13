# SDK、框架与插件边界

完整契约见 [插件架构](plugin-architecture.md)。

| 原生能力 | 使用位置 | 项目补充 |
|---|---|---|
| Agent / SandboxAgent / ModelSettings | 功能插件 | 从插件配置构造原生对象 |
| Tool / function_tool / Guardrails | 工具或智能体插件 | 参数说明、权限与运行位置 |
| Skill / Skills / Manifest | 智能体插件 | 从普通文件目录加载资源 |
| MCPServer | 创建连接的插件 | 凭据、连接生命周期与关闭 |
| Runner / RunConfig / RunState | execution 与调用插件 | 调用作用域、取消清理、插件回调 |
| RunResult / RunResultStreaming / Usage | 调用者直接使用 | 不增加镜像结果和用量报表 |
| Session / SQLAlchemySession | 会话所属插件 | 会话索引、用户归属和并发策略 |
| Trace / Span / processor | execution | 一次性导出配置、TraceContext、安全关闭 |
| SQLAlchemy / Alembic | database | 引擎生命周期、声明归属、独立版本表 |
| Event / Message / Dependent / Matcher | Framework 和 channels | 外部协议适配与经过认证的 UserContext |

execution 不依赖 database；持久执行记录、UsageTotals、增量账本和持久状态模型均不在当前职责内。结束后需要额外业务统计的插件可以订阅 on_run_end 并读取原生 Usage，其失败不影响已有模型结果。

filesystem 只操作普通文件、目录和 ZIP，业务插件负责技能/工具登记及安装记录。Framework 的 manager 负责加载，registry 负责已加载对象及注册归属。
