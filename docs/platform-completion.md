# 平台功能补齐与验收

更新日期：2026-10-31。此记录承接[平台审查](platform-capability-audit.md)，以实际代码、公开服务与行为测试为准。原审查保留作历史问题证据。

本次范围是机器人与 Agent 平台的基础管理和执行闭环。按确认的范围，不建设通用工作流引擎；不增加 OIDC/企业 SSO；Web 仍只预留，Claude SDK 仍是后续后端。

## 模块与边界

```text
src/opsmesh/
├── framework/                 # Driver、Adapter/Bot、Event/Message、Matcher、Depends、插件、Agent合同
├── modules/
│   ├── accounts/              # 本地身份、凭据/登录会话、部门、角色及可委派权限
│   ├── agents/                # 草稿、版本、发布、克隆和启停
│   ├── models/                # 模型版本、撤回及依赖影响
│   ├── bots/                  # 连接账号、外部身份映射、Agent频道绑定和可靠投递
│   ├── scheduling/            # date/interval/Cron计划、租约、补跑、并发及执行记录
│   ├── triggers/              # 持久事件规则、签名Webhook、输入映射、幂等及Run关联
│   ├── sessions/              # 会话归属、历史、关闭及执行占用
│   ├── runtime/               # Run状态、审批待办/策略/决定、恢复及SDK适配
│   ├── capabilities/          # Tool/Skill/MCP/脚本版本治理、知识集合/文档/索引任务
│   ├── execution/             # 本地/Docker执行限制与环境提供商
│   ├── workspaces/            # 文件、目录、制品、Manifest、快照及保留
│   ├── memory/                # namespace权限/策略、版本、生成任务及保留
│   ├── notifications/         # 订阅、模板、收件箱、Bot通知、审批提醒和投递证据
│   ├── operations/            # Outbox人工恢复、插件部署策略与实例同步状态
│   ├── usage/                 # 计量、预算、价格/汇率及对账查询
│   ├── audit/                 # 操作证据与个人/全局/系统查询
│   └── configuration/         # 版本化设置与运行准入
├── bootstrap/                 # 跨域授权、资源引用、调度/触发桥接和服务组装
├── entrypoints/http/routes/    # HTTP薄路由、输入校验及响应
├── infrastructure/            # PostgreSQL/SQLite、Redis、Outbox、内容存储及观测
├── dependencies.py            # 普通函数形式的受保护管理服务与代码扩展Provider
└── plugins/                   # 可选业务扩展；平台核心不依赖插件导入
```

模块管理入口要求显式 `actor_id`（知识检索等原有接口保留 `principal_id`），同时执行授权与操作审计。`Depends` 返回管理服务；后台运行器、原始存储、模型计量写入和通知投递循环留给组装根。注册代码工具、后端、钩子及 embedding 的 Provider 是受信部署代码扩展口，不当作普通用户 CRUD。

平台支持 `plugins=()`。NoneBot 风格的导入声明、Matcher、Depends、上下文及插件元数据继续保留，未添加第二套插件注册框架。Python 插件本身是受信代码；管理门面减少正常命令处理遗漏鉴权，不是恶意 Python 的隔离沙箱。

## 原审查逐项闭环

| 问题或能力 | 实际实现 | 回归证据 |
| --- | --- | --- |
| F01 调度代身份绕过 | 创建/更新/立即触发/实际执行共享主体、代运行、Agent版本和资源校验；解析并固定发布引用 | `tests/scheduling/` |
| F02 批次租约时钟失效 | 每次领取使用当前时钟、续租、CAS收尾、历史记录及失主保护 | `tests/scheduling/` |
| F03 角色自提权 | 分离人员维护、角色管理/分配和凭据权限；可委派范围限制、最后管理员保护和事务Outbox | `tests/accounts/`、`tests/http/test_accounts_management.py` |
| F04 入口权限不一致 | HTTP/Depends统一受保护管理服务，Run提交继续通过应用准入和资源授权 | `tests/http/test_management_boundaries.py`、`tests/runtime/` |
| F05 系统审计不可查 | `scope=mine/all/system`，独立授权，支持action/resource/outcome/时间及分页 | `tests/http/test_management_boundaries.py` |
| 定时任务 | CRUD、启停、立即触发、预览、Cron/IANA、漏触发策略、并发排队/跳过、输入/RunOverrides、会话策略、回复目标、每次触发与Run状态 | `tests/scheduling/`、`tests/platform/test_scheduling.py` |
| 事件触发 | 精确事件源/类型、字段相等过滤、输入映射、Agent目标、签名Webhook、持久去重及执行记录 | `tests/triggers/` |
| 审批 | 跨Run待办、指定审批人、是否允许owner、原因要求、截止时间、决定actor与时间、提醒、到期阻断 | `tests/http/test_approvals_management.py`、`tests/runtime/` |
| 账号与Bot | 改密/重置、登录会话撤销、角色/部门调整；Bot连接配置版本变更与重连、连接状态、身份映射查询/解绑 | `tests/accounts/`、`tests/bots/` |
| 模型和能力 | 版本清单、独立可用性/CAS、撤回原因与影响查询；模型下一次请求、工具调用与MCP连接重新检查 | `tests/resources/`、`tests/platform/test_agent_bindings.py` |
| 知识 | 集合/文档CRUD、文本/Markdown分块导入、幂等任务、失败重试、索引重建、向量内容摘要/CAS | `tests/resources/`、`tests/http/` |
| 工作区 | 授权列表、tree/stat、文件删除/改名、快照列表/删除、创建/恢复和所属主体 | `tests/resources/`、`tests/http/` |
| 记忆 | namespace列表及ACL/生成/模型/保留策略在线更新、撤权、版本冲突；原有正文和生成能力继续可用 | `tests/resources/`、`tests/http/` |
| 通知 | 每用户持久收件箱、订阅启停/模板/Bot目标、失败/审批通知、提醒、已读、失败重试及不确定状态 | `tests/notifications/`、`tests/http/` |
| Outbox运维 | 待投/已投/死信元数据查询、人工重投和必填原因、授权及恢复审计 | `tests/operations/` |
| 费用运营 | 模型/资源价格、汇率版本和对账结果查询；既有计量/补价/预算不重复实现 | `tests/http/test_management_boundaries.py`、`tests/usage/` |
| 插件部署 | 部署清单、版本/依赖、持久启停策略、依赖约束、实例心跳与期望版本差异 | `tests/operations/` |

