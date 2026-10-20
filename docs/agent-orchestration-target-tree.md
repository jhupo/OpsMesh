# 服务端与 SDK 源码完整目标树及文件说明

这是当前完整源码清单，按 2026-10-20 磁盘源码核对。每个文件右侧均说明现有职责。
范围仅为 `src/opsmesh` 和 `sdk/python/src/opsmesh_sdk`，包括包的 `__init__.py`。
不列测试、部署、operator、工程配置、缓存和历史备份；仅缓存的旧目录不属于源码清单。
本文是 V4 第 0.8、9、10 节引用的完整源码清单，设计入口仍为 [V4 重构方案](source-refactor-plan-v4.md)。
依赖方式以 V4 第 12 节为准：普通业务优先直接调用公开函数，不再维护根/二级依赖白名单。
树中现存 Protocol、bootstrap 业务协调和 Session.info 查找尚未精简，不代表必须保留这些间接层。
文件数量用于核对清单，不作为实施或拆分目标。实现、验证与未验证范围见
[统一编排交付记录](agent-orchestration-delivery.md)。

| 范围 | 当前源码文件数 |
| --- | ---: |
| 服务端 `src/opsmesh` | 309 |
| SDK `sdk/python/src/opsmesh_sdk` | 47 |
| 合计，包含 `__init__.py` | 356 |

相对原设计的 296/48，服务端保留有独立职责的内容存储事务、文件版本和 Agent/Flow
执行合同及入口文件；没有为凑数量合并不相容的事务和协议。SDK 的开发测试 runner
并入 development.testing，正式运行入口在 runtime。确定性定义值放 definitions.contracts，
纯 SDK 输入放 execution.contracts，根 contracts 只保留事务内的命名装配合同。

## 服务端源码

