# opsmesh 平台能力与实现缺口审查（补齐前历史记录）

**后续状态：** 本文保留补齐前复现证据；修复与新增能力见[平台补齐记录](platform-completion.md)。以下“当前/尚未”均指原审查时状态。通用工作流经确认不纳入范围。

审查日期：2026-10-31（Asia/Shanghai）。对象：本地开发工作副本 当前源码。范围：260个Python源码文件的模块/入口清单，14个平台模块的公开合同，以及对应组装、实现和测试；提取到100个HTTP路由操作声明（不是100条不同URL）。三组并行审查分别检查调度触发、平台管理、资源治理，根审查核对跨模块链路。此报告不是逐行形式化验证，也不宣称找出所有缺陷。

本次仅审查和记录，没有修改业务实现、远端测试部署或现有用户数据，没有调用付费模型。运行了5项现有调度测试和3个隔离离线复现。此前389项回归通过是既有测试的结果，不能证明下列未覆盖场景正确。

## 1. 结论与平台职责

**定时触发属于平台核心功能。** 平台拥有计划配置、执行身份、权限、持久状态、到期触发、并发策略、恢复、执行记录和结果交付。插件可以提供日报/巡检等具体处理器，但不应要求用户先写插件才能配置一次或周期性Agent任务。

现有date/interval → TimerEvent → Session → queued Run链路已经实现，且`plugins=()`时可执行Agent。进程内定时器位于`framework/scheduling.py`，平台持久调度位于`modules/scheduling/`；旧plugins路径仅兼容导出。**目前仍是基础调度，管理、身份、运行治理和结果交付没有形成完整闭环。**

模块归属与功能完备是两个不同判断：搬入正确目录解决了职责问题，不意味着能力已经完整。以下区分已复现缺陷、已实现部分中的缺项和需要明确范围的后续增强；不靠增加空目录表示完成。

## 2. 应先处理的缺陷

### F01 · P1：调度服务绕过代运行身份检查

- 位置：`src/opsmesh/modules/scheduling/internal/service.py:53`；对照`entrypoints/http/routes/scheduler.py:22`。
- 服务`create()`检查创建者的`schedules.manage`及执行主体自身的`schedules.execute`，但没有检查创建者是否可以指定另一主体。`schedules:impersonate`仅在HTTP入口检查。
- 通过公开服务或`Depends(get_scheduling)`配置任务时，只有计划管理权限的调用者可以指定管理员作为执行主体。离线复现中，该用户的`agent:run`与`schedules:impersonate`均为FORBIDDEN，随后仍成功触发了1个管理员身份的Run。
- Agent发布版本解析同样只在HTTP入口。直接服务调用可保存未固定的名称、未知或禁用版本，等到触发时才失败或选取当时的新版本。
- 修复归属：调度应用服务统一检查actor、被委托主体、执行权限及目标版本，并固化有效配置；HTTP与插件调用同一受保护入口。

### F02 · P1：串行批量调度使用旧时钟，导致正常运行时重复派发

- 位置：`src/opsmesh/modules/scheduling/internal/service.py:104`、`:123`、`:149`。
- `tick()`在遍历开始只取一次`current`，后面的每个任务都使用这个旧时间计算租约；逐项等待dispatcher消耗了时间，但每个回调timeout又重新计时。
- 离线复现：租约0.3秒，第一任务耗时0.18秒；第二任务尚在执行时租约已到期，另一个调度器领取，派发记录为`first, second, second`，旧实例收尾得到CONFLICT。
- 平台Run可能凭事件幂等键合并重复提交，但任意TimerEvent处理器的外部副作用没有因此得到保护。它也不是文档承诺的“进程失败后至少一次交付”，而是正常执行中的租约错误。
- 修复归属：调度领取时使用实时租约时钟，区分计划时间与租约时间，处理租约续期、过期所有者收尾及并发管理变更；增加多任务耗时场景测试。

### F03 · P1：账号维护权限可被扩大为全部平台权限

