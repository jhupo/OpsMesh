# Linux 部署

安装器只部署框架服务。`opsmesh` 可执行包自带 Python 运行环境和框架依赖，不携带官方插件、Agents SDK、数据库驱动或 Langfuse。官方插件与额外依赖由独立插件运行包提供；两者在同一个 Release 发布。

## 系统要求与产物

支持 glibc Linux x86_64 和 ARM64，默认 systemd。官方构建基线是 Ubuntu 22.04；以产物 `GLIBC_MIN` 为准。Alpine/musl、Windows 和跨架构加载二进制依赖不受支持。服务器不需要安装 Python、pip、Git 或编译器。

| 附件 | 用途 |
|---|---|
| `opsmesh-<version>-linux-<arch>.tar.gz` | 框架可执行程序与运行环境 |
| `opsmesh-plugins-<version>-linux-<arch>.tar.gz` | 可选官方插件、资源和额外依赖 |
| `*.whl`、各包的 `*.tar.gz` 源码包 | 普通 Python 环境开发和包安装 |
| `SHA256SUMS` | Release 附件校验和 |
| `install.sh` | 框架的 systemd 安装、升级和回退 |

普通 wheel 不等于冻结可执行程序的插件运行包。可执行宿主应搭配同次 Release、同架构的插件运行包；不要把其他 Python 版本的 site-packages 直接复制过来。插件运行包包含 CPython ABI 相关依赖，其适用范围见包内构建信息。

校验和用于完整性核对。GitHub Release 工作流也生成构建来源证明，验证时使用实际仓库身份；不要仅凭文件名相信第三方重打包。

## 安装空宿主

从同一个 Release 下载框架包、`SHA256SUMS` 和安装器后：

```bash
sudo bash install.sh \
  --archive opsmesh-0.5.1-linux-x86_64.tar.gz \
  --checksums SHA256SUMS
```

首次安装默认生成空插件清单和空环境文件。可以用 `--manifest FILE`、`--env-file FILE` 显式提供配置；升级未提供这些参数时保留已有配置。

安装器检查操作系统、架构、glibc、systemd 和校验和，安装专用服务账户，验证宿主配置并启动服务。它不下载或解压插件，不检查 PostgreSQL，不执行插件迁移，也没有 `--migrate` 参数。

空宿主仅保持框架服务运行，不提供 `/chat`、API 认证、机器人或数据库功能。通道健康检查也只有安装并启用 channels 后才存在。默认只监听回环地址，外部访问应由管理员配置 TLS 反向代理。

```text
/opt/opsmesh/
  releases/<release>/       # 管理员拥有的框架程序
  current -> releases/...
  previous -> releases/...
/etc/opsmesh/
  host.json                # 宿主清单
  opsmesh.env              # 可选的插件环境变量，按需创建
/var/lib/opsmesh/
  plugins/                 # 外部插件部署位置
/etc/systemd/system/opsmesh.service
```

## 按需部署插件

基础插件运行包的 `site/` 包含 channels、execution、filesystem、database、cron 及其额外依赖。knowledge 使用单独构建的完整插件运行包和预先准备的模型目录，见[发布说明](releases.md)。管理员验证校验和后，解压到独立的版本目录，例如：

```bash
sudo install -d -m 0755 /var/lib/opsmesh/plugins/official-0.5.1
sudo tar -xzf opsmesh-plugins-0.5.1-linux-x86_64.tar.gz \
  --strip-components=1 -C /var/lib/opsmesh/plugins/official-0.5.1
```

host.json 只保存宿主基础配置：

```json
{
  "environment": "production",
  "data_dir": "/var/lib/opsmesh",
  "framework": {"command_start": ["/"]}
}
```

停止服务后，用 CLI 登记已有运行包。框架不负责下载或解压：

```bash
sudo systemctl stop opsmesh
sudo -u opsmesh /opt/opsmesh/current/opsmesh --manifest /etc/opsmesh/host.json \
  --plugin-action register --module opsmesh.plugins.filesystem \
  --directory /var/lib/opsmesh/plugins/official-0.5.1/site
sudo -u opsmesh /opt/opsmesh/current/opsmesh --manifest /etc/opsmesh/host.json \
  --plugin-action configure --module opsmesh.plugins.filesystem \
  --config /etc/opsmesh/filesystem.json
sudo -u opsmesh /opt/opsmesh/current/opsmesh --manifest /etc/opsmesh/host.json \
  --plugin-action enable --module opsmesh.plugins.filesystem
sudo -u opsmesh /opt/opsmesh/current/opsmesh --manifest /etc/opsmesh/host.json --check
sudo systemctl start opsmesh
```

filesystem.json 是 CLI 配置输入，例如 `{"root":"/var/lib/opsmesh/files"}`；配置写入 SQLite 后以数据库为准。登记的 site 中其他已安装入口点在下一次宿主准备时发现，默认允许依赖使用，不主动启动；显式禁用记录不会覆盖。

