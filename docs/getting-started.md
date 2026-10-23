# 快速开始

## 选择部署方式

0.1.0 服务端支持 Linux amd64，生产部署有两种方式：

| 方式 | 应用运行形式 | 基础设施 |
| --- | --- | --- |
| Docker Compose | 已构建的 API/Worker 镜像，Gateway 使用 API 镜像的独立命令 | 安装器管理 PostgreSQL、Redis 和持久 Temporal |
| 本机 systemd | 内含 Python 运行时的冻结服务端二进制目录包 | 提前准备 PostgreSQL、Redis、持久 Temporal、Docker 和主机工具 |

安装器面向使用 systemd 的 Debian/Ubuntu amd64 主机。准备可用的 HTTPS 域名和反向代理；
API 默认只监听主机 `127.0.0.1:8000`。服务端不包含 Web 管理界面。

## 安装发行版

下载固定版本的脚本，检查内容后执行：

```sh
curl -fLO https://github.com/jhupo/OpsMesh/releases/download/v0.1.0/install.sh
sudo sh install.sh --version v0.1.0 --mode compose --origin https://agents.example.com
```

系统前置工具的安装和服务管理需要 root。脚本下载原生运维 CLI，由其校验发行清单、
摘要和镜像引用，创建持久数据目录并执行迁移。安装时不拉取源码、不安装 Python 包。

本机方式使用 `--mode systemd`；执行前必须完成[部署手册](../deploy/server/DEPLOYMENT.md)
中的外部服务和连接配置。需要成套备份时，还需声明独占的 Temporal systemd 服务及数据库。

安装完成后保存终端返回的初始管理员账号和密码，不要将输出写入公开日志。
`/opt/opsmesh/.env` 保存配置和密钥，`data/storage` 保存产品数据，`plugins` 保存插件包；
`current` 指向只读发行目录。

## 登录和配置模型

通过自己的 HTTPS 地址调用 `POST /api/v1/auth/login`，JSON 内容为 `email` 和 `password`。
后续请求将返回的 `session_token` 放入 `X-Session-ID`；不要把凭据放在 URL 或 shell 历史中。

首次运行前按[平台管理](platform-management.md)配置 Provider、加密凭据和 Agent，发布配置。
没有默认免费模型，也不会将安装凭据转换为模型凭据。`OPSMESH_AGENT_CATALOG_PATH` 可导入
首次种子配置，但不会覆盖在线修改；格式见[种子示例](../deploy/server/agent-catalog.example.json)。

Agent 配置用于创建持久执行实例；普通 Agent 直接调用原生 SDK，SandboxAgent 绑定
显式创建的 Docker 环境。调用执行实例创建接口后再提交 Run；接口合同及生命周期见
[Execution](../src/opsmesh/execution/README.md)。创建编排时先发布定义、创建绑定实例，
再提交编排 Run，详见 [Workflows](../src/opsmesh/workflows/README.md)。

MCP 和外部工具通过插件注册。框架管理安装、资源发现、授权和生命周期，具体连接由
适配器提供。系统插件具有宿主进程权限，只有管理员可以安装可信包。

## 检查和日常运维

```sh
curl --fail https://agents.example.com/api/v1/health/ready
opsmesh --root /opt/opsmesh doctor
opsmesh --root /opt/opsmesh update --check
opsmesh --root /opt/opsmesh logs --service worker
```

`doctor` 检查主机前置工具；就绪接口检查配置的基础设施，不证明外部模型或通道已可用。
`opsmesh status` 需要管理员 Session。生产 API 文档默认关闭，可在受控网络启用
`OPSMESH_ENABLE_API_DOCS`，通过 `/docs` 或 `/openapi.json` 查看当前合同。

升级前读取目标版本发行说明，完成未决 Run、审批和外部效果对账。备份需要停止入口，
确认无活动环境，并协调业务数据库、Temporal、文件、插件与密钥；完整命令见
[升级与恢复](../deploy/server/delivery-operations.md)。

## 常见问题

| 现象 | 检查方向 |
| --- | --- |
| 无页面可打开 | 本版本只有后端 API，无内置 Web UI |
| API 未就绪 | 检查数据库迁移、Redis、Temporal 地址和命名空间，以及 API/Worker 日志 |
| 模型调用失败 | 检查 Provider 协议、模型名、凭据、访问网络及配置的重试次数和时限 |
| 沙箱不能写文件 | 检查 Worker Docker 权限、同路径宿主挂载、UID/GID 和 POSIX ACL |
| 插件未生效 | 使用 `opsmesh plugin list` 检查各宿主 applied/hosts 状态和装载错误 |
| 通道不能收发 | 检查 QQ/钉钉配置、外部身份绑定、路由授权和独立 Gateway 进程 |

报告问题时附上版本、部署方式、失败步骤和脱敏日志；请勿上传 `.env` 或完整备份。
