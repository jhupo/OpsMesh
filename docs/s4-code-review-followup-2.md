# S4 第二次修复复审（2026-10-16）

审查提交：`ebb23c04`，对比基线：`814fa794`。范围为上一轮 R1–R3 的修复及受影响
调用方、迁移、API/schema/SDK 和测试。没有修改产品代码。

结论：上一轮三个具体触发条件已被修正；本次改动另引入 1 项 P1、1 项 P2，尚不能
据 `s4-code-review-followup.md` 末尾的关闭声明完成验收。

> 修复与复验：文末《修复记录（2026-10-16 晚，第二次接续）》已关闭 F1/F2；S4
> 验收结论以该节为准。

## F1 [P1] 新管理端入口跳过状态校验，可绕过最后管理员保护

位置：`src/opsmesh/identity/users/service.py:296`，调用入口为 `apply_admin_update`。

路由从 `set_status` 改为 `apply_admin_update` 后，直接调用 `_apply_status`。
`status not in USER_STATUSES` 校验仍只在旧的公开函数 `set_status` 中，新入口没有
执行该校验；HTTP schema 也仅限制字符串长度，数据库没有状态枚举 CHECK。

因此 `PATCH /api/v1/users/{id}` 传 `{"status":"activ"}` 会返回 200，并提交
非法状态。`_apply_status` 仅在 status 等于 `disabled` 时调用最后管理员 guard，
而认证的 `ensure_active_user` 会拒绝所有不等于 `active` 的状态。如果目标是唯一
管理员，该请求绕过保护，使平台没有可用管理员；下一次请求已无法通过认证。

本轮通过真实 FastAPI 路由、Pydantic schema 和 service 复现，数据库与认证依赖以
诊断替身隔离（认证替身调用真实 `identity_of`/`ensure_active_user`）：

```text
sole-admin status=disabled -> HTTP 409
sole-admin status=activ -> HTTP 200 + commit 被调用
下一次 GET /api/v1/users/me -> HTTP 403
```

应将有效状态校验放进两个入口共同经过的逻辑，在任何字段变更前拒绝非法值，并补
管理端 HTTP 回归测试：未知/空状态拒绝、用户状态未变、唯一管理员仍能访问。
只测试 `set_status` 的拒绝路径无法覆盖本次回归。

## F2 [P2] SMTP 测试操作键的公开长度与服务层不一致

位置：`src/opsmesh/platform/mail/routes.py:162`，
`src/opsmesh/platform/mail/schemas.py:60`。

`TestMailRequest.idempotency_key` 和导出的 OpenAPI 允许 1–200 字符；路由再添加
10 字符的 `smtp-test:` 前缀，而 `register_mail` 对最终键仍限制为 200。
因此公开合同允许的 191–200 字符操作键全部被拒绝，不能创建 SMTP 测试。

本轮发送包含 200 字符键的真实 HTTP 请求：schema 校验成功，路由进入
`register_mail` 后返回 HTTP 400 / `invalid_argument`，最终键长为 210。
应统一客户端操作键与持久化键的长度约束：例如移除已由 source scope 区分的多余
前缀，或调整公开最大长度并同步生成合同；补最大合法长度及越界测试。

## 上一轮三项修复复核

- R1：`source_revision` 经登记、持久化、领取和 `SourceScope` 传递，准入比较邀请
  `send_count`；0003 新增对应列。定向调用中旧修订被拒且没有 SMTP 调用，当前修订
  通过并发送（SMTP 为替身）。
- R2：管理端改为一次 `apply_admin_update`，先管理员集合/目标行、后审计；资料与
  头像自服务路径也先锁目标行。定向调用确认组合 PATCH 与单角色 PATCH 均先取行锁。
  新增 PG 测试包含确定性交错；本轮因数据库未配置而跳过，没有独立重现远端结果。
- R3：同键重试重放，新键在旧意图 failed 后建立新的 pending 意图；schema/OpenAPI/
  SDK 已同步。F2 是新增长度边界回归，不是重复报告原来的固定测试键问题。

## 本轮验证证据与限制