需要 HTTP 入口时给 channels 保存其 API 配置，再启用入口插件。插件模型、凭据引用和端口都由插件自己定义。详见[插件配置](plugin-configuration-design.md)和[通道配置](channels-and-callbacks.md)。

运行中的服务独占宿主数据目录；离线 CLI、检查和迁移操作需停止服务，在线入口由管理插件提供。

环境文件不是必需文件。systemd 和安装检查允许 `/etc/opsmesh/opsmesh.env` 不存在；需要插件凭据时通过 `--env-file` 显式提供，升级保留已有文件。可选 Docker 示例的 `env_file.required: false` 需要 Docker Compose 2.24.0 或更新版本。

检查命令会导入插件并验证配置，不执行生命周期启动。因此它不证明数据库、MCP 或模型服务可用。手工执行命令需要提供与 systemd 相同的插件凭据；`opsmesh.env` 不会被命令行自动读取。

插件源码和资源是发布内容；不要在运行目录里混放不同版本依赖。升级插件时部署新版本目录、更新登记位置并重启，保留旧目录用于明确回退。源码插件拥有服务账户权限，只能加载管理员信任的包。第三方插件自行说明其 Python 依赖、外部程序与兼容范围。

## 数据库与其他外部服务

只有启用 database 或依赖它的功能插件才需要 PostgreSQL。模型、MCP stdio 子进程、浏览器、Docker 沙箱与 Langfuse 都属于选用插件的运行要求，框架安装器不代为安装。

数据库迁移是独立运维动作，由管理员在适当维护窗口执行：

```bash
/opt/opsmesh/current/opsmesh --manifest /etc/opsmesh/host.json \
  --command opsmesh.plugins.database.commands:check
/opt/opsmesh/current/opsmesh --manifest /etc/opsmesh/host.json \
  --command opsmesh.plugins.database.commands:migrate
```

执行命令的环境必须带有数据库配置与凭据，且宿主已加载待迁移的功能插件及其 database 依赖。迁移前按业务要求备份；从旧共享历史升级时阅读 [数据库迁移](database-migrations.md)。宿主升级或二进制回退均不会自动改变数据库。

Langfuse 是独立服务；OpsMesh 不安装其 PostgreSQL、ClickHouse、Redis、对象存储或 Worker。页面超时应检查这些服务，而不只是增加查询超时。追踪接入及上游限制见 [Langfuse 说明](langfuse-tracing.md)。

## 日志、升级与回退

```bash
systemctl status opsmesh
journalctl -u opsmesh -f
sudo systemctl restart opsmesh
sudo systemctl stop opsmesh
sudo bash install.sh --archive NEW-FRAMEWORK-ARCHIVE.tar.gz --checksums SHA256SUMS
sudo bash install.sh --rollback
```

服务使用 systemd readiness 通知，必要插件启动失败时不报告 READY。退出时框架先停止接单并清理执行，再释放资源。默认写入范围是 `/var/lib/opsmesh` 和临时目录；额外路径由管理员通过 systemd override 明确放行。

框架升级保留插件目录和数据。回退只切换框架程序，不替换当前插件、配置或数据库；管理员必须保证组合版本兼容。需要整组回退时，分别恢复插件记录及程序版本，不应把二进制回退理解为数据恢复。

## 可选 Docker

`deploy/docker/Dockerfile` 从解压后的框架发行包构建镜像，不复制源码仓库，不内置插件或数据库。准备清单与环境文件后使用 `deploy/docker/compose.yaml`；外部插件通过挂载目录提供，并保持配置中的路径一致。

容器内 UID 为 10001，挂载目录需具有适当权限。镜像内的 `/healthz` 也依赖 channels，空宿主没有这一接口。数据库迁移和插件部署仍由管理员单独执行。

## 构建与验收

构建命令、tag 发布附件和工作流职责见 [发布说明](releases.md)。构建必须在目标架构 Linux 执行。源代码单元测试、wheel 安装检查、冻结可执行程序测试及 systemd 安装测试是不同证据；只有对应工作流实际成功，才能声明该版本通过相应验收。

## 状态与文件维护

宿主状态库结构版本为 2，启动时事务升级版本 1 记录并补齐 protected=false。更早程序不能管理版本 2 状态库；程序回退前应明确恢复相匹配的状态数据。常驻核心插件通过 CLI 或部署登记 protected 标记，具体约束见[插件配置](plugin-configuration-design.md)。

filesystem 使用完整模块名的平级目录。现有嵌套目录需停服后使用[文件目录维护工具](../packages/filesystem/README.md#behavior)检查及搬迁；数据库共享迁移历史使用[独立转换工具](database-migrations.md)。这两项都是显式维护动作，不会在服务启动时自动搬迁文件或改变业务表。
