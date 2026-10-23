# OpsMesh 开发约定

OpsMesh 是基于 OpenAI Agents Python SDK 的 Agent 执行与编排服务。具体生命周期合同见 [.agents/references](.agents/references/README.md)，操作方法见[技能目录](.agents/skills/README.md)。

## 执行规则

- 只有用户明确要求时才运行测试，包括 pytest 收集、验收脚本、真实服务联调和模型调用。技能的默认流程不能代替授权。静态检查可以按改动范围执行。
- 保留现有未提交工作；不自动提交、推送、发布或清空数据库、存储、插件数据、备份。删除代码前核对导入、配置、脚本、文档和打包入口。
- 不为绕过检查添加旧路径包装、兼容别名、生产假实现或静默回退。验证缺口必须明确报告，不能把跳过写成通过。
- SDK 用于系统插件开发、注册和打包，不提供业务 API 客户端。

## 仓库地图

| 路径 | 所有权 |
| --- | --- |
| `src/opsmesh/agents` | 配置、注册表、原生 SDK Agent/SandboxAgent 的组装 |
| `src/opsmesh/execution` | 持久实例、Session、环境、Agent Run、审批和效果回执；不导入 Temporal |
| `src/opsmesh/workflows` | 编排定义、发布、绑定实例、图调度、编排 Run；引擎适配位于 `engines/` |
| `src/opsmesh/plugins` | 包安装、适配器注册、加载代际和热更新 |
| `src/opsmesh/resources` | 存储接口、目录权限、存储命名空间 |
| `src/opsmesh/gateway` | 自研通道、外部身份绑定、消息路由和持久收发 |
| `src/opsmesh/users`、`platform` | 身份、额度、审计和健康探测 |
| `src/opsmesh/temporal` | 公共 Temporal 连接、加密载荷和事务 Outbox |
| `src/opsmesh/bootstrap` | API、Worker、CLI 和跨模块装配 |
| `sdk/python` | 独立插件开发 SDK、打包 CLI 和示例 |
| `deploy/cli`、`deploy` | 独立运维 CLI、镜像、Compose、systemd 和监控 |
| `migrations`、`tests`、`scripts` | 不可变迁移历史、当前合同测试、工程入口 |

## 设计边界

- 优先使用锁定 SDK 的 Agent、Runner、Session、MCP 和 Sandbox 协议，不再实现一套 SDK。模型厂商与外部连接通过注册的适配器提供。
- Agent 配置、持久执行实例和单次 Run 是不同实体。Run 完成或取消不删除 Agent 实例、沙箱、Session 或目录；删除由用户显式请求。
- 纯图调度、配置和值模型不能做网络、数据库或进程 I/O。Temporal Workflow 的 I/O 通过 Activities 和官方 Agents 集成完成；编排适配不回流到 execution。
- 注册只声明资源；插件 `setup(ctx)` 不连接 MCP、不启动进程或执行不可逆操作。存量 Run 不因热更新静默换版本。
- 私有资源按用户所有权与显式授权查询。审批、幂等键、取消、加密、审计、Token/并发额度不能因改动丢失。
- 历史迁移不随当前 ORM 删除。已退役表显式列在 `bootstrap/schema.py`；不能忽略所有未知表来掩盖 schema 漂移。
- 网关内置 QQ 与钉钉；插件实现 `opsmesh_sdk.channels.ChannelAdapter`。独立 Gateway Worker 持有长连接，API 不启动通道。外部群角色不能转换为平台权限。

## 技能与资料

按实际任务读取对应 `SKILL.md` 和必要引用，不要求全部读取。用户指令优先。

| 技能 | 适用任务 |
| --- | --- |
| [openai-knowledge](.agents/skills/openai-knowledge/SKILL.md) | 核实 OpenAI Agents SDK 接口、MCP、Sandbox、模型集成 |
| [temporal-python-sdk](.agents/skills/temporal-python-sdk/SKILL.md) | Workflow、Activity、Worker、取消、恢复、重放及官方 Agents 集成 |
| [code-change-verification](.agents/skills/code-change-verification/SKILL.md) | 源码、工程配置与生成物的静态检查；测试仍需用户要求 |
| [docs-sync](.agents/skills/docs-sync/SKILL.md) | 同步实现、README、部署文档和维护参考 |
| [find-skills](.agents/skills/find-skills/SKILL.md) | 用户寻找或安装外部技能 |

最新在线文档说明上游当前行为，`uv.lock` 和已安装版本源码决定本仓库能调用的接口。资料冲突时写清版本差异，不能凭最新文档直接修改锁定版本的调用。

## 开发与校验

服务端 Python >=3.12；插件 SDK >=3.11.8。新环境使用 `uv sync --frozen --all-groups`。不因文档修改刷新依赖。

```text
uv run ruff format --check <受影响的 Python 路径>
uv run ruff check <受影响路径>
uv run mypy
uv run lint-imports --no-cache
uv run python scripts/audit_app_layout.py --architecture-check
uv run python scripts/export_compute_requirements.py --check
```

改动插件 SDK 时在 `sdk/python` 独立校验其边界。当前测试目录、外部服务要求和覆盖缺口见 [tests/README.md](tests/README.md)；工程脚本见 [scripts/README.md](scripts/README.md)。未授权时不要运行这些文档中的测试和验收命令。

交付说明写清变更、静态检查结果、实际执行过的测试与未验证范围。同步失效路径和示例；HTTP 合同来自实际路由生成的 `/openapi.json`，不维护手写副本。

## 维护参考

本结构参考 [OpenAI Agents Python AGENTS.md](https://github.com/openai/openai-agents-python/blob/main/AGENTS.md) 与其维护参考目录，并按 OpsMesh 实现重写。引入的技能版本和适配说明见 [.agents/README.md](.agents/README.md)。
