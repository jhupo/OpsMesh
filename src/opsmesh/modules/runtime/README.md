# runtime

Run状态机、协调、上下文、输出、审批、检查点与SDK后端。

已实现持久Run状态机、并发与租约、事件流、取消、审批/检查点/恢复、资源收尾与OpenAI Agents SDK后端。后端按构建、工具、模型、流、状态、护栏、沙箱、追踪和错误映射拆分；Claude后端尚未实现。

终态写入与 Session 释放之间的中断通过 `internal/session_recovery.py` 恢复。组合根提供
实现 `active_claims` 的 `SessionClaims`，只查询当前被占用的会话并核对其 Run 是否终结；终态事件消费及再次
`execute`/`cancel` 也会幂等补释放。释放以 Run ID 和 CAS 校验当前所有者，不会清除后来任务的
占用。占用枚举是必需合同，不再保留扫描全部终态 Run 的兼容路径。
不使用永久 cleanup-done 标记跳过对账，因为取消前读到旧状态的 worker 可能延迟领取会话。

`internal/records.py` 拥有 Run 与事件流的索引查询；`internal/approvals.py` 拥有审批状态转换，
`internal/workers.py` 拥有工作器租约与恢复，`internal/service.py` 编排执行、取消和终态提交。
后台批量遍历使用公开的 `queries.py` 游标，不依赖私有数据表。

执行上下文通过 `framework/agents/resources.py` 的类型化服务、资源和状态传入。SDK 私有
状态只存在于对应后端；沙箱实现由组合根按后端注入，框架合同不依赖 OpenAI SDK。

公开调用合同位于`api.py`，领域事件位于`events.py`；实现、仓储与表归`internal/`。跨模块只依赖公开合同，`bootstrap/`负责具体实现的组装。当前支持状态与测试证据见[SDK覆盖表](../../../../docs/design/sdk-coverage.md)，职责设计见[架构设计](../../../../docs/design/architecture.md)。