```text
src/opsmesh/
├── __init__.py                          # 服务端包标识与必要公开导出，不启动应用
├── config.py                            # API、DB、Temporal、隔离后端等部署配置
├── bootstrap/                           # 当前进程装配；其中业务协调待移回所属模块
│   ├── __init__.py                      # bootstrap 包标识
│   ├── accounts.py                      # 装配账号开通、首管理员及默认额度初始化
│   ├── api.py                           # 创建 FastAPI 应用，组织启动和关闭
│   ├── approvals.py                     # 注入审批目标校验和决定应用函数
│   ├── capabilities.py                  # 注册能力类型的静态声明校验函数
│   ├── cli.py                           # 首管理员等显式平台管理命令入口
│   ├── connections.py                   # 管理数据库、HTTP、Redis 等基础连接的应用生命周期
│   ├── extensions.py                    # 装配隔离宿主、委托调用及实例授权依赖
│   ├── health.py                        # 装配基础设施探针，不以探针代替产品验收
│   ├── http_policy.py                   # 装配 HTTP 认证、限流、跨域和错误处理
│   ├── metrics.py                       # 装配运行事实的只读指标查询
│   ├── models.py                        # 显式注册各模块 ORM 元数据
│   ├── orchestration.py                 # 注入模型解析、用户授权、额度、资源和扩展调用
│   ├── platform.py                      # 装配审计、审批、策略与必要维护命令
│   ├── request_context.py               # 从可信请求边界建立身份、调用链和追踪上下文
│   ├── routers.py                       # 显式注册公开路由，不提供独立 execution API
│   ├── sandbox.py                       # 装配受支持的隔离执行后端
│   ├── server_cli.py                    # API 服务启动命令及参数解析
│   ├── source_notifications.py          # 将源业务事实装配到持久订阅投递，不做通用事件分派
│   ├── telemetry.py                     # 初始化日志、指标、追踪及退出排空
│   ├── temporal.py                      # 装配 Temporal Client、加密 codec 和官方插件
│   ├── worker.py                        # 注册 Workflow、Activity 和显式批准的可信工具
│   └── worker_cli.py                    # Worker 进程启动、信号处理及退出入口
├── capabilities/                        # 静态能力目录、版本、包与公开授权
│   ├── __init__.py                      # 能力模块包标识
│   ├── lifecycle.py                     # 定义、草稿、不可变版本及读写授权
│   ├── models.py                        # tool/mcp/skill/channel/extension 共用定义和版本 ORM
│   ├── packages.py                      # 包路径、体积、摘要和存储校验，不执行包代码
│   ├── publication.py                   # 能力公开审核目标、决定应用及版本可见性
│   ├── publication_routes.py            # 作者提交/撤回及管理员公开审核 API
│   ├── routes.py                        # 五种能力共用的定义、草稿、版本、包导入和下载 API
│   ├── schemas.py                       # 通用请求响应和按能力类型区分的静态声明联合
│   ├── validation.py                    # 静态声明、资源摘要和依赖一致性校验
│   ├── mcp/                             # MCP 发布定义，不启动进程或连接服务
│   │   ├── __init__.py                  # MCP 定义包标识
│   │   ├── schemas.py                   # 地址/stdio 启动声明、工具清单及参数结构
│   │   └── validation.py                # MCP 包、声明、传输类型及版本一致性校验
│   ├── skills/                          # Skill 发布声明，不内置领域 Skill
│   │   ├── __init__.py                  # Skill 定义包标识
│   │   ├── schemas.py                   # SKILL.md、资源索引及静态声明结构
│   │   └── validation.py                # Skill 包解析、资源摘要及版本一致性校验
│   └── tools/                           # 工具发布声明，不运行上传工具
│       ├── __init__.py                  # 工具定义包标识
│       ├── schemas.py                   # 工具入口、输入输出、effects 和执行声明
│       └── validation.py                # 入口存在性、声明、包及版本一致性校验
├── channels/                            # 通用接入事实与投递保障，厂商协议归扩展
│   ├── __init__.py                      # 通道模块包标识
│   ├── attachments.py                   # 通道附件与受权文件资源关联
│   ├── contracts.py                     # 身份映射、准入和通用编排启动注入合同
│   ├── models.py                        # 账号、映射、路由、Inbox 和 Delivery ORM
│   ├── routes.py                        # 通用账号、映射、收件与投递管理 API
│   ├── schemas.py                       # 通道配置、消息信封和投递回执合同
│   └── service.py                       # 入站幂等、身份映射、编排路由和未知发送核对
├── extensions/                          # 可执行包安装、宿主和单次调用，不内置领域业务
│   ├── __init__.py                      # 扩展模块包标识
│   ├── access.py                        # 安装、实例和当前 Invocation 授权
│   ├── activities.py                    # 单次隔离 Invocation 的 Activity 与效果确认
│   ├── contracts.py                     # 宿主请求、隔离调用和具体跨所有者依赖合同
│   ├── delegation.py                    # 每次调用的不可伪造委托身份和范围
│   ├── deployment.py                    # 常驻部署唯一生命周期：启动、停止、状态、调用分派及并发协调
│   ├── drain.py                         # 排空时停止接受新调用并协调所属部署
│   ├── events.py                        # 扩展命名空间通知发布，不创建第二个订阅引擎
│   ├── hooks.py                         # 顺逆序、失败策略、补丁白名单和逐次效果回执
│   ├── host.py                          # 受限宿主 RPC、逐次授权和幂等回执，不暴露模型 Key
│   ├── invocation.py                    # 单次调用的准入、状态和外部效果回执
│   ├── maintenance.py                   # 未决调用统计和有界维护
│   ├── managed_activities.py            # 常驻部署生命周期及实际调用的 Activity
│   ├── managed_process.py               # 隔离常驻扩展进程的后端控制，不拥有 MCP 进程
│   ├── models.py                        # 安装、实例、Invocation、部署和宿主回执 ORM
│   ├── routes.py                        # 管理员安装、实例及受限调用管理 API
│   ├── sandbox.py                       # 已安装扩展的隔离入口、调用信封和宿主通信，环境操作经注入合同
│   ├── schemas.py                       # 安装、实例、调用、部署与效果核对合同
│   ├── security.py                      # 安装、实例、用户、调用范围和资源权限交集
│   ├── service.py                       # 安装上限、启停、替换及实例配置生命周期
│   ├── state.py                         # 扩展命名空间状态、隔离和 CAS 并发更新
│   ├── state_transfer.py                # 自身状态的受权导入导出，不实现通用备份引擎
│   └── workflow.py                      # 扩展调用的持久等待、取消与回执协调
├── identity/                            # 用户身份及数量额度，不管理模型连接
│   ├── __init__.py                      # 身份模块包标识
│   ├── access.py                        # 平台角色等身份授权基础判断
│   ├── context.py                       # 已认证用户的最小身份值
│   ├── errors.py                        # 登录、账号与身份相关错误
│   ├── provisioning.py                  # 身份内部账号开通、默认额度和首管理员组合
│   ├── auth/                            # 登录 Session 与认证
│   │   ├── __init__.py                  # 认证包标识
│   │   ├── contracts.py                 # 认证所需账号读取和开通等注入签名
│   │   ├── dependencies.py              # X-Session-ID 校验及当前用户解析
│   │   ├── maintenance.py               # 过期登录 Session 的有界清理
│   │   ├── models.py                    # 密码凭证和登录 Session ORM，不是 API Key 仓库
│   │   ├── passwords.py                 # 密码哈希、验证及强度校验
│   │   ├── routes.py                    # 登录、注销、改密及登录 Session 管理 API
│   │   ├── schemas.py                   # 认证请求和响应合同
│   │   └── service.py                   # 认证状态、Session 签发和撤销
│   ├── invitations/                     # 平台账号入站邀请，不负责发送邮件
│   │   ├── __init__.py                  # 平台邀请包标识
│   │   ├── contracts.py                 # 接受邀请时的账号开通注入合同
│   │   ├── models.py                    # 邀请凭证摘要、有效期、撤销和接受事实
│   │   ├── routes.py                    # 管理员签发、撤销及用户接受邀请 API
│   │   ├── schemas.py                   # 平台邀请请求与响应合同
│   │   └── service.py                   # 凭证签发、过期校验、撤销和账号开通协调
│   ├── quotas/                          # 唯一 Token、存储和执行容量数量账本
│   │   ├── __init__.py                  # 额度包标识
│   │   ├── models.py                    # 用户数量上限、已用、预留及容量占用事实
│   │   ├── routes.py                    # 管理员分配/调整，普通用户查询本人额度
│   │   ├── schemas.py                   # 数量额度、预留和用量响应合同，无币种或金额
│   │   └── service.py                   # 同事务预留、实际用量结算、释放及待核对处理
│   └── users/                           # 用户资料与平台角色
│       ├── __init__.py                  # 用户包标识
│       ├── avatars.py                   # 头像格式、大小校验和注入存储操作
│       ├── models.py                    # 用户资料、状态、角色及默认工作根目录 ORM
│       ├── ownership.py                 # 当前用户状态及用户所有权校验
│       ├── routes.py                    # 本人资料与管理员用户管理 API
│       ├── schemas.py                   # 用户资料和角色请求响应合同
│       └── service.py                   # 用户创建、资料更新、禁用及角色调整
├── orchestration/                       # 用户组装及运行的统一所有者
│   ├── __init__.py                      # 编排模块包标识
│   ├── access.py                        # 编排对象通用访问判断
│   ├── contracts.py                     # 事务内准入、额度及配置访问的命名注入合同，不进入 Workflow 历史
│   ├── dependencies.py                  # 获取 bootstrap 注入的编排依赖
│   ├── configurations/                  # 固定修订的使用配置及协作授权，不拥有模型历史
│   │   ├── __init__.py                  # 使用配置包标识
│   │   ├── close_workflow.py            # 配置关闭的持久取消、等待与清理流程
│   │   ├── closing.py                   # 关闭配置时协调其自有任务、触发器和运行资源
│   │   ├── compilation.py               # 授权后解析固定定义与绑定，生成不可变执行输入
│   │   ├── contracts.py                 # 配置编译所需的模型选择与修订授权注入合同
│   │   ├── file_routes.py               # 编排工作目录的受限文件访问 API
│   │   ├── files.py                     # 使用配置成员权限与文件目录权限的组合校验
│   │   ├── models.py                    # 使用配置、修订、成员、邀请、关闭及运行来源引用 ORM
│   │   ├── routes.py                    # 使用配置、成员、邀请和关闭 API
│   │   ├── schemas.py                   # 使用配置、成员、邀请和关闭请求响应合同
│   │   ├── service.py                   # 配置生命周期、固定修订、成员与邀请授权
│   │   └── bindings/                    # 用户对能力版本的使用绑定，不复制发布目录
│   │       ├── __init__.py              # 能力绑定包标识
│   │       ├── access.py                # 绑定所有权、能力版本和当前使用授权校验
│   │       ├── connections.py           # 固定 MCP 声明、使用参数与实际连接的一致性校验
│   │       ├── models.py                # 用户能力使用绑定及修订 ORM
│   │       ├── routes.py                # 能力使用绑定与显式连接操作 API
│   │       ├── schemas.py               # 能力绑定、参数和连接引用请求响应合同
│   │       ├── service.py               # 绑定创建、修改、禁用及版本/安装一致性校验
│   │       └── validation.py            # 复用 SDK 纯解析算法，服务端重算并校验固定声明与实际配置
│   ├── definitions/                     # 专家/团队均通过通用组装定义表达
│   │   ├── __init__.py                  # 组装定义包标识
│   │   ├── contracts.py                 # 图、流程、节点和步骤的确定性纯值，不依赖 ORM
│   │   ├── graph.py                     # 节点引用、依赖、环路及展开边界校验
│   │   ├── models.py                    # 可复用定义、不可变修订和定义授权 ORM
│   │   ├── routes.py                    # 组装定义的创建、编辑、发布和授权 API
│   │   ├── schema_validation.py         # 有界离线 JSON Schema 校验，不访问远程引用
│   │   ├── schemas.py                   # Agent 图、流程及定义授权请求响应合同
│   │   └── service.py                   # 定义与修订生命周期、内容校验和对象授权
│   ├── execution/                       # 内部官方 SDK 适配，无业务 ORM 或公开 API
│   │   ├── __init__.py                  # 内部执行适配包标识
│   │   ├── agents.py                    # 原生 Agent、handoff、Agent-as-tool 组装
│   │   ├── contracts.py                 # 已解析输入、执行结果及显式依赖函数签名
│   │   ├── guardrails.py                # 原生 guardrail 适配，不实现内容防护算法
│   │   ├── hooks.py                     # 原生回调到受控扩展 hooks 的适配
│   │   ├── mcp.py                       # 将已授权现有连接接入 Agent 的原生 MCP 接口
│   │   ├── model_provider.py            # 官方模型接口适配，不管理上游配置或保存 Key
│   │   ├── runner.py                    # 原生 Runner 调用及执行中断适配
│   │   ├── session.py                   # 原生 Session 协议到历史存储 Activity 的适配
│   │   └── tools.py                     # 原生工具绑定，实际调用委托已授权 Activity
│   ├── runs/                            # 唯一任务事实与运行控制
│   │   ├── __init__.py                  # 编排运行包标识
│   │   ├── access.py                    # 实际调用用户、Session 和运行对象访问校验
│   │   ├── activities.py                # 授权、数据库、准入、状态与历史发布 I/O
│   │   ├── admission.py                 # 重新授权、Token 及容量的同事务准入
│   │   ├── agent_workflow.py            # 原生 Agent 任务的持久运行、中断与等待
│   │   ├── assets.py                    # 经资源授权读取输入、发布输出和关联产物
│   │   ├── contracts.py                 # Run 输入、检查点、领取结果及控制引用
│   │   ├── events.py                    # 运行进度事实和有界订阅读取
│   │   ├── flow_workflow.py             # 流程依赖、并行、分支、等待及子任务取消协调
│   │   ├── hooks.py                     # 运行 hook 授权、调用回执与失败边界
│   │   ├── maintenance.py               # 本模块有界清理及未决任务统计
│   │   ├── mcp.py                       # 运行 MCP 授权、原生调用及效果回执组合
│   │   ├── model_activities.py          # 模型网络调用、最窄边界解密及实际用量采集
│   │   ├── model_operations.py          # 受限宿主模型调用，共用授权/额度/效果路径
│   │   ├── model_routes.py              # 宿主模型调用、结果及未知效果核对 API
│   │   ├── model_workflow.py            # 独立模型操作的持久执行与核对等待
│   │   ├── models.py                    # Agent Run、FlowRun、步骤和效果回执 ORM
│   │   ├── reconciliation.py            # 已知/未知外部效果核对，不重跑不确定写入
│   │   ├── routes.py                    # 统一运行 API，不暴露第二套 execution 入口
│   │   ├── scheduling.py                # 同 Session FIFO 领取、释放及后继唤醒
│   │   ├── schemas.py                   # 提交、结果、进度、取消、恢复及核对合同
│   │   ├── service.py                   # 幂等提交、控制命令及唯一任务状态推进
│   │   ├── steps.py                     # 流程步骤的确定性参数映射与条件比较
│   │   ├── tool_activities.py           # 工具及 MCP 效果边界的授权、回执与取消
│   │   └── usage.py                     # 实际 Token 回执、结算、释放及待核对
│   ├── runtime/                         # 环境、实际 MCP 连接及进程生命周期
│   │   ├── __init__.py                  # 运行设施包标识
│   │   ├── access.py                    # 环境/连接的所属范围、资源授权与当前状态校验
│   │   ├── activities.py                # 实际环境 I/O、效果确认及数据库操作回执
│   │   ├── contracts.py                 # 生命周期操作、后端调用及回执合同
│   │   ├── control.py                   # 环境操作取消、当前权限与控制回执
│   │   ├── files.py                     # 隔离环境与已授权资源间的受限文件传输
│   │   ├── mcp.py                       # 远程连接及隔离 stdio 进程的创建、复用、发现与关闭
│   │   ├── mcp_activities.py            # 实际 MCP I/O、生命周期效果、心跳与取消
│   │   ├── mcp_routes.py                # MCP 连接、状态、发现和关闭 API
│   │   ├── mcp_service.py               # MCP 连接生命周期事实与当前授权、幂等提交
│   │   ├── models.py                    # 环境、MCP 连接、操作状态和效果回执 ORM
│   │   ├── routes.py                    # 环境创建、操作、续接和释放 API
│   │   ├── sandbox.py                   # 官方隔离后端适配，不复制沙箱引擎
│   │   ├── schemas.py                   # 显式环境/MCP 创建、续接、操作和释放合同
│   │   ├── service.py                   # 环境操作的幂等提交、效果核对和生命周期协调
│   │   └── workflow.py                  # 环境/连接操作的持久等待与回执驱动
│   ├── sessions/                        # 每位用户私有的模型会话，不是登录 Session
│   │   ├── __init__.py                  # 模型会话包标识
│   │   ├── access.py                    # 私有会话、实际用户和使用配置关联校验
│   │   ├── activities.py                # 原生 Session 存储的数据库 Activity 入口
│   │   ├── contracts.py                 # 原生历史存储、写集和发布回执值
│   │   ├── history.py                   # 原生 item 写集、读取及成功批次原子发布
│   │   ├── models.py                    # 唯一原生历史、待提交项及发布回执 ORM
│   │   ├── routes.py                    # 本人会话和记忆 API，忙时拒绝外部修改
│   │   ├── schemas.py                   # 会话、历史读取、重置及关闭请求响应合同
│   │   └── service.py                   # 会话创建复用、授权、空闲重置和关闭
│   └── triggers/                        # 唯一触发、定时与持久订阅所有者
│       ├── __init__.py                  # 触发器包标识
│       ├── activities.py                # Temporal Schedule 同步及触发发生的 I/O
│       ├── contracts.py                 # 触发来源、投递和执行引用合同
│       ├── models.py                    # 触发配置、来源订阅和投递幂等回执 ORM
│       ├── routes.py                    # 触发器配置、启停和受控 Webhook 入口
│       ├── schemas.py                   # Schedule、Webhook 和订阅请求响应合同
│       ├── service.py                   # 当前授权、触发投递及固定修订运行提交
│       └── workflow.py                  # 触发投递和固定编排启动的持久协调
├── platform/                            # 最小平台管理和强制运行约束
│   ├── __init__.py                      # 平台模块包标识
│   ├── approvals/                       # 通用人工审批的唯一决定事实
│   │   ├── __init__.py                  # 审批包标识
│   │   ├── models.py                    # 申请、目标修订、审批人、状态及决定回执 ORM
│   │   ├── port.py                      # 实现最小跨模块审批合同，不向调用方暴露 SQL
│   │   ├── routes.py                    # 当前主体可读和可决定的审批 API
│   │   ├── schemas.py                   # 申请、审批收件箱、决定及取消请求响应合同
│   │   └── service.py                   # 创建、过期、取消、审批人授权及决定幂等
│   ├── audit/                           # 同业务事务追加最小审计事实
│   │   ├── __init__.py                  # 审计包标识
│   │   ├── models.py                    # 审计事实及幂等标识 ORM，不设全局哈希链头
│   │   ├── routes.py                    # 授权审计查询及受限来源追加 API
│   │   ├── schemas.py                   # 审计输入、只读查询及分页响应合同
│   │   └── service.py                   # 来源校验、脱敏、幂等及调用方事务内写入
│   ├── health/                          # 基础健康和构建信息
│   │   ├── __init__.py                  # 健康检查包标识
│   │   ├── routes.py                    # 存活、就绪及构建信息 API
│   │   └── schemas.py                   # 探针和构建信息响应合同
│   ├── model_connections/               # 管理员统一提供上游；不是用户连接模块
│   │   ├── __init__.py                  # 平台模型连接包标识
│   │   ├── activities.py                # 实际连通性探测、边界解密和探测回执
│   │   ├── models.py                    # 管理员上游、加密 Key、可用模型及用户授权 ORM
│   │   ├── routes.py                    # 管理员配置/授权，用户只读获准模型列表
│   │   ├── schemas.py                   # 上游配置、授权和可用模型合同，响应不返回明文 Key
│   │   ├── service.py                   # 上游配置、授权撤销、Key 更新、轮换及使用时校验
│   │   └── workflow.py                  # 上游探测的持久任务，不运行用户 Agent
│   ├── operations/                      # 必要平台维护命令，不包含主机更新或业务归档
│   │   ├── __init__.py                  # 平台操作包标识
│   │   ├── activities.py                # 在调用方事务中执行具体所有者维护函数
│   │   ├── admission.py                 # 平台排空和恢复准入开关
│   │   ├── handlers.py                  # 显式维护命令与当前所有者函数的装配合同
│   │   ├── models.py                    # 维护操作、进度及幂等回执 ORM
│   │   ├── routes.py                    # 管理员维护命令提交与查询 API
│   │   ├── schemas.py                   # 排空、轮换、清理操作及回执请求响应合同
│   │   ├── service.py                   # 命令授权、幂等提交、进度及状态推进
│   │   └── workflow.py                  # 有界分批维护、进度、等待和取消的持久协调
│   └── policies/                        # 基础操作约束，不内置领域评审算法
│       ├── __init__.py                  # 平台策略包标识
│       ├── evaluation.py                # 有界声明式规则匹配，不执行用户表达式
│       ├── models.py                    # 当前操作策略和不可变规则修订 ORM
│       ├── redaction.py                 # 平台脱敏规则值，由 bootstrap 显式注入
│       ├── routes.py                    # 管理员规则配置和受控检查 API
│       ├── schemas.py                   # 有界 allow/deny/require_approval 声明结构
│       └── service.py                   # 规则更新、当前检查及拒绝/审批路径解释
├── resources/                           # 用户文件资源与产物，不管理 MCP 进程
│   ├── __init__.py                      # 资源模块包标识
│   ├── artifacts/                       # 运行产生的文件产物
│   │   ├── __init__.py                  # 产物包标识
│   │   ├── models.py                    # 产物与文件/运行引用及发布事实 ORM
│   │   ├── routes.py                    # 授权产物查询、读取与下载 API
│   │   ├── schemas.py                   # 产物元数据及公开响应结构
│   │   └── service.py                   # 产物发布、引用校验及访问授权
│   ├── files/                           # 目录、文件及内容版本
│   │   ├── __init__.py                  # 文件资源包标识
│   │   ├── access.py                    # 用户、目录范围及共享资源访问校验
│   │   ├── content.py                   # 文件内容读取、摘要校验及受限传输
│   │   ├── models.py                    # 目录、文件、内容版本及授权事实 ORM
│   │   ├── routes.py                    # 目录和文件管理、上传及下载 API
│   │   ├── schemas.py                   # 文件、目录、版本及授权请求响应合同
│   │   ├── service.py                   # 资源授权、版本冲突、存储额度和持久化协调
│   │   └── versions.py                  # 不可变内容版本与当前版本的冲突校验
│   └── storage/                         # 文件系统内容存储及数据库提交协调
│       ├── __init__.py                  # 存储基础包标识
│       ├── filesystem.py                # 内容寻址存储与路径边界、原子文件操作
│       └── transactions.py              # 数据库事务与文件写入/清理的提交回滚协调
├── shared/                              # 中性值、基础函数与最小跨模块调用合同
│   ├── __init__.py                      # shared 包标识
│   ├── approvals.py                     # 最小审批目标、决定值与调用签名，不含状态规则或 SQL
│   ├── audit.py                         # 审计归属、不可变写入输入及同事务写入签名
│   ├── contracts.py                     # 中性 JSON、标识符和 UTC 时间值
│   ├── errors.py                        # 中性错误结构，不绑定具体产品模块
│   ├── files.py                         # 安全相对路径、文件名和基础文件操作函数
│   ├── pagination.py                    # 中性分页参数及数量边界
│   ├── policies.py                      # 策略结果值与检查签名，不解释平台规则
│   ├── serialization.py                 # 规范 JSON、摘要及稳定序列化基础函数
│   ├── version.py                       # 发行版本与构建标识读取
│   ├── db/                              # SQLAlchemy 和事务基础函数
│   │   ├── __init__.py                  # 数据库基础包标识
│   │   ├── base.py                      # ORM Base、UUID 主键和时间字段 mixin
│   │   ├── errors.py                    # 数据库异常到中性领域错误的映射
│   │   ├── idempotency.py               # 调用方事务内的幂等创建及回执基础函数
│   │   ├── pagination.py                # 有界 SQL 查询分页函数
│   │   └── session.py                   # Engine、Session 工厂及事务上下文基础函数
│   ├── http/                            # 中性 HTTP 传输与响应基础函数
│   │   ├── __init__.py                  # HTTP 基础包标识
│   │   ├── client.py                    # HTTP 客户端创建、超时和连接释放
│   │   ├── client_ip.py                 # 按显式代理信任配置解析客户端 IP
│   │   ├── context.py                   # 中性请求关联标识上下文
│   │   ├── dependencies.py              # 获取应用注入的请求级数据库等基础依赖
│   │   ├── errors.py                    # 中性错误的 HTTP 状态及响应映射
│   │   ├── middleware.py                # 中性请求上下文和错误边界中间件
│   │   ├── pagination.py                # HTTP 分页参数转换
│   │   ├── responses.py                 # 通用响应构造，不定义业务响应模型
│   │   └── sse.py                       # SSE 帧编码、断连与流释放基础函数
│   ├── redis/                           # 派生缓存与发布订阅基础函数，不建立任务队列
│   │   ├── __init__.py                  # Redis 基础包标识
│   │   ├── cache.py                     # 显式失效和 TTL 缓存函数
│   │   ├── client.py                    # Redis 客户端连接及释放
│   │   └── keys.py                      # 缓存与通知通道键构造
│   └── security/                        # 安全基础算法，不拥有用户凭据实体
│       ├── __init__.py                  # 安全基础包标识
│       ├── encryption.py                # 加密、解密及密钥版本基础函数
│       ├── rate_limits.py               # 限流计数及判定基础函数
│       ├── redaction.py                 # 只使用调用方显式规则的脱敏函数
│       └── url_validation.py            # 平台自身 HTTP 地址校验，不控制用户运行网络
├── telemetry/                           # 日志、指标、追踪，不决定业务授权
│   ├── __init__.py                      # 遥测包标识
│   ├── context.py                       # 追踪与关联上下文传播
│   ├── exporters.py                     # OTLP 等观测出口及资源释放
│   ├── instrumentation.py               # 第三方 DB、HTTP 等库的观测接入
│   ├── logging.py                       # 标准日志、有界队列、脱敏、截断及排空
│   ├── metrics.py                       # 指标注册与采集接口
│   └── tracing.py                       # Span 创建、属性和追踪上下文接口
└── temporal/                            # 持久执行基础设施，无业务状态机
    ├── __init__.py                      # Temporal 基础包标识
    ├── codec.py                         # Workflow/Activity 敏感载荷加密编解码
    └── dispatch/                        # 唯一事务 Outbox 与持久执行投递
        ├── __init__.py                  # 投递包标识
        ├── contracts.py                 # 启动、信号等投递意图及引用值
        ├── delivery.py                  # 幂等投递回执、失败和重试事实
        ├── dispatcher.py                # 提交后将 Outbox 意图投递到 Temporal
        ├── models.py                    # Outbox 和投递回执 ORM
        └── outbox.py                    # 在调用方事务中追加投递意图，不自行提交
```

