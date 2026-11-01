# 运维与部署

核对日期：2026-10-30。当前包含CLI、HTTP/SSE、机器人、协调/Run Worker、审批恢复、资源/记忆、用量及Outbox。临时PostgreSQL17.11、Redis5.0.14.1和本地协议已有验收；本次独立Linux主机新增PostgreSQL16/S3完整恢复、Redis7协调、真实模型Responses、Docker隔离/远程mTLS与K3s Job验收。真实外部机器人账号及多节点生产故障转移尚未验收。逐项证据见[SDK覆盖表](design/sdk-coverage.md)。

## 安装、初始化与启动

Linux 正式部署使用 `opsmesh-VERSION-linux.tar.gz` 安装包，在解压后的包目录执行下列命令。默认在目标机器创建 Python 虚拟环境并运行 `opsmesh.service`；Docker 为显式可选模式，操作系统服务安装不覆盖 Windows：

```sh
sudo bash install.sh
sudo systemctl status opsmesh.service --no-pager
sudo journalctl -u opsmesh.service -f
```

本地安装配置为 `/etc/opsmesh/opsmesh.env`，数据为 `/var/lib/opsmesh`。升级从新包目录执行 `sudo bash install.sh --upgrade`，首次初始化与迁移分别处理，已有配置和数据保留。包内是 wheel、带哈希的依赖清单及部署文件，不打包解释器、`.venv` 或开发源码树；安装时复用本机可用 Python，否则在线下载，再在线安装运行依赖。完整参数、`systemctl` 启停、已安装环境的 CLI、Compose 命令与备份步骤见 [Linux 安装指南](../deploy/linux.md)。历史 `v0.3.0` 没有该 Linux 安装包；源码开发者可以用构建脚本生成制品。

### 手动开发运行

从项目根目录执行。`.env`不会由应用隐式读取，使用`uv run --env-file`明确加载；首次可从`.env.example`复制后填写本地配置，填入的凭据不得提交。

```console
uv sync --extra openai
uv run --env-file .env opsmesh init --admin admin
uv run --env-file .env opsmesh check
uv run --env-file .env opsmesh serve
```

`init`安装版本化schema，并要求用户表为空后创建首个本地管理员；密码默认交互输入。非交互场景可通过`OPSMESH_ADMIN_PASSWORD`提供，避免放在命令行参数或脚本正文中。已有管理员的数据库应使用`opsmesh migrate`迁移，不能再次`init`当成管理员重置操作。

默认`OPSMESH_DATA_DIRECTORY=.opsmesh`，未配置数据库URL时使用其中的SQLite文件。`serve`和`worker`在启动时调用只读`verify_schema()`；未初始化、缺表、版本不匹配或结构漂移会失败，不会启动时偷偷建表。`opsmesh check`只验证已注册schema，不会验证所有模型凭据或逐一连通外部服务。

新增模块表或升级前停止相关worker，备份数据库与内容目录，运行`opsmesh migrate`再执行`opsmesh check`。当前只有初始v1迁移及新增注册表安装，结构变更/旧无版本数据库明确拒绝；没有自动升级任意历史结构或破坏性降级命令。不得通过手改fingerprint绕过验证。[迁移说明](../migrations/README.md)

## 进程与执行位置

| 入口/模式 | 实际行为 | 边界 |
| --- | --- | --- |
| `opsmesh serve` | HTTP管理/运行API与SSE；默认同时启动后台协调器 | `OPSMESH_WORKER_ENABLED=false`可关闭该进程的协调器；不是Web前端 |
| `opsmesh worker` | 启动Driver、插件、持久任务调度和后台协调器，不启动HTTP服务器 | 分离gateway/worker时，给两个进程分别配置worker_enabled，并共享数据库与必要内容存储 |
| `run_worker_mode=inline` | 在应用进程中运行SDK | 无OS隔离；适合开发/可信部署 |
| `subprocess` | 每Run启动受监督Python子进程，记录退出与日志 | 地址空间分离不等于文件/网络/CPU隔离；真实子进程测试已运行 |
| `docker` | 每Run启动容器，构造CPU/内存/PID/网络限制和受控环境 | 目前只有argv/策略测试，没有启动真实容器验收；需自行构建含代码、依赖和插件的镜像 |
| SDK工具沙箱 | OpenAI SandboxAgent由后端创建native客户端/session | 与整个SDK Run的worker隔离是两层；UnixLocal在Windows明确拒绝 |
| stdio MCP / remote MCP | 前者仅允许显式可信宿主程序，后者支持Streamable HTTP及SSE服务 | 不会因为Agent选择sandbox就把stdio MCP程序自动搬入沙箱 |

