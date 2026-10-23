# 网关与调用者身份

## 所有者与入口

`gateway` 拥有通道、外部身份、绑定、观察到的群成员、路由、收发件和连接租约。
`bootstrap/gateway.py` 实现执行端口；`bootstrap/gateway_worker.py` 负责独立进程。
SDK 的 `identity.py` 与 `channels.py` 提供无宿主依赖的合同。
调用者经 execution.context、加密 PreparedAgent 和 workflow_runs.encrypted_context
传到原生 SDK、Agent 工具和编排工具。MCP 连接仍由原生 SDK 管理。

## 不变量

- 只有可信宿主注入 Principal 与 InvocationSource；用户输入、模型参数和昵称不授予身份。
- 外部身份按 channel、scope、id_kind、external_id 区分。管理员核实后绑定平台用户。
- 外部群管理员是事实提示，不是平台角色。当前平台组目录未实现，group_ids 默认空。
- 私聊默认路由不匹配群聊；群路由必须显式启用。私有历史按路由与会话隔离。
- 工具发现过滤与执行授权分开。执行、恢复及读取已缓存结果前重新检查当前权限。
- 插件拥有业务授权与数据过滤。提示词、工具隐藏、MCP 连接本身都不能代替业务授权。
- API 不拥有长连接。每通道数据库租约控制网关消费；配置和插件变更自动协调。
- 持久收件后确认厂商事件。执行提交、收件关联与执行发件事务一致。
- 发送不确定时保留 uncertain，后续分片等待；重试必须明确接受重复风险并写审计。
- 解绑、停用、修改路由均不能将旧 Run 结果发给新账号，也不能删除旧用户历史。
- 通道配置、身份绑定、路由与人工恢复同事务审计；凭据和回复地址不通过查询暴露。
- 附件通过 resources 文件接口和公共 HTTPS 读取；禁止任意路径、私网和重定向。

## 改动检查

核对 SDK 合同、通道配置 schema、外部 ID 范围、群可见性、Run 来源传播、工具和 MCP
缓存前授权、租约失效、投递不确定、迁移与独立 Gateway 部署同步。
真实厂商和 Temporal 行为必须单独取得运行证据，静态检查不能证明已验收。
接口与当前能力限制以 [Gateway README](../../src/opsmesh/gateway/README.md) 为准。
