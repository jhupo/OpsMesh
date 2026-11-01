# opsmesh

异步事件驱动、插件可扩展的 Agent 与机器人平台。以 OpenAI Agents SDK 为首个执行后端，通过独立的 AgentBackend 接口保留接入其他 SDK 的边界。

[English](README.en.md) · [v0.3.1 发布说明](docs/releases/v0.3.1.md) · [设计与实现状态](docs/README.md) · [HTTP 使用](docs/runtime-api.md) · [贡献指南](CONTRIBUTING.md) · [MIT](LICENSE)

## 当前功能

`0.3.1` 是供开发者试用的早期版本，已实现可运行的 Python 框架与平台后端：

- NoneBot2 风格的 Driver、Adapter、Bot、Event、Message、Matcher、插件元数据、Depends 和上下文。
- 本地用户、部门、角色权限、Agent 草稿/不可变版本、模型配置与代码工厂。
- OpenAI Agents SDK 执行、Responses HTTP/WebSocket、类型化输出、handoff/Agent-as-tool、四阶段护栏、钩子、审批恢复和会话压缩；Hosted MCP 与原生 Shell/ApplyPatch/Computer 扩展。
- 固定 Git commit 的 Skill、隔离脚本工具、MCP stdio/HTTP/SSE、向量/词法混合检索、授权工具网关、工作区与快照；独立子沙箱、原生和平台两阶段记忆、实时记忆更新与保留清理。
- HTTP 管理/运行入口、SSE、Run 单次工作区/模型设置覆盖；统一机器人适配器、钉钉 Stream、QQ 官方 Webhook、持久定时任务；独立保留 OneBot/NapCat 兼容实现。见[机器人接入](docs/design/bot-adapters.md)。
- PostgreSQL/SQLite、版本迁移、事务 Outbox、Redis 限流/租约；本地/S3内容存储和完整备份/验证/恢复CLI；审计、日志、指标、W3C/OTLP 追踪，Token/资源计量、版本价格、汇率、补价、预算和供应商账单对账。
- inline、独立 Python 子进程、Docker、逐 Run Kubernetes Job 四种 Run Worker 方式。原生 SDK 沙箱与整个 Run Worker 的隔离分别配置。

Web 门户和控制台仅预留目录；Claude 后端为后续适配。已在独立 Linux 环境验证 PostgreSQL、Redis、S3 工作区快照与完整备份恢复、Docker 资源限制/取消、SDK Memory、远程 Docker mTLS、真实模型审批重启/流式输出，以及 K3s 逐 Run Job。默认测试使用受控模型，付费模型测试显式启用。生产多节点故障转移和真实机器人账号仍需部署验收。逐项证据见[覆盖表](docs/design/sdk-coverage.md)。

## 快速开始

本版本替换了仓库原 `0.2.x` 及更早版本的架构。旧数据库、配置、插件及检查点不能直接沿用；请先备份旧部署，在新目录和独立数据库中安装，按[发布说明](docs/releases/v0.3.0.md)重新配置。

### Linux 服务安装

