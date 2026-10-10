# 用户邀请与邮件设置

平台管理员通过 `PUT /admin/system/mail` 保存 SMTP 服务器、端口、连接加密方式、认证信息、发件人、站点访问地址和邀请有效期。邮件设置保存在 PostgreSQL，SMTP 密码使用现有 `OPSMESH_CREDENTIAL_ENCRYPTION_SECRET` 加密，读取接口只返回 `password_configured`。部署时须保留加密密钥及其轮换配置。

先应用数据库迁移并重启 API：

```bash
uv run alembic upgrade head
```

新增迁移为 `0108_mail_user_invitations`，提供升级和降级。降级会删除邮件配置和邀请记录，已有用户不被删除。

## 管理流程

1. 使用邮件设置 API 保存并启用 SMTP 配置。端口默认 587 / STARTTLS，也支持 465 / TLS；站点地址指向独立客户端项目的邀请接受页面，本仓库不提供该页面。
2. 使用 `POST /admin/system/mail/test` 验证已保存配置，测试发送有明确的外部邮件副作用。
3. 使用 `POST /admin/user-invitations` 提供邮箱、可选姓名和管理员权限，创建邀请；不要求管理员代设密码。
4. 账号以 invited 状态创建；失败保留邀请记录及投递状态，使用 resend 接口显式重试。重复创建同一待激活邮箱不会重复发信。
5. 独立客户端处理邮件链接 `/accept-invitation#token=...`，调用 `POST /auth/invitations/accept` 设置账号信息并激活，再通过登录接口取得 Token。

邀请令牌只在邮件中提供，数据库只存 SHA-256 摘要，不在 API 响应、审计或日志中返回。客户端读取 URL fragment 后应立即清除 URL 中的令牌，不写入本地或会话存储。链接仅使用一次，有效期默认 72 小时，可配置为 1–168 小时。重新发送会生成新令牌并使旧链接失效。

发送请求在持久保存邀请意图之后执行有超时的 SMTP 调用，记录 `pending` / `sent` / `failed` 状态。失败需管理员显式重试，不做无界自动重发。已发送邀请 30 秒内禁止重复发送；处理中记录至少等待一分钟后才允许恢复重试。账号停用期间不能接受邀请；重新启用未接受邀请的账号恢复为待激活，不跳过邮箱验证。

## API

以下路径省略可配置的 `/api/v1` 前缀。除接受邀请外，全部要求平台管理员权限。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /admin/system/mail` | 读取邮件配置，密码不回传 |
| `PUT /admin/system/mail` | 保存邮件配置；省略或留空密码保持原值，`clear_password` 显式清除 |
| `POST /admin/system/mail/test` | 向 `email` 发送测试邮件 |
| `POST /admin/user-invitations` | 创建并发送邀请，字段 `email`、`display_name`、`platform_admin` |
| `POST /admin/users/{user_id}/invitation/resend` | 重新发送待激活用户的邀请 |
| `POST /auth/invitations/accept` | 使用 `token`、`password`、`display_name`、`username` 激活账号 |

邀请响应包含 ID、用户 ID、投递状态、到期时间和发送时间。`delivery_status=failed` 表示记录已保存但邮件投递失败，不应展示发送成功。用户列表增加 `invitation_delivery_status` 字段，支持 `status=invited` 筛选。

## 验证范围

`tests/test_platform_mail_invitations.py` 覆盖管理员访问边界、SMTP 密码加密与脱敏、重复邀请幂等、失败重试、令牌失效与一次性消费、停用账号拒绝接受邀请、激活后登录。测试使用隔离数据库与捕获邮件的适配器，不向真实邮箱发送邮件。生产 SMTP 连通性与收件箱投递需要管理员使用实际配置验证。

## 当前代码归属

- 邮件接口、DTO、配置模型、服务和 SMTP：`src/opsmesh/messaging/email/`。
- 管理员创建/重发邀请：`src/opsmesh/identity/invitations/admin_routes.py`；接受邀请：`src/opsmesh/identity/invitations/routes.py`。
- 邀请 DTO：`src/opsmesh/identity/invitations/schemas.py`；状态、有效期、激活与发送协调：`src/opsmesh/identity/invitations/service.py`。
- 邮件模型由 `bootstrap/models.py` 显式注册；只有一个 `PlatformMailSettings` 定义。

API 和迁移以当前接口参考及 migrations 为准。邀请有效期和站点地址仍使用现有配置字段；规则由邀请服务解释，SMTP 仅负责发送。邮件路由即使不经过平台父路由注册，也必须验证管理员身份。