## SDK 源码

```text
sdk/python/src/opsmesh_sdk/
├── __init__.py                          # SDK 公共客户端和稳定纯值的公开导出
├── client.py                            # 平台 API 客户端、分组及 HTTP 连接生命周期
├── context.py                           # 每次扩展调用的不可变主体、范围、deadline 和宿主入口
├── errors.py                            # SDK 请求、取消、校验及未知效果错误
├── manifest.py                          # 包声明、权限、effects、导出及执行模式的纯合同
├── values.py                            # API 引用、范围和幂等键等稳定纯值
├── api/                                 # 从服务端 OpenAPI 生成，不手写业务规则
│   ├── __init__.py                      # 生成 API 分组的公开导出
│   ├── capabilities.py                  # 能力目录、版本、包及公开审核客户端和 DTO
│   ├── channels.py                      # 通道账号、映射、Inbox 和 Delivery 客户端及 DTO
│   ├── extensions.py                    # 安装、实例、调用、部署和状态管理客户端及 DTO
│   ├── identity.py                      # 登录、用户、数量额度和平台邀请客户端及 DTO
│   ├── orchestration.py                 # 定义、运行、Session、环境、MCP 和触发器客户端及 DTO
│   ├── platform.py                      # 模型上游、授权、审计、审批和维护客户端及 DTO
│   └── resources.py                     # 目录、文件、内容版本与产物客户端及 DTO
├── composition/                         # 便利组装函数，不代替服务端授权或持久调度
│   ├── __init__.py                      # 组装构建辅助函数的公开导出
│   ├── parameters.py                    # 服务端和客户端共用的有界纯参数解析与规范化，无 I/O 或授权
│   └── templates.py                     # 读取模板、解析资源、组织定义导入与发布调用
├── development/                         # 作者使用的扩展包构建、验证和本地合同测试
│   ├── __init__.py                      # SDK 开发工具包标识
│   ├── build.py                         # 静态包构建、schema 导出和摘要，不执行包源码
│   ├── cli.py                           # validate/build/test 开发命令入口
│   └── testing.py                       # 本地声明与宿主合同验证，不代替真实服务端验收
├── extensions/                          # 扩展开发纯合同，不包含领域业务实现
│   ├── __init__.py                      # operation、hook 等稳定扩展声明的公开导出
│   ├── channels.py                      # 厂商通道扩展实现所用的通用协议适配合同
│   ├── events.py                        # 扩展命名空间通知结构，不提供通用平台事件分派
│   ├── hooks.py                         # Continue/Patch/Block 与阶段/补丁声明值
│   ├── operations.py                    # 带输入输出、effects 和结果信封的函数声明
│   ├── package.py                       # 包导出集合及入口合同，不注册全局业务插件
│   └── tools.py                         # 将扩展操作声明为可绑定工具的纯合同
├── host/                                # 当前 Invocation 范围内的受限宿主客户端
│   ├── __init__.py                      # 受限宿主能力的公开导出
│   ├── events.py                        # 向平台发布本扩展命名空间通知
│   ├── jobs.py                          # 委托平台提交通用编排，返回运行引用，不复制调度器
│   ├── models.py                        # 受权模型请求与结果客户端，不能读取管理员模型 Key
│   ├── observability.py                 # 上报日志、指标和追踪，不查询运维监控后端
│   └── state.py                         # 当前命名空间状态读取、写入和 CAS 更新
├── runtime/                             # 正式隔离运行入口，不依赖 development 或本地测试宿主
│   ├── __init__.py                      # SDK 隔离运行包标识
│   ├── loading.py                       # 包入口加载、当前调用上下文和执行生命周期
│   ├── managed.py                       # 隔离环境中的常驻扩展执行入口
│   └── runner.py                        # 隔离环境中的单次扩展导出执行入口
├── sources/                             # 能力包源客户端，不接管服务端安装授权
│   ├── __init__.py                      # 包源客户端和导入辅助函数公开导出
│   ├── github.py                        # GitHub Releases 查询、清单解析及有界包下载
│   └── packages.py                      # 摘要校验、上传及管理员导入请求组织
└── transport/                           # HTTP 和隔离宿主通信，不知道服务端 ORM
    ├── __init__.py                      # SDK 传输合同的公开导出
    ├── contracts.py                     # 请求、响应、取消范围和传输上下文纯合同
    ├── host.py                          # 受限宿主通信及平台委托请求适配
    ├── http.py                          # 平台 HTTP 请求、X-Session-ID、超时及资源释放
    ├── pipe.py                          # 隔离进程的有界管道请求/响应传输
    └── streams.py                       # 流式响应解析、取消和关闭
```

