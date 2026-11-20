# HTTP 与 OpenAPI 接口说明

核对日期：2026-11-19。状态：已有传输/OpenAPI 配置说明，以及未来业务接口的设计要求。**当前没有实现统一业务 HTTP 适配器，也没有可宣称已经部署的业务 OpenAPI 契约。** 本文不新增路由。

## 1. 三类接口的职责

| 接口 | 所属层 | 当前或目标行为 |
| --- | --- | --- |
| Python API，如 `load_plugin`、`require`、`Gateway.call_api` | 框架 / 适配器 | 当前已有；`call_api` 调用适配器实现，并不创建 HTTP endpoint |
| 对外业务 HTTP API | HTTP 适配器 + Driver | Driver 传输已有；HTTP 到统一业务事件及回复仍未实现，所需边界见本文第 3 节 |

OpenAPI 说明 HTTP 的路径、方法、输入输出和安全要求；它不是框架事件注册表。`on_request()` 注册的是 `request` 类别的 Matcher，`on_command()` 注册的是带命令规则的消息 Matcher，都不会自行注册 URL。

## 2. 已有 HTTP 与 OpenAPI 能力

传输模型见 [`internal/driver/model.py`](../src/opsmesh/internal/driver/model.py)：

```python
@dataclass
class HTTPServerSetup:
    path: URL
    method: str
    name: str
    handle_func: Callable[[Request], Awaitable[Response]]
```

Driver 的 `Request` / `Response` 是传输对象；框架 `Event` / `Message` 是业务分发对象。两组对象不能因为名称类似而合并。当前 `Response.content` 接受字符串、字节或空值，不能假定传入字典就会自动序列化为 JSON。

[`drivers/fastapi.py`](../src/opsmesh/drivers/fastapi.py) 已提供：

| 配置 | 默认值 | 行为 |
| --- | --- | --- |
| `fastapi_openapi_url` | `None` | OpenAPI JSON 地址，默认关闭 |
| `fastapi_docs_url` | `None` | Swagger UI 地址，默认关闭 |
| `fastapi_redoc_url` | `None` | ReDoc 地址，默认关闭 |
| `fastapi_include_adapter_schema` | `True` | `setup_http_server` 注册的路由是否包含在 schema 中 |

使用 FastAPI 驱动时，可以在初始化时启用这些现有选项：

```python
import opsmesh

opsmesh.init(
    driver="~fastapi",
    fastapi_openapi_url="/openapi.json",
    fastapi_docs_url="/docs",
    fastapi_redoc_url="/redoc",
)
# 在此注册实际适配器、加载插件，再启动驱动。
opsmesh.run(host="127.0.0.1", port=8080)
```

示例需安装 FastAPI 驱动依赖，只说明文档开关；它没有提供业务路由。生成的文档取决于实际注册的路由及 schema，不能拿页面可打开作为业务 API 已完成的证据。

当前注册路径为：

```text
Adapter / 启动装配代码
  → setup_http_server(HTTPServerSetup)
  → FastAPI.add_api_route(..., _handle, include_in_schema=...)
  → _handle_http：构造 Driver.Request
  → await setup.handle_func(request)
  → 将 Driver.Response 的 content/status_code/headers 转成 HTTP 响应
```

通用包装函数只声明原始 FastAPI `Request`；`HTTPServerSetup` 没有业务请求模型、响应模型和错误 schema 字段。因此目前这条注册链**不会从插件的 Depends 或 handler 注解自动得到完整业务 OpenAPI 模型**。驱动也不会自动调用 `handle_event`。

## 3. 目标 HTTP 接入的边界

以下是尚未实现的 HTTP 适配器目标链路；当前 Gateway 的接口和实际行为见[网关文档](gateway.md)，不能把这条目标链路当成现有路由：

```text
HTTP method/path + 认证 + 请求解码       ← HTTP 适配器
  → Gateway + 框架 Event/Message        ← HTTP 适配器
  → handle_event → on_* 对应 Matcher    ← 框架
  → Depends / 业务处理 / 显式回复       ← 业务插件
  → Gateway.send → 请求回复状态         ← HTTP 适配器
  → 序列化、状态码、HTTP Response       ← HTTP 适配器与 Driver
```

handler 的普通 `return` 不是 HTTP 回复；当前 `handle_event` 返回 `None`。未匹配、无回复、重复回复、异常、超时和客户端断开必须在目标回复契约中明确，不能假设每个请求必然由某个 Matcher 返回 JSON。

HTTP 适配器拥有协议契约，业务插件不导入 FastAPI，不扫描 Matcher 生成路由。请求/响应 schema 必须与实际校验和编码有同一来源；实现前明确采用适配器维护显式 schema、扩展现有路由描述，还是其他复用方式，并说明需要修改哪些现有文件。当前尚未选择并实现 schema 发布机制，本文不新增第二套路由注册框架。

### 尚待落实的接入契约

