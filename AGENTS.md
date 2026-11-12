# Agents SDK 技能

按当前任务读取对应 SKILL.md；目标插件设计见 docs/plugin-architecture.md，不能把设计当作已实现代码。

| 技能 | 说明 | 何时使用 |
|---|---|---|
| [opsmesh-agents-integration](.agents/skills/opsmesh-agents-integration/SKILL.md) | 原生 Agent、插件边界与上下文 | 设计或修改智能体插件、模型接入及 SDK 依赖时 |
| [opsmesh-agents-runtime](.agents/skills/opsmesh-agents-runtime/SKILL.md) | Runner、流式、Session 和原生审批状态 | 设计或修改 execution 接入及调用生命周期时 |
| [opsmesh-agents-tools](.agents/skills/opsmesh-agents-tools/SKILL.md) | 原生工具、技能引用、Guardrails 和 MCP | 编写工具插件、接入技能或 MCP 时 |
| [opsmesh-agents-sandbox](.agents/skills/opsmesh-agents-sandbox/SKILL.md) | SandboxAgent、文件来源和资源生命周期 | 设计或修改沙箱与技能挂载时 |
| [opsmesh-agents-verification](.agents/skills/opsmesh-agents-verification/SKILL.md) | Usage、Trace 与受影响测试 | 修改观测、统计或 SDK 兼容性时 |