Docker Run worker不挂宿主Docker socket，默认只读根文件系统、丢弃capabilities、禁提权并设置临时`/tmp`。但当前会挂载整个平台数据目录到`/var/lib/opsmesh`，不能宣称容器仅能看见一个租户或一个workspace；模型凭据、数据库访问、挂载范围和容器间权限需要部署侧进一步收紧和验收。`bridge`网络也不是域名白名单。磁盘配额在中立Docker命令Provider中明确不支持。

子进程/容器只继承基础环境与`OPSMESH_RUN_WORKER_ENVIRONMENT_ALLOWLIST`声明的变量；需要模型凭据时显式配置允许的secret变量。worker不会默认继承整个宿主环境。设置数据中仍可能包含数据库连接凭据，进程/容器管理权限应视为敏感权限。

## 并发、租约和资源所有权

`configuration/admission`提供`max_active_runs`、输入长度与请求速率配置。跨worker的后端执行容量由共享数据库CAS池持有：按run/attempt获取随机令牌、续租，丢失租约即取消本地执行，等待审批或收尾后释放。这限制**有效后端执行槽位**，不是操作系统已经启动的worker进程总数，也不能物理杀死网络分区后无法联系的任意远端进程。部署时保持时钟同步，并实测失联/恢复。[容量实现](../src/opsmesh/infrastructure/coordination/admission.py)、[容量测试](../tests/infrastructure/test_admission.py)

Run本身另有attempt领取/fencing和Session逻辑占用。审批暂停可以释放worker资源，但保留会话恢复依据；不能通过重复提交审批或强行复用session绕开状态校验。资源准备失败也执行清理。每Run生成的工具callback按principal/run/attempt登记所有者，最后一个owner退出才释放；旧attempt晚退出不会撤销新attempt仍使用的callback，持久工具定义保留审计。

WorkspaceService使用持久每workspace租约、递增fence和心跳，覆盖创建、读写、物化、快照、恢复和删除。实际文件IO持有数据库行锁直到线程完成，即使任务取消也不会在交锁后出现晚写。两个独立service/database连接同root的竞争已有测试。**直接修改磁盘、可信宿主命令或绕过WorkspaceService的第三方程序不受该租约约束**；运行应使用独立环境/工作区，不能把共享本地目录当成强安全隔离。[并发测试](../tests/resources/test_workspace_concurrency.py)

## 数据库与内容存储

数据库保存业务定义、身份、Run/attempt、审批、检查点引用、模型历史、计量/预算、审计及Outbox。当前模块记录使用版本化JSON aggregate，单记录CAS和“记录+事件”事务已实现；不是任意多业务对象的全局事务，也没有为所有JSON字段创建索引。大规模列表、热主体账本和长期保留需要模块专用查询、分区或迁移。[初始存储限制](../migrations/0001-initial.md)

活动工作区使用本地/共享POSIX目录；不可变SHA-256 blob可配置本地存储或S3兼容存储。S3不提供工作区POSIX挂载，worker仍需共享活动目录，所有worker必须使用一致数据库、storage backend及bucket/prefix。`OPSMESH_STORAGE_BACKEND=s3`启用S3，bucket由部署预先创建；凭据通过`OPSMESH_S3_*_SECRET_REF`解析或默认凭据链提供。内容读取验证SHA-256摘要，不信任ETag作为摘要。备份同时覆盖数据库、blob和工作区，否则恢复可能留下有效但不可读取的引用。文件记忆删除先撤销可见性并删除关联记录；namespace retention可裁剪过期来源、任务和阶段结果，共享blob由独立维护GC处理。

完整离线备份与恢复CLI要求操作者通过`--maintenance`确认已停止写服务、Worker和GC。备份写入新的目录并最后生成清单；需另行保存其manifest SHA-256。`backup-verify`验证数据库、活动文件树和全部blob；`restore`只接受与目标数据库引擎一致、且数据库、数据目录和blob命名空间均为空的目标。PostgreSQL通过环境变量向`pg_dump/pg_restore`传连接凭据，恢复使用单事务，不执行覆盖或清理现有对象。维护流程示例：

