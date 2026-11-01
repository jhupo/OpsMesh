# 当前状态与后续路线

核对日期：2026-10-31。以下区分已实现行为、实际验证和后续环境验收。Web按需求仅预留目录；不引入企业SSO/OIDC。OpenAI为当前后端，Claude为未来适配位置。

## 本轮平台补齐

后续审查 R01—R08 已实现修复，包含 Bot 权限委托、终态会话恢复、定时重放、通知并发、记忆失效、workspace 恢复和历史补价；行为与验证见[审查修复记录](review-fixes.md)。

原审查F01—F05与平台管理闭环已经实现：受保护HTTP/DI、完整持久计划、事件/Webhook触发、通知收件箱、审批策略、本地账号/Bot/资源治理、费用版本查询及Outbox/插件运维。接口、测试及一致性边界见[平台补齐记录](platform-completion.md)。通用工作流经确认不建设。

## 当前已落地

| 范围 | 已实现行为 | 证据与实际边界 |
| --- | --- | --- |
| 框架与插件 | Driver/Adapter/Bot/Event/Message、Matcher、规则/权限、嵌套Depends/yield、上下文、元数据/require/导入加载/失败回滚/禁用、持久调度 | [框架测试](../tests/framework/)；独立NoneBot风格实现，不是全部现有插件兼容层 |
| 身份与管理 | 本地密码/令牌、用户/部门/角色授权、配置/审计；Agent草稿/工厂/不可变版本/发布克隆、模型档案 | [平台测试](../tests/platform/)、[HTTP测试](../tests/http/) |
| SDK基础与扩展 | HTTP/WebSocket、类型化输出、自定义工具结束/Agent输入、handoff/as-tool、动态指令/Prompt、四阶段护栏/钩子、Hosted MCP、Shell/ApplyPatch/Computer | [原生工具](../tests/runtime/test_native_tools.py)、[高级扩展](../tests/runtime/test_advanced_extensions.py)、[Hosted MCP](../tests/runtime/test_hosted_mcp.py)；真实Runner，可控模型 |
| Run与恢复 | 持久状态/attempt/容量/会话占用、审批/取消/恢复、收尾屏障、RunOverrides冻结配置/指纹、类型化模型设置合并、会话压缩及计费 | [RunOverrides](../tests/runtime/test_run_overrides.py)、[HTTP覆盖](../tests/http/test_run_overrides.py)、[压缩/WS](../tests/runtime/test_compaction_and_websocket.py) |
| 资源与工具 | 授权网关、Git固定commit Skill、eager/lazy装配、隔离脚本/wheel、MCP stdio/HTTP/SSE、向量/词法混合检索与embedding插件 | [资源测试](../tests/resources/)；默认embedding为确定性词/字符hash，不是预训练语义模型；生产可注册embedding |
| 环境与工作区 | 受控命令执行、Run子进程/Docker配置、原生sandbox、工作区快照/session引用、独立子Agent环境/产物、清理、blob GC | [Linux子沙箱](../tests/runtime/test_isolated_sandbox.py)、[来源](../tests/runtime/test_sandbox_sources.py)、[GC](../tests/resources/test_content_gc.py)；Linux真实Docker与远程mTLS通过 |
| 文件记忆 | 平台两阶段生成、native Memory授权/计量/捕获前flush、实时更新/CAS/幂等、审批后回调恢复、namespace retention | [原生Memory](../tests/runtime/test_sandbox_memory.py)、[记忆恢复](../tests/platform/test_live_memory_resume.py)、[生命周期](../tests/resources/test_memory_lifecycle.py)；Linux实际文件闭环通过 |
| 机器人 | 中立适配器注册/生命周期；钉钉Stream+OpenAPI；QQ官方签名Webhook+OpenAPI；独立OneBot兼容；平台身份/Agent路由和投递幂等 | [适配器设计与范围](design/bot-adapters.md)、[协议测试](../tests/adapters/)；没有向真实外部机器人账号发消息 |
| 持久化与可靠性 | SQL迁移/漂移拒绝、CAS、事务Outbox、死信/重投、Redis限流/租约 | [PostgreSQL](../tests/infrastructure/test_postgresql.py)17.11真实临时实例8项通过；Redis5.0.14.1已实测；集群故障待验收 |
| 观测与费用 | JSON日志、有限标签指标/时长、W3C/OTLP、请求/资源用量、版本价格/FX、补价、预算、账单对账 | [OTLP](../tests/infrastructure/test_telemetry_export.py)、[账单](../tests/usage/test_resource_billing.py)；loopback真实协议，不等于供应商账单已验收 |
| 工程与部署 | src布局、MIT/贡献/安全文档、uv锁、CI、CLI/HTTP/SSE、独立Linux安装包、默认本地/systemd、可选Compose/Kubernetes | [Linux安装](../deploy/linux.md)、[部署](../deploy/README.md)；v0.3.1增加Linux安装包，历史v0.3.0不包含此制品；构建脚本生成wheel/哈希依赖清单/部署文件，实际验证以制品CI为准；未发布PyPI或托管生产服务 |

