# 平台事件触发器

触发规则独立于插件 Matcher 和持久定时计划：它把受权的外部事件转为固定版本 Agent 的 Run。
`api.py` 定义规则、调用和投递记录；`internal/matching.py` 做字段匹配和输入映射，
`webhook.py` 验证请求，`delivery.py` 持久去重/提交/对账，`service.py` 负责统一权限和 CRUD。

## 规则与权限

- `source` 和 `event_type` 精确匹配；filters 对固定 envelope 或 payload 点路径做 JSON 相等比较。
  input 可为固定文本/消息列表，也可将事件字段映射为传给 Agent 的 JSON 对象。
  没有任意代码表达式、动态 import 或可执行模板。
- 管理需要 `triggers.manage`；指定其他执行主体需要 `triggers:impersonate`。
  创建者和执行主体都必须有固定 Agent 版本的 `agent:run`，执行主体还须有 `triggers.execute`。
  触发时重新验证创建者、执行主体及资源权限，已停用 Agent 不会继续执行。
- 外部事件调用者还需对应规则的 `triggers.execute`。这项权限明确授予调用该规则的能力，
  规则创建者负责选择可委托的目标主体。读取需要 `triggers.read`。
- 支持 RunOverrides、工作区/沙箱来源校验、per_event/shared 会话、Bot ReplyTarget、失败/审批通知标志。
  组合根提供目标校验与 Run submitter，submitter 直接调用受保护 Run 用例，不重新进入事件分发。
- 更新、启停、删除均检查配置 revision；删除保留墓碑和历史。已接受投递保存原配置快照，
  后续编辑仅影响新事件；同一事件 ID 不会因为配置版本变化再次执行。
  已接受事件在字段过滤规则改变后仍能按原快照恢复；停用在执行授权已被撤回时仍可操作，重新启用需重新校验。

## 入口与协议

`POST /api/v1/triggers/events` 为已登录用户的显式外部事件入口。
`POST /api/v1/triggers/{id}/webhook` 用规则中的 env secret 引用验证签名，无需登录令牌。
管理路由包括 create/list/get/update/delete/enabled/deliveries。

Webhook 请求正文为 JSON 对象（最大 64 KiB），提供三个头：

```text
X-OpsMesh-Timestamp: <UTC Unix seconds>
X-OpsMesh-Delivery: <caller delivery id, 1..200 characters>
X-OpsMesh-Signature: sha256=<hex HMAC-SHA256>
```

签名原文为 `timestamp + "." + delivery_id + "." + raw_body`；密钥至少 32 字符，仅通过 env 引用解析。
时间容许正负 300 秒。签名绑定时间、投递ID和原始正文；重试可刷新时间和签名并保留投递ID。
创建密钥引用需要 `secret:use`；本地账户授权保留旧 `secrets:use` / `secrets:*` 角色配置的兼容匹配。持久配置和响应均不保存或返回密钥内容。

## 可靠性与审计

规则与投递记录位于版本化 `trigger_records`，bootstrap 在数据库迁移前注册该表。
投递键包含规则、事件源和外部事件ID；相同键不同正文拒绝为 IDEMPOTENCY_CONFLICT。
多实例通过 CAS 和新鲜租约只允许一个活跃提交者。Run 提交使用稳定幂等键；进程崩溃后的
调用者重试可接管过期投递，继续相同 Run，不能把任意外部副作用视为 exactly-once。
首次接受时持久化原事件 envelope，重签时间戳不会改变输入映射。批量事件在任何提交前先检查所有匹配规则的调用权限。
不确定响应失败可通过 submission_resolver 只读查找已提交 Run，并严格核对主体、固定 Agent 版本和关联元数据；
明确拒绝与幂等冲突保留原失败语义。
明确失败记为 failed，不盲目自动重放；业务需要重新触发时提交新的外部事件ID。

提交成功先记 submitted 与 run_id；`reconcile()` 投影 running、waiting_approval 和真实终态。
匹配失败的已认证 webhook 记 ignored；HTTP事件只有匹配规则才创建投递。
配置变更和投递状态事实与记录同一事务写入 Outbox，带 actor 和对象版本，省略输入内容及凭据引用。

默认只从上述显式外部入口处理事件，不订阅平台内部 Outbox，不自动消费自身 Run/投递事件。
这避免触发器产生事件后再次递归启动 Agent。插件需要接入其他外部事件时显式调用公开服务；
它不提供通用工作流的分支、步骤状态或补偿引擎。
