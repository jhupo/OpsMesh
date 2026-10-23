# 插件与 MCP 生命周期

## 所有者与入口

`src/opsmesh/plugins` 是通用包宿主；`agents/plugins`、`gateway/plugins.py` 是资源适配器。`sdk/python/src/opsmesh_sdk` 提供 plugin.yaml、setup(ctx)、注册声明与 ZIP 打包。bootstrap 注册适配器并提供 API/Worker/Gateway 视图。通道实现 SDK 的 ChannelAdapter；工具和 MCP 授权边界见[网关与调用者身份](gateway-and-identity.md)。

## 不变量

- 插件包的提取、校验和原子安装只有一套；各资源适配器负责解析资源声明与语义校验，不分别实现安装器。
- 包默认位于 `StorageLayout.plugin_packages` 的 `<id>/<version>`；插件私有数据位于 `StorageLayout.plugin_data/<id>`。所有 API/Worker/Gateway 共享包、配置与数据根，热更新不是跨主机分发协议。
- setup 只同步注册资源，可在安装校验与不同宿主中重复调用；不连接外部服务、启动任务或修改持久业务数据。
- MCP 是插件可贡献的资源类型，不需要再建一套同义 connectors 协议。工厂返回原生 SDK MCPServer，由绑定配置选择，再由运行时连接后提供给 Agent.mcp_servers。
- MCP 传输、工具发现和资源协议由 SDK 管理；OpsMesh 管理发现、绑定、配置、权限和运行所有权。远程服务、stdio 可执行文件及依赖不内置到通用宿主。
- stdio/HTTP 连接的打开与清理由拥有它的异步作用域管理，遵守 AnyIO 任务亲和性；关闭连接不能等同于删除持久逻辑实例。
- Temporal 不能在 Workflow 中打开真实 MCP 连接：Activity 解析工厂，官方 temporal_mcp_server 提供桥接。
- 启用/安装发布新代际，API/Worker/Gateway 轮询确认。未收到所有活宿主确认不能声称全局生效；失败保留旧代际，存量对象不能被强制替换。
- 包版本不可变。卸载注册不删除用户数据或仍被引用的包文件。

## 改动检查点

核对 ZIP 路径/体积/符号链接校验、资源声明一致性、未启用包的校验、同 ID 版本冲突、宿主代际确认、旧对象引用和连接异常清理。详见 `src/opsmesh/plugins/README.md`、`sdk/python/README.md`。
