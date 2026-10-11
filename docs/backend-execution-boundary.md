# 后端执行边界与 SDK 职责

API 持久化任务意图后返回 Run 标识。Worker 的异步执行器领取 Run，在短事务中重新检查租户、执行身份、授权快照、审批、成本和 Runtime 状态，再释放事务。模型执行只有 `AsyncAgentRunExecutor` 一个入口；直接工具、审批和子工作流节点由 `DirectWorkflowExecutor` 处理。

模型 SDK 在批准的 Docker Runtime 中启动独立进程。Worker 通过 Docker exec 双向通道传递冻结请求、会话操作、产品工具回调和事件。Runtime 不接收数据库连接、Docker 客户端或 ORM 对象。回调只能使用冻结的工作空间、Run 和能力范围；嵌套专家的上下文也必须匹配已批准的范围。

SDK 拥有模型与工具循环、Session 历史、RunState、审批中断和恢复、原生 MCP 消息，以及支持的 Sandbox/Memory 能力。平台提供持久化 Session 的公共接口适配、授权、隔离、审批事实、租约、成本和审计。平台不拼接历史消息，不复制工具结果为用户消息，不设置压缩阈值，不启用另一套压缩算法；只转发 SDK 实际产生的压缩日志。

OpenAI 的原生 Sandbox 工具要求 Responses API。Chat Completions 的 SDK 仍在隔离 Runtime 中执行，其产品工具和 MCP 可用；原生 Sandbox 能力不可用。SDK Memory 还要求持久 Runtime 和用户、Agent 的存储范围。

MCP 调用分成事务准备、异步 Runtime 等待、事务结算。准备阶段持久化 `running` 调用记录，带配额的服务器在短事务中锁定以串行化准入；等待阶段释放锁、数据库 Session 和阻塞 I/O 槽位。完成阶段更新同一条记录，保留原生 `CallToolResult` 的内容和 `isError`，对未脱敏的原生返回计算哈希，再脱敏持久化。MCP 返回 `isError=true` 会记录为失败。

取消是明确的执行合同。SDK 使用其取消接口；控制面先撤销对应执行身份的网络，再回收该身份的全部进程与私有临时目录，自托管 MCP Job 持久化取消意图。关闭通道会取消尚未完成的 MCP 操作。外部业务操作已产生的副作用不能通过本地取消撤销。

纯同步 HTTP 路由使用 `def`，由框架放入线程池。保留的异步路由负责真正的异步流、上传和等待。Worker 使用有界异步任务并发和有界阻塞 I/O 通道；长时间模型或工具等待不占用事务线程。

`0116_execution_contracts` 将既有配置迁移为唯一的合同，包括网络 `none/restricted/internet`、MCP 连接、队列 Runtime 路由和冻结授权快照。迁移保存被修改的原始配置以支持降级；生产读取代码不再识别旧别名。用户输入和已完成的历史审计数据不作通用递归改写。

部署必须同时更新后端镜像和 Runtime 镜像。已有容器不会因模板镜像变更而自动获得 SDK 宿主；需要按 Runtime 生命周期重新配置。模型访问权限应绑定到具体 Agent 使用的 Runtime，不能以平台出网许可代替模板和 Agent 的授权。
