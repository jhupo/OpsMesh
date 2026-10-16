# S4 修复复审（2026-10-16）

审查提交：`814fa794b9573d219f558833409925a6adb296ce`，基线：`5ada55ee`。
按上一轮问题复核修复和实际调用方；本轮只增加评审记录，没有修改产品代码。
依据 V4 第 16.2 节 S4 的邀请来源、发送幂等、并发与未知效果验收。

结论：仍有 3 项 P2，需要修复。`s4-code-review.md` 修复段中“14 项全部关闭”的结论
不能覆盖下面的残留问题和新并发缺陷。

> 修复与复验：文末《修复记录（2026-10-16 晚，接续复审）》已关闭 R1–R3；S4 验收
> 结论以该节为准。

## R1 [P2] 发送前准入没有绑定邀请令牌的修订

位置：`src/opsmesh/platform/mail/activities.py:99`，
`src/opsmesh/bootstrap/mail.py:46`。

创建邀请后不消费旧意图，先调用 resend，再消费旧意图即可触发。resend 只轮换
`token_hash`、增加 `send_count` 并登记新邮件；旧、新邮件的 `source_ref` 都是同一个
邀请 UUID。Worker 新增的重授权只比较收件人及 pending 状态，没有收到或检查令牌
修订。因此重发后的邀请仍 pending，旧意图也会通过准入，旧正文被解密并发送；其中
的令牌已经无法接受邀请。旧邮件还可能在新邮件之后到达。

本轮调用真实 registrar、`resend_invitation`、`InvitationMailAuthorization` 和
`deliver_mail_intent`，只替代持久化及 SMTP 边界，得到：

```text
ROTATED_INVITATION: invalid old token mail was admitted and sent
```

应让意图携带并验证明确的邀请修订，或在轮换事务内使旧待发送意图失效，同时保留
已开始发送的未知效果边界。`test_a_delivery_re_admits_its_source_and_refuses_a_revoked_invitation`
只覆盖 revoked，不覆盖仍 pending 的令牌轮换；需要补“轮换后消费旧、新两份意图”场景。

## R2 [P2] 管理员集合锁与审计链头锁的顺序相反

位置：`src/opsmesh/identity/users/service.py:163`、`:197`，
调用方：`src/opsmesh/identity/users/routes.py:111`。

只修改 roles/status 时，新逻辑先持有管理员行锁，再由 `append_platform_event` 获取
审计链头锁。但同一 PATCH 同时修改 display_name/work_root 和 roles/status 时，路由
先调用 `update_profile`，其审计追加已持有链头锁，再进入管理员集合锁。锁一直持有
到外层事务结束，因此以下交错会形成等待环：

1. 请求 A 修改资料和角色，取得审计链头锁。
2. 请求 B 只修改角色，取得管理员集合行锁，然后等待 A 的链头锁。
3. A 继续修改角色，等待 B 的管理员集合行锁。

PostgreSQL 会中止其中一个事务；该路径没有死锁重试或业务错误映射，请求会失败。
只改资料也可能在 commit flush 更新被锁管理员行时形成同类等待环。

本轮以真实路由和业务函数记录查询锁顺序，结果为：

```text
combined PATCH ['audit_head', 'admin_row', 'admin_row', 'audit_head']
roles-only PATCH ['admin_row', 'admin_row', 'audit_head']
```

这是实际控制流证据，持久化边界为替身，未声称已在真实 PG 复现死锁。
应在完整用户更新事务统一管理员/目标行与审计锁的获取顺序；只在角色函数内部排序
管理员 ID 不能解决跨所有者锁顺序。现有并发降权测试直接调用单个 service 函数，
没有覆盖组合 PATCH，应增加真实 PG 的上述双请求交错验收。

## R3 [P2] SMTP 配置不变时仍不能发起一次新的测试

位置：`src/opsmesh/platform/mail/routes.py:154`、`:165`。

幂等键从“仅收件人”改成“收件人 + 配置 updated_at”，但仍没有独立的测试操作标识。
同一个配置、同一个收件人，无论间隔多久、先前意图已经 sent/failed/cancelled，所有
POST 都永久返回首次意图。首次因临时网络故障失败后，恢复网络并再次测试不会进行
任何发送，除非无意义地重新保存配置。这仍保留了上一轮 P2-13 的核心问题。

本轮调用真实 `test_smtp` 路由、登记和失败函数，替代持久化边界，确认第二次请求返回
相同 ID 且 status=failed，没有登记新发送：