## 执行语义

计划默认每次创建独立会话，可选择共享会话或指定既有会话。计划触发提交后显示 `submitted`，完成状态从实际 Run 投影，不把提交成功显示成任务完成。一次执行保留稳定的 occurrence/event/idempotency 身份。并发策略针对仍活动的 Run，包含审批等待；无 Agent 的插件事件处理则记录派发结果。

Cron 使用五个数字字段、列表/范围/步长和 IANA 时区。夏令时不存在的本地时间跳过，重复的本地时间按两个不同 UTC 时刻处理；预览明确显示对应策略。专业节假日表、秒级 Cron 和业务日历不是本轮实现范围。

事件触发器只接收显式外部事件入口和经过验证的 Webhook，不自动订阅全部内部 Outbox，避免 Run 事实递归触发自身。Webhook 对时间戳、delivery ID 和原始请求体校验 HMAC-SHA256，限制重放时间窗口；同一事件身份配不同内容会拒绝。执行前重新校验原配置身份与资源。

通知以持久记录去重，Bot投递复用已有消息幂等机制。收件箱不要求已连接机器人。已确认失败可以通过管理接口重试；失联或崩溃后无法判断外部是否已发送的记录为 `unknown`，不自动再次发送。通知订阅使用精确事件类型；失败和审批事件可作为告警规则，当前交付渠道为收件箱和平台Bot，邮件/短信等需要另行增加渠道适配。

审批期限由第一次暂停时刻计算，恢复/重复通知不会延长期限。到期后 Run 进入阻断状态并释放会话，不自动批准工具。显式审批人必须仍为活动账号，普通 owner 不能自行修改需独立授权的审批策略。提醒使用稳定身份，多个协调进程不会产生多个同身份收件箱记录。

## 一致性、存储与部署

新增表通过现有显式迁移器登记，已有通用JSON聚合表不做破坏性格式替换。旧部署在停写、备份后运行 `opsmesh migrate`，再运行 `opsmesh check`；应用启动只验证schema。PostgreSQL与SQLite使用同一公开存储边界；Redis仍负责已有共享限流/协调，不能替代数据库事实。

账号权限聚合变更使用事务与Outbox。其他管理用例先持久化审计意图，成功/失败后追加结果；这不是业务表与审计表全局原子事务。未完成意图也保留可追查证据。审计记录不保存原始密码、密钥或模型输入。

快照删除会拒绝已知持久引用，随后标记撤回，保留manifest与blob引用。跨域发布与撤回没有全局分布式锁；竞争时后续读取会明确拒绝已撤回版本，不物理丢弃内容。物理GC继续要求独立维护模式。模型/工具/Skill/MCP撤回也不修改不可变定义。

插件策略每实例轮询并报告应用版本，属于可观测的最终一致性，不是所有进程瞬时切换。禁用阻止后续注册调用，不强制中断已执行的处理器。只能管理已部署代码，不提供在线上传/安装任意Python。

基础表仍是版本化JSON聚合；查询采用有界返回，但部分后台扫描随记录量增长。生产规模需要依据负载补专用索引、分页扫描及归档。单VM多进程测试不等同于跨主机高可用验收。

## 验证

Windows Python 3.13 全量检查：**494 passed，22 skipped（300.17秒）**；收集总数516与当前测试清单一致。Ruff lint/format、mypy（323个源码文件）、仓库依赖/文档检查、源码包和wheel构建、源码包链接检查全部通过。Linux Python 3.14.4 同一源码包全量回归：**494 passed，22 skipped（131.24秒）**。22项跳过均为显式外部环境/模型集成开关；本轮PostgreSQL迁移另做运行服务验收。

测试仅使用离线模型/本地协议替身，不消耗模型网关余额，不向真实外部机器人发送消息。历史真实模型、Docker/K3s和S3验证见[测试环境](testing-environment.md)，不能用旧记录代替本轮新增场景。


Linux隔离测试API已更新到本轮源码，使用独立的测试网关和数据库。升级前保存PostgreSQL dump和数据目录副本，迁移后54张登记表通过校验；17个管理查询接口、真实PostgreSQL上的Cron创建/预览/删除和通知订阅创建/删除通过。原管理员登录、已发布Agent均可读取，升级前后Run记录数一致且没有提交模型任务。

当时验收的是开发构建，使用独立的测试发行目录、日志和进程记录，未改变服务器原有部署。完整验证记录见导出的Windows/Linux测试日志及`opsmesh-linux-upgrade.json`。
