# 代码审查 R01—R08 修复记录

日期：2026-10-31。对应本轮代码审查的八个已复现问题。修复保持现有模块所有权，平台能力不依赖业务插件，不引入新的数据库表或自动修改已有角色授权。

## 实际行为

| 编号 | 修复后的行为 | 主要回归 |
| --- | --- | --- |
| R01 | Bot 身份绑定需要对目标主体的独立权限；管理员映射仅管理员可管理；替换/解绑同时检查旧身份。Bot 连接配置和频道路由变更检查已有身份，事务快照防止并发绕过 | `tests/bots/test_authorization_boundaries.py`、`tests/http/test_bot_authorization.py` |
| R02 | 所有 Bot 密钥引用先校验 `secret:use`，全部通过后再解析；更换地址但沿用旧密钥仍需校验 | 同上及 `tests/accounts/test_permission_actions.py` |
| R03 | 终态 Run 可重复释放自身会话占用；后台按当前占用会话对账，终态 Outbox 消费也补收尾；旧 Run 不会释放后继 Run 的占用 | `tests/runtime/test_session_recovery.py` |
| R04 | 定时计划恢复时复用已落库 occurrence，只对新出现的持久化记录计算容量，不再因重复计数漏触发 | `tests/scheduling/test_materialization_recovery.py` |
| R05 | 投递结果在事务内合并最新阅读状态；相同投递批次的晚到回执可解决 unknown，旧批次不能覆盖新结果；用户提供未来 revision 也不能覆盖确认回执 | `tests/notifications/test_notifications.py` |
| R06 | namespace 版本失效或被删除时，旧记忆任务持久化为 failed，记录原因并退出待执行队列 | `tests/resources/test_resource_recovery.py` |
| R07 | 快照恢复使用稳定操作身份、分配租约、私有暂存和原子目录发布，可在存储故障、取消、提交确认丢失后重试；未完成恢复保留源快照引用；底层创建在任何目录写入前拒绝已分配的恢复目标 | 同上 |
| R08 | 补价使用历史定义读取，允许为已撤回模型发布价格并结算历史用量；推理入口仍拒绝该版本 | `tests/http/test_management_boundaries.py` |

PostgreSQL 回归使用隔离的临时实例，验证多个数据库连接实例之间的会话释放、Bot 配置/身份快照竞争，以及通知阅读与回执合并。

## 权限兼容与升级

已有 `bots.manage` 角色不会自动获得目标账号管理或密钥使用权限。需要管理员按实际职责显式添加：

- `accounts:bot_identity:manage`，resource 为允许管理的目标 principal ID。管理员身份额外要求调用方本身为平台管理员。
- `secret:use`，resource 为允许配置的密钥引用，例如 `env:SUPPORT_BOT_TOKEN`。

对已有身份映射的 Bot 修改连接配置或路由时，需要具备这些映射的目标身份管理权限，防止通过间接配置入口接管已有账号。正常的 Bot 消息发送仍使用 `bots.send`。

本地账户匹配统一采用 `secret:` 命名空间，旧 `secrets:` 兼容别名已在后续清理中删除。权限匹配与角色委派上限使用相同规范化规则，保留原资源和部门限制；不把精确授权扩大成通配授权。其他权限动作命名没有在本次自动重写。

## 恢复及存储边界

Run 的会话收尾不依赖一次性的“已清理”标记，因此取消后迟到的 worker 遗留占用也可重新对账。内置 Session 服务以 SQL 筛选当前占用者；旧的自定义 SessionClaims 提供者可继续使用兼容路径，也可增加占用枚举回调以减少历史扫描。

Workspace 恢复按“同一主体、同一目标、同一快照”接续，不覆盖后来修改的已完成目标。底层无幂等键的调用可重试 staging 状态，完成后再次恢复仍会冲突。私有 staging 对正常文件读取不可见；放弃且不重试的暂存文件保守保留，维护清理须先确认操作与租约不再有效，不自动按时间删除仍可能恢复的内容。

独立复核补充了取消场景：已有 staging 恢复记录时，底层 create 必须在同一目标租约内、任何 mkdir 之前拒绝，避免取消时遗留空目录被误认为已发布。回归测试同时验证后续恢复仍包含原快照文件。

通知以递增 attempts 和当前状态校验结果归属；读取通知不会改变投递所有权。已过期但尚不确定的同一批次，收到确定回执后可更新为已发送。存储本身不可用仍须恢复后处理，修复不承诺任意外部 Bot 的 exactly-once 投递。

## 同步进行的优化

- 将会话恢复提取到 `RunSessionRecovery`，将通知结果合并提取到 `NotificationDeliveryState`，将工作区恢复提取到 `WorkspaceRestoration`，避免继续扩充大方法。
- Driver、进程内 Scheduler 和持久调度引擎的内存错误历史最多保存最近 100 条，保留原 list 查询方式。
- 对当前占用会话使用数据库条件筛选，不把全部会话历史载入恢复循环。

尚未在本轮实施费用账本的数据表拆分、全部后台查询的索引化和 runtime 全面拆分。这些属于独立的数据迁移或结构优化，应以性能基线、迁移兼容和完整回归推进，不能与这八个缺陷是否修复混为一谈。

## 验证

在最后一次工作区取消修复完成后，冻结业务源码并运行最终检查：

| 检查 | 实际结果 |
| --- | --- |
| Windows / Python 3.13 全量 `uv run pytest -q` | **558 passed，26 skipped，327.90 秒** |
| 显式 PostgreSQL 集成：基础持久化、通知并发、Bot 授权竞争 | **12 passed，28.91 秒**，使用独立临时实例 |
| `uv run ruff check .` | 通过 |
| `uv run ruff format --check .` | 445 个文件已符合格式 |
| `uv run mypy` | 328 个源码文件通过 |
| `scripts/check_repository.py` | 文档、静态模块依赖边界、包内容检查通过 |
| `uv build` 与 `scripts/check_sdist.py` | wheel、源码包构建和源码包 Markdown 链接检查通过 |

默认全量测试跳过的 26 项需要显式外部环境，其中 12 项 PostgreSQL 用例已单独启用并通过；其余 Redis、S3、Docker、Unix 原生沙箱、Kubernetes 和真实模型等用例未在本轮重新执行。历史部署结果仍属于此前验收时点。

定向测试覆盖原复现场景及并发、取消、旧数据兼容和权限拒绝路径。没有使用真实机器人发送或付费模型调用，本轮未更新测试服务器部署。已有 Bot 管理角色须按上面的权限说明显式补授权，无需新增数据库表或迁移已有表结构。
