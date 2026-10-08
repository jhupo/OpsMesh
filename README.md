# OpsMesh

[![Backend CI](https://github.com/jhupo/OpsMesh/actions/workflows/backend-ci.yml/badge.svg)](https://github.com/jhupo/OpsMesh/actions/workflows/backend-ci.yml)
[![License: LGPL-3.0-only](https://img.shields.io/badge/License-LGPL--3.0-only-blue.svg)](LICENSE)

OpsMesh 是一个开源的企业级 Agent 控制面，为工作空间、Agent、团队、任务编排、能力、隔离执行、审批、审计和运行运维提供统一后端。

后端是当前主要产品面；Web Portal 使用 React、TypeScript、TanStack Router、TanStack Query 和 shadcn/ui，通过版本化 API 合同连接后端。

## 当前能力

- 用户认证、API Token 和工作空间 RBAC
- Agent Profile、Team、Task、Run、Approval 和事件流
- Skill、Tool、MCP、插件和 Marketplace 能力治理
- Docker/自托管 Runtime、Worker、队列和资源控制
- 工作空间文件、Artifact、知识、记忆、通知、成本和审计
- 平台管理员的跨工作空间管理接口

## 快速开始

环境要求：

- Python 3.11.8 或更高版本
- [uv](https://docs.astral.sh/uv/)
- Docker 和 Docker Compose
- Node.js 与 pnpm（开发 Web Portal 时需要）

安装 Python 依赖并运行基础检查：

```bash
uv sync --all-groups
uv run ruff check .
uv run mypy
uv run pytest backend/tests/test_health.py
```

在 PowerShell 中创建本地配置并启动完整开发栈：

```powershell
Copy-Item deploy/local/env.example .env
docker compose -f deploy/local/compose.yml up --build
```

API 默认地址为 `http://localhost:8000`：

```bash
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/health/ready
```

单独运行 API：

```bash
uv run uvicorn backend.app.main:create_app --factory --reload --host 0.0.0.0 --port 8000
```

## Web Portal

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

开发服务器默认把 `/api` 转发到 `http://127.0.0.1:8000`。远程后端可以通过 `VITE_API_PROXY_TARGET` 配置目标地址；浏览器 API 前缀默认是 `/api/v1`。

前端检查：

```bash
pnpm lint
pnpm typecheck
pnpm build
```

## API

开发环境启用 OpenAPI 时：

- Swagger UI：[`/docs`](http://localhost:8000/docs)
- OpenAPI JSON：[`/openapi.json`](http://localhost:8000/openapi.json)
- 接口索引：[docs/api.md](docs/api.md)

所有工作空间资源都带有工作空间范围。长任务由 API 持久化并入队，Worker 在隔离 Runtime 中执行；API 不在请求线程内运行 Agent。

## 部署

- 本地开发 Compose：`deploy/local/compose.yml`
- 服务器 Compose、systemd 和监控：`deploy/server/`
- 自托管 Worker：`runtime/`
- 发布分发说明：`deploy/DISTRIBUTIONS.md`
- 服务器部署说明：`deploy/server/DEPLOYMENT.md`
- 自托管连接器说明：`runtime/CONNECTOR.md`

## 贡献

请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [AGENTS.md](AGENTS.md)。

提交改动前至少运行受影响的 lint、类型检查和产品流程测试。涉及租户隔离、凭据、Runtime、审批或管理员权限的改动必须同时验证允许和拒绝路径。

## 安全

安全问题请按照 [SECURITY.md](SECURITY.md) 私下报告，不要直接创建公开 Issue。

## 许可证

OpsMesh 平台、Operator 和 Runtime 使用 [LGPL-3.0-only](LICENSE)。仓库同时保留 [COPYING](COPYING)、[LICENSE-MIT](LICENSE-MIT) 和 [NOTICE](NOTICE) 中声明的第三方许可和版权信息。