每项详细范围见[88项SDK覆盖表](design/sdk-coverage.md)，完整测试结果见[实施记录](implementation-report.md)。目标架构不能代替实现证据。

## 本次补齐与实际环境验收

- 按Run Kubernetes Job：实际K3s v1.34.1集群，多协调器领取、跨Pod审批恢复、取消、镜像缺失deadline失败和日志回收通过；API使用只读observer权限。
- S3：真实SeaweedFS4.51与PostgreSQL16联测通过；完整离线备份、摘要验证、恢复到新数据库/新目录/新前缀后账号登录、文件/快照/blob均可读取。
- Docker：真实CPU/内存/PID、只读根文件系统、network=none、取消/timeout回收通过；SDK Memory及远程Docker mTLS通过。
- 模型：授权gateway的gpt-6.1-sol真实Responses审批重启/工具幂等及流式结构化输出通过。未配置供应商单价，费用标记unpriced，不伪造0费用。
- 目录：公开Adapter/Agent合同直接归所属包；删除8个未实现叶子包的占位源码，工程检查阻止此类空包进入发行物。

## 仍属于部署与扩展范围

平台服务与可选插件须按职责区分：Agent管理、Bot/频道绑定、会话运行、知识检索和持久调度已有实现；聊天管理命令、日报/巡检规则可以由插件调用这些服务。通知订阅、模板、收件箱及Bot渠道已实现；通用工作流排除，邮件等新增渠道为后续扩展。持久调度已从旧plugins路径移到`modules/scheduling`。逐项状态见[平台与插件职责](design/plugins.md#内置插件的实际状态)。

真实外部机器人账号/企业MCP与特定云厂商凭据、供应商账单、数据库复制与主从切换、Redis Cluster/Sentinel、跨物理主机故障及长期负载需要对应部署环境。单台测试VM上的K3s/多进程结果不宣称跨主机高可用或RTO/RPO保证。Web与Claude按约定不在当前实现范围。

## 后续扩展

- Claude Agent SDK实现中立后端，并独立声明工具、审批、会话和恢复能力；OpenAI原生检查点不跨SDK复用。
- 新Bot Adapter复用现有Driver、Event/Message、Matcher和DI。Web门户/控制台已有预留位置，仍未实现。
- 本地/S3不可变内容存储、完整备份恢复、远程mTLS Docker及逐Run Kubernetes Job已实现；新增云厂商Provider需要单独适配和验收，活动工作区仍使用POSIX目录。
- 大规模运行按真实负载增加模块专用索引、分页、数据分区与审计/指标归档；当前JSON聚合、共享POSIX内容目录适合验证当前模型与接口。
- SDK新增能力按固定版本、支持矩阵、可验证行为和拒绝路径逐项扩展。Realtime/Voice属于独立SDK子系统，不默认声称文本Runner已经覆盖。

## 显式环境测试

PostgreSQL：设置`OPSMESH_TEST_POSTGRES_INTEGRATION=1`与`OPSMESH_TEST_POSTGRES_BIN`指向本机二进制目录，运行`tests/infrastructure/test_postgresql.py`。测试创建随机密码和loopback端口的临时cluster，停止自己的进程。

Redis：设置`OPSMESH_TEST_REDIS_INTEGRATION=1`，运行`tests/infrastructure/test_cache.py`；仅启动自己的临时实例。

Linux native sandbox：设置`OPSMESH_TEST_SANDBOX_INTEGRATION=1`，运行`test_sandbox_memory.py`、`test_isolated_sandbox.py`和`test_sandbox_sources.py`。测试使用真实SDK、UnixLocal客户端和实际文件，模型为可控响应。

所有生成与执行资源保留明确owner；未知外部副作用不自动重放。预算采用预占并记录实际花费，不保证请求已发出后费用不会超过估算。
