# ADR 0002：SDK、模型与执行环境分别适配

- 状态：采用，2026-10-30。
- 决策：AgentBackend适配整套SDK；模型提供商适配请求；ExecutionProvider适配沙箱/资源。OpenAI SDK类型仅存在于openai_agents后端，公共合同表达平台语义。
- 版本：可选依赖锁定openai-agents==0.24.0；文档核对的开发源码即使version相同也不视为正式包相同行为。uv.lock锁定完整依赖，逐项兼容性按覆盖矩阵验证。
- 扩展：公共语义之外使用带backend_id/schema_version的类型化扩展，不静默丢弃能力。原生扩展代码属于后端层，可由插件包提供但不能泄漏到HTTP模型。
- 结果：可调用Claude模型不等于接入Claude Agent SDK；切换后端需要验证工具、上下文、编排、护栏、流、记忆及恢复。原生RunState不保证跨SDK可移植。
