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

以下为当前源码的安装布局。已发布的 `v0.5.8` 安装器仍使用 `/opt/opsmesh`，不能与这里的新版安装器混用。使用同一次构建的框架包、`SHA256SUMS` 和 `install.sh`，先将 `VERSION` 设为所下载的版本号（不带 `v`）。

```bash
: "${VERSION:?Set VERSION to the downloaded release version}"
sudo bash install.sh \
  --archive opsmesh-$VERSION-linux-x86_64.tar.gz \
  --checksums SHA256SUMS
```

首次安装默认使用宿主配置示例，插件目录库从空状态初始化；不提供 `--env-file` 时不创建环境文件。可以用 `--manifest FILE`、`--env-file FILE` 显式提供配置；升级未提供这些参数时保留已有配置。

安装器检查操作系统、架构、glibc、systemd 和校验和，安装专用服务账户，验证宿主配置并启动服务。配置检查不连接 PostgreSQL；安装器不下载或解压插件，不执行插件迁移。

空宿主只提供本机管理 socket，不监听 HTTP。`/healthz` 由正在运行的 channels HTTP 连接提供，不是宿主就绪接口。HTTP 连接默认监听回环地址；对外访问应由管理员配置 TLS 反向代理。

```text
/usr/local/bin/opsmesh      # 指向 /usr/local/lib/opsmesh/current/opsmesh
/usr/local/lib/opsmesh/
  releases/<release>/       # root 管理的框架程序
  current -> releases/...
  previous -> releases/... # 有可用回退版本时创建
  plugins/<release>/       # root 管理的外部插件代码
/etc/opsmesh/
  host.json                # 宿主清单
  opsmesh.env              # 可选的插件环境变量，按需创建
/etc/systemd/system/opsmesh.service  # 本机管理员安装的单元
/var/lib/opsmesh/           # SQLite 目录库、业务文件及其他持久状态
/run/opsmesh/host.sock      # 服务运行期间的私有控制 socket
```

