# 编排模块

`agents` 管理原生 SDK Agent 配置；`execution` 管理配置创建出的逻辑实例、持久沙箱、
Session 和单次 Agent Run；`workflows` 管理编排定义、绑定实例、图执行及其 Run。
Temporal 适配器集中在 `engines/temporal`，由 `bootstrap` 装配。

`triggers` 管理已有实例的定时触发与签名 Webhook；Temporal 原生 Schedule 同步、触发 Workflow
和评测 Workflow 也位于引擎适配目录。运行事件支持持久序号续传。输入、权限和生命周期见
[平台管理与自动化](../../../docs/platform-management.md)。

## 文件职责

```text
workflows/
  contracts.py                图、节点、绑定、冻结计划
  models.py / schemas.py      编排定义、草稿、发布版本、共享权限
  access.py / service.py      定义权限与管理
  publication.py              发布不可变版本
  invitations.py / routes.py  共享及定义 HTTP 接口
  schema.py / validation.py   有界 JSON、离线 Schema、定义校验
  graph.py / compiler.py      DAG、嵌套边界、子编排闭包、资源槽编译
  conditions.py / mappings.py 条件和显式数据映射
  bindings.py                 发布版本与已有 execution 资源的权限校验
  values.py / context.py      纯值处理及运行时协议
  runner.py                   有界图调度、超时、取消、分支收敛
  nodes/
    base.py / registry.py     节点协议和显式注册
    agent.py / tool.py        调用执行实例或宿主工具适配器
    control.py                条件、转换、汇合、并行、循环、等待、子编排
  instances/
    models.py / schemas.py    冻结发布闭包和绑定，不复制 Agent 或沙箱
    access.py / service.py    创建、启停、删除标记
    routes.py                 实例 HTTP 接口
  runs/
    models.py / schemas.py    Run、节点、事件、信号、工具效果回执
    access.py / service.py    私有 Run、提交、准入、结束
    nodes.py                  从执行路径定位冻结节点
    control.py                审批、取消、定址信号
    operations.py / events.py 幂等效果、人工对账、有序加密事件
    routes.py                 状态、节点详情、结果、控制及对账接口
  engines/
    base.py / registry.py     引擎接口和宿主注册的事务级工厂
    temporal/
      engine.py               事务 Outbox：启动、取消、唤醒
      workflow.py / runtime.py 图执行的 Temporal 适配
      activities.py           授权、数据库、工具和 execution Run 关联
      agent_workflow.py       原生 Agent/Runner 和官方 Agents 插件
      agent_activities.py     模型、工具、历史和沙箱的授权 I/O
      agent_bindings.py       原生 SDK 资源在 Workflow 内的适配
      callbacks.py            动态指令、Prompt、守卫及 Hooks 的 Activity 代理
      failures.py             沙箱文件异常的原生 SDK 合同转换
      session.py              Session 协议到 Activity 的桥接
      sandbox_workflow.py     显式沙箱操作
      execution_dispatch.py   execution 注入使用的事务派发函数
```

图调度器依赖运行时协议和节点注册表，不能导入 Temporal、HTTP 或 ORM。
增加引擎需要实现运行时协议和引擎接口，并由宿主注册；当前提供 Temporal 引擎。
数据库表和接口不属于 Agent SDK 插件开发 SDK 的业务客户端。

## 使用流程

所有路径相对于配置的 API 前缀。

1. 通过 `/workflows` 创建定义并发布版本。Agent 节点声明 `binding` 槽名，
   Tool 节点声明 `binding` 和 `operation`；定义不创建运行实例。
2. 先通过 execution 创建 Agent 实例，按需创建环境和 Session。
3. `POST /workflows/instances` 指定 `revision_id`、`name`、`request_key` 和
   `bindings`。必须精确提供整个子编排闭包使用的槽，不能越权绑定其他用户资源。
4. `POST /workflows/runs` 指定 `instance_id`、`request_key`、`input`，可选
   `engine`（默认 `temporal`）。Run 与 Outbox 在同一事务提交。
5. 读取 Run、`/nodes`、`/events`、`/result`。Agent 节点的
   `execution_run_id` 指向单独的 execution Run；其原生工具审批走 execution 接口。
6. 编排工具节点审批前读取 `/nodes/{node_id}` 的参数和冻结描述，再向
   `/nodes/{node_id}/approval` 提交当前 `approval_id` 和 `allow`。
7. `/signals` 需要精确的 `path`、`name`、`request_key` 和 `value`；例如
   `entry/await_reply`。嵌套路径使用 `branch_0`、`item_0`、`iteration_0`。
   同一定址只接受一个幂等信号值；唤醒通知通过 Outbox 投递给现有 Workflow。
8. `/cancel` 停止本次编排。显式删除编排实例只写删除标记，不能删除关联的
   Agent 实例、Session、环境或目录；这些资源仍由各自模块管理。

