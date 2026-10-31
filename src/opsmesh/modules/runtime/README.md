# runtime

Run状态机、协调、上下文、输出、审批、检查点与SDK后端。

已实现持久Run状态机、并发与租约、事件流、取消、审批/检查点/恢复、资源收尾与OpenAI Agents SDK后端。后端按构建、工具、模型、流、状态、护栏、沙箱、追踪和错误映射拆分；Claude后端尚未实现。

终态写入与 Session 释放之间的中断通过 `internal/session_recovery.py` 恢复。组合根提供
`session_claim_reader`，只查询当前被占用的会话并核对其 Run 是否终结；终态事件消费及再次
`execute`/`cancel` 也会幂等补释放。释放以 Run ID 和 CAS 校验当前所有者，不会清除后来任务的
占用。没有占用枚举接口的第三方 SessionClaims 保持兼容，恢复时退回终态 Run 扫描。
不使用永久 cleanup-done 标记跳过对账，因为取消前读到旧状态的 worker 可能延迟领取会话。

公开调用合同位于`api.py`，领域事件位于`events.py`；实现、仓储与表归`internal/`。跨模块只依赖公开合同，`bootstrap/`负责具体实现的组装。当前支持状态与测试证据见[SDK覆盖表](../../../../docs/design/sdk-coverage.md)，职责设计见[架构设计](../../../../docs/design/architecture.md)。
