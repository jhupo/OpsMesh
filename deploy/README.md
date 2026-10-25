# Linux 部署

发布物为 `opsmesh-v版本-linux-x86_64.tar.gz` 和 `opsmesh-v版本-linux-aarch64.tar.gz`，内含独立可执行程序、运行库、迁移、systemd 文件和安装脚本。部署主机不需要 Python、uv 或项目源码。仅提供 Linux 安装，基线为 Ubuntu 22.04 / glibc 2.35；需要 systemd、PostgreSQL 和对应 CPU 架构。沙箱插件需要自行配置原生后端。

## 安装

从仓库 GitHub Releases 下载对应压缩包和 `SHA256SUMS`。在下载目录执行，替换示例中的版本和仓库名：

```sh
sha256sum --ignore-missing -c SHA256SUMS
tar -xzf opsmesh-v0.2.0-linux-x86_64.tar.gz
cd opsmesh
./bin/opsmesh keygen
cp opsmesh.env.example /root/opsmesh-install.env
chmod 600 /root/opsmesh-install.env
```

编辑 `/root/opsmesh-install.env`，填入独立 PostgreSQL 数据库 URL 和生成的加密密钥。保留模板中的数据、日志目录；监听端口可配置。密码中的 URL 保留字符需要编码。安装器不安装或清空数据库，不接管已有安装。

```sh
sudo sh ./install.sh --config /root/opsmesh-install.env --repository OWNER/REPOSITORY --start
```

仓库名指向你发布 OpsMesh 的公开 GitHub 仓库，必须提供；不预设更新来源。自定义端口时同时传 `--health-url http://127.0.0.1:端口/health`。首次安装可加 `--auto-update` 启用每天检查并应用稳定版本。

安装使用受限的 `opsmesh` 账户，目录为：

```text
/opt/opsmesh/releases/版本/     # 不可变的运行包，旧版本保留
/opt/opsmesh/current           # 当前运行包的原子符号链接
/etc/opsmesh/.env              # opsmesh:opsmesh 0600
/etc/opsmesh/update.json       # root 0600，公开更新源与健康地址
/var/lib/opsmesh/              # 插件、用户工作区、会话等私有数据
/var/log/opsmesh/              # 各进程轮转日志
```

`opsmesh.target` 管理 API、tasks、scheduler、gateway 四个服务。迁移 oneshot 必须成功后才启动服务；API 不持有通道长连接。首次启动未配置的管理员账密写入 `/var/lib/opsmesh/initial-admin-用户ID.json`（服务账户所有，权限 `0600`）；API journal 仅显示文件位置，不记录密码。使用本机管理员权限读取，登录后修改密码并删除该文件：

```sh
sudo systemctl status opsmesh.target 'opsmesh-*.service'
sudo journalctl -u opsmesh-api.service
sudo systemctl restart opsmesh.target
sudo systemctl stop opsmesh.target
```

默认监听 `0.0.0.0:8000`，公网部署应配置 HTTPS 反向代理。运行目录受 systemd 限制；原生 Docker 沙箱如需访问 Docker socket，应通过 `systemctl edit opsmesh-tasks.service` 显式配置所需组和权限。安装器不自动授予 Docker 管理权限。

## 更新

```sh
sudo /opt/opsmesh/current/bin/opsmesh update --check
sudo systemctl start opsmesh-update.service
sudo systemctl enable --now opsmesh-update.timer
sudo systemctl disable --now opsmesh-update.timer
sudo journalctl -u opsmesh-update.service
```

自动更新是可选功能，timer 每天执行并随机延迟最多一小时。更新器只选择配置仓库最新的稳定 `vMAJOR.MINOR.PATCH` Release，匹配本机架构，核验 SHA-256，再展开到新的版本目录。全程由部署锁防止重复更新。

切换前停止 API、调度、网关入口，并检查排队、运行、等待人工输入的任务及沙箱文件操作租约。有未完成工作时恢复原服务并推迟更新；不会强制取消任务或重放检查点。已有空闲会话保留，SDK 版本变化后的检查点仍受原有恢复约束。

允许升级后停止整组服务，原子切换 `current`，重新加载 unit，执行新版本迁移并启动。更新器检查四个进程与 API 存活；这不代表 QQ、钉钉、沙箱或模型已完成联通验证。数据库迁移或启动失败会明确报错，保留新旧版本及用户数据，不自动降级数据库或删除现场。升级前备份数据库、数据目录和加密密钥，按迁移版本决定人工恢复方案。

更新 timer 仅管理 systemd 安装。配置和数据不从发布包覆盖。长期有任务排队或等待审批时需先完成或显式取消，自动更新会持续推迟。

## 插件

管理员将可信插件 ZIP 放到服务账户可读的路径，进入配置目录后导入：

