# 自动化与扩展合同（2026-09-18）

## 当前状态

后端现在支持工作区级自动化配置。自动化引用一个已发布的工作流版本和一个团队，触发方式可以是消息或周期调度。消息事件进入持久化收件箱后按 `external_event_id` 去重，后台维护任务将它转换为现有 `Task`，由现有 Task/Run 执行链处理。任务进入终态后，通过已配置的 Webhook 订阅创建签名投递，投递成功或死信都会回写自动化事件。

自动化不创建第二套执行引擎，也不创建永久占用的 Agent 或容器。等待消息和周期到期时只保存 Postgres 状态，执行和投递仍由现有 worker 负责。

## Web 画布合同

`GET /api/v1/workspaces/{workspace_id}/orchestrations/authoring-contract` 返回节点 JSON Schema、编辑器元数据 Schema、节点类型、选择器和数据绑定字段。创建或修改编排时，`editor.positions`、`viewport` 和 `zoom` 只保存布局，不参与执行语义；`definition.nodes` 是唯一的执行定义。发布后工作流版本不可变，任务引用具体版本。

画布应使用这些合同生成表单和节点，而不是把业务规则复制到前端。后端发布和运行时仍会重新验证工作区资源、团队、MCP allowlist、权限和版本。

## 插件 SDK 边界

`plugin_sdk/` 是独立的轻量工作区包 `opsmesh-plugin-sdk`。外部连接器只依赖它，不导入 `backend` 源码。SDK 当前提供 `PluginManifest`、能力声明、`AutomationClient`、`verify_delivery`、`IncomingMessage` 和 `AcceptedEvent`。

SDK 只支持远程执行声明。插件不能被动态导入 API 或 worker 进程，也不能凭 manifest 自动获得权限。安装时校验签名与明确批准的权限集合，执行时复查插件状态、发布密钥、版本和绑定配置；能力原有的凭证、工作区授权与审批规则仍生效。

当前 `plugin_installation_supported: true`，`plugin_execution_modes: ["remote"]`。这是远程资源的控制面生命周期：外部连接器自行部署与运行，OpsMesh 不启动、停止或更新外部服务进程。启停无需重启 OpsMesh，但不是 Python 模块热加载。平台托管插件容器、第三方镜像发布、钉钉等具体渠道适配器及 Web 页面均未实现。

## 安装与信任

1. 在工作区通过现有 API 配置远程 MCP、安装技能、消息自动化和 Webhook 回复订阅。MCP 的地址、认证引用、发现与 allowlist 仍归 MCP 模块管理；回复签名密钥仍归 Webhook 模块管理。
2. 发布者用 `opsmesh_plugin_sdk.packages.sign_package(manifest, publisher_key_id, private_key)` 签名；安装 `opsmesh-plugin-sdk[signing]` 即可使用。包是 `SignedPluginPackage` JSON，包含 manifest、密钥标识及 Base64 Ed25519 签名，绝不上传私钥。
3. 管理员通过可信的独立渠道确认发布者公钥，再向 `POST /api/v1/workspaces/{workspace_id}/plugins/trust-keys` 提交 `key_id`、`plugin_key`、Base64 `public_key`。信任仅对该工作区和精确插件 key 有效。
4. 向 `POST /api/v1/workspaces/{workspace_id}/plugins` 提交签名包、声明能力到本工作区资源 ID 的完整绑定，以及明确批准的权限集合。跨工作区、已占用的资源、签名错误、未批准权限和原始凭证均被拒绝。配置按 capability 的 JSON Schema 校验，不允许从网络解析 Schema 引用。

安装请求形状如下，`package` 使用 SDK 实际生成的签名 JSON：

```json
{
  "package": {"manifest": "此处替换为实际 manifest 对象", "publisher_key_id": "publisher-1", "signature": "实际签名"},
  "bindings": {
    "log_tools": {"resource_id": "工作区 MCP server UUID"},
    "reply": {"resource_id": "工作区 Webhook subscription UUID"}
  },
  "approved_permissions": ["mcp.call", "messages.send"]
}
```

`bindings` 的 key 必须与 manifest 中 capability key 完全对应。四种绑定配置分别为：

| kind | 绑定资源 | Schema 校验及冻结的配置 |
| --- | --- | --- |
| mcp_server | 远程 McpServer | server_type、connection |
| skill | WorkspaceSkillInstall | manifest、config、version、checksum |
| message_trigger | 消息 Automation | 完整自动化 configuration |
| reply_channel | WebhookSubscription | target_url、event_types |

