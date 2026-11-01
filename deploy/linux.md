# Linux 安装与服务管理

Linux 安装提供 `local` 和 `docker` 两种模式，默认是 **本地 Python 安装 + systemd 服务**。本地模式不需要 Docker 或 Kubernetes；HTTP API 和后台协调器在同一个服务中运行，默认使用 SQLite。Docker 模式需要显式指定，使用 Compose 管理 API、Worker、PostgreSQL 和 Redis。

以下命令在包含 `deploy/install.sh` 的源码根目录运行。已经发布的原 `v0.3.0` 发行包不包含此安装脚本，不能用旧发行包中的路径执行这些命令。

## 安装要求

- Linux、Bash、GNU 常用命令和 root 权限；本地模式还需要正在运行的 systemd、`curl` 与 Git。
- 本地模式使用固定版本 uv 和独立的 Python 环境，不修改系统 Python。首次安装需要下载 Python 与锁定依赖的网络访问。
- Docker 模式要求已经安装并启动 Docker Engine，且 `docker compose version` 可用。
- 默认 HTTP 地址为 `127.0.0.1:8000`。同一台机器安装多个实例时，分别指定实例名和端口。

此脚本不提供 Windows 服务安装，也不会安装 Kubernetes。通过 Python 虚拟环境手动开发运行的说明仍见[项目 README](../README.md)。

安装器不执行 `apt`、`dnf` 等系统包安装。缺少前置命令时会报告并停止。uv 固定为 `0.13.5`，默认自动下载适用于 glibc Linux 的 x86_64/aarch64 构建并核验 SHA-256；其他架构可预先安装匹配版本的 uv。应用使用独立的托管 Python 3.13，首次需要时由 uv 下载。

## 默认本地安装

```sh
sudo bash deploy/install.sh
```

等价的显式命令：

```sh
sudo bash deploy/install.sh --mode local
```

首次安装会要求输入本地管理员密码，密码至少 12 个字符。管理员密码只用于初始化，不写入常驻服务配置。脚本安装 OpenAI Agents SDK 和 S3 可选依赖，但不会替用户创建模型凭据、模型档案或 Agent。

安装后使用：

```sh
sudo systemctl status opsmesh.service --no-pager
sudo systemctl restart opsmesh.service
sudo journalctl -u opsmesh.service -f
curl --fail http://127.0.0.1:8000/ready
```

访问 `http://127.0.0.1:8000/docs`，通过 HTTP API 登录并配置模型、Agent 和机器人。`/ready` 验证数据库可用性；它不会调用模型或验证外部机器人账号。

### 文件与进程归属

| 位置 | 用途 |
| --- | --- |
| `/opt/opsmesh/releases/` | 各次安装的代码和独立 Python 环境；旧目录保留 |
| `/opt/opsmesh/current` | 当前应用版本的符号链接 |
| `/opt/opsmesh/python`、`tools`、`cache` | root 管理的 Python、固定版本 uv 与安装缓存 |
| `/etc/opsmesh/opsmesh.env` | 本实例的环境配置，重复安装时保留 |
| `/var/lib/opsmesh` | SQLite、工作区、blob 和运行数据 |
| `/etc/systemd/system/opsmesh.service` | systemd 单元 |
| `journalctl -u opsmesh.service` | 应用标准输出和错误日志 |

服务以独立的 `opsmesh` 系统用户运行，应用安装目录由 root 管理。更换实例名时，上述路径、服务名和系统用户随实例名变化；不会复用默认实例的数据。

安装时只复制应用所需源码、部署文件和锁文件，不复制 `.git`、开发环境 `.env` 或运行数据。管理员可以在首次运行前预建 root 所有的 `/etc/opsmesh/opsmesh.env`，安装器保留其内容；本地配置文件权限为 `0640`，Docker 配置为 `0600`。

### 启停与开机启动