```console
uv run opsmesh backup ./opsmesh-backup --maintenance
uv run opsmesh backup-verify ./opsmesh-backup --manifest-digest <64位清单摘要>
uv run opsmesh restore ./opsmesh-backup --manifest-digest <64位清单摘要> --maintenance
```

恢复中断会保留`.restore-incomplete`标记，应用拒绝启动，必须先恢复完成并校验。blob bundle接口也可以用于分层迁移，但数据库、活动目录及插件版本必须由同一维护窗口协调。真实PostgreSQL16与SeaweedFS4.51 S3组合恢复已在专用测试环境演练，验证账号登录、活动文件、空目录、只读文件、快照与blob；SQLite恢复也有自动回归。该CLI不负责停止其他进程、复制密钥或配置版本控制/云端备份保留策略。

SQLite已做真实事务、并发和故障测试，适合本地或小规模单机；文件IO防晚写期间持有SQLite写事务会串行化其他数据库写入，不应据此推导高吞吐。PostgreSQL17.11已在临时服务器验证并发迁移、结构漂移拒绝、跨连接CAS、Outbox事务回滚、SKIP LOCKED竞争、旧租约拒绝、多worker容量和应用重建后的审批恢复；这些测试不替代生产HA和备份恢复演练。

## Outbox、死信和重投

模块的create/update/delete可以与Outbox事件同事务提交；无效/重复事件使业务写入回滚。事件ID稳定，保留type/source/payload/schema_version/occurred_at及扩展字段。消费者以`event.id`幂等，不能把租约理解为外部副作用恰好一次。

协调器将Run调度、Memory任务和事件投递拆成独立循环。当前每批领取10条事件、租约120秒、并发投递，并为每条事件续租；租约owner包含批次随机标识。确认/拒绝需原owner，丢失租约时停止确认。失败采用指数延迟、上限300秒；默认5次尝试后进入死信，重复崩溃也消耗尝试次数。

当前维护入口是`AsyncDatabase`公开方法，尚无专用死信CLI/HTTP管理页面：

- `dead_letters(limit=100)`检查原事件、尝试数、最后安全错误和进入死信时间。
- 修正目标服务、权限或handler后，显式调用`requeue_event(event_id)`。
- `pending_events()`仅查看未领取可投递事件，不能用它替代`claim_events()`的并发领取。
- 不要批量无条件重投可能已产生外部副作用的事件；先核对下游幂等键或处理凭证。

Outbox、投递结果与审计能恢复，不代表插件自身的文件/网络副作用可以自动回滚。对已发送机器人消息但未收到确认的情况，发送层保留不确定结果，避免自动重复发送。[Outbox实现](../src/opsmesh/infrastructure/messaging/outbox.py)

## Redis

未配置Redis时请求限流使用当前进程的内存计数，重启清空，也不能限制多个gateway的合计请求量。配置`OPSMESH_REDIS_URL`后限流使用单条Lua原子INCR+首请求EXPIRE；Redis连接失败不会静默降级为各进程独立计数。`RedisLeaseCoordinator`使用SET NX EX和比较token再续期/删除；Run/审批/成本的持久真相仍在数据库。

已实际启动并清理一次独立的本机Redis **5.0.14.1** 临时实例：只绑定127.0.0.1随机空闲端口、关闭持久化、数据/日志放临时测试目录、Windows无可见窗口；未连接或改动已有Redis服务。验证原子限流、租约竞争、过期重领和旧token不能续约/删除新租约。为兼容Redis5，连接显式使用RESP2。

默认测试只跑协议替身与本地并发测试。显式集成命令如下；找不到redis-server时跳过，不安装软件：

```powershell
$env:OPSMESH_TEST_REDIS_INTEGRATION = '1'
uv run pytest tests/infrastructure/test_cache.py -q -s
Remove-Item Env:OPSMESH_TEST_REDIS_INTEGRATION
```

这不覆盖Redis Cluster/Sentinel、TLS/ACL、故障转移、网络分区和生产负载。不得用本地单实例测试代替这些验收。

## 日志、指标、追踪和审计

CLI配置`opsmesh` logger输出JSON：timestamp、level、logger、message及显式提供的run_id/request_id/tool_call_id/event_id；异常默认记录类名，避免原始异常payload。它没有自动拦截所有第三方SDK日志，也没有完整日志脱敏/轮转系统；插件写入message的敏感数据仍可能被记录。

