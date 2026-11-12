---
name: opsmesh-agents-runtime
description: 轻量 execution、原生 Runner、流式资源与插件回调。用于执行接入、取消、审批继续或调用生命周期修改。
---

# SDK 执行与调用插件职责

先读 [插件架构](../../../docs/plugin-architecture.md) 的 execution 与原生会话章节。

- execution 只执行已构造 Agent/SandboxAgent，直接返回原生 RunResult；stream 在异步上下文内提供原生 RunResultStreaming。不要恢复 AgentLoop、调用状态表、Usage 账本、数据库依赖或能力装配中心。
- 原生 RunConfig、Session、RunState、hooks 由调用插件提供。调用者通过原生结果读取输出、interruptions 和 Usage，不加平行 Result 类。
- 当前 Matcher 确定回调归属；后台调用以 plugin_id 显式指定。调用插件及 require 依赖闭包参与回调，无关插件不广播，重复依赖不重复执行。
- before_run 可阻断；SDK 对应 hook 保留异常行为；on_run_end(result,error,trace_id) 的观察失败只记录，不覆盖结果或原异常。不能把全部回调都改为吞错或全部改为业务阻断。
- 流正常耗尽、提前退出、取消及异常都关闭原生 SDK 工作，再结束回调和追踪作用域。函数返回流对象不等于执行完成。
- MCP、模型客户端、沙箱和长期 Session 的所有者负责资源关闭，作用域覆盖实际调用。execution 不取得这些资源的业务所有权。
- 审批由 SDK needs_approval/RunState/approve/reject 处理，功能插件负责决策权限和保存策略；Framework pause/got 只处理消息交互。
- 重启重新加载代码和配置，不自动恢复旧执行。会话持久性由选定 SDK Session 决定，不增加 Jobs、持久租约和审批调度。

验证只覆盖受影响路径，区分确定性 SDK 测试与真实外部联调。
