# OpsMesh

OpsMesh 是 Python 事件框架与插件宿主。框架提供事件、消息、Matcher、规则、权限、依赖注入和插件生命周期；插件负责通道接入、智能体执行或存储。智能体插件直接使用 OpenAI Agents SDK 的原生 Agent、工具、MCP 和 Session。

项目处于早期开发阶段，公开接口尚未承诺向后兼容。生产部署支持 Linux x86_64 和 ARM64，默认使用 systemd；Docker 为可选方式。

## 包与职责

| 发行包 | 导入模块 | 内容 |
|---|---|---|
| `opsmesh` | `opsmesh` | 框架、驱动接口、宿主和命令行 |
| [opsmesh-channels](packages/channels/README.md) | `opsmesh.plugins.channels` | FastAPI 入口、QQ Webhook、钉钉 Stream 与统一用户上下文 |
| [opsmesh-execution](packages/execution/README.md) | `opsmesh.plugins.execution` | 原生 Runner、流清理、插件回调与可选 Langfuse 追踪 |
| [opsmesh-filesystem](packages/filesystem/README.md) | `opsmesh.plugins.filesystem` | 插件目录、文件操作与 ZIP 导入导出 |
| [opsmesh-database](packages/database/README.md) | `opsmesh.plugins.database` | PostgreSQL 连接、事务和插件独立迁移 |

四个插件拥有独立包定义与依赖，不包含在框架包中。宿主默认不加载插件，也不要求 PostgreSQL、模型密钥或 Langfuse。功能插件通过 `require("execution")` 等声明实际依赖，由框架按需加载，无须在宿主清单重复列出全部依赖。

## 安装框架服务

从同一个 GitHub Release 下载对应架构的框架包、`SHA256SUMS` 与 `install.sh`：

```bash
sudo bash install.sh \
  --archive opsmesh-0.5.0-linux-x86_64.tar.gz \
  --checksums SHA256SUMS
journalctl -u opsmesh -f
```

安装器部署框架服务及其自带 Python 运行环境，首次安装生成基础配置，升级保留现有配置、插件与数据。插件通过宿主 CLI 单独登记。HTTP 服务由 channels 插件提供，数据库迁移使用 database 插件的维护命令。

需要插件时，另行部署同版本、同架构的插件运行包，通过宿主 CLI 登记模块、配置和启用状态后启动。插件运行包携带插件源码、资源和额外 Python 依赖，服务器无需 pip。完整步骤见 [Linux 部署](docs/linux-deployment.md)。

## 开发

要求 Python 3.11 或以上。在虚拟环境中安装锁定的开发依赖和需要修改的本地包：

```bash
python -m venv .venv
# Activate the virtual environment for your shell.
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e . -e packages/channels \
  -e packages/filesystem -e packages/database -e packages/execution
```

只安装框架包时使用 `python -m pip install -e .`。上面的开发锁用于整个仓库验证，包含插件测试需要的依赖，不是框架的运行依赖。仅运行受改动影响的测试，例如：

```bash
python -m pytest tests/unit/test_execution_runner.py tests/unit/test_execution_tracing.py -q
```

Langfuse 定向测试需要 execution 的可选追踪依赖，见其包说明。Linux 文件系统发布操作依赖相应系统调用；Windows 开发环境不能替代 Linux 发布验收。贡献与验证约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 目录

```text
src/opsmesh/        # 独立框架发行包
packages/          # channels、execution、filesystem、database 插件发行包
examples/          # 普通对话、沙箱对话与客户端示例
tests/             # 框架及插件的行为验证
deploy/            # 仅部署框架的安装器、systemd 与可选 Docker
packaging/         # 可执行包构建定义与依赖锁
scripts/           # 构建、产物检查及安装验收
tools/             # 停服期间的目录与数据库历史维护
.github/workflows/ # CI、Linux 构建及 tag Release
docs/              # 当前架构、部署和插件契约
licenses/          # 上游许可文本
```

## 发布与文档

`v<version>` tag 触发工作流；版本必须匹配核心和四个插件。验证成功后，Release 同时附带五个包的 wheel／源码包、两种架构的框架可执行包、独立插件运行包和校验文件。发布渠道为 GitHub Release，见 [发布说明](docs/releases.md)。

- [架构与职责](docs/plugin-architecture.md)
- [通道和执行回调](docs/channels-and-callbacks.md)
- [Langfuse 追踪与兼容边界](docs/langfuse-tracing.md)
- [数据库迁移](docs/database-migrations.md)
- [SDK 与框架边界](docs/sdk-and-plugin-boundaries.md)
- [插件配置](docs/plugin-configuration-design.md) · [插件生命周期](docs/plugin-lifecycle-design.md)
- [安全策略](SECURITY.md) · [变更记录](CHANGELOG.md)
- [普通对话示例](examples/chat/README.md) · [沙箱示例](examples/sandbox/README.md)

源码插件是受信任的进程内 Python 代码，具有宿主进程的权限。框架的插件启停与文件目录校验不构成恶意代码沙箱。MCP、模型供应商、数据库和观测服务的部署由使用者按实际插件配置负责。

## 许可证

项目采用 [MIT License](LICENSE)。上游来源与随附许可见 [licenses](licenses)。插件携带的外部资源仍须遵守各自许可。
