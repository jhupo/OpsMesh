# configuration

动态平台配置、配额与插件启用策略。

已实现版本化平台设置与运行准入配置。插件代码由应用启动时加载；HTTP配置不能加载任意远程Python代码。

公开调用合同位于`api.py`，领域事件位于`events.py`；实现、仓储与表归`internal/`。跨模块只依赖公开合同，`bootstrap/`负责具体实现的组装。当前支持状态与测试证据见[SDK覆盖表](../../../../docs/design/sdk-coverage.md)，职责设计见[架构设计](../../../../docs/design/architecture.md)。
