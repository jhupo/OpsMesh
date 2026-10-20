# 安装、升级与恢复

本项目交付 API/Worker 镜像、Runtime 镜像、独立服务器包与原生 `opsmesh` 管理 CLI，不部署 Web Portal。完整分发边界见 [DISTRIBUTIONS](../DISTRIBUTIONS.md)，环境变量与服务配置见 [DEPLOYMENT](DEPLOYMENT.md)。

## 首次安装

Linux amd64 主机需准备 Docker、systemd 和 PostgreSQL 客户端工具。Compose 模式使用发布资产中声明的 PostgreSQL/Redis 服务；systemd 模式要求提前配置这两个依赖。使用已验证的发布版本，安装根目录默认 `/opt/opsmesh`：

```bash
sudo sh deploy/install.sh --version <release-tag> --origin https://api.example.com
```

安装器按固定仓库和版本下载发布资产，核对 manifest、平台/协议字段、包 SHA-256 和不可变镜像摘要；不在安装时从源码构建或临时解析 Python 依赖。保管一次性交付的初始管理员密码。配置、数据和加密密钥位于可变安装目录，发布目录保持不可变。

运行代码的包名为 `opsmesh`；数据库迁移在服务器包的 `migrations/`。原生启动器 `opsmesh-server` 提供 version、check、api、worker、migrate、bootstrap-admin 和 reset-admin-password 命令，使用随包解释器。管理员密码命令只适用于授权的主机管理环境。

## 管理命令

使用初始管理员账号通过 `/api/v1/auth/login` 登录，将返回的 `session_token` 设置为 `OPSMESH_SESSION_ID`；API 管理请求通过 `X-Session-ID` 携带该 Session，远程 `--api-url` 必须 HTTPS。主机操作还需要对应的安装根目录与 Linux 权限。首次管理员默认为 `admin@example.com`，主机重置密码同时撤销已有登录 Session。

```bash
opsmesh --root /opt/opsmesh doctor
opsmesh --api-url https://api.example.com status
opsmesh --root /opt/opsmesh logs --service worker
opsmesh --root /opt/opsmesh backup verify <backup-uuid> --restore-check
opsmesh --root /opt/opsmesh backup create
opsmesh --root /opt/opsmesh deploy --version <release-tag>
opsmesh --root /opt/opsmesh status --operation-id <operation-uuid>
opsmesh --root /opt/opsmesh rollback <operation-uuid> --acknowledge-data-loss
opsmesh --root /opt/opsmesh backup restore <backup-uuid> --acknowledge-data-loss
```

`status` 使用 Session；备份校验使用安装记录及主机工具。发行安装由管理员显式运行，不依赖应用内更新 API 或常驻服务。不要在命令参数中放 Session 凭据，备份文件和密钥只交给授权管理员。

## 发行切换

由主机管理员显式运行安装/发行命令。切换前核对已签名发行清单、备份、Worker 停止状态和数据库迁移影响；切换后检查 API/Worker 就绪、schema head 与 Runtime 镜像。不得依赖应用 API 在线，也不由后台进程自动切换。

发布合同版本为 3，分别保存 `api_image`、`worker_image` 和 `runtime_image` 的完整不可变引用，以及数据库目标修订、可升级来源和可运行的回退修订。安装目录用版本和完整 commit 共同标识，避免同版本的不同构建相互覆盖。本机构建记录可以使用 Docker 的 `sha256` 镜像 ID；公开发布必须使用各服务自己的 GHCR 仓库及 OCI digest，并通过发布验证。

安装记录必须对应实际运行的构建与镜像；本地部署不能伪装成已发布的 OCI 资产。

## 失败恢复

发行切换失败时停止后续部署步骤，由授权主机管理员依据本地安装记录和已验证备份显式恢复。镜像回退不等于数据库恢复；不能自动重放状态未知的 MCP 工具或 Run。恢复前保留数据库、文件存储、加密密钥和相关审计记录；操作后检查 schema head、API/Worker 就绪及 Runtime 合同。

快照格式为 2，包含业务 PG、Temporal persistence/visibility、文件存储和配置/密钥，分别校验摘要并在临时数据库演练恢复。创建快照先排空、停应用，再短暂停止专用 Temporal Server；恢复整体恢复这五份内容，失败时不启动应用。备份目录只允许主机管理员读取。

恢复前核对当前安装与快照的业务数据库、Temporal 和 Redis 连接配置。连接地址、命名空间或相关数据库密码发生漂移时，命令在数据库 I/O 前拒绝；管理员核对实际服务及快照配置后再恢复，不能仅把快照导入当前连接后让恢复的配置指向另一套服务。

Compose 的 Temporal 属于同一个受管理 project。systemd 安装需要通过 `--temporal-service-unit <name.service>` 声明专用 Temporal 服务，并在安装环境/持久 `.env` 配置 `OPSMESH_TEMPORAL_DATABASE_URL` 和 `OPSMESH_TEMPORAL_VISIBILITY_DATABASE_URL`。它们必须指向该服务独占的两个 PostgreSQL 数据库，不能与业务库相同。缺少协调条件时拒绝快照/恢复；共享 Temporal 或 Temporal Cloud 需另行配置受支持的成套恢复方案。

`operations/<uuid>.json` 可在 API 停止时读取，保留最后完成阶段和备份引用。恢复保留快照中的排空状态；核对快照之后的外部效果后，通过管理员 `POST /platform/operations` 提交 `owner=platform`、`command=admission.resume` 和唯一幂等键，再恢复正常工作。快照恢复不会撤销已发生的外部写入。
