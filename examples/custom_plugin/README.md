# 可运行的代码插件

在仓库根目录执行：

```bash
uv run python examples/custom_plugin/demo.py
```

该命令初始化临时 SQLite 数据库、加载插件、派发 `/hello Ada`、释放 yield 依赖并构建一个 AgentSpecification。它不会发布 Agent、发送机器人消息或访问模型。退出时清理临时数据。

`opsmesh_example_plugin/__init__.py` 展示实际公开 API：

- `OpsMeshPluginMetadata`、`on_command`、嵌套 `Depends` 和 yield 资源清理。
- `DispatchContext` 用于事件作用域；`get_run_context()` 用于 Agent 工具和 hook 运行作用域，两者不可互相替代。
- `get_agent_factories().register(...)` 注册带 Pydantic 配置校验的 Python 工厂。
- `get_code_tools().register(...)` 注册函数实现；HTTP `/tools` 只能为已注册 `code_ref` 创建声明式工具版本。
- `get_runtime_extensions().register_hook/register_guardrail` 注册执行扩展；HTTP Agent 配置只能引用这些名称。

部署时把此插件打包安装，或者让 `examples/custom_plugin` 位于 Python 导入路径，设置 `OPSMESH_PLUGINS=opsmesh_example_plugin`。网关、后台 Worker 和隔离 Run Worker 必须使用相同版本插件包与配置，才能重新构造同名声明。Docker Run Worker 的镜像也必须包含插件。

导入期仅注册 Python 声明。数据库变更、网络连接与模型调用放在受管理的启动或运行阶段。插件 `require("other_plugin")` 返回普通模块并建立插件依赖；这里没有额外 `setup`、导出注册表或运行时下载代码机制。

禁用插件会阻止新的 matcher、工厂和代码工具调用；正在执行的用户代码不会被任意强杀。失败导入回滚框架追踪的注册，不承诺撤销任意 Python 副作用。已冻结的代码声明更新、失败插件重试及可靠升级需要重启进程；不承诺热卸载。

普通 Web 请求可以创建已部署能力的声明式 Agent。代码插件还可以写异步处理器、依赖提供函数、工厂、工具、护栏和 hook，因此扩展能力高于 HTTP 配置入口。发布工厂返回的定义之前，仍需真实模型配置及平台授权。
