# Repository skills

技能的触发条件、使用顺序和 OpsMesh 适配规则见仓库根目录 `AGENTS.md`。先读取对应 `SKILL.md`，再按技能要求读取引用资源。相关技能列表中的外部/兄弟技能仅为导航，不代表已安装，也不要求自动安装。

## Backend skill sources

以下技能于 2026-10-10 使用 skill-installer 从固定提交安装，保留上游技能正文和引用文件。每个目录包含来源许可证；更新时重新检查与仓库规范的冲突并记录新提交。

| 本地目录 | 上游路径 | 固定提交 |
| --- | --- | --- |
| `architecture-patterns` | [wshobson/agents: plugins/backend-development/skills/architecture-patterns](https://github.com/wshobson/agents/tree/46891e7e60da0e52baf1050b7b6391b64e84c6d9/plugins/backend-development/skills/architecture-patterns) | `46891e7e60da0e52baf1050b7b6391b64e84c6d9` |
| `python-design-patterns` | [wshobson/agents: plugins/python-development/skills/python-design-patterns](https://github.com/wshobson/agents/tree/46891e7e60da0e52baf1050b7b6391b64e84c6d9/plugins/python-development/skills/python-design-patterns) | `46891e7e60da0e52baf1050b7b6391b64e84c6d9` |
| `python-testing-patterns` | [wshobson/agents: plugins/python-development/skills/python-testing-patterns](https://github.com/wshobson/agents/tree/46891e7e60da0e52baf1050b7b6391b64e84c6d9/plugins/python-development/skills/python-testing-patterns) | `46891e7e60da0e52baf1050b7b6391b64e84c6d9` |
| `python-code-review` | [Jallen64/python-code-review-skill: repository root](https://github.com/Jallen64/python-code-review-skill/tree/d4f40675fa64cd0ab90e0992e5df6498d2e10f5a) | `d4f40675fa64cd0ab90e0992e5df6498d2e10f5a` |
| `verification-before-completion` | [obra/superpowers: skills/verification-before-completion](https://github.com/obra/superpowers/tree/bb92a77741419a4ab5f06e711a283343f1ada0c3/skills/verification-before-completion) | `bb92a77741419a4ab5f06e711a283343f1ada0c3` |

安装包含各技能的 `SKILL.md`、上游提供的 `references/` 以及许可证；`python-code-review` 从仓库根目录安装，还保留其上游 README。未安装整个插件、额外 Agent 或其他技能。

上游通用建议不覆盖 OpsMesh 的 feature-first 结构、SDK 优先、租户隔离、事务/队列边界、产品流程验证和项目工具配置。具体适配规则集中在 `AGENTS.md`，避免修改上游正文后失去版本可追溯性。
