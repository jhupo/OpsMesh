# OpsMesh

OpsMesh 是基于 NoneBot2 v2.5.0 的 Python 异步事件框架。[Gateway](src/opsmesh/internal/adapter/gateway.py) 表示适配器管理的事件来源实例，取代了上游的 `Bot`。框架保留上游的 Driver、事件分发和插件加载机制；业务插件使用 `on_message`、`on_command`、`on_request` 等入口声明处理器。

## 当前代码与设计方案

- **文档入口**：[docs 索引](docs/README.md)区分现状、方案和待迁移代码；[框架架构](docs/framework.md)说明文件树、调用链及设计思想，[HTTP 与 OpenAPI](docs/openapi.md)说明已有传输能力和接口契约要求。
- **了解现有接口**：从[事件处理器](src/opsmesh/plugin/on.py)、[事件分发](src/opsmesh/message.py)和[插件加载](src/opsmesh/plugin/load.py)入手。[本地 NoneBot2 文档索引](.agents/skills/opsmesh-nonebot2/SKILL.md)按主题列出与本仓库基线一致的官方说明。
- **理解网关流程**：[Gateway 文档](docs/gateway.md)说明已完成的框架命名改造，以及登记、事件/命令分发、注入、回复和注销。通用 HTTP 业务适配器仍未实现，`on_request()` 不会自动注册 HTTP 路由。
- **协议适配器**：独立的 [QQ 插件](packages/qq/README.md) 和 [钉钉插件](packages/dingtalk/README.md) 直接接入框架。旧的 `packages/channels` 已删除；依赖旧通道抽象的示例和测试待按新适配器接口迁移。
- **业务插件示例**：[示例消费者插件](examples/plugin-consumer/README.md)展示标准 wheel、插件自带 `settings.json`、`config_file` 和 QQ/钉钉命名依赖配置；对应测试见 `tests/unit/test_example_plugin.py`。

## 安装与发布

- 在 GitHub Releases 下载框架及独立插件 wheel。Linux 服务使用同版本的 `opsmesh`、`opsmesh-cli` wheel 和 `opsmesh-deploy-<tag>.tar.gz`，安装步骤见[服务指南](docs/linux-plugin-service.md)。
- Cron、Execution 及旧 chat、groups、sandbox 示例尚未迁移到当前接口，不在本版发包范围内。

仅本地开发框架时，在 Python 3.11 或更高版本的虚拟环境中从仓库根目录运行：

```bash
python -m pip install -e .
```

只安装框架不需要业务插件或模型凭据。可选 Driver 依赖见[包定义](pyproject.toml)。

## 项目资料

[贡献指南](CONTRIBUTING.md) · [安全策略](SECURITY.md) · [历史变更](CHANGELOG.md) · [MIT 许可证](LICENSE) · [上游许可](licenses/)
