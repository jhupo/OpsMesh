# 运行合同

## 数据所有权

PostgreSQL 保存账户、组、令牌摘要、插件和发布记录、实例、任务、尝试、检查点、交互、计划、额度、通道、收发记录和审计。迁移随服务打包；新迁移不依赖当前 ORM 动态生成表。

输入、配置、模型与通道凭据、结果、检查点、交互正文、沙箱状态和路由目标使用 Fernet 加密。原生 Session 历史使用 SDK EncryptedSession 加密，账户密码使用 PBKDF2-SHA256 哈希。索引字段、名称、ID、状态和审计元数据不加密。工作区文件、沙箱快照、插件自建 Session 和日志的磁盘加密由部署及插件负责。

目录布局如下，所有 API/Worker 必须使用同一个数据卷：

```text
data/
  packages/<installation-id>/        # 固定版本代码及打包技能
  users/<user-id>/
    workspaces/<workspace-id>/       # 所有者管理的目录
    agents/<instance-or-publication-id>/
      .opsmesh/sessions/<session-id>/ # SDK 历史及原生快照
    plugins/<plugin-name>/data/      # 同包不同版本共用的插件持久数据
    artifacts/<artifact-id>/         # 显式导出的结果文件
  logs/                             # 各进程 JSON 日志
```

框架不会因为 Run 完成、取消、禁用插件或发布新版本而删除这些目录。插件持久数据可跨版本保留，插件自身负责其数据格式升级，不应假定数据会随代码安装而清空。

## 状态

任务执行内部实现集中在 `tasks/runtime/`：`runner.py` 管理单次尝试的超时、取消、清理与结果落库；`leases.py` 管理领取、尝试身份校验、续租与过期回收；`invocation.py` 校验固定版本依赖并装配插件调用上下文；`children.py` 管理子任务的幂等键、层级与并发额度。`tasks/interactions.py` 管理检查点和人工输入的持久化，不依赖执行器。Worker 负责轮询与进程退出，插件负责业务编排。

```text
tasks/
  routes.py          # HTTP 输入与响应
  schemas.py         # 请求与响应结构
  models.py          # 任务、尝试、检查点、交互和计划记录
  service.py         # 提交、取消、恢复和所有权
  interactions.py    # 检查点与人工响应的事务规则
  events.py          # 有序加密事件写入及插件事件预算
  streaming.py       # 鉴权与短事务事件读取
  schedules.py       # 计划表达式与下次触发时间
  scheduler.py       # 到期计划与交互超时轮询
  runtime/
    runner.py        # 单次执行生命周期
    leases.py        # 领取、续租、归属校验与过期回收
    invocation.py    # 插件授权与调用上下文
    children.py      # 子任务执行
```

Session 的路径和 Manifest 默认布局放在 `sessions/layout.py`，创建、恢复及关闭原生沙箱仍由 `sessions/runtime.py` 管理；文件管理通过 `sessions/sandboxes.py` 使用相同的路径规则。网关的持久出站入队和发送集中在 `gateway/delivery.py`，通道连接管理位于 `gateway/worker.py`。导入合同禁止任务管理依赖执行内部实现，也禁止运行时代码导入 HTTP 路由。

任务从 `queued` 领取后变为 `running`；本次尝试携带租约与 SDK Trace ID。结果为 `succeeded`、`failed`、`cancelled`、`timed_out`、`interrupted`，或等待人工输入的 `waiting_input`。等待状态不占用运行 Worker 的协程槽位。

超时和租约失效并不撤销已经发生的业务效果。关闭 Worker 会取消本机执行协程并保留状态；进程硬退出后由后续 Worker 把失效租约标成中断。无检查点的中断不能直接恢复，用户应核对效果后创建新任务。

单次原生 Runner 的普通异常不保证附带可恢复 RunState。插件只能在 SDK 支持的边界保存原生状态，或保存自己的业务阶段和外部效果引用。恢复入口负责理解这些状态，宿主不序列化 Python 栈。

检查点通过内部上下文保存，不提供客户端上传 RunState 的接口。SDK 版本变化时拒绝旧检查点；升级前处理存量等待和中断任务。依赖撤回、账户禁用、组授权变化或插件禁用也可能使恢复被拒绝。

## 交互与消息

插件返回 `Suspended` 表达需要谁做什么，宿主负责保存检查点、登记待办和通知。插件不需要在持久任务中挂起等待用户消息的 Python 协程。

一个决定只绑定一个交互和检查点。重复提交相同内容返回原记录，提交不同决定返回冲突。默认接收者是原调用者，也可由可信插件指定其他平台用户；只有指定接收者能响应。

