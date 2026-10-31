# 平台持久调度

`api.py` 提供计划、预览和触发历史合同；`internal/service.py` 负责授权和管理，
`calendar.py` 计算日历，`repository.py` 负责配置版本与租约，`engine.py` 负责触发与 Run 对账。
HTTP 和 `Depends(get_scheduling)` 使用同一服务，不依赖业务插件。

## 配置与权限

- 支持带时区 date、秒 interval、数字五字段 cron（分钟、小时、日、月、星期）。
  字段支持 `*`、范围、列表、步长，星期 0/7 都为周日；同时限定日和星期时使用 OR。
  不支持秒字段、英文别名、宏、节假日日历。找不到五年内触发时间时拒绝配置。
- IANA timezone 决定 cron 的本地日历；持久时间均为 UTC。
  夏令时前进产生的不存在本地时间跳过，回拨重复的本地时间执行两个不同 UTC occurrence。
- 服务验证管理者权限，指定其他执行主体需要 `schedules:impersonate`；执行主体必须有
  `schedules.execute`，管理者和执行主体均须有对应固定发布版本的 `agent:run`。
  创建、更新、触发时均重新校验，Agent 名称在创建时固定为有效发布版本。
- 资源/消息目标授权经组合根注入的 `validate_target` 校验；未配置此校验器时拒绝资源覆盖和回复目标。
- `revision` 是配置版本，独立于内部 CAS/租约/触发游标版本。更新、删除、手动运行、预览需要匹配版本。
  原有 `set_enabled` 保留省略版本的兼容调用；新客户端应传版本。有效调度租约期间管理修改返回 CONFLICT。
  即使执行主体或 Agent 权限撤回，具有管理权的调用者仍可停用计划；再次启用须完整通过执行校验。

## 触发及恢复

- `coalesce` 把积压时间范围合为一次有效触发，保留最早漏触发时间作为稳定事件键，然后移到未来。
  `skip` 超过宽限则持久记为 skipped；`catch_up` 每 tick 最多 `max_catch_up` 次，余下积压保留。
- 默认每计划最多一个活动 Run；submitted、running、waiting_approval 都占并发名额。
  `queue` 持久保存等待项，最多 `max_queued_occurrences`；超限记 skipped/queue_full。
  `skip` 在满额时记 skipped/concurrency_limit。收到 Run 终态后释放名额。
- 每次有效触发保存不可变配置快照、事件键、Run ID、提交/完成时间、状态及安全错误。
  一次性计划耗尽不等于 Agent 完成：计划停止触发，occurrence 保持 submitted，直到 `reconcile()` 得到 Run 终态。
- 若 occurrence 已提交而计划游标尚未推进时中断，恢复按稳定 occurrence ID 跳过已保存记录，
  不再次占用队列/并发计数；只将本次新增且实际持久化的状态计入容量。
- Run 默认 `per_fire` 新会话；`shared` 复用显式 `session_id` 或计划派生会话。
  支持文本/消息列表、RunOverrides、Bot ReplyTarget 和失败/审批通知标志。
- `run_now` 不移动计划游标，使用调用者提供的幂等键；删除采用墓碑，取消尚未提交的排队项，保留审计历史和已提交 Run。
- 每次领取、续租均使用真实当前时钟；传入 tick 的 `now` 只用于判断到期。
  调用有小于租约的超时，过期 owner 无权收尾。崩溃后可能至少一次派发，Run 提交使用稳定事件键去重；
  任意插件外部副作用仍应自行使用幂等键，不承诺 exactly-once。
- 所有数据放在既有 scheduler_records 的版本化 JSON 记录中，以 kind 区分计划与 occurrence。
  老计划无 kind 的记录可直接读取；首次写入时补充字段，不增加未迁移 SQL 表。

## 组合根约定

提供 Agent resolver、Run resolver 和资源校验器。dispatcher 提交 Agent Run 后，必须将 Run ID 写入
`context.values['run_id']`；同步插件处理器没有 Agent 目标时可直接完成。
可选 submission_resolver 按主体及幂等键查找已提交 Run，恢复“提交成功但响应丢失”的关联；
恢复必须匹配 Agent 固定版本与事件/计划/occurrence 元数据，明确业务拒绝与幂等冲突不被吞掉。
通知观察器接收失败/待审批 occurrence，实际渠道投递及投递记录归通知服务。
