# 编排与 Temporal 边界

## 所有者与入口

`src/opsmesh/workflows` 管理定义、共享、不可变发布、实例、节点与编排 Run。`runner.py`、`context.py`、`nodes/` 定义图语义；`engines/base.py`、`engines/registry.py` 定义引擎协议；`engines/temporal` 提供当前引擎。`bootstrap/worker.py` 装配 Worker。

## 不变量

- 流程是创建定义 → 发布版本 → 创建绑定实例 → 提交 Run。`POST /workflows/runs` 使用 instance_id，不使用旧 `/execution/runs` 的 kind/revision_id 协议。
- 实例冻结发布闭包与已有执行资源绑定。定义/发布不创建 Agent 或沙箱；子编排闭包的槽必须全部精确绑定并授权。
- 纯图调度不导入 ORM、HTTP 或 Temporal。条件和映射使用有界值与明确语法，不能执行任意 Python 或远程 JSON Schema 引用。
- Temporal Workflow 只做确定性编排；数据库、模型、工具、MCP、历史和沙箱 I/O 在 Activity 边界。原生 Runner 使用官方 Temporal OpenAI Agents 集成，不能将整个 Agent Run 包成一个失去恢复语义的大 Activity。
- Run 和 Outbox 意图在同一数据库事务提交，派发使用稳定 Workflow ID；事务回滚不能启动孤立任务。
- 节点路径定址审批/信号，重复请求遵守幂等语义。取消子 Agent Run 不删除绑定实例；编排删除不级联销毁独立资源。
- 审批和信号等待通过 Outbox 通知唤醒；排队准入采用有界退避与准入前 Continue-As-New。原生 RunState 存活期间不切换历史。
- 动态指令、Prompt、守卫和 Agent Hooks 走 Activity 代理并冻结注册 revision；其他不可安全传输的回调合同在准入时拒绝。回调上下文是副本，不能依赖修改 Agent 或工厂状态影响 Workflow。
- Temporal 载荷按最终加密编码大小限额，完整准入结果在 Activity 返回前预检；缺失沙箱文件通过公开 FailureConverter 保留原生 SDK 异常。
- 已执行的历史必须保持可重放；修改 Workflow 命令序列需评估版本兼容，而不是重试旧历史碰运气。

## 改动检查点

核对发布隔离、绑定授权、并行收敛、循环预算、节点效果回执、信号定址、取消传播与重放。详细树和 HTTP 流程见 `src/opsmesh/workflows/README.md`；使用 `temporal-python-sdk` 核对锁定 SDK。