绑定示例（UUID 替换为当前用户已有资源）：

```json
{
  "agents": {
    "reviewer": {
      "instance_id": "00000000-0000-0000-0000-000000000001",
      "max_turns": 16,
      "timeout_seconds": 600
    }
  },
  "tools": {
    "search": {
      "kind": "mcp",
      "resource_id": "example-plugin/mcp/search",
      "config": {},
      "allowed_operations": ["search"],
      "require_approval": true
    }
  }
}
```

Agent 节点默认 `input_mode: "json"`，把 `arguments` 对象转换为 JSON 文本输入。
`input_mode: "messages"` 要求参数仅含 `messages` 数组，按 execution 的原生 SDK
消息合同校验后传入，保留图片等多模态内容。数据映射使用 `{"$ref":"input.query"}` 或
`{"$ref":"steps.review.output"}`，只能读取已声明依赖的结果。
条件节点输出布尔值，下游通过 `when` 选择执行；没有任意 Python/表达式求值。
并行支持 all/any/quorum，foreach 和 repeat 都需要静态上界。

可信网关来源带 session_scope 时，runs/sessions.py 按路由代际、平台用户、会话、编排实例
和 Agent 绑定选择私有原生 Session，不共享其他用户的固定绑定历史；普通编排保留显式绑定。
这份映射属于编排运行管理，底层 Session 的创建、存储、重置与删除仍属于 execution。
决策分支示例见 [TypeSafe Decision](../../../sdk/python/examples/typesafe-decision/README.md)，
厂商协议和业务规则不进入图调度器或网关核心。

## 工具及恢复边界

`bootstrap/workflows.py` 提供宿主工具适配器。插件工具按当前安装版本、配置和管理员
权限校验；MCP 资源由已安装插件注册的原生 MCPServer 工厂提供，SDK 管理连接生命周期。
不内置业务连接器或 OAuth 流程。独立 MCP 工具节点要求审批，返回原生 CallToolResult
JSON（包括错误标志）；业务图可以据此分支。Agent 内 MCP 仍走 Agent 配置和 SDK。

工具审批绑定精确参数、工具描述、版本和配置摘要，执行前重新检查；热更新后如果
描述变化，原审批不能执行新工具。参数、结果、信号、事件和冻结绑定加密存储。
连接工厂和 tools/list 也必须适合重复调用；业务写入只能放在工具调用中。

工具审批和外部信号通过事务 Outbox 唤醒 Workflow，等待期间不进行每秒数据库轮询。
Agent 排队准入采用退避，并在获得执行资格前 Continue-As-New 控制历史大小；
已中断的原生 RunState 留在当前 Workflow，审批后继续，不跨新历史序列化迁移。
动态指令、Prompt、守卫和 Agent Hooks 的 I/O 由独立 Activity 执行，注册版本与
准入快照一致才允许调用。支持范围及载荷限制见 [execution](../execution/README.md)。

工具写入先记录稳定效果 ID；插件收到该 operation_id，可以用于外部幂等。
MCP 协议没有通用业务幂等承诺，不自动将该 ID 注入业务参数。结果不确定时不重复
执行，Run 进入 `needs_attention`。管理员先对账 `/effects` 与关联 execution Run，
提交带证据的 `/reconciliation`；若最后只剩已对账的 Agent Run，再调用 `/settle`。
对账关闭为 failed，不伪造原编排成功或继续执行剩余节点。

取消、超时或 quorum 收敛会取消并等待所属分支；结束时还会取消已经提交但响应丢失的
Agent 准入。占用尚未确认释放时保留 needs_attention，不能据此删除环境。
编排并发限制按 Run 生效，Agent 调用还受 execution 的用户配额约束。

## 迁移与验证范围

编排实例迁移 `0003_workflow_instances` 依赖 `0002_execution_instances`，后续迁移
继续沿 Alembic 图演进。迁移保留旧定义和历史记录，不自动转换旧 revision_id 节点
或旧 driver Run；不能因源码清理删除已发布迁移。当前 head 以 Alembic 图为准。

已部署系统升级前应先处理旧 Worker 的活动任务，再切换新版。旧图 Workflow、
provider probe 和旧 MCP Connection Workflow 不再注册；这些历史不承诺由新引擎重放。
当前实例编排的测试入口及覆盖缺口见 [测试说明](../../../tests/README.md)。
PostgreSQL 迁移、Temporal 重放/恢复/取消、Docker、外部模型和 MCP 行为需要在
隔离环境验证，源码和静态检查不能证明这些运行时行为。

维护时按 [变更校验技能](../../../.agents/skills/code-change-verification/SKILL.md)
执行受影响范围的静态检查；检查通过不能替代真实服务中的恢复、取消和重放验证。