## 树中的关键约束

- 不再存在服务端根 `execution`；公开运行 API 统一归 `orchestration.runs`。
- `orchestration.execution` 只接收不可变输入和注入依赖，不拥有业务 ORM、不反向查询任务表。
- 普通 Agent 图不强制产生一对一 FlowRun；显式流程才建立 FlowRun 和真实步骤/子任务引用。
- `capabilities.mcp` 管定义；`runtime.mcp` 管连接和进程；`execution.mcp` 管原生 SDK 接入。
- 运行设施可以复用；MCP 进程实际在隔离环境，任务结束不能误停共享连接或环境。
- 模型 Key 归 `platform.model_connections`，`shared.security.encryption` 只负责加解密算法。
- 删除 `identity.costs`、`identity.credentials` 和 SDK `host/secrets.py`；保留登录密码凭证。
- SDK manifest/context 同步移除用户密钥槽，不让扩展取得管理员模型 Key。
- 专家、团队由用户通过通用组装表达；通知、邮件、知识库和领域规划归独立扩展包。
- 删除货币预算后，Token、存储、容量准入、实际用量、未知效果核对仍需服务端强制保证。

## 职责、依赖与执行合同

### 定义、使用配置和绑定

`orchestration.definitions` 只拥有可复用图/流程、不可变修订和定义授权；
`orchestration.configurations` 拥有固定定义修订的使用配置、成员、邀请和关闭流程；
`configurations.bindings` 拥有能力版本的使用参数与实际连接引用，不复制能力目录。
配置只保存 Run 来源引用，任务状态唯一归 `runs`；成员关系不授予其他成员的历史读取权限。
配置通过 definitions、capabilities/runtime 的公开操作完成授权与绑定；Run 可以直接调用配置授权。
依赖规则不再逐个禁止二级模块互相导入；保留实际模块无环、状态唯一所有者和执行适配无 ORM 的边界。

