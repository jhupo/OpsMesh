# 当前测试与验证范围

只有用户明确要求时才在本地运行测试，包括收集、真实基础设施验收及模型调用。此目录不是重构完成或可发布的证明；CI 配置也不能替代实际执行证据。

## 保留的合同

| 目录 | 范围与外部要求 |
| --- | --- |
| `agents` | 原生模型工厂的重试参数与时限约束；不调用外部模型 |
| `bootstrap` | 宿主 Agent/模型目录、API 装配、管理员初始化、Worker 生命周期、指标、登录维护、备份静默检查；部分需要 PostgreSQL/Redis |
| `engineering` | 迁移链与当前 ORM、CI 选择、沙箱持久路径；迁移比较需要 PostgreSQL |
| `execution` | 模型真实用量回执、公开 handoff 与内部工具 Agent 的流式边界、插件热更新后的审批边界、工作区 manifest/Skill 准备、容器身份和持久目录、真实 Docker、原生 SDK 模型/工具/SQLiteSession；模型 smoke 需要独立凭据 |
| `gateway` | 原始消息审核、过滤器凭据/超时、逐条身份、原生消息输入、共享群路由、群激活、结构化回复、会话隔离和独立调度；真实 PostgreSQL 覆盖去重、合批恢复、审核隔离、FIFO、限流、投递对账、广播与路由撤销；媒体本地存储需要 POSIX |
| `platform` | 审计隔离、公开 HTTP 合同、配置发布/回滚、金额预留结算、触发与 Outbox 幂等、数据集版本；部分需要 PostgreSQL |
| `users` | 身份、额度、删除与头像；部分需要 PostgreSQL |
| `shared` | 通用安全、日志脱敏、审计合同和底层机制 |
| `temporal` | Outbox、加密载荷和重放；持久 Agent 普通/流式执行及重放用例需要真实 PostgreSQL、Temporal 和模型配置，标记为 openai_smoke |
| `../sdk/python/tests` | 独立插件开发 SDK、注册及打包 |
| `../deploy/cli/tests` | 运维升级、恢复和插件安装 |

`conftest.py` 只使用显式配置的 `OPSMESH_TEST_DATABASE_URL`，迁移到 head 后在测试间清空当前及已知退役表。必须使用一次性测试数据库。Temporal/Docker 及模型场景的环境变量由对应文件声明；缺失时跳过不代表通过。

## 清理与覆盖缺口

旧顶级 providers、capabilities、Agent revision 与 `workflows/temporal` 的测试和产品验收脚本已删除；对应生产入口已经退役。保留登录维护和备份合同并迁移其依赖，不维护兼容路径。

新版 execution 实例与 workflow 绑定实例仍缺少完整产品集成覆盖：SandboxAgent 创建及恢复、冻结绑定授权、并行/循环、节点审批与对账、MCP 热更新和连接恢复、用户取消、Worker 重启及跨版本历史重放。普通 Agent 的实例创建、Session 持久性、普通/流式模型执行与同版本重放有真实集成用例。SDK smoke、纯等待节点发行验收与存储加密重放只覆盖各自边界，不能代替其他场景。

平台管理新增合同不代表已执行：还需在真实服务上验证迁移、原生 Schedule 同步/暂停、签名 Webhook、评测取消及审批恢复、SSE 会话撤销与续传、预算并发争用和模型重试。实现与接口说明见 [平台管理与自动化](../docs/platform-management.md)。

发行 CI 保留真实模型 smoke 和 Docker MCP 检查，并拒绝缺失的模型配置；旧 24 场景产品验收已撤下，因此当前自动检查的覆盖范围比旧目标小。补齐上述集成验收前，不应宣称完整产品流程已经验收。
