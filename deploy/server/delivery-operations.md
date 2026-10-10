# 安装、升级与恢复

本项目交付 API/Worker 镜像、Runtime 镜像、独立服务器包与原生 `opsmesh` 管理 CLI，不部署 Web Portal。完整分发边界见 [DISTRIBUTIONS](../DISTRIBUTIONS.md)，环境变量与服务配置见 [DEPLOYMENT](DEPLOYMENT.md)。

## 首次安装

Linux amd64 主机需准备 Docker、systemd 和 PostgreSQL 客户端工具。Compose 模式使用发布资产中声明的 PostgreSQL/Redis 服务；systemd 模式要求提前配置这两个依赖。使用已验证的发布版本，安装根目录默认 `/opt/opsmesh`：

```bash
sudo sh deploy/install.sh --version <release-tag> --origin https://api.example.com
```

安装器按固定仓库和版本下载发布资产，核对 manifest、平台/协议字段、包 SHA-256 和不可变镜像摘要；不在安装时从源码构建或临时解析 Python 依赖。保管一次性交付的初始管理员密码。配置、数据和加密密钥位于可变安装目录，发布目录保持不可变。

运行代码的包名为 `opsmesh`；数据库迁移在服务器包的 `migrations/`。原生启动器 `opsmesh-server` 提供 version、check、api、worker、migrate、bootstrap-admin、reset-admin-password 和 updater 命令，使用随包解释器。管理员密码命令只适用于授权的主机管理环境。

## 管理命令

API 管理请求使用环境中的 `OPSMESH_PLATFORM_ADMIN_TOKEN`，远程 `--api-url` 必须 HTTPS。主机操作还需要对应的安装根目录与 Linux 权限。

```bash
opsmesh --root /opt/opsmesh doctor
opsmesh --api-url https://api.example.com status
opsmesh --root /opt/opsmesh logs --service worker
opsmesh --root /opt/opsmesh backup create
opsmesh --root /opt/opsmesh backup verify <backup-uuid> --restore-check
```

`status`、`update check/plan/apply/status/cancel` 和 `backup create` 访问管理员 API；备份校验和恢复操作使用安装记录及主机工具。不要在命令参数中放 Token，备份文件和密钥只交给授权管理员。

## 升级

```bash
opsmesh --api-url https://api.example.com update check
opsmesh --api-url https://api.example.com update plan --version <release-tag> --idempotency-key <request-key>
opsmesh --api-url https://api.example.com update status <plan-uuid>
opsmesh --api-url https://api.example.com update apply --plan <plan-uuid> --fingerprint <verified-fingerprint>
```

计划和审批是持久记录。独立 updater 验证 manifest 与指纹，进入维护，排空工作，停应用服务，创建并恢复验证备份，运行迁移，切换发布，检查就绪，再恢复准入。Runtime 镜像与正在运行的宿主需同步更新；活动槽必须先释放，不能把 API 镜像升级当作全部执行代码已更新。

发布合同版本为 2，分别保存 `api_image`、`worker_image` 和 `runtime_image` 的完整不可变引用。安装目录用版本和完整 commit 共同标识，避免同版本的不同构建相互覆盖。本机构建记录可以使用 Docker 的 `sha256` 镜像 ID；公开发布必须使用各服务自己的 GHCR 仓库及 OCI digest，并通过发布验证。

从版本 1 的安装记录迁入时，先停止 updater、备份安装根目录，并使用 `scripts/migrate_release_manifest.py <old-record> <new-record>` 显式转换保留版本。该脚本保留源记录；运维核对新记录后，将对应包放入 `<tag>-<commit>` 目录并切换 `current`。当前执行代码只接受版本 2，不会自动读取旧合同或静默转换。安装记录必须对应实际运行的构建与镜像；本地部署不能伪装成已发布的 OCI 资产。

## 失败恢复

不确定副作用或恢复状态时保持维护，先检查 update 事件、备份验证与数据库事实。恢复是显式主机操作：

```bash
sudo opsmesh --root /opt/opsmesh update recover --plan <plan-uuid> --strategy resume
```

可选策略还有 rollback 和 restore；数据库 restore 的数据丢失风险需要显式 `--ack-data-loss`。镜像回退不等于数据库恢复，不能自动重放状态未知的 MCP 工具或 Run。恢复前保留数据库、文件存储、加密密钥和相关审计记录；操作后检查 schema head、API/Worker 就绪及 Runtime 合同。