这是管理员手工安装发行归档的布局，不是系统包管理器的打包规范。目录划分参照 [FHS 3.0 的本机安装约定](https://refspecs.linuxfoundation.org/FHS_3.0/fhs/ch04s09.html)，服务单元和状态目录参照 [systemd.unit](https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html) 与 [systemd.exec](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html)。

## 命令入口

`opsmesh-service` 是 Python 分发包名，命令名是 `opsmesh`，systemd 单元名是 `opsmesh.service`。安装器在 `/usr/local/bin` 创建命令链接，不修改 `PATH`，也不覆盖同名的其他程序。`--help` 和 `--version` 不需要清单，启动、检查和维护命令需要 `--manifest`：

```bash
opsmesh --version
opsmesh --help
systemctl status opsmesh.service
```

若报 `command not found`，运行 `/usr/local/bin/opsmesh --version` 检查安装；若绝对路径正常，检查登录环境的 `PATH`。若报 `--manifest` 缺失，说明程序已经找到；管理插件时按下文传入 `/etc/opsmesh/host.json`。正在运行的进程由 `systemctl` 管理，不要再用同一清单启动第二个宿主。

## 按需部署插件

基础插件运行包的 `site/` 包含 channels、execution、filesystem、database、cron 及其额外依赖。knowledge 使用单独构建的完整插件运行包和预先准备的模型目录，见[发布说明](releases.md)。管理员验证校验和后，解压到独立的版本目录，例如：

```bash
sudo install -d -m 0755 /usr/local/lib/opsmesh/plugins/official-$VERSION
sudo tar -xzf opsmesh-plugins-$VERSION-linux-x86_64.tar.gz \
  --strip-components=1 -C /usr/local/lib/opsmesh/plugins/official-$VERSION
```

host.json 只保存宿主基础配置：

```json
{
  "environment": "production",
  "data_dir": "/var/lib/opsmesh",
  "runtime_dir": "/run/opsmesh",
  "framework": {"command_start": ["/"]}
}
```

插件配置文件使用 `{"config": {...}, "dependencies": {...}}` 结构；例如 filesystem.json
为 `{"config": {"root": "/var/lib/opsmesh/files"}, "dependencies": {}}`。

停止服务后，用 CLI 登记已有运行包。框架不负责下载或解压：

```bash
sudo systemctl stop opsmesh
sudo -u opsmesh opsmesh --manifest /etc/opsmesh/host.json \
  --plugin-action register --module opsmesh.plugins.filesystem \
  --directory /usr/local/lib/opsmesh/plugins/official-$VERSION/site
sudo -u opsmesh opsmesh --manifest /etc/opsmesh/host.json \
  --plugin-action configure --module opsmesh.plugins.filesystem \
  --config /etc/opsmesh/filesystem.json
sudo -u opsmesh opsmesh --manifest /etc/opsmesh/host.json \
  --plugin-action enable --module opsmesh.plugins.filesystem
sudo -u opsmesh opsmesh --manifest /etc/opsmesh/host.json --check
sudo systemctl start opsmesh
```

filesystem.json 是 CLI 配置输入，例如 `{"root":"/var/lib/opsmesh/files"}`；配置写入 SQLite 后以数据库为准。登记的 site 中其他已安装入口点在下一次宿主准备时发现，默认允许依赖使用，不主动启动；显式禁用记录不会覆盖。

需要 HTTP 入口时，由消费插件在自己的依赖配置中声明
`dependencies.channels.api.<name>`，并在自己的 HTTP 应用中完成所需的认证；
channels 只负责传输、正文 `user` 转换和框架分发。
插件模型、凭据引用和端口仍由消费插件定义。详见[插件配置](plugin-configuration-design.md)和[通道配置](channels-and-callbacks.md)。

运行中的服务独占宿主数据目录。登记包、离线检查和迁移仍在维护窗口执行。

扩展资源可以通过 Linux 本机控制接口查询、加载、启动、停止和重启。宿主使用
`<runtime_dir>/host.sock`，以服务账号执行命令。systemd 用 `RuntimeDirectory=` 创建 `/run/opsmesh` 并在服务停止后清理；未指定 `runtime_dir` 的独立实例使用 `<data_dir>/control/host.sock`。内置插件在各自的包元数据中声明 `opsmesh.builtin_plugins`，随官方插件包发布。在线启动依赖内置提供方的扩展前，该提供方须已在宿主启动时就绪。

```bash
sudo -u opsmesh opsmesh --manifest /etc/opsmesh/host.json \
  --online-plugin-action stop --module example_chat
sudo -u opsmesh opsmesh --manifest /etc/opsmesh/host.json \
  --online-plugin-action start --module example_chat
```

用完整模块名替换 `example_chat`。两条命令在同一个运行中的宿主完成，结果包含宿主 PID、插件状态和清理标记。人工停止保持到显式恢复或宿主下次按持久配置启动。恢复使用已加载的代码；代码版本替换见下文。

环境文件不是必需文件。systemd 和安装检查允许 `/etc/opsmesh/opsmesh.env` 不存在；需要插件凭据时通过 `--env-file` 显式提供，升级保留已有文件。可选 Docker 示例的 `env_file.required: false` 需要 Docker Compose 2.24.0 或更新版本。

检查命令会导入插件并验证配置，不执行生命周期启动。因此它不证明数据库、MCP 或模型服务可用。手工执行命令需要提供与 systemd 相同的插件凭据；`opsmesh.env` 不会被命令行自动读取。

在线 `restart` 只重启已导入代码的资源，不会重载 Python 模块、安装新版本或卸载代码。源码插件拥有服务账户权限，只能加载受信任的包。第三方插件自行说明其 Python 依赖、外部程序与兼容范围。

## 数据库与其他外部服务

只有启用 database 或依赖它的功能插件才需要 PostgreSQL。模型、MCP stdio 子进程、浏览器、Docker 沙箱与 Langfuse 都属于选用插件的运行要求，框架安装器不代为安装。

数据库迁移是独立运维动作，由管理员在适当维护窗口执行：

```bash
opsmesh --manifest /etc/opsmesh/host.json \
  --command opsmesh.plugins.database.commands:check
opsmesh --manifest /etc/opsmesh/host.json \
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

服务在必要插件启动成功且本地管理 socket 成功绑定后才向 systemd 报告就绪。退出时先停止接单并清理执行，再释放资源。默认写入范围是 `/var/lib/opsmesh`、`/run/opsmesh` 和私有临时目录；额外路径由管理员通过 systemd override 明确放行。

从 v0.5.7 升级到 v0.5.8 时，宿主和官方插件应一起升级。第三方插件若使用已移除的 `opsmesh.adapters` 或全局 Driver 查找，须先迁移到新的事件路径和显式依赖，再验证业务入口。不要覆盖原插件目录，也不要把不同版本的依赖混放在同一个 `site/`。

1. 在独立目录准备同版本、同架构、同 Python ABI 的运行包，并预检插件声明与配置。`--check` 不启动资源，也可能跳过加载失败的非必需插件；不能只靠其退出码判断所有插件都可用。
2. 停服后切换服务目录库中的插件路径，保持启用、保护、配置与业务数据不变，再运行新版安装器。v0.5.8 的 `register` 只用于首次登记，已登记模块需要离线维护其目录记录；没有替换目录或卸载的 CLI 命令。仅运行安装器不会更换插件路径。
3. 检查 `systemctl is-active opsmesh`，用 `--online-plugin-action status` 确认应运行的插件在新 PID 上返回 `ready`；有 HTTP 连接时再验证其 `/healthz` 及业务入口。

`install.sh --rollback` 只切换宿主程序与 systemd unit，不恢复插件登记、配置或数据库。组合升级需要回退时，先恢复与旧宿主匹配的插件路径和目录库，再回退二进制；不要对不兼容的组合单独执行二进制回退。

从 `/opt/opsmesh` 的旧布局迁移时，安装器保留原配置和 `/var/lib/opsmesh`，不会移动或删除原程序和插件，也不会把旧布局纳入 `--rollback`。先用同版本、同架构的运行包在新目录备齐插件，停服后切换目录库中的登记路径，再安装新宿主。确认运行中的必需插件及业务入口正常后，只清理已无进程、配置、挂载或登记引用的旧文件。现有清单不会自动补字段；升级到支持 `runtime_dir` 的版本后，可为 systemd 宿主显式配置 `/run/opsmesh`。回退到 `v0.5.8` 前需移除该字段。

## 可选 Docker

`deploy/docker/Dockerfile` 从解压后的框架发行包构建镜像，不复制源码仓库，不内置插件或数据库。准备清单与环境文件后使用 `deploy/docker/compose.yaml`；外部插件通过挂载目录提供，并保持配置中的路径一致。

容器内 UID 为 10001，挂载目录需具有适当权限。镜像内的 `/healthz` 也依赖 channels，空宿主没有这一接口。数据库迁移和插件部署仍由管理员单独执行。

## 构建与验收

构建命令、tag 发布附件和工作流职责见 [发布说明](releases.md)。构建必须在目标架构 Linux 执行。源代码单元测试、wheel 安装检查、冻结可执行程序测试及 systemd 安装测试是不同证据；只有对应工作流实际成功，才能声明该版本通过相应验收。

## 状态与文件维护

宿主目录库 `<data_dir>/state.sqlite3` 的结构版本为 3。服务打开目录库时在事务内把版本 1、2 的已有记录标记为 `config_initialized=true`，版本 1 同时补齐 `protected=false`。不认识该结构版本的旧宿主不能直接打开；回退前应准备与目标版本匹配的目录库。常驻插件保护与配置规则见[插件配置](plugin-configuration-design.md)。

filesystem 使用完整模块名的平级目录。现有嵌套目录需停服后使用[文件目录维护工具](../packages/filesystem/README.md#behavior)检查及搬迁；数据库共享迁移历史使用[独立转换工具](database-migrations.md)。这两项都是显式维护动作，不会在服务启动时自动搬迁文件或改变业务表。
