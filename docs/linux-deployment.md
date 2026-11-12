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
  --archive opsmesh-0.5.0-linux-x86_64.tar.gz \
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

独立插件运行包的 `site/` 包含四个官方插件及其额外依赖。管理员验证校验和后，解压到独立的版本目录，例如：

```bash
sudo install -d -m 0755 /var/lib/opsmesh/plugins/official-0.5.0
sudo tar -xzf opsmesh-plugins-0.5.0-linux-x86_64.tar.gz \
  --strip-components=1 -C /var/lib/opsmesh/plugins/official-0.5.0
```

将实际 `site` 路径加入宿主清单：

```json
{
  "environment": "production",
  "plugin_paths": ["/var/lib/opsmesh/plugins/official-0.5.0/site"],
  "plugins": [
    {
      "module": "opsmesh.plugins.filesystem",
      "required": true,
      "config_namespace": "opsmesh.plugins.filesystem",
      "config": {"root": "/var/lib/opsmesh/files"}
    },
    {
      "module": "opsmesh.plugins.execution",
      "required": true,
      "config_namespace": "opsmesh.plugins.execution",
      "config": {"tracing": {"destination": "local"}}
    }
  ]
}
```

这个示例只启用文件与执行能力，不打开 HTTP 监听。需要网络入口时，启用 channels，并在其 `config.api` 配置监听地址，详见[通道配置](channels-and-callbacks.md)。安装包存在不等于自动启用；入口插件列入启动清单；依赖通过 `require()` 自动加载，不需要重复列出。只有覆盖依赖的默认配置、显式禁用或校验来源时才添加对应条目，配置用条目可设置 `autoload: false`。只选择需要的模块，未启用的包不会成为业务功能。

```bash
sudo -u opsmesh /opt/opsmesh/current/opsmesh \
  --manifest /etc/opsmesh/host.json --check
sudo systemctl restart opsmesh
```

`host.json` 的 `framework` 只配置框架基础行为，例如 `"framework": {"command_start": ["/"], "command_sep": ["."]}`；省略字段时使用框架默认值。插件参数放在各自的 `plugins[].config` 中，由插件定义字段与校验规则。

环境文件不是必需文件。systemd 和安装检查允许 `/etc/opsmesh/opsmesh.env` 不存在；需要插件凭据时通过 `--env-file` 显式提供，升级保留已有文件。可选 Docker 示例的 `env_file.required: false` 需要 Docker Compose 2.24.0 或更新版本。

检查命令会导入插件并验证配置，不执行生命周期启动。因此它不证明数据库、MCP 或模型服务可用。手工执行命令需要提供与 systemd 相同的插件凭据；`opsmesh.env` 不会被命令行自动读取。

插件源码和资源是发布内容；不要在运行目录里混放不同版本依赖。升级插件时部署新版本目录、修改清单并重启，保留旧目录用于明确回退。源码插件拥有服务账户权限，只能加载管理员信任的包。第三方插件自行说明其 Python 依赖、外部程序与兼容范围。

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

框架升级保留插件目录和数据。回退只切换框架程序，不替换当前插件、配置或数据库；管理员必须保证组合版本兼容。需要整组回退时，分别恢复插件清单及程序版本，不应把二进制回退理解为数据恢复。

## 可选 Docker

`deploy/docker/Dockerfile` 从解压后的框架发行包构建镜像，不复制源码仓库，不内置插件或数据库。准备清单与环境文件后使用 `deploy/docker/compose.yaml`；外部插件通过挂载目录提供，并保持配置中的路径一致。

容器内 UID 为 10001，挂载目录需具有适当权限。镜像内的 `/healthz` 也依赖 channels，空宿主没有这一接口。数据库迁移和插件部署仍由管理员单独执行。

## 构建与验收

构建命令、tag 发布附件和工作流职责见 [发布说明](releases.md)。构建必须在目标架构 Linux 执行。源代码单元测试、wheel 安装检查、冻结可执行程序测试及 systemd 安装测试是不同证据；只有对应工作流实际成功，才能声明该版本通过相应验收。

升级至 v0.5.0 时，插件模块改为 `opsmesh.plugins.<name>`，核心导入移除 `.framework`；源码目录扁平化不改变独立插件 wheel 的部署方式。将 channels 的旧 `config.http` 监听字段合入 `config.api`；QQ 若启用则单独配置 `config.qq` 监听地址，更新反向代理的 QQ 回调上游。钉钉使用 Stream。