权限声明是管理员确认的上限说明，不会生成工具授权。Agent/Team 仍需原有 grants 与 MCP allowlist。健康状态不纳入冻结配置；凭证引用可冻结，凭证明文永不进入包。配置变化使旧绑定拒绝执行，更新应创建新资源绑定新版本，不提供兼容回退。

Marketplace 的 `plugin` listing 使用相同签名包作为 manifest；公开发布仍经过已有审核。购买方 `config` 填写上述 bindings 与 approved_permissions，安装复用同一个 PluginService 事务。市场不另建插件执行器。

## 版本、启停与卸载

`GET /plugins` 列出安装；`GET /plugins/{id}/releases` 列出不可变版本；`GET /plugins/{id}/bindings?version=1.0.0` 返回具体版本绑定，省略 version 时使用 current_version；`GET /plugins/{id}/dependencies` 返回阻止卸载的依赖 ID。以上路径均带同一 workspace 前缀，集合查询分页。

升级再次 `POST /plugins`，版本号必须不同、资源绑定必须独立，并提供当前 `expected_generation`。所有变更通过数据库事务与安装行锁串行化，过时的 generation 返回 409。`POST /plugins/{id}/actions` 接受：

| action | 效果 |
| --- | --- |
| disable | 禁止这个安装所有版本的后续能力调用 |
| enable | 校验当前版本密钥及配置后启用；不自动启用底层资源 |
| switch_version + version | 将默认发现版本切回一个可用、受信任且配置完整的已安装版本 |
| retire_version + version | 无依赖时退役非当前版本，随后其绑定不可执行 |
| uninstall | 无依赖时终止安装；保留审计、版本与拒绝执行的绑定记录 |

所有 action 都要求 `expected_generation`。升级和回滚只改变默认版本，不改写已有编排、任务及授权快照的资源 ID；旧版本在显式退役前仍受原有审批和实时门禁保护。卸载不删除用户通过资源 API 创建的 MCP/技能/订阅，也不释放原资源的插件归属；同一个已卸载的插件身份不能重新安装。需要临时停用应使用 disable。

退役/卸载保守检查员工、团队策略、其他技能、能力资源、编排草稿及保留的发布版本、未终结 Task/Run、自动化及未完结消息/投递。存在任意引用时返回 409 和依赖列表，不强删、不静默改写。先迁移或删除依赖，不能靠换一个草稿绕过历史发布版本。

`POST /plugins/trust-keys/{id}/revoke` 永久撤销该工作区公钥。MCP 发现/执行、技能装入 Run、Run 执行前、消息接收/派发及 Webhook 投递复用 `plugins/policy.py` 实时门禁。禁用期间消息派发保留待处理状态；回复拒绝进入现有死信路径，可按既有操作流程排查和重放。已经发出的远程请求不能被追溯撤销，已装入运行中模型上下文的文字也无法收回；检查作用于下一次平台调度或能力调用。

## 存储与验证边界

迁移 `0096_remote_plugin_lifecycle` 增加工作区信任密钥、安装、不可变 release、唯一资源 binding 四张表，包含租户复合外键、状态约束与版本唯一性。升级会禁用原来的无执行资源插件安装和旧格式 listing，要求重新提交签名包；没有兼容解析。降级将插件市场安装置为 disabled 并清空安装资源指针，再移除新表。

签名使用现有轻量依赖 [cryptography 的 Ed25519 公共 API](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/)，不自研加密、不引入通用插件框架。远程能力继续使用现有 MCP SDK 和 Webhook HTTP 客户端；拒绝 importlib/pip 动态加载第三方入口，因为它会突破 API/Worker 的执行隔离边界。

本地验证覆盖公开审核到签名安装、跨工作区拒绝，以及消息/周期触发到工作流终态和签名回复，包含禁用、篡改签名、权限缺失、版本回滚、依赖阻止卸载和密钥撤销后阻断待投递消息。测试使用模拟 HTTP，未调用真实渠道或模型。PostgreSQL 锁竞争和迁移升降级需要在发布门禁使用真实 PostgreSQL 验证，SQLite 流程检查不作为这部分证据。

## 中心的职责

- 工具中心管理可调用工具、MCP 发现、凭证、Schema、风险和试调用。
- 技能中心管理可复用指令、资料、能力依赖及工作区安装快照。
- 插件中心管理版本化扩展包及其声明能力。
- 编排中心引用前三者的能力 ID，把它们组合成可发布的任务图。

四个中心共用同一能力目录、授权快照、审批、审计和运行时，不各自实现执行器。
