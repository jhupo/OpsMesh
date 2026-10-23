# 网关与调用者身份

## 所有者与入口

`gateway` 拥有通道、外部身份、绑定、观察到的群成员、路由、收发件和连接租约。
`bootstrap/gateway.py` 实现执行端口；`bootstrap/gateway_worker.py` 负责独立进程。
SDK 的 `identity.py` 与 `channels.py` 提供无宿主依赖的合同。
gateway 内按 api、channels、identity、routing、messaging、storage 与 adapters 归类。
路由与提交前授权共用 routing/service.py，公共执行事件在 contracts.py，不保留旧导入路径。
调用者经 execution.context、加密 PreparedAgent 和 workflow_runs.encrypted_context
传到原生 SDK、Agent 工具和编排工具。MCP 连接仍由原生 SDK 管理。

## 不变量

- 只有可信宿主注入 Principal 与 InvocationSource；用户输入、模型参数和昵称不授予身份。
- 外部身份按 channel、scope、id_kind、external_id 区分。个人入口经核实后绑定平台用户；公开群可显式绑定受限服务账号和目标实例，成员不逐个注册。
- 外部群管理员是事实提示，不是平台角色。当前平台组目录未实现，group_ids 默认空。
- 私聊默认路由不匹配群聊。共享群路由只匹配已观察的完整 conversation_key，优先于个人路由；停用不能回退到成员个人权限。
- 原生 Session 按通道、授权账号、目标实例和会话范围隔离。同群默认共享，私聊或 user 范围按发送者隔离；路由 revision 不构成另一个历史来源。
- 工具发现过滤与执行授权分开。执行、恢复及读取已缓存结果前重新检查当前权限。
- 插件拥有业务授权与数据过滤。提示词、工具隐藏、MCP 连接本身都不能代替业务授权。
- API 不拥有长连接。每通道数据库租约控制网关消费；配置和插件变更自动协调。
- 持久收件后确认厂商事件。执行提交、收件关联与执行发件事务一致。
- 发送不确定时保留 uncertain，后续分片等待；重试必须明确接受重复风险并写审计。
- 解绑、停用、修改路由均不能将旧 Run 结果发给新账号，也不能删除旧用户历史。
- 通道配置、身份绑定、路由与人工恢复同事务审计；凭据和回复地址不通过查询暴露。
- 附件通过 resources 文件接口和公共 HTTPS 读取；禁止任意路径、私网和重定向。
- 审核厂商只出现在独立 message_filter 插件。逐条原文审核后才激活、合批与提交；审核失败有界重试后 blocked，不能自动放行或撤回。
- reply/ignore 只由显式 message 输出路由解释，Agent 和 Workflow 都可使用。回复选择属于 Agent/业务配置，不属于审核器。
- 原生 Session 是唯一对话记忆来源。bootstrap 只转换当前批次的协议值，不查询历史、不拼平台 recent_messages、不固定机器人名字或接话规则。
- 多人批次保留每条原文、身份和路由快照，逐条重新授权；顶层不继承首位群管理员身份。工具和 MCP 使用 Principal/InvocationSource，而不是消息中的自称。
- 推送使用已核实的会话收件依据并复用同一发件箱；QR 只创建禁用通道，不自动绑定或授权用户。
- 同一 Session 的普通消息顺序处理，debounce 合批有静默窗口、最大等待、条数和体积上限；运行中新消息留在下一批。群控制命令需匹配显式 control_roles。
- subjects/broadcast 基于已观察且当前可授权的目的地，不代表厂商全量用户目录。
- QQ C2C 流式输出只记录公开根 Agent、handoff 与根沙箱文本；工具 Agent 及其后代不公开。
- 不转发隐藏 reasoning、内部子节点输出或未经守卫校验的文本；公开摘要需显式启用。
- 原生 Runner 流式执行由官方 Temporal Agents 集成和 WorkflowStream 提供，不在 get_response 重建流式 Runner。

## 改动检查

核对 SDK 合同、通道配置 schema、外部 ID 范围、群可见性、Run 来源传播、工具和 MCP
缓存前授权、租约失效、投递不确定、迁移与独立 Gateway 部署同步。
真实厂商和 Temporal 行为必须单独取得运行证据，静态检查不能证明已验收。
接口与当前能力限制以 [Gateway README](../../src/opsmesh/gateway/README.md) 为准。
