# accounts

本地用户、部门、角色、登录与资源授权；不接入外部身份源。

已实现本地账号、Argon2密码校验、可撤销登录令牌、部门、角色与资源授权。身份提供商限定为本地账号。

公开调用合同位于`api.py`，领域事件位于`events.py`；实现、仓储与表归`internal/`。跨模块只依赖公开合同，`bootstrap/`负责具体实现的组装。当前支持状态与测试证据见[SDK覆盖表](../../../../docs/design/sdk-coverage.md)，职责设计见[架构设计](../../../../docs/design/architecture.md)。
