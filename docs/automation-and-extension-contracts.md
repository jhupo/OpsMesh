# 自动化与扩展合同（2026-09-18）

## 当前状态

后端现在支持工作区级自动化配置。自动化引用一个已发布的工作流版本和一个团队，触发方式可以是消息或周期调度。消息事件进入持久化收件箱后按 `external_event_id` 去重，后台维护任务将它转换为现有 `Task`，由现有 Task/Run 执行链处理。任务进入终态后，通过已配置的 Webhook 订阅创建签名投递，投递成功或死信都会回写自动化事件。

自动化不创建第二套执行引擎，也不创建永久占用的 Agent 或容器。等待消息和周期到期时只保存 Postgres 状态，执行和投递仍由现有 worker 负责。

## 消息驱动协作（2026-09-18）

本阶段实现的是通用消息接入和协作功能。外部插件在各自仓库实现渠道鉴权、收发和业务 API；本仓库维护平台，Python SDK 已迁至独立仓库，没有钉钉适配器或订单业务模块。

自动化配置新增 `allowed_message_actions`，默认只有 `["start"]`，管理员必须明确开启续接和控制；`notify_progress` 默认 false，开启后发送任务状态及待审批信息。同一个 sender 在同一 automation、workspace 和 conversation 内才能引用自己的已接受事件。目标必须显式指定 `reply_to_event_id`，不会用“最近一条消息”猜测任务。

| IncomingMessage.action | reply_to_event_id | 行为 |
| --- | --- | --- |
| start | 不提供 | 新建任务；已有同会话任务时按 queue/skip 策略处理 |
| follow_up | 必须 | 原任务未结束则追加指令；结束后新建任务，input.previous_context 携带前次任务 ID、状态与有界脱敏结果 |
| add_instruction | 必须 | 追加到原任务消息记录，供后续运行构造上下文；不重写已发出的模型请求 |
| pause | 必须 | 人工接管：暂停任务、取消当前 Run 及其待审批项 |
| resume | 必须 | 解除人工暂停，生成新的持久化待执行 Run；由既有队列恢复机制派发 |
| cancel | 必须 | 取消原任务及运行；终态任务不会被自动重开 |

“人工接管”目前是暂停、补充指令、恢复的任务控制，不是实时人工坐席系统；“续接”复用任务上下文，不共享不同用户的厂商会话。缺少信息时可由员工给出补问结果，用户通过 follow_up 继续；没有新增另一套对话引擎或自动理解控制命令的模型。

`external_event_id`（SDK 的 event_id）在 automation 内唯一。相同消息重试返回相同事件；相同 ID 换内容会拒绝。控制动作、任务状态和事件消费在同一个数据库事务内落地，重试不会重复追加指令。消息派发、回复准备按 checked_at 轮转，暂停或等待的旧事件不会长期占满扫描批次。

`AutomationClient.state(event_id)` 对应 `GET /automations/{id}/events/{event_id}`，返回事件状态、任务状态、有界结果及待审批 ID/类型/风险，不暴露审批密文或私有凭证。该接口要求现有 workspace READ 权限；它面向可信连接器，不是让渠道终端用户直接持有平台令牌。

`automation.reply` 的 data 是 SDK `AutomationReply`：kind 为 result、progress、action_required 或 control_applied，包含 conversation_id、sender_id、source_event_id、task_id、contract_version 和单调 sequence。最终业务结果上限为 32,000 个 ASCII JSON 字符，超限或不符合 Schema 时返回空 output 和 output_contract_rejected，不将截断对象伪装成合法业务结果；完整产物继续走现有工作区授权接口。Webhook 通知仍是状态变化提示，实时预览走下述订阅接口。

外部接收方调用 `parse_automation_delivery`，先验签，再核对 workspace/automation，按 envelope.id 持久化去重，并用 event_id + sequence 忽略迟到通知。平台采用至少一次投递；已成功/死信的重复 worker job 不重复发送，真实网络请求发出后连接中断仍可能重试，因此不能宣称跨系统 exactly-once。明确重放继续使用既有 Webhook 重放接口。

回复入队和真正发送前都检查自动化主体权限及发送者授权，投递还检查插件状态。撤销后未发出的消息进入死信，已发出的消息无法收回。通知中的 pending_actions 仅提示用户通过现有审批 API/UI 处理，不把聊天 sender_id 当成平台审批身份。

## 通用配置示例与复用边界

### 结构化输入输出与模型可见范围（2026-09-18）

自动化配置拥有 `contract_version`、`input_schema`、`output_schema`、`model_input_fields` 和可选的 `output_binding`。Schema 使用现有 JSON Schema 2020-12 校验器；引用只在本地解析，不远程加载。更改 Schema、模型字段投影或结果绑定必须提高 contract_version；已接受事件继续使用其冻结的合同。版本不符或输入不合法时在入站返回 400，不创建任务。

