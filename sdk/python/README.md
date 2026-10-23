# OpsMesh Plugin SDK

`opsmesh-sdk` 用于系统插件的开发、注册和打包，不提供业务 API 客户端，
也不依赖服务端、数据库、Temporal 或 OpenAI Agents SDK。
插件由管理员安装并启用，宿主在加载与热更新时调用同步入口 `setup(ctx)`。

## 插件包

```text
demo-agent-plugin/
|-- plugin.yaml
|-- main.py
`-- skills/
    `-- log-review/
        |-- SKILL.md
        `-- references/
            `-- examples.md
```

```yaml
id: demo-agent-plugin
version: 0.1.0
name: Demo Agent Plugin
description: Tools, Skills and lifecycle hooks
icon: ""
kind: plugin
entry: main.py
```

`kind` 可取 `tool/skill/mcp/hook/middleware/channel/plugin`。
同一个插件可以同时提供多种资源；组合插件使用 `plugin`，特定分类必须注册对应资源。
兼容原有 `hook` 分类注册模型中间件。
`version` 表示当前包版本。读取清单或检查归档不会执行插件入口。

## 注册工具、Skills 和 Hook

```python
from opsmesh_sdk import AgentHookEvent, PluginContext

def count_lines(text: str) -> int:
    """Count lines in supplied text."""
    return len(text.splitlines())

async def tool_finished(event: AgentHookEvent) -> None:
    # Read only the data needed by this callback; never retain SDK objects.
    tool_name = event.data["tool"].name
    print(f"Completed tool: {tool_name}")

def setup(ctx: PluginContext) -> None:
    ctx.tool("count_lines", count_lines, timeout_seconds=30)
    ctx.skills("skills")
    ctx.hook("tool_end", tool_finished, priority=100)
```

完整源码见 [组合插件示例](examples/demo-agent-plugin)。

`ctx.tool` 接受普通同步或异步 Python 函数，通过函数签名生成工具结构。
每个工具可声明 `config_fields`、`admin_only`、`timeout_seconds`、
`effects` 和 `requires_approval`。工具内可用 `get_tool_config()`、
`get_invocation()` 获取宿主绑定的配置和身份；这些函数只在宿主调用范围内可用。

`ctx.skills("skills")` 注册包内的技能根目录，每个子目录必须包含 `SKILL.md`。
插件不能注册包外目录或符号链接。宿主保留每个技能的完整目录树，包括
`scripts/references/assets` 和其他随附文件。注册到目录不等于安装进每个 Agent：
只有 Agent 选中的技能才通过原生 Skills 能力写入其工作目录。

## 生命周期 Hook

`ctx.hook(point, async_handler, priority=100, timeout_seconds=30)` 支持：

| point | event.data |
| --- | --- |
| agent_start | 空 |
| agent_end | output |
| handoff | source，即移交来源 Agent |
| tool_start | tool |
| tool_end | tool、result |
| llm_start | system_prompt、input_items |
| llm_end | response |

事件还包含当前 `context` 和 `agent`，它们是 SDK 借用对象，不可持久保存。
同一通知的回调串行等待；不同运行的通知可以并发发生。
开始通知按 priority 从小到大执行，结束通知逆序执行；
同优先级按插件 ID 和注册顺序确定，结束时同样逆序。
回调必须返回 None；异常或超时直接传播，不静默忽略，也不自动重试。
开始回调失败后不会合成一个不存在的 SDK 结束通知，不能用它管理必须释放的资源。

这些是 Agent 生命周期通知，不是授权、审批或请求改写接口。
改写模型请求/响应使用 `ctx.middleware(ModelMiddleware实例, priority=100)`；
该接口仍通过执行宿主的模型 I/O 边界调用。输入输出检查、审批使用各自的
原生 guardrails/approval 合同，生命周期 Hook 的返回值不能代替这些决定。
通道继续通过 `ctx.channel(...)` 注册，由通道宿主启动并释放。

## 与新 agents 模块连接

宿主的 `PluginRegistry.agent_bindings(invoke_tool=dispatcher, plugin_ids=...)`
创建新的 `AssemblyBindings`，只注册宿主授权选择的已启用插件。
已有 bindings 可用 `register_agent_plugins(registry, bindings, invoke_tool=dispatcher)`
加入插件贡献。Agent 插件适配位于服务端 `agents/plugins`，通道注册与运行位于 `gateway`。

注册 ID：

| 内容 | ID |
| --- | --- |
| 工具 | demo-agent-plugin/tools/count_lines |
| Skill | demo-agent-plugin/skills/log-review |
| MCP | demo-mcp-plugin/mcp/service |
| 本插件的 Hook 组合 | demo-agent-plugin/hooks |
| 本次已注册插件的 Hook 总组合 | opsmesh.plugins.hooks |

