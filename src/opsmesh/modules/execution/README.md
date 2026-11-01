# execution

执行环境、沙箱会话、资源限制、命令和文件执行边界。

已实现本地进程与Docker执行提供商、资源限制、超时/取消、输出限制和会话生命周期。平台进程隔离与OpenAI原生沙箱桥接分别配置；真实Docker环境尚未验收。

公开调用合同位于`api.py`，领域事件位于`events.py`；实现、仓储与表归`internal/`。跨模块只依赖公开合同，`bootstrap/`负责具体实现的组装。当前支持状态与测试证据见[SDK覆盖表](../../../../docs/design/sdk-coverage.md)，职责设计见[架构设计](../../../../docs/design/architecture.md)。
