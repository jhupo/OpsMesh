# S14 常驻部署合同

2026-10-19，按 V4 第 0.7 节修正：当前不实现网络控制。本文是常驻部署的执行细化，
不是已完成或已验收声明。

## 所有者与调用链

- capabilities 保存不可变包声明；管理员安装并批准包的权限上限。
- extensions 保存每个实例的 ManagedDeployment。显式 start/stop 请求在同一 PG
  事务写部署事实、审计与 Outbox，ExtensionWorkflow 通过 Activity 操作独立 Docker
  容器。安装不启动部署；实例配置不启动部署；Invocation 不创建常驻容器。
- managed_worker 的实例不引用 orchestration.runtime Environment；sandbox 实例必须引用已显式
  创建的原生 Environment。两者均使用同一套 Invocation 和宿主回执。
- orchestration.runtime 拥有原生 sandbox 环境和操作回执。bootstrap 选择部署或
  原生环境，extensions 不反向导入 orchestration。
- SDK 提供独立进程入口和私有 Unix socket 协议。核心进程不 import 包代码；
  常驻进程没有公开监听端口、Docker socket、用户 Session 或管理员凭据。

## 状态、授权与并发

- 一个实例同一时刻只有一个部署；部署固定实例修订、artifact 和 scope。
  状态为 starting → ready → stopping → stopped；效果不确定进入 needs_attention。
- owner 显式启动/停止自己的实例；启动和 Activity 再授权当前用户、范围、安装、
  实例修订。配置更新前必须停止部署。管理员停用/卸载发布停止意图，先撤销授权。
- 容器标识由部署 UUID 生成，重试只能核对同标签、同 artifact 的已有容器，
  不能创建第二个实例或自动重启失联进程。stopped 后的新 start 是新的部署事实。
- max_concurrency 是包声明的资源上限，范围 1–64；PG 锁住实例后领取 Invocation，
  容量不足保持 queued，由原 Temporal Workflow 等待重试，排队截止时间仍有效。
  SDK 进程再限制并发，不产生第二套持久队列。needs_attention 调用占位至人工核对。
- 调用仍只在实例 owner 的主体和当前 scope 下运行；每次宿主 RPC 重新授权。
  常驻仅保留进程/连接，不使 Invocation 委托跨越完成或 deadline。
- 单次取消只断开该调用，不能杀死共享容器。超时/连接断开不能证明外部操作未发生，
  保留 needs_attention。停部署终止其进程，受影响的已开始调用也必须待核对。
- 长连接由显式部署在冻结的 owner/scope 授权内维持，至停止或授权撤销。
  平台宿主访问仍须有 deadline 的 Invocation 委托；SDK startup/shutdown 没有宿主
  客户端或平台身份。包绕过宿主的外部写入不具有平台幂等保证，必须自行提供效果回执。
- 平台 admission.drain 同事务关闭准入并发布所有常驻部署的停止意图；未确认停止前
  计入活跃工作，备份与主机更新不能越过。恢复准入不自动重启部署。使用配置关闭同样
  先停止其自有部署；状态恢复拒绝仍有常驻进程的实例。
- 已提交但尚未启动的部署 Workflow 在排空期间仍可投递，以完成停止；Activity
  重新检查关闭的准入，只执行清理，不能启动包进程。普通安装/调用仍禁止开始。
- Docker 开始/停止效果提交后，即使 Activity 被取消也先等待该效果结束，再记录不确定
  状态并交由原 Workflow 核对；不能让停止越过尚在创建容器的线程。

## 当前网络范围

运行容器和官方原生 sandbox 使用底层默认网络行为。平台不创建专用网络，不安装
防火墙规则、不固定 DNS、不设置运行容器的 network_mode，也不添加网络就绪门或
直接联网拒绝逻辑。SDK manifest、安装、实例与部署不再声明 network_targets。
当前不提供网络隔离或目标白名单保证；未来网络能力归执行基础设施，不归 shared。
包安装使用 `pip --no-index` 与固定依赖；这不等于容器网络隔离，构建和入口发现也不
额外设置 Docker 网络模式。

## 验收门槛

真实 PG/Temporal/Docker 必须覆盖显式启动/停止与重放、进程复用、并发容量、不同
调用的宿主身份、单次取消不影响邻居、致命退出不自动重启、未知写入不重跑、
停用/卸载排空。旧受控网络验收不再属于当前范围，也不能用作当前隔离保证。
SDK 需独立安装、打包与常驻协议验证；所有新表合入唯一空库初始迁移。

当前验证范围见[统一编排交付记录](agent-orchestration-delivery.md)。对象归属调整前的分步报告
和原始日志已清理，不能作为当前版本通过的证据。完整封板仍需当前交付记录列明的真实
模型、MCP、沙箱、外部协议、发行安装、整套恢复与生产切换证据。
