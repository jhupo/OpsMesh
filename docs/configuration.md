# 配置与运行参考手册

OpsMesh 基于 **Pydantic v2** 与 **python-dotenv** 提供统一、大小写不敏感的配置体系，同时服务于框架核心、驱动扩展与业务插件。

---

## 一、配置来源与优先级

当调用 `opsmesh.init(_env_file=..., **kwargs)` 时，配置按以下优先级从高到低合并（高优先级覆盖低优先级）：

1. **显式关键字参数**：`opsmesh.init(port=9000, log_level="DEBUG")`
2. **操作系统环境变量**：`PORT=9000`
3. **环境专属文件**：`.env.{environment}`（默认 `.env.prod`，由 `ENVIRONMENT` 决定）
4. **基础环境文件**：`.env`
5. **配置类字段默认值**：`Config` 或插件 `BaseModel` 中定义的默认值

> **嵌套配置与复杂类型**：
> - 复杂类型（`set`、`list`、`dict`、`BaseModel`）支持 JSON 字符串赋值，如 `SUPERUSERS=["webhook:ops_root"]`；
> - 支持双下划线 `__` 嵌套展开，如 `DB__HOST=127.0.0.1`、`DB__PORT=5432` 自动组装为 `{"db": {"host": "127.0.0.1", "port": 5432}}`。

---

## 二、核心配置项（`opsmesh.config.Config`）

| 配置项（环境变量名） | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `environment` (`ENVIRONMENT`) | `str` | `"prod"` | 运行环境名（由 `Env` 读取，决定加载 `.env.{environment}`） |
| `driver` (`DRIVER`) | `str` | `"~fastapi"` | 驱动组合表达式，格式 `<driver>[+<mixin>]*`，`~` 为 `opsmesh.drivers.` 缩写 |
| `host` (`HOST`) | `IPvAnyAddress` | `"127.0.0.1"` | ASGI 服务端监听地址 |
| `port` (`PORT`) | `int` (`1..65535`) | `8080` | ASGI 服务端监听端口 |
| `log_level` (`LOG_LEVEL`) | `int \| str` | `"INFO"` | 日志输出等级，支持数值（如 `25`）或大写等级名（`TRACE`/`DEBUG`/`INFO`/`SUCCESS`/`WARNING`/`ERROR`） |
| `invoke_timeout` (`INVOKE_TIMEOUT`) | `float \| None` | `30.0` | 网关动作调用默认超时（秒），`None` 表示不限 |
| `superusers` (`SUPERUSERS`) | `set[str]` | `set()` | 超级用户主体集合，严格使用 `"{adapter}:{user_id}"` 作用域格式 |
| `command_start` (`COMMAND_START`) | `set[str]` | `{"/"}` | 命令前缀集合（如 `["/", "!"]`） |
| `command_sep` (`COMMAND_SEP`) | `set[str]` | `{"."}` | 多级子命令分隔符集合（如 `["."]` 对应 `/svc.restart`） |
| `session_expire_timeout` (`SESSION_EXPIRE_TIMEOUT`) | `timedelta` | `2m` | 多轮会话续接超时（支持 `HH:MM:SS` 或 ISO 8601 如 `PT5M`） |

---

## 三、驱动组合表达式示例

```conf
# 仅 ASGI 服务端（FastAPI）
DRIVER=~fastapi

# 仅 ASGI 服务端（Quart）
DRIVER=~quart

# 服务端 + HTTP 客户端 + WebSocket 客户端
DRIVER=~fastapi+~httpx+~websockets

# 服务端 + AIOHTTP（同时提供 HTTP 与 WebSocket 客户端）
DRIVER=~fastapi+~aiohttp

# 纯无网络事件循环（适合本地脚本、MQ/SDK 推送适配器）
DRIVER=~none

# 自定义第三方驱动与 Mixin（完整模块路径）
DRIVER=my_pkg.driver:CustomDriver+my_pkg.mixin:CustomMixin
```

---

## 四、内置驱动专属配置项

### FastAPI 驱动（`opsmesh.drivers.fastapi.Config`）

| 配置项 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `fastapi_openapi_url` | `str \| None` | `None` | OpenAPI JSON 路径（默认关闭） |
| `fastapi_docs_url` | `str \| None` | `None` | Swagger UI 路径（默认关闭） |
| `fastapi_redoc_url` | `str \| None` | `None` | ReDoc 路径（默认关闭） |
| `fastapi_include_adapter_schema` | `bool` | `True` | 是否在 OpenAPI Schema 中包含适配器注册路由 |
| `fastapi_reload` | `bool` | `False` | 是否启用 Uvicorn 热重载 |
| `fastapi_reload_dirs` | `list[str] \| None` | `None` | 重载监控目录列表 |
| `fastapi_reload_delay` | `float` | `0.25` | 重载防抖延迟（秒） |
| `fastapi_reload_includes` / `excludes` | `list[str] \| None` | `None` | 重载包含/排除 glob 模式 |
| `fastapi_extra` | `dict[str, Any]` | `{}` | 透传给 `FastAPI(...)` 的额外初始化参数 |

### Quart 驱动（`opsmesh.drivers.quart.Config`）

支持 `quart_reload`、`quart_reload_dirs`、`quart_reload_delay`、`quart_reload_includes`、`quart_reload_excludes` 与 `quart_extra`。

---

## 五、标准 `.env.example` 参考

```dotenv
ENVIRONMENT=prod
DRIVER=~fastapi+~httpx
HOST=127.0.0.1
PORT=8080
LOG_LEVEL=INFO

INVOKE_TIMEOUT=30.0
SUPERUSERS=["webhook:ops_root"]
COMMAND_START=["/"]
COMMAND_SEP=["."]
SESSION_EXPIRE_TIMEOUT=PT2M

# 自定义插件配置（自动供 get_plugin_config 读取）
DEPLOY_ENV=prod
```
