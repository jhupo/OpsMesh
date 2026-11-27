# 插件开发与加载指南

插件是 OpsMesh 组织业务能力的基本单元。一个插件可以是一个独立的 `.py` 文件、一个包含 `__init__.py` 的 Python 包目录，或一个通过 `pip` 安装的第三方包。

---

## 一、插件加载铁律与 6 种加载方式

> **核心铁律**：**不要在加载插件前直接使用 Python 的 `import` 语句导入插件模块！**
> OpsMesh 通过 `sys.meta_path` 上的 `PluginFinder` / `PluginLoader` 接管受管插件的导入流程，在执行期自动绑定 `__plugin__`、`__plugin_meta__` 与 `MatcherSource`。如果提前裸 `import`，模块已在 `sys.modules` 缓存，再调用 `load_*` 会抛出 `Module ... is not loaded as a plugin!`。

所有加载接口均必须在 `opsmesh.init()` **之后**、`opsmesh.run()` **之前**调用：

| 方式 | 调用示例 | 适用场景 |
| :--- | :--- | :--- |
| **TOML 声明式加载**（推荐） | `opsmesh.load_from_toml("pyproject.toml")` | 生产工程在 `[tool.opsmesh]` 集中管理 `plugins` 与 `plugin_dirs` |
| **JSON 声明式加载** | `opsmesh.load_from_json("plugins.json")` | 与外部配置中心或部署脚本集成 |
| **扫描本地目录** | `opsmesh.load_plugins("plugins", "extra")` | 批量导入目录下全部非 `_` 开头的文件/包插件 |
| **加载单个插件** | `opsmesh.load_plugin("pkg.mod")`<br>`opsmesh.load_plugin(Path("plugins/a.py"))` | 按模块名或路径加载单个本地/第三方插件 |
| **加载内置插件** | `opsmesh.load_builtin_plugin("single_session")` | 按需加载 `opsmesh.plugins.*` 下的框架内置插件 |
| **插件间依赖声明** | `audit = opsmesh.require("plugins.audit")` | 插件 A 依赖插件 B 的导出对象时，在 A 顶部显式声明（未加载时自动加载） |

### 嵌套子插件

在父插件包 `plugins/ops/__init__.py` 中调用 `opsmesh.load_plugins(str(Path(__file__).parent / "sub"))` 时，子插件自动归入 `parent.sub_plugins`，其索引 ID 为 `"ops:<sub_name>"`。

---

## 二、插件元数据（`PluginMetadata`）

推荐在每个插件入口声明 `__plugin_meta__`，供文档生成、依赖检查与适配器兼容性推导（`inherit_supported_adapters`）使用：

```python
from pydantic import BaseModel
from opsmesh.plugin import PluginMetadata


class MyPluginConfig(BaseModel):
    api_endpoint: str = "https://ops.internal"
    retry_times: int = 3


__plugin_meta__ = PluginMetadata(
    name="服务巡检",
    description="定时与交互式触发服务健康巡检",
    usage="/health [service]",
    type="application",                 # 推荐 "application" 或 "library"
    homepage="https://example.com/ops",
    config=MyPluginConfig,
    supported_adapters={"~webhook"},    # None 表示支持全部适配器；"~" 代表 "opsmesh.adapters."
    extra={"owner": "sre-team"},
)
```

---

## 三、事件响应器（`Matcher`）与默认 `block` 策略

| 快捷函数 | 监听事件类型 | 默认 `block` | 说明 |
| :--- | :--- | :---: | :--- |
| `on_message` | `"message"` | **`True`** | 通用消息响应器 |
| `on_command` | `"message"` | **`True`** | 基于 `TrieRule` 的前缀命令匹配，支持 `aliases` 与 `force_whitespace` |
| `on_shell_command` | `"message"` | **`True`** | 支持 `shlex` 切词与 `opsmesh.rule.ArgumentParser` 参数解析 |
| `on_startswith` / `on_endswith` | `"message"` | **`True`** | 纯文本前缀 / 后缀匹配（支持 `ignorecase`） |
| `on_fullmatch` / `on_keyword` | `"message"` | **`True`** | 纯文本完全匹配 / 关键词包含匹配 |
| `on_regex` | `"message"` | **`True`** | 基于 `re.search` 匹配 `str(message)`，可配合 `RegexStr`/`RegexGroup`/`RegexDict` |
| `on` | 自定义（默认 `""` 任意） | **`False`** | 基础事件响应器 |
| `on_notice` / `on_request` / `on_metaevent` | 对应事件类型 | **`False`** | 事件观察者，默认不阻断低优先级监听器 |
| `on_type` | `is_type(*types)` | **`False`** | 按 Python 事件类或类元组 `isinstance` 匹配 |

### 分组管理（`MatcherGroup` & `CommandGroup`）

```python
from opsmesh.permission import SUPERUSER
from opsmesh.plugin import CommandGroup, MatcherGroup

# 共享权限与优先级
ops_group = MatcherGroup(priority=5, permission=SUPERUSER)
alert_watcher = ops_group.on_notice()
custom_webhook = ops_group.on(type="webhook")

# 共享命令前缀（生成 /svc.start 与 /svc.stop）
svc = CommandGroup("svc", permission=SUPERUSER)
svc_start = svc.command("start")
svc_stop = svc.command("stop")
```

---

## 四、规则（`Rule`）与权限（`Permission`）

