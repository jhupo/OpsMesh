# 公开应用入口与 QQ 示例收敛

本次针对示例直连 bootstrap/CLI、BotProject 同时拥有 Agent 与账号、技能重复上传，以及配置重复存储进行重构。不是新增另一种业务项目类型。

## 职责与调用链

`opsmesh.create_application` 是平台 Python 入口，组装实现仍归 bootstrap；`opsmesh.framework` 保持 Driver、Adapter、Matcher、Depends 等机制，不导入平台业务。包根目录延迟加载平台，单独导入 framework 不会初始化平台服务。

`app.register_tool(definition, callback)` 同时注册函数及其待发布合同；`ToolDefinition.bind()` 从同一份 schema、描述和超时生成 Agent 工具绑定。避免示例在几个常量中复制工具定义。

`app.define(ApplicationDefinition(...))` 声明独立资源；`app.publish(actor_id=...)` 通过带授权的公开模块服务发布。创建资源与启动消费者分开，不隐式初始化数据库、创建用户或把管理员身份用于群聊。平台工作区可声明为空目录；知识库直接声明平台集合，不创建本地数据镜像。

BotAccount 表示入口账号，BotRoute 保存群到已发布 Agent 的绑定。`ConversationGateway` 只声明群、运行身份与处理策略，启动时读取平台路由和 Agent，再取得工作区、发布版本、名称。它不生成或持久化另一份 Agent，不维护绑定 JSON；直接路由、未启用账号、错误委派身份和不匹配的知识集合会拒绝启动。

原 `bootstrap/projects.py`、`bootstrap/project_runtime.py`、`entrypoints/cli/projects.py` 已删除，不保留兼容转发。标准平台 CLI 和代码应用共用 `entrypoints/http/server.py` 的 ASGI 生命周期。`opsmesh.run(factory)` 提供通用 publish/serve 参数解析。

## 示例与凭据

`examples/qq_community` 仅保留 main.py、AGENTS.md、tools、skills 和 README。删除 .env.example、opsmesh.yaml 和 workspace 包装目录；完整保留原人物 Skill 文件及出处，不把群聊输出格式或业务审核规则写进技能。

创建参数直接传给应用函数。命令行可以读取仓库外的 JSON 参数对象，转成同一个函数的关键字参数；没有环境变量展开器和第二套项目清单模型。QQ 密钥保存在进程内 SecretStr 映射，持久化 Bot 配置只有 `runtime:` 引用，重启由调用方再次提供。原 env 引用仍是平台部署凭据的一种正常来源。模型服务独立管理名称、base URL 和凭据；示例只接收模型引用。

只从明确声明的 `skills/gentle-sister` 发布技能，并物化到平台管理的 `.agents/gentle-sister`。示例 main.py、tools、配置文件和整个工程根目录都不会复制进沙箱。AGENTS.md 作为 Agent instructions 提交；运行时数据由平台数据库与工作区负责。

## 审查与验证边界

已合并的重复机制：项目资源发布与现有 ApplicationDeployment、两套 ASGI 启动、独立绑定 JSON 与 BotRoute、手写工具绑定与 ToolDefinition、工作区 skills 副本与技能发布。框架目录没有 QQ 社区、Jev 或人物提示词。

对话 Inbox 继续使用平台数据库，离线发布继续检查 consumer.lock、未完成批次及待确认知识写入。单群 Session、批次合并、审核、Agent 决策、投递及知识捕获没有在示例重新实现。

这次不部署服务器，也不自动导入服务器的旧项目配置或旧独立队列。移除专用项目入口是有意的接口调整；旧部署需要停止消费者、核对数据后切换配置。当前每个网关仍要求单个本机消费者；跨主机领导者选举没有实现。HTTP 可以修改资源配置，运行中的会话策略需要重启重新解析，不能视为动态热重载。

验证覆盖真实平台服务发布与幂等、权限范围、技能/知识工具、Agent 版本更新后路由和权限同步、知识保留、敏感值不落资源/工作区、纯框架导入、CLI 失败退出与客户端释放、持久重配保护。完整检查结果见任务交付说明。
