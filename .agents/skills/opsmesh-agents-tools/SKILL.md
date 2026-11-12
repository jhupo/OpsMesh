---
name: opsmesh-agents-tools
description: 智能体插件中的原生 SDK 工具、Guardrails、MCP 与技能引用。用于工具或 MCP 接入及生命周期设计。
---

# 插件工具、技能和 MCP

先读 [当前架构](../../../docs/plugin-architecture.md) 的 filesystem 和原生工具/MCP 章节。

- 工具按 SDK 原生 function_tool、FunctionTool 或托管类型定义；由工具插件导出，智能体插件 require 后直接加入 Agent.tools。
- 不建立 execution 工具中心、通用转换层或新的工具装饰器。filesystem 返回源码/内容路径，不隐含导入执行。
- 智能体插件从受控技能目录构造原生 Skill/Skills；ZIP 先由 filesystem 校验并解压，不直接传给 Skill。
- 工具参数说明使用 SDK 支持的类型注解与英文 docstring；不手写另一份和函数签名重复的 schema。
- 原生工具需要 Framework DI 时显式调用 Dependent，传入 wrapper.context 并管理依赖退出栈。不能声称 SDK 自动识别 Depends。
- MCP 连接由创建它的插件管理；保留原生 HTTP/SSE/stdio 语义，核对具体版本。不同凭据不能轮换同一共享连接。
- 工具挂在 SandboxAgent 上不等于工具代码在沙箱执行。参数与调用者权限由实际工具和入口负责，不让模型参数生成可信身份。
- Guardrails 与工具审批沿用 SDK；需要审批的插件自己处理原生中断和决策，不要求 execution 增加审批业务。

保留插件完整目录，避免拆散相对导入；publish_directory 只发布到新目录，不覆盖已有安装。Langfuse 管理的技能需由功能插件取回文件、加载并关联使用追踪，上传技能不等于模型已使用。活动源码按重启生效约定更新。
