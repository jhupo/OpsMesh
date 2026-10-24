# 工作区验收记录

## 0.1.2 本轮验证

日期：2026-10-23。验证使用本机 Windows / Python 3.13，以及本次创建的独立
PostgreSQL 18 测试实例与数据库；未连接或修改生产数据库。

| 检查 | 本轮结果 |
| --- | --- |
| `pytest tests deploy/cli/tests sdk/python/tests -ra` | 311 passed，12 skipped，63.44 秒 |
| PostgreSQL 网关与迁移 | 上述回归包含真实空库迁移/ORM 比较、加密旧配置转换、未完成批次恢复和未排空时拒绝升级 |
| 新增审核与群身份用例 | 逐条原文审核、精确撤回、审核失败阻断、共享群路由、多人身份、配置变化重新审核及慢任务独立调度 |
| SDK 独立测试 | 7 passed；注册、归档与过滤器合同 |
| Ruff、mypy、import-linter、架构审计 | 服务端与 SDK 通过；360 个类型检查文件、350 个服务端文件、56 个唯一表所有者；11 + 1 条依赖边界 |
| Compute 生成物与版本发布策略 | 通过；没有引入额外计算环境依赖 |
| 工作区发行包 | 三个包的 wheel/sdist 构建通过；主 wheel 的 350 个源码文件逐项一致，隔离目录 API、Worker 导入和 56 张模型表通过 |
| TypeSafe 审核插件 | ZIP 构建、清单检查、动态加载和 message_filter 资源注册通过；没有调用真实厂商接口 |

12 个跳过项包括 Windows 不支持的 POSIX/宿主维护、真实 Docker、Temporal 和模型配置。
这些项目须由 Linux CI/发布门禁验证，不能记为本地通过。HTTP mock 验证 Jev 协议、代理
参数、图片和响应校验，不证明生产代理或真实模型可用；本轮也未重跑真实 QQ/钉钉。
发行 wheel、原生归档、镜像和托管安装以对应提交的 CI 及 tag 流水线结果为准。

## 0.1.1 基线记录

以下为上一轮工作区的历史验收，不能代替 0.1.2 新产物验证。对象包含网关重构、迁移
`0006_gateway_processing`、执行和编排集成调整。
本记录不是已经发布的 0.1.0 二进制或镜像的验收报告。

## 环境与边界

- 本机 Windows / Python 3.13；服务器 Linux / Python 3.14。
- 服务端、SDK 和运维 CLI 使用当前工作区源码；服务器复用匹配版本的依赖环境。
- PostgreSQL 使用新建的隔离测试数据库和角色；测试会清空该数据库的功能表。
- Temporal 使用真实服务和独立测试 Workflow ID；Docker 使用已有 compute 验收镜像。
- 锁定依赖：openai-agents 0.22.3、temporalio 1.34.0、
  temporalio-openai-agents 1.1.0、openai 3.28.0、mcp 2.3.0。
- 真实模型使用用户指定的 gpt-6.1-sol；凭据只通过测试环境变量注入。
- 未替换生产服务，未修改生产数据库，未发布 Git 引用或发行包。

## 执行证据

