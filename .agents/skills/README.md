# Repository skills

技能的触发条件、使用顺序和 OpsMesh 适配规则见仓库根目录 `AGENTS.md`。先读取对应 `SKILL.md`，再按技能要求读取引用资源。相关技能列表中的外部/兄弟技能仅为导航，不代表已安装，也不要求自动安装。

## Backend skill sources

以下技能于 2026-10-10 使用 skill-installer 从固定提交安装，保留上游技能正文和引用文件。每个目录包含来源许可证；更新时重新检查与仓库规范的冲突并记录新提交。

| 本地目录 | 上游路径 | 固定提交 |
| --- | --- | --- |
| `architecture-patterns` | [wshobson/agents: plugins/backend-development/skills/architecture-patterns](https://github.com/wshobson/agents/tree/46891e7e60da0e52baf1050b7b6391b64e84c6d9/plugins/backend-development/skills/architecture-patterns) | `46891e7e60da0e52baf1050b7b6391b64e84c6d9` |
| `python-testing-patterns` | [wshobson/agents: plugins/python-development/skills/python-testing-patterns](https://github.com/wshobson/agents/tree/46891e7e60da0e52baf1050b7b6391b64e84c6d9/plugins/python-development/skills/python-testing-patterns) | `46891e7e60da0e52baf1050b7b6391b64e84c6d9` |
| `verification-before-completion` | [obra/superpowers: skills/verification-before-completion](https://github.com/obra/superpowers/tree/bb92a77741419a4ab5f06e711a283343f1ada0c3/skills/verification-before-completion) | `bb92a77741419a4ab5f06e711a283343f1ada0c3` |

安装包含各技能的 `SKILL.md`、上游提供的 `references/` 以及许可证。未安装整个插件、额外 Agent 或其他技能。

上游通用建议不覆盖 OpsMesh 的 feature-first 结构、SDK 优先、租户隔离、事务/队列边界、产品流程验证和项目工具配置。具体适配规则集中在 `AGENTS.md`，避免修改上游正文后失去版本可追溯性。

## Repository-owned SDK guidance

`temporal-python-sdk/SKILL.md` 于 2026-10-12 根据 Temporal 官方 Python/AI 集成文档及锁定发行包编写，维护官方链接、执行边界和验证入口。它不是第三方源码快照；版本以 `uv.lock` 为准，升级时重新核对技能中的基线与公共 API。

## Adapted design and simplification skills

以下技能于 2026-10-13 引入，保留来源许可证并针对 OpsMesh 修改；它们不是未经修改的上游快照。更新时按固定提交对照本地适配，不能直接覆盖。

| 本地目录 | 上游文件 | 固定提交 | 本地适配 |
| --- | --- | --- | --- |
| `codebase-design` | [mattpocock/skills: skills/engineering/codebase-design](https://github.com/mattpocock/skills/tree/49dd158d1076134a641b33efb035946536778336/skills/engineering/codebase-design) | `49dd158d1076134a641b33efb035946536778336` | 保留深模块与删除测试；取消强制术语、TypeScript 示例和并行设计流程，验证使用真实基础设施。保留并适配 `DEEPENING.md`。 |
| `improve-codebase-architecture` | [mattpocock/skills: skills/engineering/improve-codebase-architecture](https://github.com/mattpocock/skills/tree/49dd158d1076134a641b33efb035946536778336/skills/engineering/improve-codebase-architecture) | `49dd158d1076134a641b33efb035946536778336` | 保留按实际摩擦评估架构；移除强制 HTML、额外技能依赖、自动分派与额外审批流程。只保留正文与 UI 元数据；按需触发。 |
| `code-simplifier` | [anthropics/claude-plugins-official: plugins/code-simplifier/agents/code-simplifier.md](https://github.com/anthropics/claude-plugins-official/blob/f359d43b406057f1a8cdbc4ea93b827642c162e6/plugins/code-simplifier/agents/code-simplifier.md) | `f359d43b406057f1a8cdbc4ea93b827642c162e6` | 将 Agent 定义适配为 Python skill，删除 React/TypeScript 和模型绑定；保持行为、授权范围与事务边界。未安装完整插件。 |
| `request-refactor-plan` | [mattpocock/skills: skills/deprecated/request-refactor-plan](https://github.com/mattpocock/skills/tree/f958fa17c1b62c3f7be38fc09512669acf6b64fc/skills/deprecated/request-refactor-plan) | `f958fa17c1b62c3f7be38fc09512669acf6b64fc` | 用户指定采用已被上游删除的旧技能，固定删除前版本；保留增量方案与验收，问题按需询问，默认聊天输出，不自动发布 issue。 |

默认模块设计使用 `codebase-design`，定向简化使用 `code-simplifier`。架构评估按任务需要使用 `improve-codebase-architecture`；`architecture-patterns` 仅在具体架构模式或依赖边界需要时使用。