Agent 选择工具使用 `ToolRef(tool_id=...)`；选择 Hook 使用
`CallbackRef(callback_id=...)` 赋给 `hooks`；选择技能使用
`SandboxCapabilityConfig(type="skills", skill_ids=[...])`。
单个插件 Hook 组合和总组合二选一，避免同一回调重复执行。
工具的模型可见名称和技能安装目录带插件命名空间，防止插件间同名覆盖：
示例技能安装到 `.opsmesh/skills/demo-agent-plugin__log-review/`。

dispatcher 的参数为
`(plugin_id, tool_name, native_tool_context, arguments, binding_config)`。
它必须逐次授权并提供 InvocationContext、稳定 operation_id、超时和效果回执；
持久执行通过 Activity 调用插件。适配层不会直接调用已注册的 Python 函数，
会保留原生工具的审批要求。管理员安装插件不免除运行时授权。

`GET /api/v1/agents/plugins` 返回当前用户可见的插件工具及其
工具的 `id/name/parameters/output_schema`、调用约束及插件内 `tool_name`，
以及 `agent_skill_ids`、`agent_hooks_id`、`hook_points`、`mcps`。
工具 `id` 可直接作为 `ToolRef.tool_id`；HTTP 调用路径使用插件内的 `tool_name`。
直接执行时，原生生命周期适配器与 Runner 在同一进程；Temporal 执行时，
宿主将生命周期通知转为 Activity，并冻结插件版本与安装配置摘要。
Hook 接收重建的原生 Agent 和上下文副本，不得依赖修改对象或工厂状态影响 Runner。
通知返回值必须为 None；存在外部效果但结果未确认时，需要人工对账，不能盲目重试。

## MCP 工厂与 Agent 绑定

`ctx.mcp(name, factory, config_fields=..., admin_only=False)` 注册工厂。
工厂签名为 `factory(config, **native_options)`，每次返回全新的原生
`MCPServerStdio`、`MCPServerStreamableHttp` 或 `MCPServerSse`，并将
`native_options` 原样交给 SDK 构造器，保留工具过滤和审批策略。
工厂也可以是异步函数。完整示例见 [MCP 插件](examples/demo-mcp-plugin)。

安装配置按资源组织，例如 `{"mcp":{"service":{"url":"https://mcp.example.com/mcp"}}}`。
Agent 引用如下；`config` 可补充非敏感覆盖值，凭据优先使用管理员加密安装配置：

```json
{
  "mcp_servers": [{
    "connection_id": "demo-mcp-plugin/mcp/service",
    "config": {},
    "allowed_tools": ["search"],
    "require_approval": "always"
  }]
}
```

宿主通过授权后的 `agent_bindings(...)` 或 `register_agent_plugins(...)` 加入工厂；
普通绑定默认排除 `admin_only` MCP，管理员宿主显式传 `include_admin_mcp=True`。
`build_agent(...)` 解析引用时创建并连接原生 Server，退出异步上下文时清理连接。
MCP 服务器实现、工具、资源、提示词和连接协议继续由 MCP/Agents SDK 管理。
仅安装插件不会把 MCP 自动加入所有 Agent，也不会启动其 stdio 子进程。

Temporal 宿主须传入 `connect_mcp`，在 Workflow 中返回官方
`temporal_mcp_server` 代理，在 Activity 中解析授权配置、调用工厂并管理连接。
默认连接器用于普通异步 Agent，不可在 Workflow 内直接连接。
框架不内置业务服务连接、不增加 OAuth 回调服务，也不复制 SDK 的 MCP 实现。

## 通道插件

宿主内置自己的 QQ 和钉钉适配器。其他协议实现 SDK 的 `ChannelAdapter`，
在 `setup(ctx)` 注册；不需要导入 OpsMesh 服务端或 octop：

```python
from opsmesh_sdk import PluginContext
from .channel import MyChannel  # Implements opsmesh_sdk.channels.ChannelAdapter

def setup(ctx: PluginContext) -> None:
    ctx.channel("my_channel", MyChannel, label="My Channel")
```

通道实现使用独立 SDK 合同：

```python
from opsmesh_sdk.channels import ChannelAdapter, InboundMessage, OutboundMessage, DeliveryReceipt
```

实现 `validate_config()`、`run()`、`close()` 和 `send()`。构造与校验不得联网；
`run()` 管理连接并将完整 `InboundMessage` 交给 `self.sink`，必须等 sink 成功后
才确认厂商消息。外部身份必须包含 scope、id_kind、external_id；不得用昵称绑定账号。
`send()` 返回 `DeliveryReceipt`，明确拒绝使用 `DeliveryRejected`，无法确定厂商是否
接收时使用 `DeliveryUncertain`。`capabilities` 声明真实能力。
宿主禁止覆盖已有 kind，负责租约、加密配置、可信绑定与路由；插件负责厂商协议。
配置字段通过 `ctx.channel(..., fields=[...])` 声明，类方法执行最终结构校验。
插件依赖声明在包中，SDK 不引入厂商运行库。
配置和使用说明见 [Gateway](../../src/opsmesh/gateway/README.md)。

