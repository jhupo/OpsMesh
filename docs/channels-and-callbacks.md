# 通道与执行回调

职责总览见 [插件架构](plugin-architecture.md)。channels 将外部协议接入 Framework；execution 将原生 SDK 生命周期接入插件回调。二者不互相依赖。

## 一个 channels 插件

`api`、`qq`、`dingtalk` 是 channels 内的协议模块，不是独立插件。宿主仅加载 `opsmesh_channels`。

```text
packages/channels/src/opsmesh_channels/
├── __init__.py          # 元数据、协议入口和公共导出
├── config.py            # 按协议读取配置分区
├── context.py           # 冻结 UserContext
├── dependencies.py      # Connection(handle)
├── lifecycle.py         # API 与机器人传输的启停、回滚、排空
├── _queue.py            # 有界内存接收缓冲
├── _dedupe.py           # 进程内回调去重
├── api/
│   ├── routing.py       # HTTP 装饰器与请求/响应契约
│   ├── routes.py        # 所有者、冲突检测和 Matcher 绑定
│   ├── channel.py       # 入口认证、事件分发和响应
│   ├── streaming.py     # 流及请求依赖的关闭作用域
│   ├── auth.py          # 可信身份及操作授权
│   └── events/messages/adapter/config/errors/responses.py
├── qq/
│   ├── adapter.py       # 回调入口、校验和框架派发
│   ├── client.py        # 出站 HTTP 与 token 刷新
│   └── connections/channel/events/security/config/errors.py
└── dingtalk/
    ├── adapter.py       # 回调入口、校验和框架派发
    ├── client.py        # 出站 HTTP
    └── connections/channel/events/security/config/errors.py
```

表中合写的文件名表示同目录下的职责文件，不是额外目录。

channels 的 `config.api` 存在即启动 API，即使没有业务路由也提供 `/healthz` 和 `/readyz`。缺少必要认证配置时不退回匿名入口。纯机器人部署可省略 API 配置；有 API 路由却没有 API 配置时启动失败。

QQ/钉钉的账号和连接由消费插件通过 `channels.qq.declare_connection(...)` 或 `channels.dingtalk.declare_connection(...)` 声明；协议公共限制来自 channels 配置分区。未声明的机器人不创建网络连接。多个连接共享协议适配器，声明独立归属，加载失败/撤销只移除本插件登记。

业务插件使用 `@channels.api.get/post/...` 声明路由，框架按所属插件 ready 状态接单；没有手动 register/activate 协议。撤销一个业务插件不会关闭其他消费者共享的 API。

## 统一消息与身份

```text
HTTP Driver / 已验证机器人回调
→ 通道认证与协议解析
→ Event + Message + UserContext
→ dispatch_event
→ Permission / Rule / Matcher / Dependent
→ 插件处理函数
→ Channel 回复
```

`UserContext(id, name, groups, roles)` 只携带可信用户信息，字段冻结，组与角色是 frozenset。`Context()` 注入原对象，使用 `user.id` 访问，没有旧字典接口和类型别名。HTTP 正文中的身份字段不能覆盖认证结果。SDK Session/执行 ID 不属于用户上下文；Framework `permissions.User` 保持权限匹配器语义。

API 的 JSON 字符串或对象中字符串 `message` 转为 Message 文本段；其他 JSON 作为 data 段。HTTPEvent 保留规范化 payload、路径和查询信息。处理函数使用 Event、EventMessage、EventPlainText、CommandArg、Context 和 Depends，不引入 HTTPBody 参数体系。

路由装饰器的 `body=Model` 只负责前置 JSON 校验。返回注解用于响应契约。业务插件决定返回字段，也可用 Channel.send / Matcher.finish；同一事件只允许一个最终响应，SSE 是这个响应中的流。

HTTP 路由默认只派发对应 Matcher。消息路由可显式选择同插件命令 Matcher，命令和普通消息沿同一次分发处理，无关插件路由不执行。机器人事件选择连接所属插件的 Matcher；Framework 全局事件钩子仍可用于宿主级策略和日志。

## 请求与流生命周期

普通响应完成编码后关闭请求依赖。流式响应把依赖栈与 ResponseStream 一起移交给 Driver；正常耗尽、提前关闭、异常、取消都结束同一作用域。streaming 模块负责源流和依赖各自的关闭期限；源流关闭失败或超时仍必须尝试关闭依赖，保留原异常。

404 表示路由不存在，503 表示通道/插件未就绪，403 表示权限/规则拒绝且没有响应，504 表示取得响应前超时。流开始后的持续时间不等于前置 request timeout。断连取消请求，默认不把工作转成后台任务，也不自动续传。

机器人内存队列用于快速接收与异步消费。队列满/关闭返回失败并撤销该事件去重占位，允许供应商随后重试。队列不是持久任务系统，进程退出可能丢失已确认事件；不会自动重跑可能已产生副作用的模型调用。

停机先停止 HTTP 接单、等待在途请求，然后 channels 的 on_stopping 停止所有入口并排空机器人工作，最后反向关闭消费者和传输资源。模型/MCP/沙箱资源不能在使用者还未排空时提前释放。

## execution 的插件接口

```python
import logging
from typing import Annotated

from opsmesh.framework.params import Context
from opsmesh.framework.plugins import require

channels = require("opsmesh_channels")
execution = require("opsmesh_execution")
logger = logging.getLogger(__name__)


@execution.before_run
async def check_run(
    user: Annotated[channels.UserContext, Context()],
) -> None:
    if "suspended" in user.roles:
        raise PermissionError("The user cannot start a model run")


@execution.on_run_end
async def observe_result(result, error, trace_id) -> None:
    if result is not None:
        logger.info("Trace %s used %s", trace_id, result.context_wrapper.usage)
```

`before_run` 可接收 agent、input、run_config、trace_id，抛错会阻断执行。`on_run_end` 接收原生 result、原异常 error 和 trace_id；成功/失败/取消后的观测异常只记录，不覆盖已有结果。日志使用普通 logging，由宿主配置输出。

SDK 对应装饰器 `on_agent_start/end`、`on_llm_start/end`、`on_tool_start/end`、`on_handoff` 保留原生参数和异常语义。它们不能与不会阻断的结束通知混为一谈。调用者原生 RunHooks、AgentHooks、Guardrails 不被替换或吞错。

回调使用 Framework Dependent 解析 `Context()` 和 `Depends()`。当前 Matcher 的插件与其 require 依赖闭包参与回调，重复依赖不重复执行；无关插件不参与。后台调用传 `plugin_id="stable_plugin_id"`，与当前 Matcher 冲突时拒绝。没有归属时不全局广播。

调用者传 `trace_context=execution.TraceContext(user_id=user.id, session_id=session_id, tags=(...))` 关联 Langfuse。run 返回原生结果，stream 在异步上下文内暴露原生 RunResultStreaming。用量直接读取 SDK Usage；execution 不写 PostgreSQL、维护统计报表或会话列表。

MCP、Session、Agent 和沙箱的构造及长期资源归属由功能插件承担。SDK 工具函数不自动识别 Framework Depends；需要时显式使用 Dependent。普通 Agent 与 SandboxAgent 共用 execution 接口，不新增沙箱执行器。
