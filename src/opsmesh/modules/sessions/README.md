# sessions

业务会话、参与者、展示历史和会话执行顺序。

已实现业务会话、所有者隔离、消息历史、Bot会话定位与会话执行顺序控制。Run检查点、沙箱会话状态、文件快照与学习记忆不存为同一种Session数据。

公开调用合同位于`api.py`，领域事件位于`events.py`；实现、仓储与表归`internal/`。跨模块只依赖公开合同，`bootstrap/`负责具体实现的组装。当前支持状态与测试证据见[SDK覆盖表](../../../../docs/design/sdk-coverage.md)，职责设计见[架构设计](../../../../docs/design/architecture.md)。
