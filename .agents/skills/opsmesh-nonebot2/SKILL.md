---
name: opsmesh-nonebot2
description: 查阅与 OpsMesh 基线一致的 NoneBot2 官方文档，并核对本地实现。用于框架的 Driver、Adapter、事件分发、Matcher、规则、依赖注入和插件加载的设计、评审与修改。
---

# NoneBot2 框架参考

[官方文档概览](references/website/docs/README.md) 来自 `nonebot/nonebot2` 的 `website/docs`，固定为 v2.5.0，提交 `c0f9a494bcd308c2ec530ba60c167f9145d21aec`。文档保留上游原文和 `nonebot` 包名，[许可证](references/LICENSE) 随附。升级框架时按根 `pyproject.toml` 的 `Upstream` 提交更新快照，不混用 master 或在线站点的其他版本。

按问题选择章节，不必通读快照。下表的源码位置相对于仓库根目录的 `src/opsmesh/`。

| 问题 | 官方文档 | 本地源码入口 |
|---|---|---|
| HTTP 入站、出站、驱动与适配器 | [Driver](references/website/docs/advanced/driver.md)、[自定义路由](references/website/docs/advanced/routing.md)、[编写适配器](references/website/docs/developer/adapter-writing.md) | `internal/driver/model.py`、`drivers/fastapi.py`、`drivers/httpx.py`、`internal/adapter/adapter.py` |
| 事件如何匹配、执行和回复 | [事件数据](references/website/docs/tutorial/event-data.mdx)、[事件响应器](references/website/docs/tutorial/matcher.md)、[处理函数](references/website/docs/tutorial/handler.mdx) | `plugin/on.py`、`message.py`、`internal/matcher/matcher.py`、`internal/adapter/gateway.py` |
| 依赖注入、规则与权限 | [依赖注入](references/website/docs/advanced/dependency.mdx)、[规则](references/website/docs/appendices/rule.md)、[权限](references/website/docs/appendices/permission.mdx) | `dependencies/`、`internal/params.py`、`rule.py`、`permission.py` |
| 插件注册、依赖与运行钩子 | [创建插件](references/website/docs/tutorial/create-plugin.md)、[跨插件访问](references/website/docs/advanced/requiring.md)、[运行钩子](references/website/docs/advanced/runtime-hook.md) | `plugin/load.py`、`plugin/manager.py`、`plugin/model.py`、`message.py` |

涉及设计取舍时，沿一个真实调用从入口追到返回点，记录现有类型、谁调用谁、结果或异常由谁接收。例如 HTTP 入站先追 `HTTPServerSetup.handle_func(Request) → Response`，再单独核对 `handle_event` 与 `Matcher.send`；不能因两条路径都存在，就假定处理函数的返回值会成为 HTTP 响应。

区分依据：上游文档说明 NoneBot2 的用法；当前 `src/opsmesh` 决定 OpsMesh 实际行为；[框架架构](../../../docs/framework.md)与[网关流程](../../../docs/gateway.md)整理现行结构。Gateway 命名改造已完成，不再引用旧重构方案。提议新增接口时，指出现有接口无法满足的具体步骤。快照中 `api/` 只有上游提交的占位和分类元数据；需要精确 API 签名时看源码。