出站记录的 `pending → sending → delivered` 与第三方发送不是一个分布式事务。发送异常或 Worker 在中间退出后保留 `uncertain`。管理员显式重试可能导致重复发送，应先核对远端结果。框架不会把异常当成已送达。

QQ 配置使用 `app_id`、`secret`，支持 WebSocket 或签名 Webhook、C2C、群、频道和频道私信。管理员可通过 SDK 原生 API 操作群成员、禁言及平台开放的其他能力；SDK 暴露方法不代表应用获得权限。钉钉使用 `client_id`、`client_secret`，由原生 Stream SDK 接收，回复和主动操作通过官方接口执行。完整配置及异步回调边界见 [网关说明](gateway.md)。

通道编辑增加配置代次，Gateway Worker 释放旧连接后加载新配置。多个 Worker 用通道租约分配连接。用户绑定按外部用户 ID 管理，不按聊天正文、昵称或群角色推断平台身份。

## 部署约束

- 只支持 PostgreSQL，依赖行锁、SKIP LOCKED 和事务 advisory lock。
- 插件是可信代码，使用独立服务账户和受限网络。普通用户输入不构成执行代码的授权。
- 多台主机必须共享代码安装目录、用户目录和密钥。默认文件接口适用于可信宿主目录，不能替代隔离不可信进程的存储服务。
- 模型域名允许列表来自管理员配置。允许的自建模型服务及 DNS、网络代理仍属于部署信任边界；不要把任意用户控制的域名加入全局列表。
- 执行器当前使用同步 PostgreSQL 会话完成短事务。插件不得在事件循环中运行长时间阻塞计算；需要阻塞工作的插件自行使用线程、进程或外部执行环境。
- 未设置 `OPSMESH_WORKER_CONCURRENCY` 时，任务 Worker 在启动时根据 CPU 亲和性、可用内存及 Linux cgroup v2 限额估算每进程并发：`min(64, max(1, floor(CPU * 2)), max(1, floor(可用内存 * 75% / 1 GiB)))`。内存先取系统可用值与各级 cgroup 剩余额度的最小值；启动日志记录探测值与选择结果。资源读取失败会明确报错，不静默使用固定值。旧 cgroup v1 环境需显式配置。
- `OPSMESH_WORKER_CONCURRENCY=8` 可覆盖估算，接受 1-64。自动值是启动时的启发式预算，不是动态扩缩容、沙箱硬内存限制或性能基准；运行多个 Worker 进程时需按共享机器的总预算配置。资源探测使用 [psutil 的 CPU 与内存接口](https://psutil.readthedocs.io/stable/)。
- 用户额度分别限制该用户同时执行的顶层任务和子任务，各默认 4。每个 Worker 另为子任务分配与顶层并发相同的容量，超出时立即报告冲突，避免父任务占满槽位后等待子任务形成死锁。嵌套调用仍以原用户计费，插件应限制分支数量。
- 文件操作与 PostgreSQL 不具备跨系统原子提交。上传使用同目录临时文件和原子替换，提交失败后应读取实际文件核对，不能假设回滚了文件系统。

停止入口并处理活跃任务后，再备份数据库、整个数据卷及密钥。恢复时使用同一安装的完整备份，先在独立环境核对数据库版本和插件依赖。


## 持久流式事件

`GET /v1/tasks/{task_id}/events` 使用 FastAPI 原生 EventSourceResponse。连接建立前验证身份、任务归属和游标，每次分页重验令牌；数据库连接仅占用一次短读取事务。任务行锁保护事件序号，状态变化和对应事件同事务提交，`TaskEvent` 正文加密。读取页上限为该页快照的事件序号，防止跨 READ COMMITTED 查询读到未来轮次事件。

客户端收到当前快照后按顺序处理 `task.status`、`task.result` 与 `plugin.*`，记录事件 ID；重连通过 Last-Event-ID（优先）或 after 查询参数继续。快照、stream.end 和 stream.error 不推进游标。终止和 waiting_input 会关闭流，审批/恢复后需重新连接。事件保留在任务记录生命周期内，目前不提供自动日志清理。

插件通过 context.events.emit 发布 JSON 进度，受单条 64 KiB、任务 10,000 序号及总量 8 MB 预算约束；终态等宿主事件不因插件耗尽预算而被省略。原生模型增量由插件自己消费 Runner.run_streamed 并发送，断线不会取消任务。当前实现每连接 0.5 秒轮询；大量并发订阅的数据库负载需要实际容量评估。