子进程输出保存到`<data_directory>/worker-logs/<run-id-hash>.log`。该文件可能包含第三方程序输出，部署时设置访问权限、保留周期和轮转，不把它当成已全面脱敏的审计数据。

`GET /api/v1/metrics`需要metrics权限。指标采用固定低基数名称和标签，包含HTTP/模型/工具/Run/首token/排队/审批/记忆时长直方图，以及运行/队列/记忆任务仪表值。不同进程快照通过共享数据库汇总；已结束Worker的累计值保留，存活进程的gauge按有效期读取。指标不是收费账本，不能用于财务补账。`GET /health`只检查HTTP进程，`GET /ready`实际查询数据库，不隐式创建schema。

`OPSMESH_TRACING_ENABLED=true`创建平台OTel provider并桥接SDK，默认写`<data_directory>/telemetry/traces.jsonl`。设置`OPSMESH_TRACING_ENDPOINT=http://collector:4318/v1/traces`选择OTLP HTTP，`OPSMESH_TRACING_QUEUE_SIZE`限制队列，`OPSMESH_TRACING_TIMEOUT_SECONDS`限制导出/关闭等待。认证header JSON由`OPSMESH_TRACING_HEADERS_SECRET_REF=env:OTLP_HEADERS`解析，不能写进Agent定义。W3C traceparent/tracestate沿HTTP→Outbox→Run传递；只有白名单关联字段进入SDK span，敏感提示/输出默认不导出。真实loopback OTLP protobuf接收和关闭超时已有测试；远端collector的TLS/认证与负载需按部署验收。追踪导出失败不回滚业务状态。

审计和usage是独立数据库记录，不依赖采样trace是否收到。HTTP管理变更先记录审计意图，记录失败时阻断相应变更；Outbox也生成持久业务审计。没有声称实现全部业务动作跨表不可分割的审计事务。查询入口为`/api/v1/audit`与`/api/v1/usage/*`，需对应主体权限。

## 费用、预算与记忆生成

价格按不可变版本、模型档案、币种及每百万token单位保存，Decimal计算输入/输出/cache费用。缺价格是`unpriced`，缺用量是`unavailable`，不是0费用。记录按稳定call_id幂等；累计fallback只记新增差额，已有请求级记录时不再累加整Run总量，禁止父累计与子调用双记。

当前预算作用域是**principal + currency**。配置预算后Run预占估算额度，实际已计价用量独立增加spent并消耗预占；settle只释放剩余hold，不再重复记成本。未知费用或不同币种保持`pending_pricing`，没有FX汇率就不能直接求和。没有预算配置时仍保留计量事实，但不执行限额门禁。并发模型请求可能已经发出并产生真实费用，预算门禁不能撤回这类花费或保证供应商账单绝不越过估算。

Hosted Web/File Search、Code Interpreter、Image Generation、Hosted MCP走供应商执行通路，平台对已观察到的调用记录资源事实。资源单价通过`/usage/resource-prices`发布，计算/存储等Provider也可上报明确单位的数量；不能把墙钟时长冒充CPU秒。没有价格的资源保持unpriced。版本汇率通过`/usage/exchange-rates`发布，跨币种转换保存来源金额/币种与汇率ID；没有汇率时保留待定。`/usage/reprice/{principal_id}`幂等补价，已结束且全部已知的预算才释放挂起预留；`/usage/reconcile`比较供应商账单行，保留匹配/差异/缺失状态，不重复收费。

当前预算是principal+currency，未实现部门/namespace独立钱包或支付。供应商未返回的工具内部资源量不会被猜测；需导入实际账单核对。

默认文件记忆由平台显式任务管理：source→提取Run→整合Run→候选→CAS发布。两个namespace使用相同source ID的集成测试经过真实平台目录、runtime、用量和存储路径，产生4个独立受计量Run（模型边界仍为离线替身），价格缺失时保留4条unpriced事实。提取以来源主体执行，当前整合使用输入集合首项的主体和模型配置；这不是独立namespace费用账户。缺模型档案明确失败，不做伪造提取。

