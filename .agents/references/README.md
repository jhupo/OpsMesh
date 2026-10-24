# OpsMesh 维护参考

本目录记录容易跨模块破坏、但可长期复用的所有权和生命周期合同。参考上游的组织方式，内容以 OpsMesh 当前源码为准；它不替代 SDK 文档、用户手册或真实运行证据。

修改某条边界前先阅读对应条目，再核对源码。审阅任务不应顺便改写合同；专门维护或实现变更确立了新不变量时才同步最窄的条目。

| 参考 | 修改前阅读 |
| --- | --- |
| [Agent 配置与组装](agent-definition-and-assembly.md) | AgentManager、配置解析、注册表、模型解析与原生 SDK 组装 |
| [实例、Session 与环境](execution-lifecycle.md) | execution 实例、Run、Session、持久沙箱、取消和删除 |
| [编排与 Temporal](workflow-engine-boundary.md) | 图调度、发布闭包、绑定、引擎适配、重放与 Outbox |
| [插件与 MCP](plugin-and-mcp-lifecycle.md) | ZIP 安装、资源适配器、热更新、MCP 工厂与连接生命周期 |
| [网关与调用者身份](gateway-and-identity.md) | 通道适配、可信绑定、群回复、工具授权与持久投递 |
| [存储与隔离](storage-and-ownership.md) | StorageLayout、文件接口、目录授权、加密与审计 |
| [工程与交付](engineering-and-delivery.md) | 迁移、CI、镜像、运维 CLI、发行与验证范围 |

每条参考包含所有者、入口、不变量和改动检查点。不要收录单次任务历史、当前 PR 状态、复制的测试结果或未实现的蓝图；同一规则由一篇负责，其余链接引用。