`IncomingMessage.data` 保存插件提交的业务对象，start/follow_up/add_instruction 按 input_schema 校验。text 可省略，文字和 data 至少有一项非空；pause/resume/cancel 不要求重复提交业务数据。整个消息上限 64,000 UTF-8 字节，追加指令的文字与可见 data 合计不得超过 4,000 字符。

插件校验真实渠道身份后填写 sender_id。用户名、级别等可以放在 data.user，但它们只是外部声明，不授予平台权限。`model_input_fields` 是 data 顶层字段的白名单，默认空；Task.input.event 仅含 event_id、text 和白名单 data，不把完整渠道身份与路由信息塞进模型上下文。路由字段由消息收件箱保存。允许一个对象字段意味着允许其整个子树，需进一步分隔隐私字段时应在外部输入合同中分开声明。

`output_binding` 复用工作流数据绑定，例如 `steps.summarize.output.structured_output.value`。省略时校验整个 Task.final_output。绑定取值沿用 16 KiB 单值限制；输出脱敏后再校验 output_schema。任务失败/取消不输出半成品；控制操作回执使用固定控制结构，不套用业务结果 Schema。输出校验失败不改写已完成的任务，但 state.output_error、Webhook error_code 及 output.rejected 明确说明拒绝交付。

例如，给下方通用编排模板对应的自动化加入这些字段（其余 team、workflow、sender 和回复配置仍必需）：

```json
{
  "contract_version": 2,
  "input_schema": {
    "type": "object",
    "required": ["question", "user"],
    "properties": {
      "question": {"type": "string", "minLength": 1},
      "user": {
        "type": "object",
        "required": ["id", "name", "level"],
        "properties": {
          "id": {"type": "string"},
          "name": {"type": "string"},
          "level": {"type": "string", "enum": ["standard", "vip"]}
        },
        "additionalProperties": false
      }
    },
    "additionalProperties": false
  },
  "model_input_fields": ["question"],
  "output_schema": {
    "type": "object",
    "required": ["title", "content"],
    "properties": {"title": {"type": "string"}, "content": {"type": "string"}},
    "additionalProperties": false
  },
  "output_binding": {"reference": "steps.summarize.output.structured_output.value"},
  "stream_output_nodes": ["summarize"],
  "stream_tool_events": true
}
```

插件提交同版本的 IncomingMessage，data 为 `{ "question": "请分析这次问题", "user": { "id": "u-1", "name": "张三", "level": "vip" } }`；sender_id 应来自渠道已验证身份。本例 user 仅用于平台收件记录，模型只看到 question。通用模板存在后续人工确认节点，因此流式总结仍是待确认预览，不能在审批前当成正式结论。

### SDK 实时订阅

`GET /api/v1/workspaces/{workspace_id}/automations/{automation_id}/events/{event_id}/stream?cursor=0-0`
返回 `application/x-ndjson`，每行一个 `AutomationStreamEvent`。SDK `AsyncAutomationClient.events` 使用 HTTPX 的流读取和标准 JSON 校验；无需复制后端或新增 SSE 解析器。原有任务 SSE 接口保留其工作区观察用途，连接器应使用经过消息作用域过滤的新接口。

| 事件 | 插件处理 |
| --- | --- |
| state | 更新任务状态、最终业务对象、待审批信息 |
| output.reset | 新的运行尝试，重置对应 run 的预览 |
| output.text | 替换该 attempt 的预览文本；provisional=true，并非已校验的最终 JSON |
| tool.started/completed/failed/waiting | 按 call_id 更新工具进度；只含名称、ID、状态 |
| approval.required | 显示等待确认；通过现有审批 API 处理 |
| output.completed/output.rejected | 接收已校验结果，或处理拒绝交付 |
| task.failed/task.cancelled | 显示失败/取消，清除未确认预览 |
| checkpoint | 保存已消费游标，无需向终端用户展示 |
| stream.reset | 游标已不在保留窗口，清除预览并采用返回的权威状态，再处理保留事件 |
| stream.reconnect | 正常连接轮换；SDK 自动携带游标重连 |
| stream.completed/stream.revoked | 停止订阅；后者表示实时授权已撤销 |

只有 `stream_output_nodes` 明确列出的已发布 agent 节点才向插件公开文本；默认空。`stream_tool_events` 默认 false。配置可见性在每次读取时按“事件冻结配置与当前配置的交集”执行。订阅要求自动化创建主体的身份，并反复校验主体、发送者与插件状态；终端用户不应获得平台凭证。

