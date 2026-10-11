# 共享 Runtime 宿主

`WorkspaceRuntime` 是工作空间批准的执行策略；`RuntimeHost` 是节点上的物理容器。相同工作空间、镜像、空间和资源预算的可信 `shared` 策略复用一个宿主；网络白名单不参与物理宿主分组。Agent 和托管 MCP 引用策略，不自动创建容器。不可信代码显式使用 `isolated`；跨工作空间不共享物理宿主。

PostgreSQL `runtime_allocations` 保存物理宿主、逻辑策略、Run/MCP/命令所有者、执行 UID 和冻结网络策略。准入锁定物理宿主行，各策略共同占用 `RuntimeHost.capacity`，默认 16、范围 1–128，并受 PID 与内存预算限制。相同所有者重复投递复用占用；事务在启动进程和网络 I/O 前结束。卷与空间容量预留属于物理宿主，解绑一个策略不删除其他策略的资源。

Worker 使用有界 asyncio Task，阻塞数据库和 Docker 控制进入有界 I/O 线程池；等待 SDK 流不占线程。SDK 在 Runtime 子进程使用原生 Runner、Session、审批恢复和 MCP；平台不增加外层 Agent 循环。执行有独立 UID、私有 RPC、HOME、临时目录和 `/workspace/runs/<run_id>`。工作负载 capabilities 为零，启用 `no-new-privileges`。

容器启动时，可信控制程序配置 nftables，然后降权运行内部代理。OUTPUT 默认拒绝，按 socket UID 强制网络权限：`none` 无网络；`restricted` 只能连接自己的 loopback 代理端口，由代理校验域名/IP 和端口；`internet` 可访问公网 TCP/UDP。宿主、其他容器、私网、link-local 和元数据地址均禁止；DNS 仅允许配置解析器。修改环境变量、请求头或使用其他身份的代理端口不能扩权。共享 Runtime 不支持私网目标。

内部代理是 Runtime 进程，不增加模型网关容器。Docker 控制面使用 root 和 `NET_ADMIN/KILL/CHOWN/DAC_OVERRIDE/SETUID/SETGID`；用户代码没有这些权限，不能写控制策略。UID 和私有文件权限提供可信工作空间内的执行边界，不替代不可信代码的容器或虚拟机隔离。

取消先撤销网络，再终止该 UID 的全部进程，包括脱离原进程组的子进程。确认退出并清除私有临时目录后才释放占用；失败保留占用供重试，UID 不提前复用。审批暂停释放槽，将保留 Run 目录封为 root-only；恢复重新领取身份并赋予目录权限。终态只删除对应 Run 目录，不清空共享宿主。

容量满时 Run 进入 `waiting_runtime`，MCP 进入 `waiting_capacity`；等待不占 Worker 执行槽，不计入 SDK 执行超时。管理命令要求物理宿主空闲，执行时持久占用并取得身份。停止影响全部绑定策略，活动占用时禁止停止。删除一个策略只解绑；最后一个绑定删除容器、卷和空间预留。

Runtime 与 allocations 响应带 `host_id`；多个策略可以具有同一容器 ID。allocations 接口返回该策略的分页占用，客户端不能把策略数量当作物理宿主数量。

Worker 心跳声明地区、能力、任务槽、MCP 槽、CPU 和内存。Docker Engine 稳定 ID 保存于 `runtime_hosts.node_id`；领取从数据库解析归属，不信任客户端 routing 或 JSON 能力中的节点提示。未分配共享 Run 可由有匹配策略和容量的节点领取；分配后仅所属节点执行和清理。分布式扩容增加机器，各自运行 Worker/Runtime，共用 PostgreSQL/Redis。候选窗口轮转并按优先级、工作空间公平领取，不承诺业务副作用 exactly-once。

升级 `0119_runtime_host_process_identity` 前必须排空任务、停止托管 MCP，备份数据库、卷和部署配置。活动占用使迁移直接拒绝；旧容器登记为 `requires_reprovision`，部署程序重建身份监督宿主后移除旧容器。降级也须排空；共享容器的多个策略必须拆分并重新配置旧版容器，不能由旧执行逻辑接管新容器。

`0120_retired_egress_configuration` 清理 Agent、可恢复版本、团队、Runtime Space 和已发布定义中的旧网关配置。冻结 Run 授权与审计证据保持原样；旧 Run 不能直接当作新授权恢复，须按当前定义重新授权创建任务。降回 0119 不恢复已淘汰的网关地址。

运行 `python scripts/verify_runtime_isolation.py --image sha256:<immutable-image>` 验证真实网络允许/拒绝、提权拒绝、并发 SDK 审批恢复与 Session 续接、取消脱离进程组的子进程、邻近任务存活和身份复用清理。脚本使用有界临时容器，不调用业务订单 API。数据库并发、租户隔离与 migration 使用 `tests/test_runtime_allocations_postgres.py` 和真实 PostgreSQL。跨机器调度与节点断电接管需要至少两台机器，不能用单机测试代替。