```sh
sudo systemctl start opsmesh.service
sudo systemctl stop opsmesh.service
sudo systemctl restart opsmesh.service
sudo systemctl enable opsmesh.service
sudo systemctl disable opsmesh.service
sudo journalctl -u opsmesh.service --since today --no-pager
```

`disable` 只关闭开机启动，不会停止当前进程；需要同时停止时使用 `systemctl disable --now opsmesh.service`。

### 修改配置

```sh
sudoedit /etc/opsmesh/opsmesh.env
sudo systemctl restart opsmesh.service
```

该文件由 systemd 读取，不需要也不应在 shell 中 `source`。每行使用 `NAME=value` 格式。模型凭据可作为环境变量定义，再由模型档案的 `secret_ref` 引用；不要将凭据直接写入 Agent 定义或版本库。不要把 `OPSMESH_ADMIN_PASSWORD` 放入这个常驻配置文件。

本地模式默认包含后台协调器，即 `OPSMESH_WORKER_ENABLED=true`。需要 PostgreSQL 或 Redis 时，先准备对应服务，再配置 `OPSMESH_DATABASE_URL`、`OPSMESH_REDIS_URL`。更换数据库 URL 不会自动迁移现有 SQLite 数据；新数据库须单独初始化，数据迁移应按备份/恢复边界评估。

需要开放其他监听地址时修改 `OPSMESH_HOST`，对外部署按实际拓扑配置 TLS 与反向代理。本地安装方式和 Run 的执行方式独立：选择 Docker 工具或 Docker Run Worker 时，仍需另外提供 Docker 执行环境。

原生服务启用 `NoNewPrivileges`、只读系统目录和用户主目录保护，默认只允许写入 `/var/lib/opsmesh`。需要其他可写工作区时，通过 `sudo systemctl edit opsmesh.service` 为所需目录添加 `ReadWritePaths=`，设置相应文件权限后重启；不要仅改应用路径而忽略 systemd 的访问边界。

### 使用已安装环境执行维护命令

使用与常驻服务相同的配置、用户和工作目录执行 CLI，避免误操作当前 shell 目录下的另一个 SQLite 数据库：

```sh
sudo systemd-run --wait --pipe --collect \
  -p User=opsmesh -p Group=opsmesh \
  -p WorkingDirectory=/var/lib/opsmesh \
  -p EnvironmentFile=/etc/opsmesh/opsmesh.env \
  /opt/opsmesh/current/.venv/bin/opsmesh check
```

将末尾的 `check` 替换为所需的维护命令。备份、恢复、迁移或实际删除存储对象前，先停止所有写入该实例数据的服务与 Worker。备份目录应位于服务用户可写的位置，并为数据库、文件和环境配置分别保留可恢复副本；完整语义见[运维说明](../docs/operations.md)。

## 可选 Docker 安装

```sh
sudo bash deploy/install.sh --mode docker
```

Docker 模式从当前源码构建镜像，先启动 PostgreSQL 和 Redis，再初始化应用并启动 API/Worker。管理员密码只传入一次性初始化容器。数据库与缓存端口只在 Compose 网络中使用，HTTP 默认绑定宿主机 `127.0.0.1:8000`。

容器启停由 Compose 管理，不创建与本地模式重复的 `opsmesh.service`。持久数据保存在本实例的 Docker volumes 中；镜像和容器可重新构建，数据卷必须单独备份。普通停止操作不删除数据卷。

在任意目录执行以下命令。非默认实例需替换全部 `opsmesh` 路径和项目名：

