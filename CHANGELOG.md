# 变更日志（Changelog）

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 规范，版本号遵循 [语义化版本（Semantic Versioning）](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added
- 新增端到端可运行示例工程 `examples/webhook_ops/`（包含自定义 FastAPI Webhook `Adapter` / `Gateway` / `Event` / `Message`、多轮 `/deploy` 命令插件与 `notice` 告警监听插件）。
- 扩充全模块单元测试套件至 8 个文件（35 项测试），覆盖配置加载、消息与模板、规则与权限、依赖注入、事件分发与多轮会话、驱动与示例 ASGI 端到端、插件生命周期与 `single_session` 并发互斥。
- 新增完整 GitHub Actions 流水线（`ci.yml`、`security.yml`、`release.yml`）、`dependabot.yml` 与 GitHub Issue / PR 模板。
- 新增完整开源文档体系：`docs/architecture.md`、`docs/adapter-guide.md`、`docs/plugin-guide.md`、`docs/configuration.md`、`CONTRIBUTING.md`、`SECURITY.md`。
- 新增顶层便捷注册函数 `opsmesh.register_adapter(adapter, **kwargs) -> A`（同时使 `Driver.register_adapter` 返回注册后的适配器实例）。
- 为 `Adapter` 补齐客户端传输方法 `stream_request()` 与 `get_session()`。

### Changed
- **通用框架核心模型统一**（基于 NoneBot2 v2.5.0 演进）：
  - `Bot` 重构为 `Gateway`（`gateway.id`、`gateway.connect()`、`gateway.disconnect()`）；
  - `call_api` / `_call_api` 重构为 `invoke` / `_invoke`，配套 `@Gateway.before_invoke` / `@Gateway.after_invoke`、`@Gateway.on_connect` / `@Gateway.on_disconnect`、`MockInvokeResult`、`ActionNotAvailable` 与 `Config.invoke_timeout`；
  - `Event` 内置 UTC `time` 字段与 `get_time()` 方法，保留 `is_tome()`（对应规则 `to_me()` 与依赖 `EventToMe()`）；
  - 多轮会话续接 `Matcher.update_type` 默认沿用 `event.get_type()`，原生支持非 `"message"` 类型事件续接；
  - `on_command` 默认 `block=True`，与 `on_message`、`on_shell_command` 及文本模式响应器保持一致。
- **全面原生化 Python 3.11+ 与 Pydantic v2**：
  - 彻底移除 512 行的 Pydantic v1/v2 兼容层 `compat.py`，引入精简纯 Pydantic v2 字段封装 `opsmesh.internal.field`；
  - 全库迁移至 Python 3.11 原生 `except*` 异常组语法，移除 `exceptiongroup` 依赖；
  - `requirements.txt` 中 `pydantic` 依赖规范为库级范围 `pydantic>=2.0.0,<3.0.0`。
- **安全与权限收紧**：
  - `SuperUser` / `SUPERUSER` 严格要求适配器作用域格式 `"{adapter}:{user_id}"`，移除旧版无前缀全局匹配回退；
  - 在 `ruff.toml` 中全局启用 Bandit `S` 安全规则集，将生产代码路径中的 `assert` 替换为显式 `TypeError` / `ValueError` / `RuntimeError`。
- 客户端驱动（`aiohttp`、`httpx`、`websockets`）与 `Request` / `Timeout` 模型同步补齐 `Timeout.ping`、`Request.ping_interval` 与 `Request.auto_redirects`。

### Fixed
- 修复 `FastAPIDriver._lifespan_manager` 与 `Lifespan.__aenter__` 在 `@driver.on_startup` / `@driver.on_ready` 抛错时未调用 `shutdown()` 导致 `anyio.TaskGroup` 泄漏的问题。
- 修复 `Driver._adapters` 误定义为 `ClassVar` 导致多 `Driver` 实例共享污染适配器注册表的问题。
- 修复 `Driver._unregister_gateway` 中 `AsyncExitStack` 与 `TaskGroup` 上下文顺序颠倒导致 `@Gateway.on_disconnect` 生成器依赖提前析构的问题。
- 修复 `MatcherGroup.on_metaevent` / `on_notice` / `on_request` 误过滤 `permission` 参数导致组级与方法级权限静默失效的问题，以及 `MatcherGroup.on_type` 仅允许单元素元组类型注解的缺陷。
- 修复 `httpx.Mixin.get_session` 默认 `timeout=None` 导致 `DEFAULT_TIMEOUT` 死代码与默认无超时的问题。
- 修复 `Matcher.new(..., expire_time=timedelta(...))` 使用本地无时区时间而非 UTC 时间的问题。
- 修复内置 `single_session` 插件解锁逻辑缺少 `try...finally` 异常安全保护的问题。
- 修复 `pyproject.toml` 缺失 `namespaces = true` 导致 wheel 打包遗漏 `opsmesh.plugins`，并在 `opsmesh.adapters` 中增加 `pkgutil.extend_path` 支持第三方命名空间适配器。

### Removed
- 彻底清理旧业务工程遗留耦合与冗余运行时特性：移除 `unload_plugin`、`EventAdmission`、`Plugin.enabled`、`Plugin._dependencies` / `_dependents`、`PluginMetadata.{adapter, dependency_config, protected, config_file}`、`Gateway.owner_plugin_id` 与 `plugin/config.py`。
- 移除全部 NoneBot2 历史兼容垫片与废弃 API：`compat.py`、`is_tome` / `to_me` / `EventToMe`、无类型注解按参数名盲猜的 DI 兼容分支、`Matcher.new(plugin=..., module=...)`、`get_matcher_plugin` / `get_matcher_module`、`typing.overrides`、`Driver.on_connect` / `on_disconnect` 重复转发、`Config.nickname` 与 `load_from_toml` 商店字典旧格式分支。

---

## [0.6.0] - 2026-11-27

### Added
- 初始立项：基于 NoneBot2 v2.5.0（commit `c0f9a49`）提取并构建独立 `opsmesh` 异步事件框架仓库。
