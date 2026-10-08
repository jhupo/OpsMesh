# 后端目录迁移：邮件设置与发送

日期：2026-10-01。范围：本文记录邮件纵向切片作为完整后端目录迁移的首批历史批次；后续业务模块已按同一方案完成归拢。

## 基线与分支

- 基线提交：`db407a55`，保存此前的控制台、用户邀请和文档整理等源码改动，共 222 个文件。
- 首批迁移分支：`codex/backend-email-module`，从该基线创建。
- 本地生成的 `output/` 设计稿和 `.tmp-release/` 发行包保留在原处，未加入源码提交。
- 原始声明哈希、迁移前后 OpenAPI/PostgreSQL 结构快照和验证日志在 `.tmp/mail-directory-migration/`；该目录是本地验证产物，不提交。

## 已实施（implemented）

```text
backend/app/messaging/
├── __init__.py
└── email/
    ├── __init__.py
    ├── routes.py       读取/保存配置、测试发送；自带管理员依赖
    ├── schemas.py      邮件配置与收件人 DTO，无 HTTP 框架依赖
    ├── service.py      配置读写、加密与发送协调
    ├── models.py       PlatformMailSettings 的唯一定义
    └── smtp.py         SMTP/STARTTLS/TLS 适配
```

### 文件与声明映射

所有源路径以基线 `db407a55` 为准；路径省略 `backend/app/`。

| 源 | 目标 | 动作与所有者 | 验证 |
| --- | --- | --- | --- |
| `core/mail.py` | `messaging/email/smtp.py` | move；发送通道 | TLS 与收件人拒绝流程 |
| `domains/platform/mail.py` 的配置 DTO | `messaging/email/schemas.py` | split；邮件公开配置契约 | 完整 OpenAPI 比较 |
| `domains/platform/mail.py` 的 PlatformMailService | `messaging/email/service.py` | split；配置与投递用例 | 加密、脱敏、发送、邀请重试 |
| `domains/platform/admin/models.py` 的 PlatformMailSettings | `messaging/email/models.py` | split；邮件持久配置，平台策略模型留原处 | 127 张表及索引比较、显式注册检查 |
| `api/routes/platform/mail.py` 的邮件接口 | `messaging/email/routes.py` | split；邮件 HTTP 适配 | 管理员允许、匿名/普通用户拒绝、独立挂载拒绝 |
| `api/routes/platform/mail.py` 的创建/重发邀请接口 | `api/routes/platform/invitations.py` | split；邀请业务仍属旧 access 模块 | 原接口、幂等、失败重发和激活流程 |
| `api/schemas/platform/mail.py` 的 EmailRecipient | `messaging/email/schemas.py` | split；复用纯 DTO，避免重复收件人定义 | OpenAPI 比较 |
| `api/schemas/platform/mail.py` 的邀请 DTO | `api/schemas/platform/invitations.py` | split；邀请 API 合同 | 令牌校验、脱敏和激活流程 |

四个原邮件文件已移除，无旧路径转发层。未删除功能或合并业务实体。当前项目调用者已切换至新路径；这是仓库内部 Python 路径调整，外部若存在未纳入仓库的直接 Python 导入，应迁移到新模块。HTTP 客户端不受路径变化影响。

### 装配与直接调用者

- `api/routes/platform/router.py` 分别显式注册邮件和邀请 router，保留 `/admin` 前缀及 tags。
- 邮件 router 自带 `require_platform_admin`，不依赖父 router 才获得权限保护。既有拒绝状态码保持 401。
- `domains/access/invitations.py` 调用新邮件服务，保留原邀请业务和事务时机。
- `api/routes/access/invitations.py` 使用重命名后的邀请 DTO。
- `bootstrap/models.py` 显式注册邮件模型；API、Worker、Alembic 和测试继续使用同一注册入口。
- 更新原邮件测试的模型导入与 monkeypatch 路径；数据库导入隔离检查覆盖新增 messaging 业务包。

### 数据与事务

- 不新增 Alembic revision，不修改历史 `0108_mail_user_invitations`，不改变表名、字段、约束、索引或 JSON 字段。
- `service.save()` 仍只执行 flush，路由仍在写请求审计后提交。
- 邀请仍先持久化 pending，再调用 SMTP，再写回 sent/failed；未引入新的 Session、队列或 outbox。
- `public_base_url` 与 `invitation_expiry_hours` 保留原 API 和配置存储位置；链接与有效期语义由邀请服务负责，SMTP 不处理邀请规则。
- 密码、密文、令牌及审计 action 的行为保持原样。

### 可执行依赖约束

在 `pyproject.toml` 增加三个 import-linter 合同：

1. `email-http-composition`：邮件路由仅由平台 API 组装。
2. `email-service-boundary`：服务、DTO、模型和 SMTP 不依赖 API、bootstrap、其他业务域、Runtime 或 HTTP 框架。
3. `email-model-owner`：邮件持久模型仅供本服务和显式注册使用，其他生产模块不能直接操作它。

现有 import-linter 图不包含 `backend/tests`；管理员边界、状态和脱敏由 pytest 进行真实应用请求验证。未放宽原有八个生产约束。

## 已验证（verified）

验证使用工作区既有 `.venv`（Python 3.13.7），未更新依赖锁文件或连接生产数据库。

| 检查 | 结果 |
| --- | --- |
| 迁移前邮件、密码重置、健康基线 | 18 passed |
| 迁移后邮件、密码重置、健康 | 19 passed，新增管理员边界场景 |
| 管理员 API、模型注册、发行入口测试 | 23 passed |
| 完整 OpenAPI 比较 | 454 个路径，整体对象完全相同，包含 operationId 与 schemas |
| PostgreSQL metadata 比较 | 127 张表的 DDL 与索引定义完全相同 |
| Ruff 全仓检查 | 0 errors |
| mypy 全应用检查 | 992 source files，0 issues |
| import-linter | 11 kept，0 broken |
| `python -m backend.app.delivery --directory . check` | 通过 API、Worker、updater 导入和唯一 Alembic head 检查 |

涉及的 42 项回归覆盖管理员允许/拒绝、密码重置、SMTP 凭据脱敏、邀请幂等、失败重发、旧/过期/已使用 token 拒绝、停用账号拒绝、激活后登录、模型注册幂等和入口行为。SMTP 使用测试适配器；数据库流程使用隔离 SQLite 与 fakeredis，结构比较使用 PostgreSQL 方言生成 DDL，不等同于真实 PostgreSQL 迁移演练。

## 未验证及后续（unverified）

- 未部署、未向远端推送、未创建远端 PR，也未发送真实邮件。
- 未重新构建独立发行包或做生产 PostgreSQL 升降级演练。本批没有 schema 变更。
- 未修改前端，无本批新增 UI 截图验收；此前基线里的 UI 改动不作为本次重新验证成果。
- 后续完整迁移批次已将 identity、workspaces、agents、teams、capabilities、resources、orchestration、runtime、platform、governance、shared 与 bootstrap 归拢；本报告保留首批邮件的细节证据。

当前无阻塞项（blocked：无）。完整迁移的最终审计、快照和测试结果记录在 `.tmp/backend-directory-migration/`；未执行真实生产部署或远端推送。