```sh
sudo env OPSMESH_ENV_FILE=/etc/opsmesh/opsmesh.env docker compose \
  --project-name opsmesh --env-file /etc/opsmesh/opsmesh.env \
  -f /opt/opsmesh/current/deploy/compose.yaml ps

sudo env OPSMESH_ENV_FILE=/etc/opsmesh/opsmesh.env docker compose \
  --project-name opsmesh --env-file /etc/opsmesh/opsmesh.env \
  -f /opt/opsmesh/current/deploy/compose.yaml logs --follow api worker

sudo env OPSMESH_ENV_FILE=/etc/opsmesh/opsmesh.env docker compose \
  --project-name opsmesh --env-file /etc/opsmesh/opsmesh.env \
  -f /opt/opsmesh/current/deploy/compose.yaml stop api worker

sudo env OPSMESH_ENV_FILE=/etc/opsmesh/opsmesh.env docker compose \
  --project-name opsmesh --env-file /etc/opsmesh/opsmesh.env \
  -f /opt/opsmesh/current/deploy/compose.yaml up -d
```

将末尾子命令替换为 `run --rm --no-deps api check` 可检查数据库。维护时先停止 `api worker`，保留 PostgreSQL 和 Redis 运行；不要使用 `down --volumes`。自动生成的 PostgreSQL 密码保存在受限权限配置中，已有数据卷的密码不会因重写环境变量而自动更新。

更详细的 Compose 配置见[部署说明](README.md)和[Compose 文件](compose.yaml)。同一实例不能直接从本地模式切换为 Docker 模式；使用新的实例名和独立数据，迁移完成后再切换访问入口。

## 安装参数

```sh
sudo bash deploy/install.sh --help
```

| 参数 | 用途 |
| --- | --- |
| `--mode local` | 本地安装，默认模式 |
| `--mode docker` | 显式选择 Docker Compose |
| `--instance opsmesh` | 实例名，决定目录、服务/项目名称 |
| `--port 8000` | 首次生成配置时使用的宿主 HTTP 端口 |
| `--admin admin` | 首次初始化的本地管理员用户名 |
| `--admin-password-file PATH` | 从受限权限文件读取首次管理员密码，用于无人值守安装 |
| `--upgrade` | 显式升级已安装实例，并执行本架构登记的数据库迁移 |
| `--no-start` | 完成初始化和检查，但不启动或重启应用；本地单元仍启用开机启动 |

多个实例示例：

```sh
sudo bash deploy/install.sh --instance opsmesh-test --port 18000
sudo systemctl status opsmesh-test.service --no-pager
```

无人值守安装先通过部署系统创建仅 root 可读的密码文件，然后传入路径。文件内容不应出现在 shell 历史、命令行参数或安装日志中。安装完成后按部署系统的密钥生命周期清理该文件。重复安装不会用 `--admin` 或密码文件重置已有管理员。

```sh
sudo bash deploy/install.sh --admin-password-file /root/opsmesh-admin-password
```

`--no-start` 在首次 Docker 安装时仍会启动 PostgreSQL/Redis 以完成初始化，只是不启动 API/Worker；对已经运行的同版本实例，不会主动停止应用。升级模式会先停止应用，指定 `--no-start` 后将其保持在停止状态。

## 重复安装与升级

相同实例重复运行安装命令会保留已有配置与数据。修改端口、模型环境变量等配置时编辑现有环境文件，不用重新初始化数据库。更换应用源码时使用显式升级：

```sh
sudo systemctl stop opsmesh.service
# 先备份数据库、数据目录和 /etc/opsmesh/opsmesh.env，再进入新源码目录。
sudo bash deploy/install.sh --upgrade
```

Docker 实例使用同样的模式和实例名：

```sh
sudo bash deploy/install.sh --mode docker --upgrade
```

升级流程会先停止写入服务，再执行迁移和检查，最后按启动选项恢复服务。升级脚本不是备份工具；迁移失败时应保留数据并排查，不能直接清空数据库或删除 Docker volumes 重试。应用代码回退也不等于数据库自动降级，回退必须使用匹配的代码、配置和备份。

旧 `0.2.x` 及更早架构的数据仍不支持原地升级。`--upgrade` 只用于当前架构的已登记迁移，详见[迁移说明](../migrations/README.md)、[备份/恢复](../docs/operations.md)与[版本兼容边界](../docs/releases/v0.3.0.md)。