- 位置：`src/opsmesh/entrypoints/http/routes/accounts.py:90`、`:100`，`modules/accounts/internal/service.py:131`、`:201`。
- 非管理员只要有`accounts:manage`，就可以创建`action="*", resource="*"`的角色并分配给自己。没有角色授予上限或管理员角色保护。
- 离线真实ASGI复现：同一非管理员修改配置403 → 创建通配角色201 → 分配给自己200 → 再次修改配置200。HTTP对创建`is_admin=True`有专门限制，但通过角色可获得实际全域操作能力。
- 修复归属：拆分账号维护、角色定义和角色分配权限，定义可委托范围并保护高权限对象。若该权限本意就是超级管理权限，需要明确声明，不能当成普通人员维护权限授予。

### F04 · P1：跨入口的受保护平台服务尚未统一

- 位置：`src/opsmesh/dependencies.py`；`modules/accounts/api.py:43`、`modules/agents/api.py:36`、`modules/workspaces/api.py:52`、`modules/usage/api.py:148`；对照HTTP路由和`entrypoints/http/security.py:39`。
- 多个Depends Provider返回没有actor参数的原始服务，权限和审计主要由HTTP入口补充。用户命令、定时任务或其他接入渠道直接调用时，必须再次手写授权，容易出现F01一类差异。
- 已有Bot绑定、工具调用、知识检索和部分记忆接口确实进行服务内授权，不能说全系统没有鉴权。
- 需要带actor/context的受保护应用用例入口，统一资源存在性、授权、业务规则和操作审计；内部受信初始化/注册接口应与用户操作入口明确分开。
- 插件是受信Python代码，本项不是声称能将恶意插件隔离在安全沙箱中，而是平台对正常用户请求缺少一致保障。

## 3. 定时触发专项清单

| 能力 | 当前实际行为 | 平台仍需补齐 |
| --- | --- | --- |
| 触发时间 | 单次带时区时间、秒间隔；时间转UTC | Cron、IANA日历时区及夏令时语义；工作日/节假日可作后续增强 |
| 计划管理 | 创建、列表、启用/停用 | 单项读取、编辑时间/输入/目标、删除、立即触发、下次触发预览；对外版本控制 |
| 漏触发 | 固定执行一次旧时间点，然后跳过积压间隔 | 显式跳过/合并/补跑策略、过期宽限和补跑上限 |
| 并发 | CAS领取、lease和单次dispatcher超时；存在F02 | 每计划最大并发、排队/跳过策略、准确租约与管理变更竞争规则 |
| 执行身份 | 保存principal_id，每次触发校验其执行权 | 创建者代执行许可下沉至服务；Agent有效性/版本固定下沉；见F01 |
| 输入与资源 | payload固定JSON序列化成模型输入 | 文本/消息输入、RunOverrides、工作区/资源覆盖、目标校验 |
| 会话 | 同一计划固定复用`schedule:{id}`会话 | 每次新会话/固定会话等明确策略，防止不需要的历史累积 |
| 执行历史 | 当前state和last_event_id；错误仅在内存errors | 每次触发的持久记录、Run关联、耗时、错误、取消/重跑证据及查询 |
| 完成语义 | dispatcher提交queued Run后，一次性计划即标completed | 区分“触发已提交”和“Agent已完成”；聚合Run的成功、失败、审批等待与取消 |
| 结果交付 | TimerEvent上下文没有reply_target | 配置结果机器人/频道、输出制品、消息权限、发送回执与失败处理 |

证据：调度[合同](../src/opsmesh/modules/scheduling/api.py)、[实现](../src/opsmesh/modules/scheduling/internal/service.py)、[HTTP入口](../src/opsmesh/entrypoints/http/routes/scheduler.py)、[应用分发](../src/opsmesh/bootstrap/application.py)。

因此，“每天09:00执行Agent、每次使用新会话、结果发到指定群、失败通知管理员、查看最近执行记录”目前不能完全通过平台配置完成。它不是新增一个`scheduled_jobs`插件就能替代的平台职责。

