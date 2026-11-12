---
name: opsmesh-agents-sandbox
description: 智能体插件中的 SandboxAgent、Skill 目录、Manifest 和沙箱资源生命周期。用于沙箱配置、文件挂载和记忆设计。
---

# 原生沙箱与文件来源

先读 [当前架构](../../../docs/plugin-architecture.md) 的原生 Agent/MCP/会话章节，再核对当前 SDK 源码。

- SandboxAgent 属于原生 Agent 体系，execution 不建立独立沙箱执行引擎。
- 智能体插件负责 Manifest、Skills、SandboxRunConfig、客户端和资源作用域；filesystem 只提供持久文件及受控普通目录。
- 源技能目录与可变工作区分离。挂载/复制后的更改不自动回写安装包，活动调用期间不覆盖其文件来源。
- Manifest 定义新工作区内容；恢复 session_state 或 snapshot 时核对原生行为，不假设重新覆盖。
- 本地工作目录不等于操作系统隔离；函数工具和 MCP 的执行位置单独核对。不能仅凭沙箱配置字段声称隔离已生效。
- 模型客户端、沙箱、MCP 的作用域覆盖完整调用与流清理。调用插件决定是否复用、何时关闭和如何清理遗留资源。
- 文件记忆、SDK Session 和 RunState 是不同内容。共享记忆并发写需要明确所有者和互斥；独立记忆生成用量不能无依据并入主调用。
- 不恢复独立 environments 业务模块、工作区平台目录或跨重启执行快照中心。

确定性测试证明编排，真实后端验证挂载、隔离和取消清理；只验证受影响能力。
