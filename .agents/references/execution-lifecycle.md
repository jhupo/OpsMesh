# 实例、Session 与环境

## 所有者与入口

`src/opsmesh/execution/instances` 保存配置创建出的逻辑实例；`sessions` 保存私有会话与 SDK 历史索引；`runtime` 管理持久环境和操作回执；`runs` 管理单次 Agent 执行、审批、信号与效果。

## 不变量

- 配置定义、执行实例、SDK 活对象和 Run 是不同层次。进程重启可重新组装 SDK 活对象，不代表删除用户实例。
- 环境或 Agent 实例由用户显式创建和删除。Run 结束、失败或取消不能顺带删除实例、Session、环境或资源目录。
- execution 不导入 Temporal 或 workflows。直接执行器运行 SDK；持久派发由 bootstrap 注入，Temporal 适配归 `workflows/engines/temporal`。
- Session 按用户和实例授权；序号、代际与忙状态保护历史顺序，不能并发覆写同一会话。原生 Session 历史由 SDK 协议操作。
- 操作使用稳定幂等标识。外部效果不确定时进入对账流程，不能把重试当成从未执行。
- 纯模型推理的 `inference` 回执只代表费用待确认；停止 Run 并释放 Session 顺序和并发占用，仍保留 Token/费用预留。远程历史、托管工具、不透明请求覆盖、历史写入和工具效果不确定时仍阻塞并要求对账，不能把所有模型失败都当成无副作用。
- 工具准入冻结描述、审批策略、插件版本和安装配置摘要；调用及审批恢复时核对当前版本，变化后必须新建 Run，不兼容缺失冻结记录的旧审批。
- 共享沙箱先合并配置图的 manifest 与静态 Skill，由 SDK 的公开接口写入；不清空其他文件。一次 Activity 内的多个步骤有独立回执，Activity 重试沿用原有步骤标识。
- 取消只停止当前 Run 的工作并结算持有的额度；不能绕过所有权、行锁、审批或效果回执。
- 沙箱容器和共享存储使用同一授权绝对路径；关闭运行时句柄和删除持久文件是不同操作。

## 改动检查点

核对用户隔离、重试幂等、Token/容量结算、取消、重启后恢复、审批恢复及明确删除。真实 Docker/PG/模型验证需要用户明确要求；静态通过不能证明这些场景通过。

源码入口：`execution/runs/{service,executor,driver}.py`、`instances/service.py`、`sessions/{service,storage}.py`、`runtime/{service,sandbox}.py`。