| 检查 | 结果 |
| --- | --- |
| Windows 服务端 `python -m pytest -ra --tb=short` | 220 passed，66 skipped；缺少本地基础设施的跳过项不能算通过 |
| Linux 服务端首次全量测试 | 274 passed，1 skipped；当时尚未添加网关和 Agent 端到端用例，真实模型单独执行 |
| Linux 修复后全量回归 `pytest -m 'not openai_smoke' -ra --tb=short --durations=5` | 283 passed，3 deselected，97.15 秒；3 项真实模型用例单独执行，见下文各自结果 |
| Linux 网关 `pytest tests/gateway/test_processing_postgres.py` | 9 passed，真实 PostgreSQL |
| Linux SDK `pytest`，在 sdk/python 执行 | 6 passed |
| Linux 运维 CLI `pytest`，在 deploy/cli 执行 | 17 passed |
| 真实模型 `pytest tests/execution/test_native_model.py` | 1 passed；Responses 工具调用、私有 SQLiteSession 恢复、用户隔离和清空历史 |
| Temporal 持久 Agent 流式执行和重放 | passed；实际模型增量入库、最终输出、Session 保存、实例仍 active、同版本历史重放 |
| Temporal 持久 Agent 普通执行及重放 | 独立复核 1 passed，9.69 秒；此前出现 TLS 连接失败和 HTTP 524，见下文限制 |
| 数据库迁移 | 空库升级到 head 和 ORM 比较通过；0006 降至 0005 后重新升级通过 |
| 干净 wheel 构建及 `scripts/check_python_package.py` | 通过；348 个源文件与当前源码逐项一致，隔离目录 API、Worker 导入和 56 张模型表通过 |
| TypeSafe 示例插件 | ZIP 构建、清单检查和本机官方 SDK 导入通过；未调用真实 Jev API |
| Ruff、mypy、import-linter、架构审计 | 通过；服务端 357 个类型检查文件，SDK 9 个；11 条服务端边界与 1 条 SDK 边界 |
| Compute 依赖生成物、Git 空白检查 | 通过 |

服务器全量测试覆盖真实 Docker 的嵌套写文件、执行 Python、工作区恢复、
符号链接与 FIFO 拒绝、有界读取，以及取消命令等待结束且不删除共享环境。
Temporal 加密载荷用例使用真实服务，检查历史中无明文输入并执行重放。
这些边界用例不等于完整 SandboxAgent 产品流程已经通过。

新增网关用例验证持久合批和恢复、普通消息 FIFO 与可中断控制命令、
重复收件、租约校验、最终回复丢弃未发送占位提示、结果不确定的投递对账、
跨 Outbox 实例限流、流式前缀与增量合并、群广播去重与幂等，以及解绑后拒绝历史重用。
这些用例使用真实数据库和合成厂商事件，不连接 QQ、钉钉。

## 发现与处理

1. 网关审计使用未注册的 gateway 来源，导致创建通道或绑定身份时事务回滚。
   修正为 platform 来源，保留 gateway 分类，并通过真实数据库用例验证。
2. 审批通知单元测试未跟进 Workflow 初始化参数和 WorkflowStream 初始化边界。
   更新测试入口；审批通知早于等待仍可被消费。
3. 本地旧 build 缓存混入已删除模块，首次 wheel 检查失败。保留旧缓存到临时目录，
   从干净 build 目录重建后通过文件集合与内容校验；源码中未恢复任何兼容模块。
4. 普通 Temporal 模型调用发生外部连接失败。关闭 SDK 重试时出现 TLS 连接失败，
   启用 2 次重试后一次调用收到 HTTP 524 并耗尽请求时限，Run 保持
   needs_attention / external_effect_unknown。随后保持相同配置独立复核通过，
   普通/流式模型执行与同版本重放均有成功记录，但不能据此保证上游可用性。

## 仍未验收

- QQ、钉钉真实收发、扫码、重连、流式结束帧和厂商主动消息额度。
- TypeSafe / jev-latest 经指定代理的真实决策流程；本次没有可用的专用代理配置。
- 其他外部 MCP 的安装热更新、连接恢复和真实权限变化。
- SandboxAgent 完整创建、模型写代码、审批恢复、取消与 Worker 重启组合流程。
- 复杂编排的并行/循环、节点审批、故障恢复，以及完整 API 到通道回复的链路。
- 重构前存量 Temporal 历史的跨版本兼容性；同版本重放不能替代此项。
- 本次工作区生成的 standalone 二进制、Docker 发行镜像及生产部署升级。

上述缺口解决前，不应将本次结果描述为全功能验收或直接发布许可。