### 参数解析与权威校验

SDK `composition.parameters` 是单一纯解析实现：有界参数替换和规范化，无网络、密钥解析、
平台配置读取或授权。服务端可以依赖该 SDK 纯模块，SDK 不能反向依赖服务端。
`configurations.compilation` 加载服务端保存的固定版本和参数，经 bindings.validation 重算
有效声明，校验参数 schema、不可变字段及实际 MCP 的 transport、endpoint、command、
environment 和环境引用等实际生效字段。客户端提交的版本 ID、摘要或展开结果都不能单独证明授权。
允许参数化的字段由发布声明明确约束，schema 禁止远程引用，并限制输入大小和展开深度。
构造或更新绑定/连接时校验；Run 领取、重试、审批恢复及敏感效果边界重新验证当前授权和修订。
配置变化不能静默改变排队 Run 的固定输入，版本或连接不一致明确拒绝；网络 I/O 不持数据库锁。

### 隔离环境与扩展执行

`orchestration.runtime` 只维护环境、命令和 MCP 连接/进程生命周期。
`extensions.sandbox` 维护扩展特有的 SDK 启动入口、Invocation 信封和宿主通信；
当前通过 bootstrap 注入环境公开操作；后续可直接调用环境操作，不直写环境 ORM，也不建立第二份环境状态。
`extensions.deployment` 唯一推进常驻部署状态与并发调用；managed_process 只操作隔离进程，
managed_activities 执行 I/O，不另设 managed.py 复制部署控制。Deployment、Invocation、
Environment、MCPConnection 分别记录不同事实，互引而不镜像状态机。
SDK runtime 在批准隔离环境加载扩展，单次入口为 `opsmesh_sdk.runtime.runner`，
常驻入口为 `opsmesh_sdk.runtime.managed`；API/发布校验不导入上传包。
取消只处置所属调用，未知效果保留回执并进入待核对，共享环境和连接不随单个 Run 自动关闭。

