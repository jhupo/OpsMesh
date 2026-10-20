# OpsMesh

## 项目目标

OpsMesh 是基础 Agent 编排平台，提供服务端、独立 Python SDK 和 Linux 部署工具，不包含前端。

- 支持用户组合 Agent、工具、MCP 和 Skill，创建、发布并运行专家或团队。
- 核心提供身份、模型接入、数量额度、任务运行、私有会话、文件资源和隔离执行等基础功能。
- 用户会话与历史相互独立，共享能力和资源按明确授权使用。
- 业务定制由独立扩展包承担，支持可配置的 GitHub Releases 包源和本地导入。

## 技能说明

按实际任务选择技能，使用前读取对应 `SKILL.md` 及必要引用，不要求每次使用全部技能。
技能用于辅助完成任务，不能覆盖用户指令。技能来源与适配记录见
[技能目录](.agents/skills/README.md)。

| 技能 | 适用场景 |
| --- | --- |
| [codebase-design](.agents/skills/codebase-design/SKILL.md) | 评估模块职责、接口、依赖及抽象是否必要 |
| [improve-codebase-architecture](.agents/skills/improve-codebase-architecture/SKILL.md) | 从实际维护问题评估架构改进 |
| [architecture-patterns](.agents/skills/architecture-patterns/SKILL.md) | 分析服务边界、事务及循环依赖，按需采用架构模式 |
| [request-refactor-plan](.agents/skills/request-refactor-plan/SKILL.md) | 制定重构方案、实施步骤及验收范围 |
| [code-simplifier](.agents/skills/code-simplifier/SKILL.md) | 简化 Python 代码，保持已有行为 |
| [temporal-python-sdk](.agents/skills/temporal-python-sdk/SKILL.md) | 实现或审查 Workflow、Activity、Worker、取消及重放 |
| [python-testing-patterns](.agents/skills/python-testing-patterns/SKILL.md) | 设计与实现受影响功能的 Python 测试 |
| [verification-before-completion](.agents/skills/verification-before-completion/SKILL.md) | 在声明完成或通过前核实实际检查结果 |
| [markdown-badges](.agents/skills/markdown-badges/SKILL.md) | 维护 README 徽章 |
