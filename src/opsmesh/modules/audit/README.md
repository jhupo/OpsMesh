# audit

操作与安全审计、脱敏及留存。

已实现持久审计记录、敏感字段脱敏、幂等写入与授权查询。保留策略与外部审计存储仍属于扩展目标。

公开调用合同位于`api.py`，领域事件位于`events.py`；实现、仓储与表归`internal/`。跨模块只依赖公开合同，`bootstrap/`负责具体实现的组装。当前支持状态与测试证据见[SDK覆盖表](../../../../docs/design/sdk-coverage.md)，职责设计见[架构设计](../../../../docs/design/architecture.md)。