| 检查 | 本轮结果 |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest tests/identity tests/governance tests/platform tests/bootstrap sdk/python/tests -o addopts='' -q` | 退出 0；179 passed，60 skipped |
| `ruff check`：上述模块和 SDK 源码/测试、0003 迁移 | 退出 0 |
| `.venv/Scripts/python.exe -m mypy` | 退出 0；156 个源码文件 |
| `.venv/Scripts/lint-imports.exe --no-cache` | 退出 0；19 kept，0 broken |
| `scripts/audit_app_layout.py --architecture-check` | 退出 0；123 个源码文件、19 个唯一表所有者 |
| `scripts/export_api_docs.py --check`、`scripts/generate_sdk_api.py --check` | 均退出 0；6 + 3 个生成文件一致 |
| 临时定向 HTTP/service 诊断 | 退出 0；复现 F1/F2，验证上述 R1–R3 控制流 |

诊断脚本：`C:/Users/Administrator/AppData/Local/Temp/opsmesh_s4_second_followup_probes.py`。
其持久化和 SMTP 使用替身；不使用备份或 SQLite，不代表真实 PG/Temporal/SMTP 验收。

**verified**：以上本地命令、HTTP 输入/响应与业务控制流证据。
**unverified**：本环境未配置 `OPSMESH_TEST_DATABASE_URL`，60 项数据库测试跳过；
本轮未运行真实 PG 并发、迁移应用、Temporal 崩溃恢复、真实 SMTP 成功发送、实际
API/SDK 进程联调。修复记录中的历史远端成功不作为本轮独立验证。
**implemented**：本轮只新增此评审记录，F1/F2 尚未修复。

---

# 修复记录（2026-10-16 晚，第二次接续）

修复分支：`refactor/s4-identity-governance-mail`（接续 `ebb23c04`，未推送）。
范围：F1/F2 全部修复并补验收。无迁移变更（0003 已覆盖所需列）；合同变更仅
`test_smtp` 路由描述随 docstring 更新，OpenAPI、docs/api 与 SDK 已重新生成并
核对一致。

## 逐项修复与验收

**F1 管理端入口恢复状态校验与最后管理员保护。**
新增 `_validated_status()`：`set_status` 与 `apply_admin_update` 两个入口都在
任何加锁或字段变更之前经它拒绝未定义状态（HTTP schema 只限长度，数据库无枚举
CHECK）。`_apply_status` 的最后管理员 guard 从“仅 `disabled`”改为“任何非
active 状态”，与认证侧 `ensure_active_user` 的判定一致。
验收（均为管理端路由级回归）：
`test_an_unknown_status_is_refused_by_the_admin_update`（`activ`、空串均
`InvalidArgumentError`，资料不变）、
`test_the_last_administrator_is_protected_through_the_admin_update`
（唯一管理员禁用被拒后仍 active 且可认证）、
`test_a_second_administrator_can_be_disabled_through_the_admin_update`
（存在其他管理员时同一入口可正常禁用）。

**F2 SMTP 测试键长度约束统一。**
`test_smtp` 不再给操作键加 `smtp-test:` 前缀：`register_mail` 的
`idempotency_scope` 已是 `mail-source:platform.smtp_test`，键在来源作用域内
已命名空间化，前缀冗余且使公开 1–200 的键在持久化侧变成 11–210。现在公开合同
（`TestMailRequest.idempotency_key`，1–200）与持久化键（列宽 200）逐字一致。
验收：`test_a_maximum_length_test_key_is_admitted_and_a_longer_one_refused`
（200 字符键登记并原样持久、201 字符被请求合同拒绝）。

## 修复轮验证证据

服务器 192.168.232.129：真实 PostgreSQL 16、真实 Temporal 1.29.1、真实 Redis；
先 DROP/CREATE `opsmesh_s4_test` 并 `alembic upgrade head`。本地：Windows +
uv 0.7.13。

| 检查 | 结果 |
| --- | --- |
| 服务器 `alembic upgrade head` + `alembic check` | 0003 应用；无漂移 |
| 服务器全量 `pytest tests -q`（干净库） | 0 failed（SUITE_EXIT=0） |
| 服务器 e2e（真实 Temporal） | PASS：audit verification succeeded；拒绝连接的邮件意图 failed 且 `outcome_unknown=False` |
| 反向实证 F1（管理端入口去掉状态校验） | `test_an_unknown_status_is_refused_by_the_admin_update` FAILED |
| 反向实证 F2（路由重新加键前缀） | `test_a_maximum_length_test_key_is_admitted_and_a_longer_one_refused` FAILED |
| 两个反向实证的源文件 | MD5 全部复原 |
| 本地 `ruff check`（src/tests/scripts/sdk/migrations） | 通过 |
| 本地 `mypy`（strict） | 156 个源码文件通过 |
| 本地 `lint-imports --no-cache` | 19 kept，0 broken |
| 本地 `audit_app_layout.py --architecture-check` | 123 个源码文件，19 个唯一表所有者 |
| 本地 `export_api_docs.py --check`、`generate_sdk_api.py --check` | 6 + 3 个生成文件一致 |
| 本地根 `pytest tests -q`（未配 PG，门控跳过） | 退出码 0 |
| sdk/python 独立 `ruff`/`mypy`/`lint-imports`/`pytest` | 通过；33 个源码文件；5 kept；99 passed |

**verified**：上表全部；F1 的拒绝与保护语义、F2 的长度边界均在真实 PostgreSQL
上经路由级测试复验，反向实证确认两项测试对对应缺陷具有区分力。
**unverified**：真实 SMTP 凭据下的成功发送；真实 Temporal 进程杀死后的崩溃恢复
窗口；SDK 与真实 API 进程的 HTTP 联调（只验到 ASGI/传输级）；本轮未重跑
R2 的确定性死锁构造（上一轮已 3/3 复现修复前失败，源码未再改动该路径）。
**blocked**：无。
**implemented**：F1/F2 全部实施完毕，第二次复审的 2 项问题已关闭，S4 可以按
V4 第 16.2 节关闭验收。
