# OpsMesh

[![Backend CI](https://github.com/jhupo/OpsMesh/actions/workflows/backend-ci.yml/badge.svg)](https://github.com/jhupo/OpsMesh/actions/workflows/backend-ci.yml)
[![License: LGPL-3.0](https://img.shields.io/badge/License-LGPL--3.0-blue.svg)](LICENSE)

OpsMesh 是企业级 Agent 控制面，提供身份与工作空间、Agent 与团队、任务编排、知识与记忆、MCP 与插件、Runtime、审批、审计和运行运维 API。客户端在独立项目开发，本仓库只交付后端服务、执行运行时和管理工具。

PostgreSQL 保存持久业务事实；Redis 承担队列、锁和事件发布。API 接收请求并保存执行意图，Worker 调度任务；Agent SDK 与用户工具在授权 Runtime 中执行。共享宿主使用物理宿主执行槽、独立执行 UID 与内核网络策略，增加 Agent 或托管 MCP 不自动创建容器。

## 目录

| 路径 | 内容 |
| --- | --- |
| `src/opsmesh/` | 按业务功能组织的运行代码；导入包名 `opsmesh` |
| `tests/` | 产品流程、权限边界与恢复测试 |
| `migrations/` | Alembic 数据库迁移 |
| `runtime/` | MCP 传输、托管进程和自托管连接器 |
| `operator/` | 安装、发布与恢复 CLI |
| `scripts/` | 验证、发布、API 文档导出和对话客户端 |
| `deploy/` | Docker 镜像、Compose、systemd 和可选监控资产 |
| `docs/` | 当前 API、执行合同和配置说明 |

## 开发

需要 Python 3.11.8+、uv；使用容器流程时需要 Docker。安装锁定的 workspace 依赖：

```bash
uv sync --frozen --all-groups
cp deploy/local/env.example .env
docker compose -f deploy/local/compose.yml up --build
```

API 默认位于 `http://localhost:8000/api/v1`。开发环境提供 `/docs`、`/redoc` 和 `/openapi.json`；生产环境关闭在线文档。数据库迁移由独立 migration 服务执行，API/Worker 不在启动时自动升级数据库。

需要在宿主机运行代码时，先启动 PostgreSQL、Redis 并配置 `.env` 的连接地址，再使用：

```bash
uv run alembic upgrade head
uv run uvicorn opsmesh.main:create_app --factory --host 127.0.0.1 --port 8000
uv run python -m opsmesh.runtime.workers.cli
```

启用 Agent 或托管 MCP 前，构建 `deploy/images/Dockerfile.runtime`，将批准的镜像摘要登记到平台白名单和 Runtime 模板，并创建满足权限及网络策略的宿主。详见 [部署](deploy/server/DEPLOYMENT.md) 和 [共享 Runtime](docs/shared-runtime-hosts.md)。

## 验证与接口文档

```bash
uv run ruff check .
uv run lint-imports --no-cache
uv run python scripts/audit_app_layout.py --architecture-check
uv run mypy
uv run pytest tests/test_health.py
uv run python scripts/export_api_docs.py --check
```

接口变更后运行 `uv run python scripts/export_api_docs.py`，提交生成文档；CI 检查它们与注册路由一致。健康测试只覆盖健康接口，功能修改还需受影响的产品流程验证。

- [详细 API 使用说明与完整接口参考](docs/api.md)
- [OpenAPI JSON](docs/openapi.json)
- [文档索引](docs/README.md)
- [服务器部署](deploy/server/DEPLOYMENT.md) 与 [发布分发](deploy/DISTRIBUTIONS.md)
- [贡献规范](CONTRIBUTING.md) 与 [安全政策](SECURITY.md)

许可证见 [LICENSE](LICENSE)、[COPYING](COPYING)、[LICENSE-MIT](LICENSE-MIT) 和 [NOTICE](NOTICE)。
