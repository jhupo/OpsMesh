---
name: openai-knowledge
description: 核实 OpsMesh 的 OpenAI Agents Python SDK、MCP、Sandbox、Session 和模型集成，使用锁定版本源码与官方资料提供依据。
---

# OpenAI 集成资料

1. 先读取根 `AGENTS.md` 和受影响的 [.agents/references](../../references/README.md) 条目。确认 `uv.lock` 中的 `openai-agents`、`openai`、`mcp` 与 `temporalio` 版本。
2. 核对实际安装源码或固定版本的官方源码：Agent/Runner 参数、MCP 连接协议、Session、Sandbox 与 Temporal 适配是否存在，谁拥有初始化和清理。当前源码比最新网站更能说明锁定版本的可用接口。
3. 官方 Agents SDK 文档在 <https://openai.github.io/openai-agents-python/>，源码在 <https://github.com/openai/openai-agents-python>。需要平台 API 事实时，优先使用已配置的 OpenAI Developer Docs MCP 搜索并获取精确页面；不可用时直接访问官方文档，不要求安装 MCP 或重启才能继续。
4. 查平台 API 的 endpoint/schema 时使用官方参数资料；查 SDK 行为时使用 SDK 文档与源码。不要把托管 MCP 工具等同于 Python 进程拥有的 MCPServer。
5. 按版本、符号和来源给出结论。区分上游支持、本仓库已接入、本仓库未暴露三个层面。没有证据的参数和默认值不能凭记忆补齐。

不自动修改全局 Codex/MCP 配置，不调用真实模型，不运行测试。涉及 Temporal 执行边界时同时使用 `temporal-python-sdk`。SDK 负责连接与协议，OpsMesh 负责配置、插件绑定、所有权、实例和执行授权；不要新增重复的连接协议层或业务 API 客户端。
