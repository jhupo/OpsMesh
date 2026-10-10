# 共享 Runtime 宿主

Runtime 是工作空间内预先批准的执行宿主。Agent 配置和 MCP 部署引用宿主；增加 Agent、请求或 MCP 不自动增加容器。只有显式选择 `isolated` 的不可信任务才创建独立容器。

`shared` 在同一模板、空间、资源限制、网络和隔离策略的已有宿主中分配执行槽。共享宿主支持并发，宿主容量由 `limits.max_concurrent_executions` 配置，默认 16，范围 1–128，不能超过 PID 上限；实际容量应匹配宿主内存与任务开销。

PostgreSQL `runtime_allocations` 保存工作空间、宿主、所有者类型和 Run/部署 ID。领取锁定宿主行并检查已占用槽数，同一所有者重复投递复用原槽；锁和事务在目录准备、SDK 启动和网络 I/O 前结束。`runtime_leases` 只描述容器生命周期，不记录执行独占状态。

Worker 通过有界 asyncio Task 并发执行请求；阻塞的数据库与 Docker 控制调用进入有界 I/O 线程池，等待 SDK 流不占用线程。Worker 心跳声明 `region`、`capabilities`、`runtime_modes`、`task_slots`、`mcp_slots`、`cpu_count` 和 `memory_mb`，领取时按 PostgreSQL 中的活动租约再次检查这些条件。Redis 队列保留优先级，并在当前有界候选窗口内按 workspace 轮转；公平游标更新与领取租约在同一 Lua 操作内提交。SDK 在 Runtime 子进程执行自己的异步 Runner 和 Session；平台没有外层 Agent 循环。每个 Run 使用独立进程组、私有 RPC、`/workspace/runs/<run_id>`、HOME 和临时目录。工作空间间不共享宿主；同一宿主必须属于相同可信权限范围和网络策略，进程目录分离不是容器级安全隔离。

容量满时 Run 进入 `waiting_runtime`，持久标记 `runtime_capacity_waiting`，Worker 不占着线程或执行槽等待。维护从 PostgreSQL 恢复等待任务并重新入队；容量等待不计入 SDK 执行超时。MCP 的对应状态是 `waiting_capacity`。

取消或终态只终止对应 Run 的进程组，核对 Linux PID 启动时间以避免 PID 重用误杀。清理失败保留槽以阻止超领，维护重试对应 Run 清理；禁止杀宿主全部进程或清空共享 `/tmp`。审批暂停释放共享槽并保留目录和 SDK 状态，批准后重新领取容量继续执行。共享宿主的终态 Run 目录会被删除。

托管 MCP 必须绑定已有运行宿主，每个运行部署占一个槽。SDK 管理各自 stdio 子进程；停止或重启等待该服务会话退出和私有 Socket 移除，其他 MCP 与 Agent 继续运行。停止后可通过部署 host 接口迁移到其他已批准宿主，详见 [托管 MCP](managed-mcp.md)。

`GET /api/v1/workspaces/{workspace_id}/runtimes/{runtime_id}/allocations` 返回分页执行占用表。宿主有活动占用时，管理命令、停止和删除被拒绝；管理命令本身以持久 running 记录阻止并发领取，提交后才执行 Docker I/O。

升级前排空 Agent 任务并确保每个托管 MCP 都已绑定宿主。迁移 `0117_shared_runtime_hosts` 建立共享宿主分配表，迁移 `0118_shared_runtime_execution_mode` 将历史 Runtime 统一为 `shared` 并删除 `pool_key`。降级前必须停止所有任务和 MCP。当前流程测试覆盖真实 PostgreSQL 并发、迁移升级/降级、容量排队恢复、Chat 授权、项目文件和单进程生命周期。

Docker 宿主身份由 Docker Engine 的稳定 ID 标识，写入 Runtime 的 `capabilities.node_id` 与 Worker 心跳。任务领取从数据库中的 Run、MCP 部署或 Runtime 控制目标解析归属，不信任请求中的节点标记。尚未分配的共享 Run 可由任意具有匹配批准宿主和空闲容量的节点领取；分配后只由所属节点执行和恢复。MCP 与容器控制始终绑定所属节点。未声明节点的存量托管宿主禁止执行，部署时必须由运维对照 Docker 容器登记节点。节点级 PostgreSQL 事务锁将容量检查与租约写入串行化；CPU、内存需求会扣除该节点所有活动租约已登记的资源预算。预算是准入预约，容器限额仍负责实际资源限制。

队列候选窗口在不同页间轮转，防止不合格任务长期占住队头。优先级与工作空间轮转作用于本次候选窗口，不承诺全队列的严格优先顺序。多节点使用共同 PostgreSQL/Redis，分别运行自己的 Worker 与 Runtime；容量扩大通过增加机器及其执行宿主实现。
