# 技能与参考资料维护

本目录遵循根目录 [AGENTS.md](../AGENTS.md)。已有能力、完整设计要求、GitNexus 调查流程、docs 状态与 OpenAPI 约定统一在那里维护，不在本目录复制另一套架构规则。

按任务读取下面的技能。涉及框架时先读固定版本的 NoneBot2 官方文档，再核对本地源码；[框架架构](../docs/framework.md)与[网关流程](../docs/gateway.md)描述现状。

维护技能时保留参考资料的版本、来源和适用范围。不要因为 OpsMesh 采用 Gateway 命名就改写 NoneBot2 官方快照里的 Bot；应在技能与项目文档中说明映射。新增或修订架构要求遵循根目录的完整设计清单，避免只补一处入口而遗漏调用链、文件树或异常恢复。

重构完成后按根目录要求更新正式架构文档、删除被替代的旧重构方案，并同步技能中的引用；不能继续把已删除方案或已完成的改造称为未来能力。

| 技能 | 说明 | 何时使用 |
|---|---|---|
| [opsmesh-nonebot2](skills/opsmesh-nonebot2/SKILL.md) | NoneBot2 官方文档快照与源码入口 | 涉及 Driver、Adapter、事件、Matcher、依赖注入或框架插件加载时 |
| [opsmesh-agents-integration](skills/opsmesh-agents-integration/SKILL.md) | 原生 Agent、插件边界与上下文 | 设计或修改智能体插件、模型接入及 SDK 依赖时 |
| [opsmesh-agents-runtime](skills/opsmesh-agents-runtime/SKILL.md) | Runner、流式、Session 和原生审批状态 | 设计或修改 execution 接入及调用生命周期时 |
| [opsmesh-agents-tools](skills/opsmesh-agents-tools/SKILL.md) | 原生工具、技能引用、Guardrails 和 MCP | 编写工具插件、接入技能或 MCP 时 |
| [opsmesh-agents-sandbox](skills/opsmesh-agents-sandbox/SKILL.md) | SandboxAgent、文件来源和资源生命周期 | 设计或修改沙箱与技能挂载时 |
| [opsmesh-agents-verification](skills/opsmesh-agents-verification/SKILL.md) | Usage、Trace 与受影响测试 | 修改观测、统计或 SDK 兼容性时 |