从 [v0.3.1 发布页面](https://github.com/jhupo/OpsMesh/releases/tag/v0.3.1)下载 `opsmesh-0.3.1-linux.tar.gz` 及对应 `.sha256` 文件，校验、解压后默认安装本地 systemd 服务：

```sh
sha256sum --check opsmesh-0.3.1-linux.tar.gz.sha256
tar -xzf opsmesh-0.3.1-linux.tar.gz
cd opsmesh-0.3.1-linux
sudo bash install.sh
sudo systemctl status opsmesh.service --no-pager
sudo journalctl -u opsmesh.service -f
```

安装包包含 wheel、带哈希的运行依赖清单和部署文件，不携带 Python、`.venv` 或整套源码仓库。安装时优先复用服务用户可访问的本机 Python 3.11–3.14；没有适用解释器时在线获取 Python 3.13，再创建虚拟环境并安装锁定依赖。依赖安装需要联网。

默认使用本地 SQLite，配置保存在 `/etc/opsmesh/opsmesh.env`，数据保存在 `/var/lib/opsmesh`。首次输入管理员密码后即可访问 `http://127.0.0.1:8000/docs`，无需 Docker 或 Kubernetes。需要 Docker 部署时显式执行 `sudo bash install.sh --mode docker`。两种模式的启停、升级、多实例及备份操作见 [Linux 安装指南](deploy/linux.md)。原 `v0.3.0` 历史发布没有该 Linux 安装包；开发者可用 `uv run python scripts/build_linux_release.py --output-dir dist/linux` 从当前源码构建。

### 手动安装发行包

支持范围以测试记录为准，项目要求 Python 3.11+。推荐在独立虚拟环境中安装。下面的发行包命令直接使用 GitHub Release，不要求 PyPI 上存在同名包。

```sh
python -m venv .venv
```

Linux/macOS 执行 `. .venv/bin/activate`，Windows PowerShell 执行 `.venv\Scripts\Activate.ps1`，然后安装并启动：

```sh
python -m pip install "opsmesh[openai] @ https://github.com/jhupo/OpsMesh/releases/download/v0.3.1/opsmesh-0.3.1-py3-none-any.whl"
opsmesh --version
opsmesh init
opsmesh check
opsmesh serve
```

`openai` extra 安装 OpenAI Agents SDK 后端及其追踪依赖；省略它可以安装不包含该 SDK 的框架和平台核心。需要 S3 内容存储时安装 `[openai,s3]`。默认使用本机 SQLite 和 `.opsmesh/` 数据目录，无须先部署数据库。

从源码开发需要 uv 0.13.5。在本版本源码目录执行：

```sh
uv sync --locked --dev --extra openai
uv run --extra openai opsmesh init
uv run --extra openai opsmesh serve
```

`init` 提示输入本地管理员密码（至少 12 字符）。启动后访问 `http://127.0.0.1:8000/docs`，按 [HTTP 使用说明](docs/runtime-api.md)登录、配置模型、发布 Agent、创建会话并提交 Run。仅提交真实模型任务时才需要提供商凭据。

配置通过 `OPSMESH_*` 环境变量读取；`.env` 不自动加载。源码方式可使用 `uv run --env-file .env ...`，发行包方式应先设置进程环境变量。可选部署示例见 [deploy](deploy/README.md)。`opsmesh migrate` 管理本架构登记的 schema，不能升级原 `0.2.x` 数据库；`serve` 只校验 schema。

```sh
uv run opsmesh check
uv run opsmesh --version
uv run --extra openai opsmesh worker
```

独立部署 Worker 时，Gateway 设置 `OPSMESH_WORKER_ENABLED=false`，Worker 设置为 `true`；两者使用相同数据库、存储和插件代码版本。不要在已有同配置 Worker 的环境无意启动重复消费者。持久队列支持多消费者，通知与外部副作用按稳定 ID 幂等。

## 插件与目录

[可运行插件示例](examples/custom_plugin/README.md)展示声明式注册、事件响应、Depends 资源释放与 Agent 工厂。插件可注册代码工具、动态指令、护栏、钩子和后端；HTTP 入口只引用已部署的代码能力。

```text
src/opsmesh/
├── bootstrap/       # 部署配置、启动和服务组装
├── application/     # 跨模块运行流程、队列协调、资源/记忆桥接
├── entrypoints/     # CLI、HTTP 各域路由、单 Run Worker入口
├── framework/       # NoneBot 风格插件机制与中立 Agent 合同
├── modules/         # 17 个业务域，api.py 公开合同，internal/ 私有实现
├── infrastructure/  # bots、run_workers、数据库/消息/存储/协调/观测
├── maintenance/     # 离线整站备份、验证和恢复
├── dependencies.py  # 插件通过 Depends 获取平台公开服务
└── plugins/         # 可选扩展命名空间、旧调度路径兼容导出
```

详细职责、目标文件树和不兼容边界见[架构设计](docs/design/architecture.md)。OpenAI SDK 的构建、工具、模型、流、状态、沙箱、追踪、错误映射分文件维护，原生 SDK 类型不进入业务合同。

## 开发检查

```sh
uv run --extra openai ruff check .
uv run --extra openai ruff format --check .
uv run --extra openai mypy
uv run --extra openai pytest
uv run python scripts/check_repository.py
uv run --extra openai python scripts/check_sdk_surface.py
uv build
uv run python scripts/check_sdist.py
```

CI 定义了 Windows/Linux 与 Python 3.11-3.14 矩阵；以[对应提交的实际运行结果](https://github.com/jhupo/OpsMesh/actions)为准。版本包在 [GitHub Releases](https://github.com/jhupo/OpsMesh/releases)发布；本项目不提供托管服务，GitHub Release 不等于已发布到 PyPI。


平台管理与本轮补齐范围见[平台补齐记录](docs/platform-completion.md)：持久计划、事件触发、通知、审批、账号/Bot/资源治理和运维入口均属于平台核心；业务插件使用受保护Provider扩展。
