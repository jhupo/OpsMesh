# Worker 协程并发与任务控制

日期：2026-10-09。本文描述当前分支实现。代码尚未部署，服务器 OpsMesh Worker 保持停止。

## 用户配置边界

平台保留用户配置的 Agent/Manager、工具权限、团队、项目、知识库和记忆机制，不增加内置业务意图分类器、向量路由或自动决策模型。业务意图由用户指定的 Manager 处理，权限、准入和风险门禁仍由平台校验。

U号租客服 Manager 的服务器配置已通过现有 Agent API 更新为 gpt-5.5，版本 7，保留 reasoning.effort=medium、Responses API、原凭据和其他配置。未调用模型验证供应商是否提供该模型，未再次查询业务订单。

## 执行模型

Worker 每次启动一个长期 asyncio 事件循环。空闲活动槽才领取 Job，领取后创建独立 asyncio Task，直到持久结算和 ACK；超过槽数的 Job 留在 Redis。普通 Job 失败单独结算，不取消其他用户的 Task。

Agent Run 的模型等待在事件循环中 await 原生 SDK，不占用一个完整 Job 线程。现有同步数据库和工具通过有界 BlockingIO 适配器执行。每次数据库阶段重新创建、提交、关闭 Session，仅将 DTO/SDK 合同对象返回协调器。SDK Session 回调、工具审查、工具执行各自拥有 Session，不与主 Run 或其他协程共享 ORM 对象。

Task、Run、Job、asyncio Task 是四个不同概念：Task 是持久业务流程，Run 是一次 Agent 执行，Job 是队列投递，asyncio Task 是本进程执行载体。长时间编码、复核、测试等待不会因为超过一次队列轮询而重新调用 SDK。

```mermaid
flowchart LR
    API[API 保存请求] --> PG[(PostgreSQL)]
    API --> Q[(Redis Job)]
    Q --> A[有空槽才领取]
    A --> T[独立 asyncio Task]
    T --> P[短事务准备请求]
    P --> SDK[await 原生 SDK]
    SDK --> G[独立 Session / 工具网关]
    SDK --> F[短事务结算]
    F --> ACK[持久完成后 ACK]
    IO[有界同步 I/O 池] --> P
    IO --> G
    IO --> F
    C[独立控制 I/O 与续租协程] --> T
    M[单槽维护池] --> PG
```

SDK 负责一次 Run 的模型/工具循环。平台负责领取、授权、配额、阶段依赖、审批、取消和恢复。消费队列的常驻循环不重启完成的 SDK Run。Provider fallback 仍遵守冻结的既有策略，最多选择一次备用 Provider，不是业务意图路由。

## 池与配置

| 配置或组件 | 当前行为 |
| --- | --- |
| --concurrency | 活动 asyncio Task 上限，默认 32 |
| --blocking-io-concurrency | 同步数据库/工具/业务 handler 的执行线程上限，默认 8 |
| 控制 I/O | 独立 2 线程，执行领取、Worker 心跳、租约与取消状态读取 |
| 维护 I/O | 独立 1 线程，恢复与低频维护不重叠 |
| --max-jobs | 本次总领取数量上限，与并发槽数独立 |
| --once | 领取至多一个 Job，使用相同异步执行路径 |

BlockingIO 在提交线程池前取得信号量，不往线程池内部无界堆积操作；取消 await 时等待已提交操作完成后才释放容量。进程内等待者仍受活动 Job 上限限制。第三方 SDK 和 Docker 适配器可能额外使用其自身受限线程，以上数字不是进程总线程数承诺。

其他同步任务（Runtime 管理、MCP 独立 Job、索引、导出、Webhook 等）进入有界 I/O 池，当前仍会在其操作期间占一个线程。CPU 密集索引没有迁移到独立计算服务。单 Run 产品工具暂时保持顺序执行；没有因此承诺副作用工具可安全并行。

WorkerNode.capacity.max_jobs 继续表示持久领域容量，不是旧参数兼容别名。心跳报告 active_jobs、available_slots、execution_backend=asyncio、blocking_io_concurrency。多副本仍需唯一 Worker ID，数据库/Provider/Runtime 配额仍适用。

32 是默认准入上限，尚未证明生产吞吐。不能按注册用户数直接设置容量；应根据到达率、执行时长、资源预算、排队 p95、event-loop lag、线程/RSS、DB 池等待和 Provider 429 调整。

## 事务与所有权