```text
SMTP_RETEST: a new call returned the old failed intent without a new send
```

需要区分“同一请求重试”和“用户发起新测试”：例如增加客户端操作幂等键，独立操作
使用新键、重试沿用原键；同步更新 schema/OpenAPI/SDK。
当前 `test_a_repeat_smtp_test_replays_and_a_new_configuration_starts_a_new_one` 把配置
修订直接定义为测试操作身份，不能证明同一配置下重新测试可用。

## 本轮验证和限制

以下均为本轮执行结果，不沿用修复提交文档中的远端验证结论：

| 命令/范围 | 本轮结果 |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest tests/identity tests/governance tests/platform tests/bootstrap -o addopts='' -q` | 退出 0；80 passed，58 skipped |
| `.venv/Scripts/python.exe -m pytest sdk/python/tests -o addopts='' -q` | 退出 0；99 passed |
| `ruff check`：identity/governance/platform/bootstrap 源码及对应测试、SDK 源码/测试 | 退出 0 |
| `.venv/Scripts/python.exe -m mypy` | 退出 0；156 个源码文件 |
| `.venv/Scripts/lint-imports.exe --no-cache` | 退出 0；19 kept，0 broken |
| `.venv/Scripts/python.exe scripts/audit_app_layout.py --architecture-check` | 退出 0；123 个源码文件，19 个唯一表所有者 |
| `.venv/Scripts/python.exe scripts/export_api_docs.py --check` | 退出 0；6 个生成文件一致 |
| `.venv/Scripts/python.exe scripts/generate_sdk_api.py --check` | 退出 0；3 个生成文件一致 |
| 临时定向诊断 `opsmesh_s4_followup_probes.py` | 退出 0；得到上述 3 条证据 |

诊断脚本位于 `C:/Users/Administrator/AppData/Local/Temp/opsmesh_s4_followup_probes.py`，
不属于生产源码或提交内容，没有用 SQLite 替代 PostgreSQL。

**verified**：上述本地命令和定向业务控制流；Session secret/public ID 分离、审计尾段
缺失检查、过期发送进入 needs_attention、SMTP 移出事件循环、自定义 API 前缀等修复
已进入代码。测试通过只证明各自覆盖范围。

**unverified**：本环境未配置 `OPSMESH_TEST_DATABASE_URL`，58 项数据库测试跳过；
本轮未执行真实 PG 并发、真实 Temporal 崩溃恢复、真实 SMTP 成功发送及实际 API/SDK
进程联调。R2 的真实数据库交错需要补验。

**implemented**：修复提交存在；本轮新增此复审记录。R1–R3 尚未修复，S4 不应据此标记
完整关闭。

---

# 修复记录（2026-10-16 晚，接续复审）

修复分支：`refactor/s4-identity-governance-mail`（接续 `814fa794`，未推送）。
范围：R1–R3 全部修复并补验收；新增迁移 `0003`（`mail_intents.source_revision`）。
合同变更：`TestMailRequest` 增加必填 `idempotency_key`，`MailIntentView` 增加
`source_revision`；OpenAPI、docs/api 与 SDK 已重新生成并核对一致。

## 逐项修复与验收

**R1 意图绑定邀请令牌修订。**
`MailIntent` 新增 `source_revision`（迁移 0003，可空：并非所有来源都有版本）；
`SourceScope`/`register_mail`/`DeliveryAttempt` 携带该修订；`InvitationMailRegistrar`
按邀请当前 `send_count` 登记（identity 已在同一事务创建或轮换令牌）；
`InvitationMailAuthorization` 在登记和领取两处都要求修订存在且等于邀请当前
`send_count`，否则 `ConflictError(code="invitation_rotated")`。轮换后旧意图领取即
失败（`source_not_admitted:invitation_rotated`），不再解密或发送旧正文；新修订的
意图正常通过准入。`content_digest` 纳入修订，同键不同修订判冲突。
验收：`test_a_rotated_invitation_never_sends_its_old_token`（真实 PG：创建→重发→
消费旧意图被拒且 failed→消费新意图通过准入）。

**R2 统一用户更新事务锁序。**
`users/service.py` 所有变更先锁行再审计：`update_profile`/`set_avatar`/
`clear_avatar` 先 `_locked_profile`（FOR UPDATE）；新增 `apply_admin_update` 作为
管理端更新的单一事务入口，先 `_lock_active_administrators`（按 id 升序逐行）再
`_locked_profile`，然后才应用各字段（每次审计追加时所有行锁已在手）。
`set_platform_roles`/`set_status` 保持公开函数（自服务路径与测试），内部委托同一
组 `_apply_*`；路由 `admin_update_user` 改为一次 `apply_admin_update` 调用。
验收：`test_a_combined_update_and_a_roles_only_update_do_not_deadlock`——
确定性交错构造：持锁会话按住最大 id 的管理员行，roles-only 请求锁住其余管理员行
并等待该行（轮询 `pg_stat_activity` 确认进入锁等待），组合请求随后启动并再次等待；
释放持锁后两者都提交。修复前同一构造下组合请求必得 `OperationalError`
（PG 死锁中止），修复后 3/3 通过。

**R3 SMTP 测试操作幂等键。**
`TestMailRequest` 增加必填 `idempotency_key`；`test_smtp` 的键改为
`smtp-test:{idempotency_key}`，删除配置修订参与键的逻辑。同一请求重试沿用原键
（重放同一意图），一次新测试使用新键（登记新发送）；配置不变也能重新测试。
schema/OpenAPI/SDK 同步更新。
验收：`test_a_test_operation_replays_and_a_new_operation_sends_again`
（同键重放；标记 failed 后新键仍登记 pending 新意图；更换配置后再测也是新意图）。

## 修复轮验证证据

服务器 192.168.232.129：真实 PostgreSQL 16、真实 Temporal 1.29.1、真实 Redis；
先 DROP/CREATE `opsmesh_s4_test` 并 `alembic upgrade head`。本地：Windows + uv 0.7.13。

| 检查 | 结果 |
| --- | --- |
| 服务器 `alembic upgrade head` + `alembic check` | 0001、0002、0003 应用；无漂移 |
| 服务器全量 `pytest tests -q`（干净库，含最终版测试） | 0 failed（SUITE_EXIT=0） |
| 服务器 `tests/shared/test_cache_invalidation.py`（真实 Redis） | 9 passed |
| 服务器 e2e（真实 Temporal） | PASS：audit verification succeeded；拒绝连接的邮件意图 failed 且 `outcome_unknown=False` |
| 反向实证 R1（准入忽略修订） | `test_a_rotated_invitation_never_sends_its_old_token` FAILED |
| 反向实证 R2（恢复未锁资料读 + 逐字段路由调用） | 死锁测试 3/3 FAILED，失败为 `OperationalError`（PG 死锁中止）；另测 20 次屏障竞争 0/20 复现，故改为确定性交错构造 |
| 反向实证 R3（键退回收件人+配置修订） | `test_a_test_operation_replays_and_a_new_operation_sends_again` FAILED |
| 三个反向实证的源文件 | MD5 全部复原 |
| 本地 `ruff check`（src/tests/scripts/sdk/migrations） | 通过 |
| 本地 `mypy`（strict） | 156 个源码文件通过 |
| 本地 `lint-imports --no-cache` | 19 kept，0 broken |
| 本地 `audit_app_layout.py --architecture-check` | 123 个源码文件，19 个唯一表所有者 |
| 本地 `export_api_docs.py --check`、`generate_sdk_api.py --check` | 6 + 3 个生成文件一致 |
| 本地根 `pytest tests -q`（未配 PG，门控跳过） | 退出码 0 |
| sdk/python 独立 `ruff`/`mypy`/`lint-imports`/`pytest` | 通过；33 个源码文件；5 kept；99 passed |
| SDK wheel 服务器干净环境（新 venv 安装 + 导入 + 请求） | PASS：`api_prefix="/gateway/api"` 实际请求 `/gateway/api/users/me` |

**verified**：上表全部；R1/R3 的拒绝与重试语义、R2 的真实 PG 死锁构造（修复前确定性
复现、修复后确定性通过）均在真实 PostgreSQL 上复验，e2e 与 wheel 干净环境复验通过。
**unverified**：真实 SMTP 凭据下的成功发送；真实 Temporal 进程杀死后的崩溃恢复窗口；
SDK 与真实 API 进程的 HTTP 联调（只验到 ASGI/传输级）。
**blocked**：无。
**implemented**：R1–R3 全部实施完毕，本轮复审的 3 项 P2 已关闭，S4 可以按
V4 第 16.2 节关闭验收。
