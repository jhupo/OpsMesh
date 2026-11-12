---
name: opsmesh-agents-verification
description: 原生 Usage、Trace、Langfuse 属性与执行观测验证。用于 execution 观测生命周期、导出或 SDK 兼容性修改。
---

# 执行观测与验证

先读 [插件架构](../../../docs/plugin-architecture.md) 的 execution 和验收章节；具体回调契约见 [通道与执行回调](../../../docs/channels-and-callbacks.md)。

- execution 无数据库依赖、调用表、Usage 账本或统计报表。读取原生 RunResult/RunResultStreaming 的 context_wrapper.usage，不重新定义 UsageTotals、CallRecord 或覆盖状态枚举。
- before_run 是执行前检查；原生 hooks/Guardrails 保留异常语义。on_run_end(result, error, trace_id) 是结束观察通知，其失败不覆盖成功结果或原异常，不触发模型重试。
- TraceContext 的 user_id/session_id/tags 是观测属性，不放入 UserContext 或模型输入。属性作用域覆盖整个 run/stream，并发调用不互相污染。
- 追踪目标在 execution 启动时设置一次；使用锁定版本的原生 processor/导出接口，不为兼容未知旧版加入 TypeError 回退。
- 检查重复导出、敏感内容采集和凭据泄漏；关闭时有界刷新。Langfuse 失败不等于模型失败，也不能只凭无数据页面判断未上报。
- Langfuse Users/Sessions 依赖标准属性；Prompts/Skills 的选择与加载属于功能插件，使用追踪不等于自动注册内容或技能加载记录。
- 以真实 Runner 和确定性模型验证成功、取消、流早退、审批、hook 异常、结束观察失败及回调依赖作用域。只运行受影响测试，明确真实 PostgreSQL、网络、沙箱、冻结包及服务器联调是否实际执行。
