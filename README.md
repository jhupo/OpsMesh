# OpsMesh

[![Backend CI](https://github.com/jhupo/OpsMesh/actions/workflows/backend-ci.yml/badge.svg)](https://github.com/jhupo/OpsMesh/actions/workflows/backend-ci.yml)
[![License: LGPL-3.0](https://img.shields.io/badge/License-LGPL--3.0-blue.svg)](LICENSE)

OpsMesh 是企业级 Agent 控制面，提供身份、工作空间、Agent、团队、编排、知识与记忆、MCP 与插件、审批、审计和运维 API。客户端在独立项目开发，本仓库交付后端、SDK 沙箱环境与管理工具。

API 在 PostgreSQL 短事务中保存业务状态和执行意图；提交通知驱动 Outbox 向 Temporal 投递。Temporal 是唯一持久执行引擎，Worker 运行原生 Workflow/Activity；Agent 使用官方 Temporal OpenAI Agents 集成、原生 Session、工具、handoff 和审批恢复。用户代码通过 SDK SandboxClient 执行。Redis 保留缓存等短期用途，不承载第二套任务队列。

本分支使用 Temporal 与原生 OpenAI Agents SDK，尚未更新生产服务器。已实现与尚未验证的范围见 [架构与验收记录](docs/architecture.md)。部署命令描述新源码，不表示某台服务器已完成切换。

## 目录

| 路径 | 内容 |
| --- | --- |
| `src/opsmesh/` | feature-first 运行代码，统一 `opsmesh.*` 导入 |
| `tests/` | 产品流程、权限、并发、恢复测试 |
| `migrations/` | Alembic 升降级迁移 |
| `operator/` | 安装、升级和恢复 CLI |
| `scripts/` | 验证、发布、API 导出与 Python 对话客户端 |
| `deploy/` | 独立 API/Worker 镜像、SDK compute 镜像、Temporal、systemd 与可选监控 |
| `docs/` | API 合同、配置、架构和验收记录 |

## 开发

需要 Python 3.11.8+、uv、PostgreSQL/pgvector、Redis 和 Temporal。容器流程需要 Docker：

```sh
uv sync --frozen --all-groups
cp deploy/local/env.example .env
docker build -f deploy/images/Dockerfile.runtime -t opsmesh-runtime:local .
docker compose -f deploy/local/compose.yml up --build
```

API 默认 `http://localhost:8000/api/v1`。开发环境提供 `/docs`、`/redoc`、`/openapi.json`；生产关闭在线文档。数据库迁移使用独立命令，API/Worker 启动不自动升级。

宿主机开发先配置 PostgreSQL、Redis、持久 Temporal 和 namespace，再分别运行：

```sh
uv run alembic upgrade head
uv run uvicorn opsmesh.main:create_app --factory --host 127.0.0.1 --port 8000
uv run python -m opsmesh.runtime.temporal.cli
```

模型路由、专家分工、工具、审批及业务编排由用户配置。框架不预置强制 Manager、总结模型或外层循环。SDK Session 直接管理历史，平台不设置隐式压缩包装或阈值。

## 验证

```sh
uv run ruff check .
uv run lint-imports --no-cache
uv run python scripts/audit_app_layout.py --architecture-check
uv run mypy
uv run python scripts/export_api_docs.py --check
uv run python scripts/export_compute_requirements.py --check
```

产品流程测试使用独立真实 PostgreSQL，配置 `OPSMESH_TEST_POSTGRES_URL`。原生 Temporal 流程按 `OPSMESH_TEST_TEMPORAL=1` 启用；外部模型、原生 Docker 和跨机器验证分别验收。健康测试、静态检查和跳过测试不能代替完整流程证据。

- [API 使用说明](docs/api.md) 与 [完整参考](docs/api/README.md)
- [OpenAPI JSON](docs/openapi.json) 与 [文档索引](docs/README.md)
- [服务器部署](deploy/server/DEPLOYMENT.md) 与 [发布分发](deploy/DISTRIBUTIONS.md)
- [贡献规范](CONTRIBUTING.md) 与 [安全政策](SECURITY.md)

许可证见 [LICENSE](LICENSE)、[COPYING](COPYING)、[LICENSE-MIT](LICENSE-MIT) 和 [NOTICE](NOTICE)。
