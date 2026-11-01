# memory

记忆策略、命名空间、生成任务、布局、索引与清理；不是聊天历史。

已实现独立namespace、来源、提取/合成两阶段生成任务、租约、幂等发布、查询和逻辑删除。生成通过平台runtime进行授权与用量计量；共享blob物理回收尚未实现。聊天历史由sessions拥有。

排队任务的输入版本失效时会持久化为 failed，错误码为 `MEMORY_NAMESPACE_CHANGED`；命名空间已删除则为 `MEMORY_NAMESPACE_UNAVAILABLE`。失效任务不会重新进入待执行队列，也不会在原任务身份下替换输入；需要针对新版本申请新任务。并发 worker 使用任务 CAS，不能覆盖已经被其他执行者领取的任务。

公开调用合同位于`api.py`，领域事件位于`events.py`；实现、仓储与表归`internal/`。跨模块只依赖公开合同，`bootstrap/`负责具体实现的组装。当前支持状态与测试证据见[SDK覆盖表](../../../../docs/design/sdk-coverage.md)，职责设计见[架构设计](../../../../docs/design/architecture.md)。