## 调用者与授权

宿主为工具提供可信 `Principal`（平台用户和角色）与 `InvocationSource`
（通道、外部身份、会话、private/group 回复范围）。普通 HTTP 请求不能提交这些字段。
通过 `get_invocation()` 在插件内部读取；发现工具与执行工具分别调用可选授权回调。
`admin_only` 仍由框架检查，`authorize` 用于插件自己的业务规则。

```python
from opsmesh_sdk import PluginContext
from opsmesh_sdk.identity import AuthorizationRequest
from opsmesh_sdk.registration import get_invocation


def allow_private(request: AuthorizationRequest) -> bool:
    return request.source.reply_scope == "private"


async def user_report() -> dict[str, str]:
    caller = get_invocation()
    # The plugin must constrain its actual data query to this user's grants.
    return {"user_id": str(caller.user_id)}


def setup(ctx: PluginContext) -> None:
    ctx.tool("user_report", user_report, authorize=allow_private)
```

回调只返回 True 才放行，可以是异步函数。request.arguments 在执行阶段提供，
发现阶段为空；发现过滤不能代替执行授权。插件应自行限制查询范围和结果内容。
框架不内置组织、服务器或项目业务规则。`Principal.group_ids` 是扩展字段，当前
平台没有组授权目录，默认空集合；外部群和已见成员保存在 Gateway，不能当平台权限。

`InvocationSource` 还包含宿主观察到的 external_role、external_id、identity_scope，
以及用于会话隔离的 session_scope；外部角色不自动成为 Principal.roles。
通道的 `MessageContext` 保留提及、引用和可见性，`OutboundMessage.event` 区分
message/delta/completed/typing。ContentPart 媒体必须指定 media_key、公共 URL、base64 data
之一；MessageContext.recent_messages 表示平台随当前事件提供的有界上下文，不是授权声明。
声明 streaming、typing、proactive、qr_binding、probe 等能力
只说明适配器接口范围，不能使厂商授予额外权限；QQ 扫码入口由内置宿主管理。
probe 仅探测认证，不启动入站连接或消费消息。

插件可在 setup 中用 `ctx.credential("api_key", "env:MY_API_KEY")` 声明凭据引用，
在工具执行中用 `get_plugin_credential("api_key")` 获取宿主解析的 SecretStr。
引用由管理员安装配置决定，模型工具参数不能选择任意 secret；setup 不读取凭据或联网。
调用结束后上下文恢复，凭据不进入工具参数、描述或结果。
非 OpenAI 决策协议的实例见 [TypeSafe Decision](examples/typesafe-decision/README.md)。

`ctx.mcp(..., authorize=...)` 也支持同类策略，resource 使用
`plugin-id/mcp/name/connect`、`.../tools`、`.../tools/tool_name`、`.../prompts`、
`.../prompts/name`、`.../resources`、`.../resource_templates` 或 `.../resources/read`。
连接与实际 RPC 均重新授权；工厂可在 `get_invocation()` 中读取用户以选择凭据。
资源列表的逐项业务过滤由 MCP 实现负责。MCP 传输、stdio/HTTP 连接与协议仍由
Agents SDK 管理；授权层不替代 MCP 服务自身的认证和数据权限。
上下文中的 MCP operation_id 仅作关联，不能代替实际工具调用的幂等键。

## 构建和安装

```bash
opsmesh-plugin build sdk/python/examples/demo-agent-plugin --output ./demo-agent-plugin.zip
opsmesh-plugin check ./demo-agent-plugin.zip
opsmesh plugin install ./demo-agent-plugin.zip
opsmesh plugin enable demo-agent-plugin
```

CLI 默认通过管理员 Session 调用在线安装接口；设置 `OPSMESH_SESSION_ID` 后使用。
在线 ZIP 导入与 CLI 走同一校验、安装和注册流程，插件默认禁用。
启用、配置、升级与卸载由运行中的 API/Worker/Gateway 自动重载，无需整体重启；
修改依赖环境或宿主程序仍需按部署流程更新。
`opsmesh plugin list` 的 `applied` 要求 API、Worker、Gateway 都报告当前版本生效。
`opsmesh plugin reload` 立即触发当前 API 加载，其他进程自动跟进。
URL 安装要求 HTTPS 和 `--sha256`；本地安装输入为 ZIP。
`opsmesh plugin --offline ...` 保留停机准备入口，仅校验归档、不执行资源校验；
其 `reload_pending` 不表示运行进程已完成加载。
安装版本不可覆盖；同一 ZIP 可重复在线提交，重新安装已卸载版本。
所有进程须共享插件包和数据目录，详见 [插件宿主](../../src/opsmesh/plugins/README.md)。

`setup(ctx)` 在安装校验、重载及不同进程中可能执行多次，只负责注册声明。
插件是管理员信任的进程内 Python 代码，拥有宿主进程权限。
普通用户的上传代码应通过隔离工具或 MCP 运行，不能直接进入这个注册表。