### 共用能力入口与不应合并的边界

capabilities.routes 通过带类型标识的请求统一五类能力的创建、草稿、版本和包操作；
各类型仅保留静态 schema 和实质校验，channel/extension 使用 SDK 纯声明合同。
类型校验函数由 bootstrap 显式注册，不能导入已安装扩展执行模块；lifecycle 唯一维护版本、
授权与事务。取消薄类型 CRUD 路由及跨路由私有函数导入，响应组装留在共用路由边界。
静态 MCP 声明、运行连接和原生 Agent 适配不可合并；HTTP/DB/中性分页也不因同名而合并。
Agent 模型调用与 host.models 共用模型解析、当前授权、额度和效果核对路径，不复制账本或 Runner。

## 实施影响与验收

此前源码调整顺序为：纯参数解析及服务端重算合同 → definitions/configurations/bindings 拆分 →
扩展隔离适配与 SDK runtime 入口 → 统一能力 CRUD → 名称、导入及工程合同收尾。
每步覆盖当前源码、调用方、bootstrap、ORM 注册、初始迁移、路由、OpenAPI、SDK 生成映射、
镜像入口、打包和相关测试；删除旧路径，不建立别名。定义/配置 ORM 分拆是源码职责调整，
不因此创建重复表；五类能力 CRUD 合并与运行 API 归并属于接口变化，须重新生成合同。

验收覆盖绕过 SDK 修改 MCP 地址/命令、参数越界、撤权/修订变化、公开草稿隔离、
配置成员的私有历史隔离、FIFO/跨 Session 并发、关闭与共享环境清理、部署取消/未知效果核对。
SDK wheel 在无开发工具依赖的干净环境执行正式单次/常驻入口；真实 PG/Temporal/沙箱/MCP/模型
分别证明相关产品效果，未执行项标记 unverified。文档清单检查不替代这些实现验收。

清单只说明源码归属，不等于所有产品场景已经验收。初始迁移、API/SDK 生成、真实 PG/
Temporal 与外部模型/沙箱的证据分别记录，未执行的真实外部效果不计为通过。

2026-10-20 清理只更新依赖规则、设计与工程检查，没有迁移上述业务实现或更改 API/数据库。
后续源码精简按 V4 第 12.3 节逐条调用链进行，不把本次规则检查计为运行重构已完成。