- **`Rule` 仅支持 `&`（逻辑与）组合**：所有 checker 全部返回 `True` 才触发响应器。常用内置规则：`startswith`、`endswith`、`fullmatch`、`keyword`、`command`、`shell_command`、`regex`、`targeted`、`is_type`。
- **`Permission` 仅支持 `|`（逻辑或）组合**：任一 checker 返回 `True` 即通过检查。常用内置权限：`MESSAGE`、`NOTICE`、`REQUEST`、`METAEVENT`、`USER(*session_ids, perm=...)`、`SUPERUSER`。

> **注意**：`SUPERUSER` 严格匹配 `"{adapter}:{user_id}"`（例如 `SUPERUSERS=["webhook:ops_root"]`），不会无前缀跨适配器放行。

---

## 五、编译期依赖注入（`Depends`）

处理器、规则、权限与预/后处理函数均支持基于类型注解的依赖注入（**必须显式标注类型，不支持无注解参数名盲猜**）：

```python
from collections.abc import AsyncGenerator
from typing import Annotated

from opsmesh import on_shell_command
from opsmesh.adapters import Event, Gateway
from opsmesh.exception import ParserExit
from opsmesh.matcher import Matcher
from opsmesh.params import Depends, ShellCommandArgs
from opsmesh.rule import ArgumentParser
from opsmesh.typing import T_State

parser = ArgumentParser("scale")
parser.add_argument("service")
parser.add_argument("-r", "--replicas", type=int, default=2)

scale_cmd = on_shell_command("scale", parser=parser)


async def db_session(event: Event) -> AsyncGenerator[str, None]:
    conn = f"conn:{event.get_session_id()}"
    try:
        yield conn
    finally:
        # 无论处理器成功还是抛错，事件结束时必然执行清理
        pass


@scale_cmd.handle()
async def handle_scale(
    gateway: Gateway,
    state: T_State,
    matcher: Matcher,
    args=ShellCommandArgs(),
    conn: Annotated[str, Depends(db_session)],
) -> None:
    if isinstance(args, ParserExit):
        await matcher.finish(args.message or "参数错误")
    res = await gateway.invoke(
        "scale_service",
        service=args.service,
        replicas=args.replicas,
        conn=conn,
    )
    await matcher.finish(f"扩容完成：{res}")
```

### 常用快捷参数依赖（`opsmesh.params`）

- 事件信息：`EventType()`、`EventMessage()`、`EventPlainText()`、`EventTargeted()`
- 命令参数：`Command()`、`RawCommand()`、`CommandArg()`、`CommandStart()`、`CommandWhitespace()`
- Shell / 正则：`ShellCommandArgs()`、`ShellCommandArgv()`、`RegexMatched()`、`RegexStr(...)`、`RegexGroup()`、`RegexDict()`
- 多轮交互：`Arg(key)`、`ArgStr(key)`、`ArgPlainText(key)`、`Received(id)`、`LastReceived()`

---

## 六、多轮会话控制流

| 原语 | 行为 |
| :--- | :--- |
| `@matcher.got(key, prompt=...)` | 若 `key` 未设置，发送 `prompt` 并挂起等待下一条事件填入 `key`，然后执行当前处理器 |
| `@matcher.receive(id="")` | 挂起等待下一条事件并存入 `Received(id)` / `LastReceived()` |
| `await matcher.pause(prompt=...)` | 暂停当前响应器，收到下一条事件后继续执行**下一个**处理器 |
| `await matcher.reject(prompt=...)` | 拒绝当前输入，收到下一条事件后**重新执行当前**处理器 |
| `await matcher.reject_arg(key, prompt=...)` | 指定重新收集某个 `got` 参数并重跑当前处理器 |
| `await matcher.reject_receive(id, prompt=...)` | 指定重新接收某个 `receive` 事件并重跑当前处理器 |
| `await matcher.finish(msg=...)` | 可选发送最后一条消息并立即结束响应器 |
| `matcher.skip()` | 跳过当前处理器（常用于依赖守卫中），继续执行下一个处理器 |

> 非 `"message"` 事件（如 `"webhook"` 回调流程）同样支持 `pause()` / `reject()`：续接响应器默认延续当前 `event.get_type()`，无需手写 `@matcher.type_updater`。

---

## 七、插件配置与全局生命周期钩子

1. **读取插件配置**：在插件模块中调用 `cfg = opsmesh.get_plugin_config(MyPluginConfig)`，自动按 `init(**kwargs) > OS 环境变量 > .env.{env} > .env > 模型默认值` 读取并完成 Pydantic v2 校验（支持 `__` 嵌套字段）。
2. **事件与运行钩子（`opsmesh.message`）**：
   - `@event_preprocessor` / `@event_postprocessor`：全局事件分发前/后执行；前者抛出 `IgnoredException` 可丢弃事件；
   - `@run_preprocessor` / `@run_postprocessor`：单个 `Matcher` 执行前/后执行；后者可声明 `exc: Exception | None` 捕获处理器异常做统一告警/审计。
3. **内置插件 `single_session`**：
   - 不会自动启用，需显式调用 `opsmesh.load_builtin_plugin("single_session")` 或写入 `pyproject.toml`；
   - 启用后，同一 `session_id` 在单次事件执行完成前到达的并发事件将被 `IgnoredException` 忽略，防止长耗时任务被同一会话重复重入（不影响 `got`/`pause` 多轮续接）。