Worker 将 SDK 片段即时送入现有 Redis TaskEventBus，不再等完整模型结果才提供预览。普通工具与直接 tool/mcp 节点经过同一个实时观察适配器；SDK 原生工具使用既有生命周期钩子，无法提供 call_id 的原生钩子会返回 null，不能保证所有厂商原生工具的字段完全一致。事件不公开工具原始参数、结果、推理内容或供应商原始事件。

文本使用可替换的累计预览，约每 100 ms 最多发一次，结束时刷新尾部；上限 32,000 字符，超出标记 truncated。累计文本复用现有脱敏规则，模型预览并非完整输出审核；配置了阻断型输出 guardrail 的 Run 不公开文本预览，最终仍需通过 guardrail 与业务 Schema。涉及保密审核的流程应仅流工具状态和最终结果。

Redis 保留窗口沿用任务流的约 10,000 条事件，不承诺所有历史片段永久保存。Postgres 继续保存最终任务状态与结果。网络断开由调用方处理 HTTPX 异常并使用已持久化 cursor 重订阅；SDK 不静默吞错。保留窗口失效发送 stream.reset，Redis 不可用则连接失败，不伪装为空流。每个连接最多约 55 秒后轮换；once=true 只读取一批用于轮询。插件必须先处理事件再保存 cursor，按 attempt_id/sequence 去重，并以最终结果覆盖预览。

配置保存在现有 JSON 配置与事件快照中，本次无需新建执行表或迁移。外部插件自行决定更新卡片、合并文本或发送最终消息，本仓库不实现具体渠道。

[message-collaboration.workflow.json](examples/message-collaboration.workflow.json) 是现有编排创建接口的请求模板：专家分析 → 知识检索 → 领导整合 → 人工确认 → 长期记忆。它不包含业务 HTTP 地址或外部插件代码。使用前必须配置 specialist、project_manager 和独立的 memory_curator 员工、团队成员关系、模型、工具授权与记忆资源范围，再将模板提交到编排创建接口并发布。memory_curator 专用 profile 持有写权限，分析与总结员工只持有必要的读权限。

工具 `search_workspace_memory` 和 `upsert_semantic_memory` 已有实现；必须给对应员工授权读/写资源，并在自动化 input_defaults 中提供 `memory_scope_id`（本例为当前工作区 UUID）。只配置工具名不授予数据权限。分析与总结员工不要开放记忆写工具；只有确认节点之后的直接工具节点使用写权限。若员工与写节点共享同一 profile，须通过既有工具审批策略强制记忆写审批，不能把 prompt 当作写入权限边界。

若需 MCP 数据，可在分析之前用画布合同插入 mcp 节点，绑定工具中心已发现并批准的 server/allowlist ID，通过 input_bindings 将结果传给专家；外部 MCP 服务自身不属于本仓库。原有角色分配、结构化输出、条件判断、审批、知识引用和三层记忆继续复用，不增加重复节点执行器。

迁移 `0097_message_collaboration` 保存控制结果、通知序号、指纹及扫描时间。已在既有产品流程文件中验证 SDK 消息入站、团队/编排、待审批、人工控制、续接和签名回复；不以单点测试替代完整流程。真实 PostgreSQL 锁竞争、升级/降级与真实模型/渠道联网仍需独立环境验证。

## Web 画布合同

`GET /api/v1/workspaces/{workspace_id}/orchestrations/authoring-contract` 返回节点 JSON Schema、编辑器元数据 Schema、节点类型、选择器和数据绑定字段。创建或修改编排时，`editor.positions`、`viewport` 和 `zoom` 只保存布局，不参与执行语义；`definition.nodes` 是唯一的执行定义。发布后工作流版本不可变，任务引用具体版本。

画布应使用这些合同生成表单和节点，而不是把业务规则复制到前端。后端发布和运行时仍会重新验证工作区资源、团队、MCP allowlist、权限和版本。

## 插件 SDK 边界

`opsmesh-plugin-sdk` 的源码唯一位于 [独立仓库](https://github.com/jhupo/opsmesh-plugin-sdk-python)。
平台通过 pyproject.toml 中的完整提交归档依赖及 uv.lock 摘要安装；不再复制源码，
也不依赖浮动 master。尚未发布 PyPI 版本，当前固定源码归档需要安装器构建 wheel。
外部连接器只依赖 SDK，不导入 backend。SDK 提供签名插件包、能力声明、同步/异步客户端、
结构化消息与回复、流式订阅、Webhook 验签及作用域校验。

SDK 目录、外部插件贡献位置和插件发布资产合同维护在独立仓库
[架构与分发合同](https://github.com/jhupo/opsmesh-plugin-sdk-python/blob/master/docs/architecture.md)。
平台还支持 [固定目录同步与受控下载](plugin-distribution.md)，仍调用同一个 PluginService。
仅拉取签名 JSON；托管 OCI 插件部署未实现。SDK 下载和业务插件安装不是同一件事。

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