以下保留 HTTP 接入所需的设计边界，**不是已经提供的类型或 API**。HTTP 适配器实施前须按完整设计要求确定细节和验收标准：

- 适配器配置 `method + path → 事件类别、业务名`，不指向 Python handler 或 Matcher；事件类别按业务语义选择，不将所有 HTTP 输入固定为 `request`。同一监听入口的重复路由需在启动时拒绝。
- 认证、请求大小、Content-Type 和 JSON 校验在入站完成；事件的可信身份、类别和名称不能由不可信请求体覆盖。Driver 的 Request 可保留原始协议数据，不复制一套传输模型。
- 同一个 Gateway 可服务多个并发请求。回复状态应绑定每次请求的事件并隔离，不能保存在 Gateway 的共享单一响应字段；生命周期覆盖分发及依赖清理，结束后拒绝再次回复。
- 业务仍通过 `on_*`、Depends 和显式 `send/finish` 处理；当前接口的结构化回复、非聊天事件约束需另行评估修改。不能将尚不存在的任意字典回复写成可运行示例。
- 非流式请求须定义无回复、重复回复、同优先级竞争、业务失败与清理失败的处理，完成清理前不提交缓存的成功响应。普通 `return` 不自动作为响应；重复回复保护也不等于阻止重复业务执行。
- 字段校验错误应与模型内部故障区分，异常响应只公开安全信息；HTTP 状态码由适配器映射。额外的框架分发结果对象是否必要尚需论证，不把它当作 HTTP 接入的既定前提。
- 明确超时、取消和同步调用的限制；当前 Matcher 执行有取消保护，不能假设取消路由就停止所有工作。当前也没有完整的原生断连传播契约。
- SSE 为独立待设计能力，需要流响应、队列背压、生产/消费作用域、首字节前后错误和断连清理的完整契约；不能把普通 JSON 路径改成返回生成器就宣称支持完成。

这些事项在对应功能实现后，应以最终行为替换本节提案描述，并同步接口文档，不再保留过期实施方案。

## 4. 业务 HTTP 接口的完整契约要求

新增或调整业务 HTTP 接口时，每个操作至少说明以下内容；不适用项注明理由：

| 项目 | 必须交代的内容 |
| --- | --- |
| 身份与版本 | 实现/提案状态、所属适配器、method/path、稳定且唯一的 operationId、契约版本、变更策略 |
| 认证与权限 | 凭据位置、认证方式、身份如何传入事件及权限检查、认证失败响应；示例不含真实密钥 |
| 请求 | path/query/header/body、Content-Type、必填项、类型/约束、默认值、大小限制、未知字段策略、示例 |
| 事件映射 | `get_type()`、业务事件名、消息内容或结构化数据、调用者和会话标识；命令是否由消息规则解析 |
| 成功响应 | 状态码、Content-Type、响应头、完整 schema、空响应及序列化规则、示例 |
| 失败响应 | 无匹配/无回复、字段校验、权限、依赖不可用、业务异常、超时的状态码和统一错误结构；避免泄露堆栈/凭据 |
| 并发与重试 | 请求关联、幂等键（如需要）、重复调用、并行 Matcher 回复竞争、限流、升级期间准入行为 |
| 时间与资源 | 超时层级、取消传播、断开后是否继续执行、请求资源释放、长任务结果获取方式 |
| 流式与会话 | 是否支持 SSE/WebSocket/临时交互；不支持时明确拒绝行为，不能靠普通 JSON 接口暗示支持 |
| 实现与校验 | 路由/schema 来源、校验及序列化代码位置、示例与实际响应一致性、正反例和错误路径验收 |

设计阶段先完整定义契约并标注未决项；实现阶段再将选定的 OpenAPI 文件或生成器作为唯一来源，记录实际输出的 OpenAPI 版本和访问地址。不得为尚未实现的 `/models/infer` 等示例路径发布“现有接口”说明。不要将 OpenAPI 版本与 OpsMesh、插件或 NoneBot2 版本混为一谈。

## 5. 阅读与核对入口

- 本地源码：[`HTTPServerSetup` / `Request` / `Response`](../src/opsmesh/internal/driver/model.py)、[FastAPI Driver](../src/opsmesh/drivers/fastapi.py)、[`handle_event`](../src/opsmesh/message.py)、[Gateway](../src/opsmesh/internal/adapter/gateway.py)。
- 固定上游文档：[NoneBot2 添加路由](../.agents/skills/opsmesh-nonebot2/references/website/docs/advanced/routing.md)。
- 格式参考：[OpenAPI 3.1.1 Specification](https://spec.openapis.org/oas/v3.1.1.html)。这是规范参考，不代表本仓库已输出该版本文档。
- FastAPI 参考：[Using the Request Directly](https://fastapi.tiangolo.com/advanced/using-request-directly/)、[Extending OpenAPI](https://fastapi.tiangolo.com/how-to/extending-openapi/)。外部资料用于理解机制，本仓库实际行为仍需对照当前依赖及源码。