## 4. 其他平台能力缺项

下表的P2表示主要调用链已存在但缺少平台操作闭环，或平台能力尚未建设。它不意味着每项都必须建一个独立模块。高级路由、云提供商和专业工作流范围需单独决定。

| 项目 | 已有能力 | 缺少的核心操作/规则 | 建议归属与证据 |
| --- | --- | --- | --- |
| 通用事件触发 · P2 | Matcher/on_event、Outbox事实派发 | 持久触发规则、事件源/过滤条件、输入映射、目标Agent、授权Webhook、启停/去重/执行记录 | 目标`modules/triggers`，目前不创建空包；`bootstrap/application.py:295`只把MessageEvent/TimerEvent转Run |
| 通知与告警 · P2 | 完成Run回复原Bot、消息投递幂等 | 失败/审批提醒、定时结果目标、订阅人/渠道/模板、通知记录、告警规则 | 目标通知服务；`application/coordinator.py:182`仅完成且有reply_target时发消息，日志/指标不等于告警 |
| 审批管理 · P2 | 单Run待批列表、同意/拒绝、恢复 | 跨Run待办箱、审批人/角色规则、到期/提醒、决定原因和业务操作者记录 | 优先归runtime中的审批服务；`framework/agents/state.py:20`、`entrypoints/http/routes/runs.py:120` |
| 机器人生命周期 · P2 | 账号创建/读取/启停、身份绑定、Agent频道绑定CRUD、OneBot连接 | 修改连接地址/传输/凭据引用并重建连接；身份映射列表/解绑；连接状态管理查询 | `modules/bots/api.py:116`及`internal/service.py:145`。Bot默认Agent/线程继承属可选策略增强 |
| 本地账号治理 · P2 | 用户/角色/部门创建、登录、单token退出、用户停用 | 改密/管理员重置、全部会话撤销、角色grant更新、部门调整、最后管理员保护 | `modules/accounts/api.py:43`，`internal/service.py:107`、`:181`；不需要OIDC/SSO |
| 模型和能力治理 · P2 | 不可变版本、引用解析、逐次调用鉴权 | 统一停用/撤回某个模型/Tool/Skill/MCP版本；Skill/MCP/脚本完整清单；引用影响查询 | `modules/models/api.py:24`、`modules/capabilities/api.py:46`、`:92`、`:102`、`:156` |
| 知识库生命周期 · P2 | 文本新增、BM25、向量/RRF检索、集合权限 | 文档列表/更新/撤回/删除、集合管理、解析分块/导入任务及错误、索引重建管理 | `modules/capabilities/api.py:176`；`internal/knowledge.py:101`重复文档ID为create冲突 |
| 工作区文件管理 · P2 | 工作区底层list、文件读写、Manifest、快照/恢复/GC | 授权HTTP工作区列表；目录浏览、stat、删除/改名；快照列表/删除 | `modules/workspaces/api.py:60`、`entrypoints/http/routes/workspaces.py:29`；不能将底层已有list说成完全缺失 |
| 记忆配置管理 · P2 | namespace ACL/策略、正文读写/CAS、生成、forget/prune | namespace列表、读者/贡献者撤权、模型/生成/live_update/保留策略在线更新 | `modules/memory/api.py:23`、`:89`；update修改正文，不修改namespace策略 |
| 操作审计 · P2 | HTTP请求审计、部分领域事实审计 | 所有入口的业务操作actor/对象版本/变更摘要；系统/全局查询和足够过滤 | `entrypoints/http/security.py:39`、`application/coordinator.py:164`。Bot绑定/模型修改的直接服务调用没有完整领域审计 |
| 事件运维 · P2 | Outbox租约、重试、死信、底层重投方法 | 授权的死信查询/原因详情/人工重投/恢复审计入口 | `infrastructure/database.py:129`、`:133`已有方法，但HTTP/CLI/平台服务没有调用链 |
| 费用运营 · P2 | 计量/成本/预算、价格与汇率发布、补价和持久对账 | 价格/汇率版本查询、对账记录查询；周期/部门/Agent聚合报表可后续增强 | `modules/usage/api.py:148`、`entrypoints/http/routes/usage.py:48`；不能误报账本或对账算法未实现 |
| 插件部署管理 · P2，按集群管理范围实施 | 本地Loader、元数据、require、配置校验、进程内启停 | 已部署插件清单/版本与运行实例状态、持久启停策略和实例一致性 | `framework/plugin/manager.py:24`是内存字典，`bootstrap/settings.py:19`是模块名列表；不意味着需要在线安装任意Python |
| 通用工作流 · 目标能力未实现 | Agent委派、handoff、as-tool、单Run审批/恢复 | 若平台承诺通用工作流，还需定义/实例/步骤状态、依赖分支、补偿和人工节点 | 当前没有工作流模块；不是Agent SDK已支持多Agent就自动获得完整工作流引擎 |

