# 安装、升级与恢复

本项目交付 API/Worker 镜像、Runtime 镜像、独立服务器包与原生 `opsmesh` 管理 CLI，不部署 Web Portal。完整分发边界见 [DISTRIBUTIONS](../DISTRIBUTIONS.md)，环境变量与服务配置见 [DEPLOYMENT](DEPLOYMENT.md)。

## 首次安装

Linux amd64 主机需准备 Docker、systemd 和 PostgreSQL 客户端工具。Compose 模式使用发布资产中声明的 PostgreSQL/Redis 服务；systemd 模式要求提前配置这两个依赖。使用已验证的发布版本，安装根目录默认 `/opt/opsmesh`：

```bash
sudo sh deploy/install.sh --version <release-tag> --origin https://api.example.com
```

安装器按固定仓库和版本下载发布资产，核对 manifest、平台/协议字段、包 SHA-256 和不可变镜像摘要；不在安装时从源码构建或临时解析 Python 依赖。保管一次性交付的初始管理员密码。配置、数据和加密密钥位于可变安装目录，发布目录保持不可变。

运行代码的包名为 `opsmesh`；数据库迁移在服务器包的 `migrations/`。原生启动器 `opsmesh-server` 提供 version、check、api、worker、gateway、migrate、bootstrap-admin 和 reset-admin-password 命令，直接运行冻结入口和所需运行库，不依赖系统 Python。管理员密码命令只适用于授权的主机管理环境。

## 管理命令

使用初始管理员账号通过 `/api/v1/auth/login` 登录，将返回的 `session_token` 设置为 `OPSMESH_SESSION_ID`；API 管理请求通过 `X-Session-ID` 携带该 Session，远程 `--api-url` 必须 HTTPS。主机操作还需要对应的安装根目录与 Linux 权限。首次管理员默认为 `admin@example.com`，主机重置密码同时撤销已有登录 Session。

```bash
opsmesh --root /opt/opsmesh doctor
opsmesh --api-url https://api.example.com status
opsmesh --root /opt/opsmesh logs --service worker
opsmesh --root /opt/opsmesh backup verify <backup-uuid> --restore-check
opsmesh --root /opt/opsmesh backup create
opsmesh --root /opt/opsmesh update --check
opsmesh --root /opt/opsmesh update --version <release-tag>
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

快照格式为 3，包含业务 PG、Temporal persistence/visibility、文件存储、系统插件代码/注册配置和配置/密钥，分别校验摘要并在临时数据库演练恢复。维护前完成或核对任务、关闭 MCP 和沙箱、停止扩展常驻进程。创建快照先停止 API/Worker，再从 PG 确认没有未完成执行或外部进程；不满足时拒绝快照。检查通过后短暂停止专用 Temporal Server；恢复整体恢复上述全部内容，失败时不启动应用。备份目录只允许主机管理员读取。

恢复前核对当前安装与快照的业务数据库、Temporal 和 Redis 连接配置。连接地址、命名空间或相关数据库密码发生漂移时，命令在数据库 I/O 前拒绝；管理员核对实际服务及快照配置后再恢复，不能仅把快照导入当前连接后让恢复的配置指向另一套服务。

Compose 的 Temporal 属于同一个受管理 project。systemd 安装需要通过 `--temporal-service-unit <name.service>` 声明专用 Temporal 服务，并在安装环境/持久 `.env` 配置 `OPSMESH_TEMPORAL_DATABASE_URL` 和 `OPSMESH_TEMPORAL_VISIBILITY_DATABASE_URL`。它们必须指向该服务独占的两个 PostgreSQL 数据库，不能与业务库相同。缺少协调条件时拒绝快照/恢复；共享 Temporal 或 Temporal Cloud 需另行配置受支持的成套恢复方案。

`operations/<uuid>.json` 可在 API 停止时读取，保留最后完成阶段和备份引用。发行切换失败保留停机现场；管理员依据日志核对后继续恢复。备份创建失败会重新启动原版本，便于完成任务或关闭环境后重试。快照恢复不会撤销已发生的外部写入；恢复前须确认当前外部执行环境也已关闭。

## 在线更新与系统扩展

`update --check` 只查询 GitHub 最新已发布稳定版；`update --version TAG` 可明确指定 RC。
`update` 准备并校验服务器与 operator 二进制包，受控停机并备份，迁移和健康检查通过后
切换不可变版本目录。不会通过 pip 更新运行中的环境；有未决任务或开放 MCP/沙箱时应先关闭。
回滚使用同一操作 UUID 恢复数据、插件和先前 operator；已有外部效果不会自动撤销。

系统扩展开发及安装见 [SDK 文档](../../sdk/python/README.md)。插件代码位于
`plugins/<id>/<version>`，持久数据位于 `data/storage/plugins/<id>`。
默认 CLI 使用 `OPSMESH_SESSION_ID` 调用管理员在线接口，安装、启停和卸载由
API/Worker 自动重载；通过 `opsmesh plugin list` 的 `applied/hosts` 确认生效。
注册配置经在线接口加密保存，只允许管理员和服务组读取。
Compose 中 API 对插件根目录可写，Worker 只读；数据目录供双方写入加载确认。
升级旧安装时，先确保插件根目录归 `10001:10001`、权限 `2750`，已有插件 ID 目录
允许 UID 10001 创建新版本；插件数据根和各插件数据目录归服务组 `10001` 且组可写。
systemd Worker 使用 UID 10002、GID 10001，须能读取 `registry.json`（权限 `0640`）。
跨机器部署必须共享包和数据目录；这里不负责远程分发插件或安装 Python 依赖。
`opsmesh plugin --offline ...` 用于离线准备，不提供进程已生效的保证。
扩展具备进程权限，管理员应核对来源和摘要。框架不将用户上传包作为系统插件自动执行。
