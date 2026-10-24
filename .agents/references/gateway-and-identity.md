# 网关与调用者身份

## 所有者与入口

`gateway` 拥有通道、外部身份、绑定、观察到的群成员、通道目标、收发件和连接租约。
`bootstrap/gateway.py` 实现执行端口；`bootstrap/gateway_worker.py` 负责独立进程。
SDK 的 `identity.py` 与 `channels.py` 提供无宿主依赖的合同。
gateway 内按 api、channels、identity、routing、messaging、management、storage 与 adapters 归类。
通道目标与提交前授权共用 routing/service.py，公共执行事件在 contracts.py，不保留旧导入路径。
调用者经 execution.context、加密 PreparedAgent 和 workflow_runs.encrypted_context
传到原生 SDK、Agent 工具和编排工具。MCP 连接仍由原生 SDK 管理。

## 不变量

- 只有可信宿主注入 Principal 与 InvocationSource；用户输入、模型参数和昵称不授予身份。
- 一个通道仅绑定一个 Agent 或 Workflow 实例及执行账号；外部身份按 channel、scope、id_kind、external_id 区分。私聊和群聊都不要求先绑定成员平台账号；可选账号关联不选择目标或执行权限。
- 外部群管理员是事实提示，不是平台角色。当前平台组目录未实现，group_ids 默认空。
- private_enabled 和 groups 控制允许会话。groups 为显式群/频道 ID 白名单，不用于选择执行目标；通道没有个人、群路由或授权回退。
- 原生 Session 按通道、执行账号、目标实例和会话隔离。同群所有成员共享，私聊按外部用户独立；配置和绑定 revision 不构成另一份历史。Workflow 各 Agent 绑定沿用现有原生 Session 映射。
- 工具发现过滤与执行授权分开。执行、恢复及读取已缓存结果前重新检查当前权限。
- 插件拥有业务授权与数据过滤。提示词、工具隐藏、MCP 连接本身都不能代替业务授权。
- API 不拥有长连接。每通道数据库租约控制网关消费；配置和插件变更自动协调。
- 持久收件后确认厂商事件。执行提交、收件关联与执行发件事务一致。
- 发送不确定时保留 uncertain，后续分片等待；重试必须明确接受重复风险并写审计。
- 换绑先排空活动收件与未决投递。binding_revision 仅目标改变递增，防止 A→B→A 重用旧回执；旧 Run 结果不能发往新账号或目标，不删除旧历史。
- 入站与换绑使用同一通道行锁；接收时核对连接配置版本，并持久化执行账号、目标和绑定版本。人工重试保留并核对原绑定，旧消息不能跨绑定重放。
- 旧个人路由迁移后通道停用，私聊入口统一关闭，需管理员明确确认访问范围。共享 group/guild 授权只从启用的旧共享路由迁移，不把个人授权变为公开入口。
- 通道配置、可选身份关联和人工恢复同事务审计；凭据和回复地址不通过查询暴露。
- 附件通过 resources 文件接口和公共 HTTPS 读取；禁止任意路径、私网和重定向。
- 审核厂商只出现在独立 message_filter 插件。逐条原文审核后才激活、合批与提交；审核失败有界重试后 blocked，不能自动放行或撤回。
- 审核仅返回 allow/drop；管理动作独立持久化，不进入普通发件 ordinal。撤回失败不阻塞拒绝说明。禁止旧 action/event=recall 与 allow_recall 运行时兼容分支。
- 管理请求来自平台管理员、可信工具、审核器或非聊天事件处理器；复查当前授权、绑定、插件代际、审批者与租约。多人批次不能借首位管理员身份发起管理。结果未知不自动重发。
- 非聊天事件单独去重入库，不写聊天 Session；适配器未提供的厂商事件不能伪造或静默轮询。平台白名单、角色和两分钟撤回时效不能用配置绕过。
- reply/ignore 只由目标的显式 message 输出模式解释，Agent 和 Workflow 都可使用。回复选择属于 Agent/业务配置，不属于审核器。
- 原生 Session 是唯一对话记忆来源。bootstrap 只转换当前批次的协议值，不查询历史、不拼平台 recent_messages、不固定机器人名字或接话规则。
- 多人批次保留每条原文和身份，收件保存通道目标版本，提交前逐条核对身份事实与同一目标；顶层不继承首位群管理员身份。工具和 MCP 使用 Principal/InvocationSource，而不是消息中的自称。
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
