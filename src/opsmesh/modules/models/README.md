# models

模型配置、提供商连接、参数与兼容能力；不拥有价格账本。

已实现不可变模型档案、版本选择、能力校验、提供商URL与环境密钥引用。SDK模型对象在对应后端构建；价格、用量与预算由usage拥有。

公开调用合同位于`api.py`，领域事件位于`events.py`；实现、仓储与表归`internal/`。跨模块只依赖公开合同，`bootstrap/`负责具体实现的组装。当前支持状态与测试证据见[SDK覆盖表](../../../../docs/design/sdk-coverage.md)，职责设计见[架构设计](../../../../docs/design/architecture.md)。
