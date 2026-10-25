# 插件合同

最小安装包包含 `plugin.yaml`、`main.py`。清单定义 `id`、`name`、`version`、可选说明和依赖。宿主为每次安装分配独立 ID，`setup(ctx)` 只声明注册项；模块顶层同样不得启动后台服务或网络连接。

```python
from opsmesh_sdk import InvocationContext, PluginContext

async def run(ctx: InvocationContext, value: object) -> object:
    return {"caller": ctx.user.user_id, "input": value}

def setup(ctx: PluginContext) -> None:
    ctx.entry("main", run)
```

`setup` 可以注册多个入口及资源，没有 `workflow` 资源类型。并行、顺序、转交、原生 Agent.as_tool 等组合方式都由插件选择。

独立 SDK 的安装和开发说明见 [SDK 分支文档](https://github.com/jhupo/OpsMesh/blob/sdk/README.md)。SDK 打包和类型检查支持 Python 3.11.8+，运行插件需要匹配的 Python 3.12+ 宿主。普通用户通过目录发现和使用管理员安装包的公开贡献，也可以用已授权的贡献发布自己的配置版本。Python 代码依赖由管理员安装到宿主环境；不在 HTTP 安装请求内修改运行环境。

## 上下文

框架构造的 `user` 是执行者身份，嵌套 Agent 不会换成发布者身份。资源所有者与执行者可以不同：模型连接属于 Agent 发布者，实际 Token 记在执行者账户；私有 workspace 的管理 API 仍只向所有者和管理员开放。

`metadata` 可以携带业务 task ID、业务资源引用、显示偏好等。插件实现查询和业务授权，例如先用 `ctx.user.user_id` 或组 ID 判断是否可读，再用业务 task ID 查询；不能因为 metadata 声称 `role=admin` 就提权。

原生 `RunContextWrapper[InvocationContext]` 可直接用作 function_tool 第一个参数。发布工具注册原生 FunctionTool，例如 `ctx.tool("identity", function_tool(inspect_identity))`，保留审批、超时和 guardrail 配置；消费者通过 `await ctx.dependencies.tool(alias)` 取得工具。调用前重新验证发布授权，并将该贡献的配置、依赖和当前调用者传入工具。

MCP 工厂接收 InvocationContext，返回新的原生 MCPServer。消费方必须使用 `async with ctx.dependencies.mcp(alias)`，让 SDK 管理连接、工具发现和清理。平台身份不会自动成为远端服务身份；远端仍必须验证凭据和执行其自身授权。

SDK 的 `tool_meta_resolver` 可把身份或业务引用放入 MCP `_meta`，参考 `examples/contributions/main.py`。这些元数据来自宿主调用上下文；远端必须确认请求来源可信，不能把任意客户端发送的 `_meta.user_id` 当成认证。

## 技能与沙箱

技能是包内含 `SKILL.md` 的目录。`setup` 注册路径，发布后消费者拿到固定版本路径。普通 Agent 不会因为注册技能就自动读取它。

原生 SandboxAgent 可使用下面的能力声明：

```python
from agents.sandbox import SandboxAgent
from agents.sandbox.capabilities import Capabilities, Skills
from agents.sandbox.entries import LocalDir

skill = await ctx.dependencies.skill("reporting")
agent = SandboxAgent(
    name="Reporter",
    model=await ctx.resources.model(connection_id, model_name),
    capabilities=Capabilities.default() + [Skills(from_=LocalDir(src=skill))],
)
```

插件使用 `async with ctx.session.sandbox(client, options, manifest=...)` 获得统一目录和恢复配置，传给原生 `RunConfig(sandbox=...)`。历史传入 `session=ctx.session.history`。Docker 扩展安装使用 `uv sync --extra sandbox`。插件通过 `ctx.sandbox(backend, factory)` 声明原生客户端工厂；宿主维护会话隔离、历史和原生快照，文件路由复用该工厂访问沙箱，详见 [会话说明](sessions.md)。`LocalDir` 是文件暂存，不能把它当成持续同步的宿主目录挂载。

Run 完成、取消或 Worker 退出均不会让宿主自动删除用户目录。沙箱插件也必须区分关闭连接与删除沙箱，显式删除才能回收用户的持久状态。

## 例子

- `native_agent`：原生 Runner、function_tool、发布依赖、RunState 人工交互。
- `sandbox_agent`：原生 Docker SandboxAgent、默认目录、持久历史和沙箱恢复；需要安装 sandbox extra 并提供可用的 Docker 后端。
- `contributions`：上下文授权工具、MCP 元数据和技能；示例 MCP 域名由管理员的 `EXAMPLE_MCP_ALLOWED_HOSTS` 控制。
- `composition`：插件代码调用已发布的 `analyst` 依赖，框架不解释编排图。

所有示例均需要实际安装和配置后才运行。示例的存在不代表已经完成业务验收，实际验证范围见 [校验边界](verification.md)。


## 进度事件

`InvocationContext.events` 提供 `async emit(event: str, data: Any) -> int`，返回任务事件 ID。名称使用小写字母、数字、点、下划线或短横线，以小写字母开头，最多 80 字符；数据必须能序列化为有限值 JSON。宿主添加 `plugin.` 前缀，持久加密后供任务调用者通过 SSE 接收。

使用 `await context.events.emit("progress", {"step": 1})`。模型流由原生 Runner.run_streamed 生成，插件按需要聚合和发送文本增量，参见 examples/native_agent。不要逐 Token 提交独立数据库事务；应合并短时间窗口或一定字数。客户端断开 SSE 不会取消执行，取消仍通过任务取消接口。

## 通道贡献

`ctx.channel(name, factory)` 只注册工厂。Gateway Worker 调用工厂，校验返回值为 `opsmesh.gateway.adapters.base.Adapter`，并管理 run 生命周期。通道实现直接依赖匹配的宿主框架，实现 capabilities/run/send/call；传输模型与错误位于 gateway/adapters，SDK 不生成传输协议。任务使用 `context.channel.reply/request` 提交持久操作，不取得 SDK 客户端、连接或凭据。平台操作只允许已声明的目录，执行时再次复核管理员身份。