### F05 · P2：管理员也无法通过HTTP查询系统审计

`entrypoints/http/routes/management.py:57`将省略或空的actor_id替换为当前用户。底层`modules/audit/internal/journal.py:79`本来支持全局查询，但HTTP无法传达该模式。系统事件的actor_id为None，通过字符串`system`也查询不到。应增加明确、受权的全局/系统筛选模式，而非放宽普通用户查询。

## 5. 已有基础能力，不应再误报为空

- Bot精确频道绑定Agent的创建/读取/列表/替换/解绑；入站身份验证、去重、Run执行和完成回复。
- Agent草稿、发布版本、启停、克隆；模型配置版本；SDK运行、流式、工具、护栏、审批和检查点。
- SQL事务/CAS、Redis协调、Outbox重试和死信底层；本地/S3文件内容、快照及离线备份恢复。
- 工具授权和幂等、MCP发现/调用、Skill装配、知识检索、文件记忆、费用账本、预算及OTel/指标。
- 进程内定时器与平台持久date/interval调度；无业务插件的Bot及TimerEvent到Agent调用链。

Web、Claude、OIDC/企业SSO不作为本次缺项；Web和Claude已有明确范围约定，SSO不需要。云厂商数量、专业日历、复杂语义路由、多节点高可用验收也不能混同为这次基础平台功能遗漏。

## 6. 验证记录与补齐顺序

现有调度回归：`uv run pytest tests/framework/test_persistent_scheduler.py tests/platform/test_scheduling.py -q`，**5 passed，6.24秒**。

离线复现使用临时SQLite/ASGI和可控回调，输出如下：

```text
F01 service_impersonation:
  manager_denied = [schedules:impersonate: FORBIDDEN, agent:run: FORBIDDEN]
  dispatch_count = 1; admin_runs = 1; run_uses_admin = true
F02 stale_batch_lease:
  dispatches = [first, second, second]; outcomes = [CONFLICT, 1]
F03 delegated_role:
  initial_is_admin = false; initial_grant = accounts:manage
  configuration_before = 403; create_wildcard_role = 201
  assign_role_to_self = 200; configuration_after = 200
```

补齐顺序建议：

1. 先修F01/F02/F03，并为调度和身份委派增加真正的边界测试；建立跨入口受保护服务规范，避免仅在HTTP修补。
2. 完成平台定时任务闭环：计划CRUD、每次执行记录、目标/输入/会话策略、通知目标，再增加Cron/漏触发/并发策略。
3. 补平台日常管理：账号凭据与会话、Bot连接/身份、能力撤回、知识修订、工作区/记忆配置、系统审计和死信运维。
4. 再实现通用事件触发、通知订阅/告警及经确认的通用工作流范围；插件只扩展处理器/步骤/渠道。

上述缺项本次尚未修复。修复交付应同时包含公开合同、服务实现、持久状态及迁移、HTTP/DI入口、权限/审计和行为回归，不能仅增加目录或Protocol即宣称完成。
