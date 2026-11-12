---
name: opsmesh-agents-integration
description: 插件中的原生 Agent 定义、SDK 边界、模型配置及调用者上下文。用于智能体插件或 SDK 接入设计与修改。
---

# 原生 Agent 与插件边界

先读 [当前架构](../../../docs/plugin-architecture.md) 和 [SDK 职责矩阵](../../references/openai-agents-boundaries.md)。修改前核对当前源码公开接口，不能仅凭设计文件假定能力已实现。

- 核心发行包位于 src/opsmesh；官方插件位于 packages/<name>/src/opsmesh_<name>，独立声明依赖并随 tag 发布。通过 require("execution") 等加载，不能把 SDK、数据库或插件包重新加入核心依赖。
- Framework 只处理通用插件、事件、依赖和生命周期，不导入 SDK。
- 智能体插件自己取得工具、技能、模型和 MCP，构造原生 Agent/SandboxAgent；execution 接收已构造对象，不创建能力中心、AgentCatalog、模型中心或通用工厂协议。
- 原生插件对象使用现有元数据或明确的模块导出，加载通过 require/load_plugin；不扫描任意属性猜测入口，不恢复 PluginExport。
- filesystem 仅提供普通文件、目录和 ZIP 操作，无 SQLite 和安装索引；技能、工具、智能体元数据属于功能插件。filesystem 不执行 Python，也不序列化 SDK 对象图。
- 入口验证身份后传入冻结的 UserContext(id,name,groups,roles)。Context() 取得同一对象，SDK 通过 wrapper.context 获取。会话/运行 ID 和服务句柄不属于用户上下文；Langfuse 用户及会话关联使用单独 TraceContext。permissions.User 仍是权限匹配器。
- 配置和代码在重启后重新加载，旧执行不自动恢复。不能通过修改共享 Agent 的 tools、hooks、模型凭据隔离调用者。
- 使用本地已安装 SDK 的公开接口核对签名与类型；插件依赖单独锁定，不能因旧虚拟环境有安装就加入核心依赖。

只修改用户指定范围；代码、注释和错误消息使用英文。按职责命名，不机械增加 Service/Manager 后缀。