```sh
cd /etc/opsmesh
sudo -u opsmesh /opt/opsmesh/current/bin/opsmesh plugins import /var/lib/opsmesh/native-agent.zip
sudo -u opsmesh /opt/opsmesh/current/bin/opsmesh plugins import /var/lib/opsmesh/another.zip --owner-id USER_ID
```

也可使用鉴权 HTTP 上传接口。插件 SDK 及打包 CLI 用于开发端，不是业务 API 客户端。框架不依赖生成 SDK；可执行发布包另行收集与框架合同匹配的插件绑定。Python 包部署需要为插件安装匹配的生成 SDK wheel。发布包包含锁定的宿主依赖，插件 ZIP 不自动安装任意 Python 依赖；需要额外库的插件应先在构建环境加入依赖并重新构建运行包。

## 构建与自动发布

在 Linux 目标架构上使用 Python 3.13、uv 与 binutils 构建（Ubuntu 可通过 `apt-get install binutils` 安装构建工具）：

```sh
uv sync --frozen --extra dev --extra sandbox --extra build
uv run --no-sync python scripts/build_release.py --tag v0.2.0
```

`dist/opsmesh` 是展开后的运行包；同目录生成压缩包和校验文件。构建脚本拒绝覆盖已有产物，不运行服务或测试。PyInstaller 不跨平台编译；需要分别在 x86_64、aarch64 上构建。

`.github/workflows/ci.yml` 执行格式、lint、类型、框架不依赖 SDK 的导入边界检查；独立的 SDK 合同作业在 Python 3.11/3.13 下生成、静态检查和打包。`security.yml` 执行 CodeQL、锁定运行依赖审计、源码密钥与配置扫描。标签发布由 `release.yml` 在上述门禁通过后构建双架构可执行包、宿主与 SDK wheel/sdist、源码 tar.gz/ZIP、安装脚本、SBOM、安全报告和校验清单；生成 GHCR 多架构镜像，逐架构扫描通过后提升版本/minor/latest 标签并发布 Release。构建来源使用 GitHub artifact attestations 签发。手动触发仅构建 Actions 产物。

CI 的独立数据库回归作业会执行测试；本地测试、模型调用及真实服务验收仍需明确授权，本轮没有执行。工作流不调用模型或真实通道。静态扫描或打包成功不能替代真实部署验证。新标签必须与两个 `pyproject.toml` 版本及 `CHANGELOG.md` 匹配，CI 不会自动提交、打标签或推送 SDK 分支。

可以下载 Release 的 `install.sh` 后安装固定版本（需要 curl、tar、awk、sha256sum）：

```sh
sudo sh install.sh v0.2.0 --config /root/opsmesh-install.env --start
```

脚本按本机架构下载并核验发布校验值。安装前建议独立验证 `SHA256SUMS` 和 GitHub 来源证明；从同一个发布地址下载的哈希不等同于独立签名验证。

```sh
sha256sum --ignore-missing -c SHA256SUMS
gh attestation verify opsmesh-v0.2.0-linux-x86_64.tar.gz --repo jhupo/OpsMesh
```

## 容器部署

Compose 使用已发布镜像，不需要部署源码或在服务器上构建。将源码包中的 `deploy/compose.yaml` 下载到部署目录，并准备只对管理员可读的环境文件：

```sh
# 文件包含 POSTGRES_PASSWORD、OPSMESH_ENCRYPTION_KEY 和所需模型等配置
# 固定版本；也可使用 Release 的 IMAGE_DIGEST.txt 固定不可变摘要
export OPSMESH_IMAGE=ghcr.io/jhupo/opsmesh:0.2.0
docker compose --env-file /path/to/compose.env -f deploy/compose.yaml pull
docker compose --env-file /path/to/compose.env -f deploy/compose.yaml up -d
```

需要 Docker Compose v2。镜像提供 linux/amd64、linux/arm64，包含构建好的可执行程序。数据库健康后迁移服务先执行，迁移成功后启动 API、任务、调度和网关四个进程。容器数据位于独立卷；模型/管理员/日志配置见 `.env.example`。首次随机管理员密码可从 API 容器日志获取。

容器沙箱需要操作者显式配置后端连接、socket 挂载与相应权限；默认 Compose 不挂载 Docker socket。不能通过简单挂载默认 socket 推断非 root 容器已获得访问权限。

镜像更新由操作者在任务完成后修改固定版本或摘要、pull 并重新部署；systemd 更新 timer 不管理容器。升级前备份 PostgreSQL、数据卷和加密密钥，失败不自动降级数据库。

本地自建镜像时，将两个原生构建包分别解压到 `dist/linux-amd64/opsmesh` 和 `dist/linux-arm64/opsmesh`，从仓库根目录执行：

```sh
docker buildx build --platform linux/amd64,linux/arm64 -f deploy/Dockerfile \
  --tag your-registry/opsmesh:0.2.0 --push .
```

不要将密钥、源码备份、虚拟环境或数据目录加入构建上下文。`.dockerignore` 只允许发行文件和 Dockerfile。
