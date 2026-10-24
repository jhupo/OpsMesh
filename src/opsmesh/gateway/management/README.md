# Conversation Management

公共合同在 `opsmesh_sdk.channel_management`；成员、消息和申请引用属于指定的
`ConversationRef(scope, kind, external_id)`。QQ OpenID 字段和 REST 路径只出现在 QQ 适配器。
`ChannelCapabilities.management` 描述适配器实现范围，permissions 查询返回当前群内事实和
三态 available。未知授权不等于可用，平台仍可能因白名单、角色或时效拒绝操作。

## 操作与授权

支持 conversation、permissions、members、member、restrictions、join_requests 查询，
以及 recall、mute、unmute、review_join、remove_member、blacklist、unblacklist 操作。
QQ 实现这些接口；钉钉当前 management 为空，不提供假实现或其他协议回退。

每个群分别在完整 GroupPolicy 的 management 中显式授权，例如：

```json
{
  "actions": ["permissions", "member", "recall", "mute", "remove_member"],
  "max_mute_seconds": 3600,
  "require_approval": ["remove_member", "blacklist"],
  "grants": {
    "typesafe-decision/message_filter/moderate": {
      "actions": ["recall"],
      "automatic": true
    },
    "conversation-management/tool/manage": {
      "actions": ["permissions", "member", "recall", "mute", "remove_member"],
      "roles": ["owner", "admin"],
      "max_mute_seconds": 600
    }
  }
}
```

平台管理员、工具、审核插件和事件处理器共用 Management 服务。工具的可信来源包含原始
receipt_id，不能由模型指定平台角色；多人批次不能继承首位管理员权限。执行前重新读取
请求者的厂商群角色，无法取得当前事实时拒绝。群角色从不授予平台权限。
审核和事件自动操作另需 automatic 授权，且只能管理各自原始事件中的主体。
插件启停、版本、凭据引用或配置改变会使旧操作取消；通道适配器代际也固定保存并复查。
运行中的 Agent 不静默换插件版本。后续同群消息可以查询回执，但仍须当前插件授权和请求者角色。

默认 remove_member 和 blacklist 需要平台管理员审批，review_join 同时拉黑也检查 blacklist
授权和审批。管理员直接提交的操作本身代表明确指令。执行前复查执行账号、目标实例访问权、
审批者当前平台权限、通道配置、绑定版本、插件代际、租约及操作时效。

## 持久执行

API 只入库，独立 Gateway Worker 持有厂商凭据与连接并执行。管理队列不使用消息 ordinal，
某次撤回失败不阻塞拒绝说明、聊天回复或其他管理请求。幂等键固定请求内容，不能用同一个
键提交不同动作。禁言采用绝对截止时间；重试不会延长禁言。
审核或事件决策缺少管理授权时，动作记录 authorization_denied 失败回执；拒绝结论及说明继续独立处理。

状态为 awaiting_approval、pending、executing、succeeded、failed、uncertain、cancelled、expired。
默认请求五分钟过期；QQ 撤回另受原始平台消息两分钟时限约束，不接受缺少平台时间戳的消息。
Worker 崩溃或取消中的操作记为 uncertain；网络异常和无法确认的响应不自动重发。
只有平台明确拒绝且标记可重试时，才进行至多三次自动尝试。人工重试 uncertain 写操作必须
明确 accept_duplicate_risk；可 cancel 停止后续处理，或提交核实后的 result 执行 acknowledge。
cancel 不证明远端未执行，原未知错误保留。取消和确认不受旧配置版本或请求过期限制。
目标换绑前必须处理未决管理操作和事件；停止通道不删除回执或业务历史。

路径相对 `/api/v1/gateway/channels/{channel_id}`，均要求平台管理员：

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| POST | /operations | request_id 与 operation，返回 202 和回执 |
| GET | /operations | 最近 100 条，包含原请求、操作者、到期时间与结果 |
| GET | /operations/{id} | 查询回执 |
| POST | /operations/{id}/resolve | approve、cancel、retry、acknowledge |
| GET | /events | 最近 100 条独立会话事件 |
| POST | /events/{id}/cancel | 取消尚未完成的事件决策 |

## 非聊天事件

ChannelAdapter 的 sink 接受 InboundMessage 或 ConversationEvent。后者单独持久化、去重，
不提交聊天 Session。插件通过 ctx.conversation_event 注册异步处理器，并在
policy.event_handlers 绑定资源；处理器返回 EventDecision，操作与事件完成状态同事务入库。
处理器有超时和三次尝试上限，五分钟前的事件不再自动执行。接收时固定处理器版本，升级后不
调用新版重放旧事件。适配器必须提供已认证、稳定 ID 和明确主体的真实厂商事件。
内置 QQ/钉钉暂未接入成员和入群申请事件推送；QQ 入群申请当前通过 join_requests 查询与
review_join 处理，不根据聊天内容伪造事件或启动隐式轮询。

## 升级与平台限制

迁移 0009 创建操作与事件表，归档旧审核上下文，转换旧 allow_recall 配置和历史审核值。
只有原先已授权的审核插件保留 recall 自动授权，不额外授予工具或禁言权限。升级前停止
入站并排空活动消息、运行及未决投递。历史撤回记录不自动重放，运行时没有旧协议分支。
此变更不自动运行迁移、连接 QQ、调用模型或执行验收。

QQ 成员列表页固定最多 30 条；申请列表最大 50 条，游标原样交回。部分查询、移除和黑名单
接口需要平台内邀白名单。禁言最长 30 天且不能针对群主、管理员和机器人；普通机器人撤回
能力也受厂商权限限制，不能把本地配置视为平台授权。

参考 QQ 官方文档：[禁言](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_restrict_chat_setting.post.html)、
[入群审批](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_approval_join_request_member_openid.post.html)、
[移除成员](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_batch_remove_members.post.html)、
[机器人群状态](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_bot_state.get.html)。
