# 开发助手资料

- [技能](skills/README.md)：如何完成某类工作；根据任务选用。
- [维护参考](references/README.md)：模块所有权、生命周期和长期不变量；修改对应边界前阅读。
- [根约定](../AGENTS.md)：执行授权、架构和校验规则。

结构参考 [openai/openai-agents-python](https://github.com/openai/openai-agents-python)，本次参考提交为 [`841dfdd938bfd4ca052475eb76d6245a356868e0`](https://github.com/openai/openai-agents-python/tree/841dfdd938bfd4ca052475eb76d6245a356868e0)。

`openai-knowledge`、`docs-sync` 通过官方 skill-installer 从该提交安装，然后适配 OpsMesh 的路径、语言、文档结构与授权规则。上游贡献发布、翻译和 Make 命令不适用于本仓库；不安装整套只服务于上游发布维护的技能。保留 [上游 MIT 许可](UPSTREAM-LICENSE)。维护参考根据本仓库源码编写，不是将上游内部实现照搬为 OpsMesh 行为。

`code-change-verification`、`temporal-python-sdk` 是仓库专用技能；`find-skills` 来源保持其目录说明。更新上游技能时先比较固定版本，不覆盖本地适配，也不修改用户全局 Codex 配置。
