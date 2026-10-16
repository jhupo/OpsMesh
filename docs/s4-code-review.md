# S4 代码 Review（2026-10-16）

复核提交：`5ada55ee`（`feat: deliver S4 identity, governance and platform mail`）。
依据：V4 第 12、15、16.2 节及 S4 实施记录。复核身份、邀请、配额、审批、审计、
邮件与 bootstrap、SDK/生成合同的相关调用链。

结论：发现 **14 项问题（7 项 P1、7 项 P2）**，当前不能关闭 S4 验收。
本轮仅增加此 review 记录，没有修改生产代码、迁移、SDK 或既有测试，也未提交或推送。
并发问题按当前代码和 PostgreSQL 默认 READ COMMITTED 时序推导，尚无本轮真实 PG
复现证据；下面逐项区分证据，不能把本地诊断当作完整产品流程验收。

> 修复与复验：文末《修复记录（2026-10-16 晚）》已关闭全部 14 项，并登记复验中
> 新发现的 2 项缺陷；S4 验收结论以该节为准。

1. **[P1] 登录令牌原文写入 Session 主键，摘要存储失效。**

   定位：[identity/auth/service.py:196](../src/opsmesh/identity/auth/service.py#L196)。
   `_start_session` 把同一个 UUID 同时用于 `LoginSession.id` 与返回给客户端的
   `session_id`；`token_hash` 并没有消除原文。数据库只读泄露便能直接取得有效的
   `X-Session-ID`，与模型注释、实施记录“只存哈希”的声明相反。
   应分别生成数据库公开标识与认证令牌，列表/撤销使用公开标识，认证只查令牌摘要。
   本地诊断调用实际 `_start_session`，确认 `persisted.id == grant.session_id`。

2. **[P1] 审计范围尾部缺失时，完整性验证仍成功。**

   定位：[governance/audit/activities.py:45](../src/opsmesh/governance/audit/activities.py#L45)、
   [workflow.py:81](../src/opsmesh/governance/audit/workflow.py#L81)。
   Activity 只校验查到的事件，没有检查完整覆盖 `from_sequence..to_sequence`。
   删除尾部事件后，剩余合法前缀的 `first_broken_sequence` 为 None；Workflow 直接
   跳到请求的下一批并最终记录 succeeded。空的首批同样通过。
   应检查请求边界和首个缺失序号，禁止以不足的 checked_count 证明整段完整。
   本地诊断通过实际 Activity 和 Workflow 函数复现：请求 1..3，只返回事件 1，
   `checked_count=1`，最终 `status=succeeded`。Activity 调度和 DB 查询使用模拟边界，
   未运行真实 Temporal/PG。

3. **[P1] 已发送但未落库的邮件会在租约过期后重复发送。**

   定位：[platform/mail/service.py:389](../src/opsmesh/platform/mail/service.py#L389)、
   [activities.py:92](../src/opsmesh/platform/mail/activities.py#L92)。
   SMTP 已接受邮件、Worker 在 `mark_sent` 提交前崩溃时，唯一持久事实是 sending。
   目前 300 秒后将其当成可重试意图再次领取并执行 SMTP；租约到期无法证明上次没有
   产生外部效果。数据库写入失败或超时后的重试也会进入这个窗口。
   应将无法确认的在途尝试转入 needs_attention，保留稳定尝试/效果回执；不能依据
   过期时间重新执行非幂等 SMTP 写入。既有租约测试反而要求 attempts 增至 2，
   固化了错误恢复语义。本地诊断确认过期 sending 被实际 Activity 重新发送；崩溃窗口
   本轮未在真实 SMTP/Temporal 上复现。

4. **[P1] 邮件消费与重试不复核来源授权，撤销邀请仍会发出邮件。**

   定位：[platform/mail/activities.py:44](../src/opsmesh/platform/mail/activities.py#L44)、
   [bootstrap/mail.py:29](../src/opsmesh/bootstrap/mail.py#L29)。
   授权只发生在 `register_mail`，Activity 领取时仅检查邮件状态。
   队列积压期间撤销或接受邀请，甚至在邀请过期后，旧意图仍能解密正文并发送。
   邀请重发轮换令牌时，旧版本的待发正文也没有当前版本绑定。
   应在领取/重试时经注入的来源合同检查当前有效状态及来源版本，再决定是否进入
   外部发送。本地诊断确认同一来源被实际 InvitationMailAuthorization 拒绝，但实际
   MailActivities 仍调用模拟 SMTP 并返回 delivered=True。

5. **[P1] 邮件取消未锁行，能够覆盖已经领取的 sending 状态。**

   定位：[platform/mail/service.py:317](../src/opsmesh/platform/mail/service.py#L317)。
   取消事务普通读取 pending 后，Worker 可通过 `mark_sending` 的行锁完成领取并提交；
   取消事务随后基于旧对象提交 cancelled，返回成功，但发送已经开始，最终还可能被
   `mark_sent` 覆盖成 sent。数据库不会自动将该普通读取与后续更新变成条件状态迁移。
   取消应使用与领取相同的行锁或带旧状态条件的原子更新；已领取的意图必须按在途效果
   语义处理。代码与事务时序确认；真实 PG 并发复现 unverified。

6. **[P1] 最后管理员检查存在写偏差，可并发移除所有管理员。**

   定位：[identity/users/service.py:302](../src/opsmesh/identity/users/service.py#L302)。
   A、B 是仅有的活跃管理员，两个事务分别锁自己的目标用户；A 检查时看到 B，B 检查
   时看到 A，均允许降权/禁用。后续审计链头锁只串行追加审计，不会重新执行此前的
   检查；两次提交后可没有活跃管理员。应在共同的管理员集合序列化边界内重新读取、
   检查和写入，并遵守统一锁序；单个目标用户的锁不够。
   代码与事务时序确认；真实 PG 并发复现 unverified。

7. **[P1] 邀请撤销/轮换未使用接受路径的行锁，可覆盖终态。**

   定位：[identity/invitations/service.py:161](../src/opsmesh/identity/invitations/service.py#L161)、
   [service.py:120](../src/opsmesh/identity/invitations/service.py#L120)。
   `accept_invitation` 使用 FOR UPDATE，revoke/resend 却普通读取。撤销事务先读到 pending，
   接受事务随后开户并提交 accepted，撤销事务仍可把状态覆盖为 revoked 并报告成功；
   已建立的账号不受影响。轮换也能基于过时的 pending 对象，在接受/撤销之后登记旧状态
   的发送。三条状态迁移必须锁住同一邀请并在锁内验证当前状态，发送登记沿用同一事务。
   代码与事务时序确认；真实 PG 并发复现 unverified。

8. **[P2] 审批幂等返回绕过授权，普通用户可读取别人的已决定审批。**

   定位：[governance/approvals/service.py:134](../src/opsmesh/governance/approvals/service.py#L134)。
   已决定分支在校验 approver 之前，凭相同 key/decision 直接返回审批对象；POST 路由
   没有补做 `may_read`，并将 details 等字段完整序列化。知道 approval_id 和原命令键的
   另一登录用户，即使 GET 会被拒绝，也能通过重放 approve/reject 取得详情。
   幂等键不应成为读取凭据，应在所有返回分支前完成当前主体授权，并校验决定输入一致。
   本地诊断确认 unrelated 用户 `may_read=False`，实际 decide 却返回完整原审批。

9. **[P2] 多处幂等查询在锁外，并发同键请求得不到原回执。**

   定位：[identity/quotas/service.py:139](../src/opsmesh/identity/quotas/service.py#L139)、
   [storage:215](../src/opsmesh/identity/quotas/service.py#L215)、
   [governance/audit/service.py:118](../src/opsmesh/governance/audit/service.py#L118)、
   [platform/mail/service.py:232](../src/opsmesh/platform/mail/service.py#L232)。
   两事务均可先查到“无回执”；配额账户锁/审计链头锁随后才获得，等待者拿锁后不再查键，
   直接继续创建。配额可能因先前预留使剩余额度不足而错误拒绝相同请求，或在唯一约束
   处报 IntegrityError；审计/邮件也由唯一约束挡住重复，而非返回原回执。
   审计和邮件 HTTP 事务未提供 conflict_message，原始 IntegrityError 还会冒出为 500。
   应将同键认领、输入比对及业务写入放进可串行化的幂等边界，或对唯一冲突进行局部回滚
   后安全读取原回执；不能简单移除唯一约束。代码与事务时序确认；真实 PG 并发复现
   unverified，当前 S4 测试没有并发同键场景。

10. **[P2] 异步邮件 Activity 直接运行同步 SMTP，阻塞整个 Worker 事件循环。**

    定位：[platform/mail/activities.py:79](../src/opsmesh/platform/mail/activities.py#L79)。
    `send_mail` 使用同步 smtplib，DNS、连接、TLS、认证和发送均阻塞；由于从 async
    Activity 直接调用，其他 Activity、取消和任务轮询无法及时调度。SMTP 单操作超时可
    配到 300 秒，而 Activity start-to-close 为 120 秒，影响不限于该邮件。
    应使用受控的同步执行边界或异步传输，并同时明确底层 I/O 的取消/超时及未知效果；
    仅把函数放入线程不能证明外部发送已停止。
    本地诊断用模拟阻塞 SMTP 验证：已就绪的另一个 asyncio task 在发送期间完全不能运行。
    与 [Temporal 官方同步/异步说明](https://docs.temporal.io/develop/python/best-practices/python-sdk-sync-vs-async)
    所述的 async Activity 运行边界一致。

11. **[P2] SMTP 尚未配置时，邮件永久停在 sending 且不能核对。**

    定位：[platform/mail/activities.py:70](../src/opsmesh/platform/mail/activities.py#L70)。
    claim 已单独提交，随后 read_settings 抛 non_retryable ApplicationError；该异常不在
    `_record_failure` 的捕获范围内，Workflow 直接失败，意图仍是 sending。取消 API 只收
    pending，核对 API 只收 needs_attention；管理员后续配置 SMTP 也没有公开恢复该意图
    的路径。应为发送前确定失败和重试耗尽建立持久终态/可恢复状态，再结束 Workflow。
    本地诊断确认异常 non_retryable、意图保持 sending、reconcile_intent 拒绝。

12. **[P2] 邮件每日上限使用普通 COUNT，不能限制并发登记。**

    定位：[platform/mail/service.py:244](../src/opsmesh/platform/mail/service.py#L244)。
    当上限只剩一个位置，两个不同幂等键事务都能读到相同 count，并各自创建意图与 Outbox；
    没有来源/日期的共同锁或原子额度记账，唯一键也不会冲突。并发量越大，超限越多。
    应在来源/日期的原子准入边界里检查和占用额度，重放不重复计入。
    代码与事务时序确认；真实 PG 并发复现 unverified。

13. **[P2] SMTP 测试键永久固定为收件人，配置修好后不能重新测试。**

    定位：[platform/mail/routes.py:153](../src/opsmesh/platform/mail/routes.py#L153)。
    同一地址的每次显式测试都用 `smtp-test:{recipient}`，正文/来源摘要也不包含配置修订。
    第一次发送失败后，修改服务器/凭据再测试，只会拿到旧 failed 回执；成功后也不能对
    同一地址测试新配置，接口仍返回 201，实际没有新 Outbox。
    应区分一次新的测试操作与该操作的网络重试，提供操作幂等键并明确配置版本边界。
    由路由固定键与 register_mail 的现有重放分支确认；未新增真实 SMTP 发送。

14. **[P2] SDK 固化 /api/v1，无法访问配置了其他 API 前缀的服务。**

    定位：[scripts/generate_sdk_api.py:320](../scripts/generate_sdk_api.py#L320)、
    [SDK identity.py:191](../sdk/python/src/opsmesh_sdk/api/identity.py#L191)。
    生成器直接把导出文档的绝对路径写入客户端。PlatformClient 没有 API prefix 参数，
    HttpTransport 的 URL.join 又使绝对路径丢弃 base_url 的路径部分，所以把前缀放进
    base_url 也无法修正。服务端支持的 OPSMESH_API_PREFIX 因此破坏这三个 SDK 分组。
    应让运行时 API 根与生成的资源相对路径正确组合，并同步 JSON/二进制/流/宿主通道。
    本地诊断使用实际 create_app(api_prefix='/custom/v1')、实际生成客户端和 ASGITransport，
    确认已注册 /custom/v1/auth/sessions，SDK 仍请求 /api/v1/auth/sessions，收到 404。

本轮检查证据（全部基于本轮工作树，命令退出码均为 0）：

| 检查 | 结果 |
| --- | --- |
| 根目录 `.venv/Scripts/python.exe -m pytest tests/identity tests/governance tests/platform tests/bootstrap -o addopts='' -q` | 77 passed，43 skipped |
| 根目录 `.venv/Scripts/python.exe -m ruff check src/opsmesh/identity src/opsmesh/governance src/opsmesh/platform src/opsmesh/bootstrap tests/identity tests/governance tests/platform tests/bootstrap` | 通过 |
| 根目录 `.venv/Scripts/python.exe -m mypy` | 156 个源码文件通过 |
| 根目录 `.venv/Scripts/lint-imports.exe --no-cache` | 19 kept，0 broken |
| 根目录 `.venv/Scripts/python.exe scripts/audit_app_layout.py --architecture-check` | 123 个源码文件，19 个唯一表所有者，通过 |
| 根目录 `.venv/Scripts/python.exe scripts/export_api_docs.py --check` | 6 个生成文件一致 |
| 根目录 `.venv/Scripts/python.exe scripts/generate_sdk_api.py --check` | 3 个生成文件一致 |
| sdk/python 目录，使用根目录 venv 的 `python -m pytest -o addopts='' -q` | 93 passed |
| sdk/python 目录，使用根目录 venv 的 `python -m ruff check src tests`、`python -m mypy`、`lint-imports --no-cache` | Ruff 通过；33 个源码文件通过；5 kept，0 broken |
| 本地诊断脚本 | 8 项异常确认，退出码 0；这是缺陷复现，不是正确性验收 |

诊断脚本保存于
`C:/Users/Administrator/AppData/Local/Temp/opsmesh_s4_review_probes.py`，从仓库根目录执行：

```powershell
.venv/Scripts/python.exe C:/Users/Administrator/AppData/Local/Temp/opsmesh_s4_review_probes.py
```

脚本调用实际业务函数，模拟数据库与 SMTP 边界；审计 Workflow 的 Activity 调度亦模拟。
SDK 前缀诊断使用实际 FastAPI ASGI 应用，没有启动独立 API 进程。它们均不访问真实业务库、
不发送邮件、不使用用户凭据。前述“8 项”对应问题 1、2、3、4、8、10、11、14。

**verified**：上述定向套件、静态/依赖/生成检查，以及明确标注的本地异常复现。
**unverified**：真实 PG 并发/迁移、本轮真实 Temporal 崩溃恢复/取消/重放、真实 SMTP 成功发送、
SDK 独立干净环境 wheel 安装与真实进程 HTTP。OPSMESH_TEST_DATABASE_URL 未配置，43 项
基础设施门控测试跳过；原实施记录的服务器证据不作为本轮新证据。
**blocked**：没有阻止输出 review 的障碍；真实基础设施验证仍待可用测试连接。
**implemented**：本轮没有实施修复。应按以上问题补充有区分力的拒绝/并发/恢复验收后，再关闭
对应 S4 门槛，不能由现有检查通过推导产品流程正确。

---

# 修复记录（2026-10-16 晚）

修复分支：`refactor/s4-identity-governance-mail`（接续 `5ada55ee`，未推送）。
范围：上面 14 项全部修复；每项均有拒绝/并发/恢复类验收；复验中新发现并修复 2 项
（F1、F2）。所有修复遵循 V4 第 15 节锁序与第 12 节依赖合同，未新增模块、迁移或
兼容路径；模型未变，`alembic check` 无漂移。

## 逐项修复与验收

**P1-1 Session 令牌与主键分离。**
`_start_session` 生成两个独立 UUID：公开行 id（列表/撤销）与认证令牌（只存
`token_hash=token_digest(token)`），`LoginResult.token` 仅返回一次。
schemas/routes/provisioning/invitations 的 `session_id` 更名为 `session_token`。
验收：`test_the_stored_session_never_equals_the_public_id_or_the_token`
（`stored.id != token`、`token_hash` 为摘要、公开 id 不能认证、令牌可以）。

**P1-2 审计尾部覆盖。**
`hashing.verify_segment()` 在链完整但事件数不足请求范围时把首个缺失序号报为
broken；`activities.verify()` 两个分支均改调它。
验收：`tests/governance/test_audit_hashing.py` 新增 3 例（完整范围通过、删除尾部
报首个缺失序号、锚点后缺失尾部）。

**P1-3 过期租约不重发。**
`mark_sending` 更名 `claim_intent`：pending→sending（attempts+1）；sending 且租约
过期→撤回 `needs_attention`、`outcome_unknown=True`、`failure_reason=attempt_uncertain`
且 attempts 不增；租约内→`mail_claim_held` 冲突。Activity 领取到撤回结果直接返回
未知效果，不解密不发送。
验收：`test_an_expired_claim_is_withdrawn_never_resent`、
`test_a_claimed_intent_cannot_be_claimed_again_before_its_lease`。

**P1-4 领取时复核来源授权。**
`MailActivities` 经 bootstrap 注入的 `mail_source_authorizations()` 注册表，在领取
之后、解密正文之前重新准入来源（`InvitationMailAuthorization`/`PlatformTestAuthorization`）；
`ForbiddenError/ConflictError`→记 failed + `source_not_admitted:{code}`；来源不在注册表
→`source_unregistered`。`DeliveryAttempt` 不再携带正文。
验收：`test_a_delivery_re_admits_its_source_and_refuses_a_revoked_invitation`
（撤销邀请后 deliver→`source_not_admitted:conflict`，意图 failed）。

**P1-5 取消不覆盖在途发送。**
`cancel_intent` 改 `with_for_update()`：pending→cancelled；sending→
`ConflictError(code="mail_in_flight")`；终态→原冲突。
验收：`test_an_in_flight_intent_is_never_cancelled_over_a_live_send`。

**P1-6 最后管理员写偏差。**
`set_platform_roles`/`set_status` 先在共同边界 `_lock_active_administrators()` 按 id
升序逐行 `FOR UPDATE` 锁定整个活跃管理员集合，再锁目标行；`_guard_last_administrator`
用锁内重读的当前值（status + platform_roles）判断是否还有其他活跃管理员。
验收：`test_concurrent_demotion_cannot_empty_the_platform`（真实 PG 双线程 + Event
屏障，3 轮，每轮一成一拒、最终恰好 1 个活跃管理员）。反向实证：把判断改回“只按行
身份”后该测试在服务器失败（见下）。

**P1-7 邀请终态不被覆盖。**
`resend_invitation`/`revoke_invitation` 改经 `_locked_invitation`（`FOR UPDATE`），
与 `accept_invitation` 同一行锁，锁内校验当前状态。
验收：`test_an_accepted_invitation_is_never_revoked_or_resent`（接受后撤销/重发均
`InvitationStateError`，终态与 send_count 不变）。

**P2-8 审批授权先于幂等返回。**
`decide()` 在所有返回分支前执行 `may_read(request, decided_by)`，未授权直接
`ForbiddenError`。
验收：`test_an_unrelated_replay_of_a_decision_is_refused`。

**P2-9 幂等认领进入串行边界。**
配额：`reserve_tokens`/`reserve_storage` 先锁账户行再查幂等键；审计：链头锁内查回执
（见 F1）；邮件：`pg_advisory_xact_lock(hashtext("mail-source-day:{source}:{day}"))`
事务级建议锁内查回执并做每日计数。
验收：`test_concurrent_reservations_of_one_key_replay_one_reservation`、
`test_concurrent_extension_appends_of_one_key_replay_one_event`、
`test_concurrent_registrations_of_one_key_replay_one_intent`（真实 PG 并发，同键只
产生一份事实）。

**P2-10 同步 SMTP 移出事件循环。**
发送改 `asyncio.wait_for(asyncio.to_thread(send_mail, ...), timeout=settings.timeout_seconds)`；
`TimeoutError`→`needs_attention` + `send_timeout`（未知效果，绝不重发）。
验收：`test_a_slow_send_does_not_block_the_event_loop`（阻塞式 SMTP 下事件循环仍
可调度；旧实现在外层 60 秒保护下超时失败）。

**P2-11 发送前失败建立持久终态。**
无 SMTP 配置→记 failed + `smtp_not_configured`；已知拒绝→failed + 原因；未知超时
→needs_attention。Workflow 对确定性失败正常完成，意图不再滞留 sending。
验收：`test_a_delivery_without_smtp_configuration_ends_failed_not_sending`、
`test_a_refused_smtp_endpoint_ends_the_intent_failed`。

**P2-12 每日上限原子准入。**
登记在来源/日建议锁内检查并占用额度，重放不重复计入。
验收：`test_the_daily_limit_admits_exactly_one_concurrent_registration`
（上限 1 时两并发一成一拒）。

**P2-13 SMTP 测试键含配置修订。**
测试键改 `smtp-test:{recipient}:{revision}`，revision 取 `configuration.updated_at`
（无配置时 `unconfigured`）；一次新的测试与新配置各产生新意图。
验收：`test_a_repeat_smtp_test_replays_and_a_new_configuration_starts_a_new_one`。

**P2-14 SDK 运行时 API 前缀。**
生成器输出相对资源路径，`render_request_call` 生成
`f"{self._channel.api_root}{operation.path}"`；`GroupChannel` 增加 `api_root`；
`PlatformClient(api_prefix=...)` + `validated_api_root()`（拒绝无前导斜杠/尾斜杠/仅根），
默认 `/api/v1`。重新生成 `docs/openapi.json`、`docs/api/*` 与 SDK `api/*.py`。
验收：`sdk/python/tests/test_api_root.py` 4 例（默认前缀、自定义前缀、路径参数组合、
非法前缀拒绝）+ 干净环境 wheel 安装后按 `/gateway/api` 前缀实际请求
`/gateway/api/users/me`（见复验表）。

## 复验中新发现的 2 项（均属本轮修复引入或暴露，已修复）

**F1 `record_extension_event` 同事务重放丢失。**
P2-9 重排时误删了链头锁前的 `session.flush()`：会话 `autoflush=False`，同事务第二次
相同 key 调用看不到挂起的回执插入，返回 `replayed=False` 并再次追加，最终撞唯一约束。
服务器干净库全量套件首次暴露（`test_an_extension_append_is_idempotent_per_invocation`
失败）。修复：链头锁后、回执查询前恢复 flush。反向实证：删除该 flush 后测试失败。

**F2 `_chain_head` 首行创建竞态。**
测试库由 `create_all` 建立、无迁移种子时 `audit_chain_head` 不存在，两个“首次追加”
都查不到行并各自 INSERT，败者在主键上收到 `IntegrityError`（生产库有种子不会触发，
但创建路径必须真正串行）。修复：创建前取
`pg_advisory_xact_lock(hashtext("audit-chain-head:1"))`，锁内重查后再建。反向实证：
移除建议锁后并发测试 3/3 失败。

另：三个文件的并发测试统一改为 `clean_concurrency_tables` fixture（前后 TRUNCATE），
不再残留状态污染同套件后续测试；降权竞态改为 3 轮，避免单轮时序侥幸。

## 修复轮验证证据

服务器 192.168.232.129：真实 PostgreSQL 16（容器 `opsmesh-refactor-validation-pg`）、
真实 Temporal 1.29.1（dev server）、真实 Redis；先 DROP/CREATE `opsmesh_s4_test` 并
`alembic upgrade head`。本地：Windows + uv 0.7.13。

| 检查 | 结果 |
| --- | --- |
| 服务器 `alembic upgrade head` + `alembic check` | 0001、0002 应用；无漂移；20 张表 |
| 服务器全量 `pytest tests -q`（干净库） | 206 passed，2 skipped，0 failed；第二次复跑同样 0 failed |
| 服务器 `tests/shared/test_cache_invalidation.py`（真实 Redis） | 9 passed（此前 2 项跳过为 Redis 门控，属 S2 范围） |
| 服务器 e2e（真实 Temporal） | PASS：audit verification `succeeded`（checked 3）；拒绝连接的邮件 workflow 以 `ConnectionRefusedError` 正常完成，意图 failed、`outcome_unknown=False` |
| 反向实证（临时恢复缺陷逻辑后跑对应测试） | 3/3 失败：只按行身份的 guard、缺失的 flush、缺失的建议锁；两个源文件 MD5 复原 |
| 本地 `ruff check src tests scripts sdk/python` | 通过 |
| 本地 `mypy`（strict） | 156 个源码文件通过 |
| 本地 `lint-imports --no-cache` | 19 kept，0 broken |
| 本地 `audit_app_layout.py --architecture-check` | 123 个源码文件，19 个唯一表所有者 |
| 本地 `export_api_docs.py --check`、`generate_sdk_api.py --check` | 6 + 3 个生成文件一致 |
| 本地根 `pytest tests -q`（未配 PG，门控跳过） | 退出码 0 |
| sdk/python 独立 `ruff`/`mypy`/`lint-imports`/`pytest` | 通过；33 个源码文件；5 kept；99 passed |
| SDK wheel 服务器干净环境（新 venv 安装 + 导入 + 请求） | PASS：`api_prefix="/gateway/api"` 实际请求 `/gateway/api/users/me` |

**verified**：上表全部；14 项修复各有对应验收，其中 P1-5/P1-6/P1-7/P2-9/P2-10/P2-12
的并发与恢复语义在真实 PG 上复验，P1-2/P1-3/P1-4/P2-11 的恢复与拒绝路径经真实
Temporal/PG 套件与 e2e 复验，P2-14 经传输级与干净 wheel 环境复验。
**unverified**：真实 SMTP 凭据下的成功发送（e2e 只验证确定性拒绝）；真实 Temporal
崩溃恢复窗口（发送后崩溃）只能用撤回语义的测试近似，未做真实进程杀死复测；
SDK 与真实 API 进程的 HTTP 联调（只验到 ASGI/传输级）。
**blocked**：无。
**implemented**：14 项 + F1、F2 全部实施完毕。S4 的 review 缺口已关闭，可以按
V4 第 16.2 节关闭 S4 验收；上述 unverified 项转入后续阶段或需要凭据时复验。