SDK原生Memory生成现通过专用模型桥接执行授权/预算检查与请求计量，并在归档快照前显式flush。重复捕获不重复生成；失败保留错误并阻止不完整产物被当成完成。Linux真实UnixLocal会话已验证生成文件进入最终快照。平台`live_update`提供read/update工具，按namespace修订CAS及幂等键发布；同一Run审批恢复保持稳定工具身份，旧attempt退出不会撤销新attempt还在使用的回调。

## 内容保留与GC

namespace策略声明来源/任务保留窗口，`POST /memory/namespaces/{id}/prune`与任务输入选择共用数据库namespace锁，避免删除正在生成所需的记录。删除/裁剪后blob可能仍被其他工作区、快照、Run或审计引用，因此物理GC独立执行：

```console
uv run opsmesh storage-gc
uv run opsmesh storage-gc --apply --maintenance --minimum-age-seconds 3600
```

默认只预览。实际删除需要维护模式：停止所有写入服务/Worker，备份数据库和内容目录，再执行；工具二次扫描持久记录与事件中的内容摘要，并使用blob租约和最小存活时间。保守扫描可能保留多余blob，以避免误删共享引用；不把误删风险交给数据库外的运行进程。多主机依赖共享一致内容存储。

Git Skill获取需要`OPSMESH_SKILL_GIT_ALLOWED_HOSTS`白名单和完整40位commit。脚本工具固定文件/wheel摘要，使用独立venv、离线wheel与禁依赖联网解析；默认`OPSMESH_SCRIPT_EXECUTION_PROVIDER=docker`。可信local执行必须显式开启，不是安全沙箱。更换Python镜像需要同时保证项目脚本所需ABI与wheel兼容。

## 故障处理与生产验收边界

- Worker失联：由attempt与launch token判断归属；未知工具副作用会阻断自动重放。不要删除记录后直接重跑冒充恢复。
- 审批暂停：只使用服务器保存且版本/摘要兼容的状态；不接受客户端原生RunState，拒绝或取消须走正式入口。
- 沙箱丢失：平台捕获的文件快照可用于新环境，不恢复原进程、PTY或网络连接；未捕获输出会明确报错。
- 记忆失败：保留旧发布版本与已完成阶段，重试复用保存结果；费用记录独立于是否成功发布。
- 关闭：Driver任务、launcher、runtime/资源服务和追踪分别尝试清理；关闭错误聚合报告，不默认为全部清理成功。无法联系的外部资源仍需人工核对。

首次生产验收至少包括真实PG迁移/备份恢复、多worker故障、容器限制和跨租户挂载隔离、真实模型/Hosted/MCP/机器人、权限撤销与审批重放、文件/数据库备份一致性、Redis生产拓扑、日志保留与远端追踪。没有完成负载测试，不提供吞吐、RTO/RPO或高可用承诺。

Web只预留，OIDC/企业SSO不在需求范围，Claude后端及跨SDK检查点迁移为后续工作。Compose提供应用/Worker/数据库示例，Kubernetes提供双副本API/Worker、迁移Job、共享PVC和探针；已在隔离K3s集群通过真实Job生命周期验收。基础配置使用Pod内子进程Run；job-mode overlay启用逐Run Kubernetes Job Provider，含最小权限Coordinator/Observer和跨Pod审批恢复。


## 新增平台管理运维

- `/api/v1/audit?scope=all`和`scope=system`需要全局审计权限；默认只看本人。支持action/resource/outcome/since/until/limit/offset。
- `/api/v1/operations/outbox`查询pending/delivered/dead，人工requeue需operations:manage和reason，保留恢复证据。重投不能替代核对未知外部副作用。
- `/api/v1/operations/plugins`、`/plugin-policy`、`/plugin-instances`展示部署与同步状态；策略5秒同步，实例30秒无心跳显示stale。不能在线安装代码。
- `/api/v1/notifications`为收件箱，`/subscriptions`配置精确事件规则与模板。unknown投递不自动重发，普通用户只能管理本人记录。
- `/api/v1/approvals`为跨Run审批待办；逾期阻断执行，指定审批人与理由经服务层检查。
- `/api/v1/usage/prices`、`/resource-prices`、`/exchange-rates`、`/reconciliations`提供运营查询。

新增可用性、知识任务、通知、触发器与插件部署记录表。升级前停写并备份，用新代码执行`opsmesh migrate`、`opsmesh check`后启动各节点；不要持续混跑新旧版本。迁移不重写不可变定义与历史Run。快照删除只撤回可用性并保留manifest；物理GC仍要求维护流程。
