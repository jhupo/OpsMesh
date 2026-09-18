# 自动化与扩展合同（2026-09-18）

## 当前状态

后端现在支持工作区级自动化配置。自动化引用一个已发布的工作流版本和一个团队，触发方式可以是消息或周期调度。消息事件进入持久化收件箱后按 `external_event_id` 去重，后台维护任务将它转换为现有 `Task`，由现有 Task/Run 执行链处理。任务进入终态后，通过已配置的 Webhook 订阅创建签名投递，投递成功或死信都会回写自动化事件。

自动化不创建第二套执行引擎，也不创建永久占用的 Agent 或容器。等待消息和周期到期时只保存 Postgres 状态，执行和投递仍由现有 worker 负责。

## Web 画布合同

`GET /api/v1/workspaces/{workspace_id}/orchestrations/authoring-contract` 返回节点 JSON Schema、编辑器元数据 Schema、节点类型、选择器和数据绑定字段。创建或修改编排时，`editor.positions`、`viewport` 和 `zoom` 只保存布局，不参与执行语义；`definition.nodes` 是唯一的执行定义。发布后工作流版本不可变，任务引用具体版本。

画布应使用这些合同生成表单和节点，而不是把业务规则复制到前端。后端发布和运行时仍会重新验证工作区资源、团队、MCP allowlist、权限和版本。

## 插件 SDK 边界

`plugin_sdk/` 是独立的轻量工作区包 `opsmesh-plugin-sdk`。外部连接器只依赖它，不导入 `backend` 源码。SDK 当前提供 `PluginManifest`、能力声明、`AutomationClient`、`verify_delivery`、`IncomingMessage` 和 `AcceptedEvent`。

SDK 只支持远程执行声明。插件不能被动态导入 API 或 worker 进程，也不能凭 manifest 自动获得权限。平台在安装、启用和每次运行时分别检查签名、能力、凭证、工作区授权和审批策略。

当前插件市场仍只支持 `plugin` listing 的识别，插件安装执行生命周期尚未开放；配置接口会明确返回 `plugin_installation_supported: false`。下一阶段再增加签名包、版本安装、隔离进程、升级切换和卸载依赖检查。

## 中心的职责

- 工具中心管理可调用工具、MCP 发现、凭证、Schema、风险和试调用。
- 技能中心管理可复用指令、资料、能力依赖及工作区安装快照。
- 插件中心管理版本化扩展包及其声明能力。
- 编排中心引用前三者的能力 ID，把它们组合成可发布的任务图。

四个中心共用同一能力目录、授权快照、审批、审计和运行时，不各自实现执行器。