准备请求、预算和请求审查、模型尝试计量、最终结算均通过独立 Session 完成。同步基础设施适配器在自己的线程内创建与关闭 Session；模型等待期间不保留主 Run Session。MCP 仍在远端调用前提交调用意图，结果回写前检查所有权。

执行上下文通过 ContextVar 隔离。DatabaseOperations 在每次操作开始、提交前和内层 Session.commit 前检查所有权。失租后旧执行者不得提交、ACK 或重新投递；由恢复流程核对持久 Run。工具外部副作用仍不保证 exactly-once。

Agent Run 锁在事件循环中按 TTL/3 续期，不再为每个模型 Run 创建续租线程；Redis 同步命令走控制适配器，保留原有原子 token 语义。队列租约由独立协程续期，不占活动 Job 槽。同步 direct workflow 节点仍使用原领域执行路径及其锁。

实时文本/工具预览使用每个模型请求最多 64 条的内存缓冲，由 I/O 适配器发布，不在 SDK 回调中同步访问 Redis。拥塞时允许丢弃旧预览；PostgreSQL 的持久事件与 Outbox 不经过该缓冲。

## 续接、补充指令和纠正

已完成后追加输入继续使用 Conversation 新 turn；ConversationMaintenance 绑定既有授权入口并读取先前会话结果。同一会话按 sequence 顺序推进，不同会话可并发。

审批恢复继续使用同 Run、加密 SDK 快照和已审批工具决定；重建 Worker 后可以续接，结束时标记快照 consumed。本地 SDK Session 作为历史事实来源时，不同时传递 previous_response_id/conversation_id，避免重复注入完整历史。

add_instruction 返回 status=accepted、delivery_mode=next_run、delivery_status=accepted，表示保存成功，供后续 Run 使用。当前 Run 构建请求时冻结消息 ID，并记录 task.instructions.delivered；SDK 返回后记录 consumed。冻结空集合，确保运行中到达的指令不会在 Provider fallback 或审批续接时混入当前 Run。

这些指令是 Task 的持续补充要求，可被后续 Run 读取；不会自动创建额外 Run。已终态 Task 必须创建 correction 或新会话输入。delivery_mode=live 当前明确拒绝；OpenAI/Claude 适配器没有宣称 LIVE_STEER。没有伪装成 Codex turn/steer，也没有为不支持的 SDK 修改内部私有状态。

已有 TaskCorrectionService 继续创建跟进 Step，保留目标 ID、修订说明和 Manager 复核策略。本次未增加完整产物版本锁定和 expected_version 控制 Inbox。

SIGINT/SIGTERM 停止领取，向活动 SDK 设置协作取消。停止导致的错误不再重试；失租与业务取消区分。同步 handler 和已发出的外部工具只能等待自己的取消能力、超时或返回，不能强杀 Python 线程。drain 保持停止领取、等待当前工作结束。

## 验证与限制

- 聚焦回归 119 passed / 6 PostgreSQL skipped，MCP 执行与适配器回归 37 passed；PostgreSQL 6 组在独立进程通过；修改前提交复现了 2 个 URL 规范化断言和 1 个旧 Runtime 工厂测试失败。Ruff 与 mypy（1081 个文件）通过。
- 完整 Worker 流程验证活动槽有界、两个 SDK Run 位于同一 OS 线程不同 asyncio Task、单 I/O 线程不限制 SDK 等待并发、第三个 Job 留队列、取消、失租和维护。
- 相同工作空间/不同工作空间 × complete/cancel/lost 共 6 种 PostgreSQL 流程通过，测试在隔离 schema 强制 create_all(checkfirst=False)。没有业务订单/真实模型调用。
- 会话历史独立事务、晚到指令冻结、审批快照跨 Worker 重建恢复、快照加密与消费、live 指令拒绝、慢预览不阻塞控制池、异步锁超 TTL 与 replacement token 保护均有流程验证。
- PostgreSQL 旧夹具先前误写 public 的 3 组测试工作空间已归档、测试用户/凭据禁用、测试 Worker 记录移除，保留不可变审计；本轮隔离 schema 验证没有重复该问题。

尚未实现：SDK 进程整体迁移到 Runtime host/RPC、原生 redis.asyncio/AsyncSession 全栈迁移、实时 steer、完整控制 Inbox/Outbox、跨租户公平队列、服务等级保留槽、独立 CPU 计算服务、跨地区部署及生产压力验收。现有 Runtime 工具边界继续生效，但不宣称 SDK 宿主已隔离迁移完成。

服务器只更新了 U号租 Manager 配置；Worker 代码未部署重启。
