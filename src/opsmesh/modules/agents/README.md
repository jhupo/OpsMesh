# agents

Agent定义、版本、工厂、绑定、校验与发布。

已实现草稿、校验、不可变发布版本、克隆、启停与受插件归属管理的代码工厂。Agent执行与检查点归runtime；工作区文件归workspaces。

公开调用合同位于`api.py`，领域事件位于`events.py`；实现、仓储与表归`internal/`。跨模块只依赖公开合同，`bootstrap/`负责具体实现的组装。当前支持状态与测试证据见[SDK覆盖表](../../../../docs/design/sdk-coverage.md)，职责设计见[架构设计](../../../../docs/design/architecture.md)。
