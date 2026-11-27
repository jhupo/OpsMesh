# 贡献指南（Contributing to OpsMesh）

感谢你关注并愿意参与 **OpsMesh** 的共建！无论是修复 Bug、完善文档、补充单元测试，还是提议新的框架特性，我们都非常欢迎。

---

## 一、核心设计原则

在提交代码前，请先了解 OpsMesh 坚持的几条核心工程准则：

1. **内核纯粹、零隐式魔法**：框架核心只提供通用异步事件驱动与协议网关机制，不引入特定业务领域的硬编码策略，不增加多余或重复封装的糖衣函数。
2. **原生现代 Python 3.11+ & Pydantic v2**：不添加 Python < 3.11 或 Pydantic v1 兼容垫片；并发异常统一使用原生 `except*`；依赖注入强制显式类型注解。
3. **四层边界清晰**：
   - `opsmesh.drivers` / `internal.driver` 仅关注传输层与进程生命周期；
   - `opsmesh.adapters` / `internal.adapter` 仅关注协议抽象与网关实例；
   - `opsmesh.message` / `matcher` / `rule` / `permission` / `dependencies` 负责调度与执行；
   - `opsmesh.plugin` 负责模块发现、隔离加载与元数据管理。
4. **严格向后可测试性**：每个核心变更都必须配套隔离良好、不依赖外部网络的单元测试。

---

## 二、搭建本地开发环境

需要 **Python 3.11+** 与 `git`：

```bash
git clone https://github.com/jhupo/OpsMesh.git
cd OpsMesh

python -m venv .venv
# Linux / macOS:
source .venv/bin/activate
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pip install -e ".[all]"
```

运行一次基线检查，确认环境就绪：

```bash
ruff format --check src tests examples
ruff check src tests examples
pytest -v
```

---

## 三、开发工作流与分支规范

1. 从主分支（`master`）切出语义化功能/修复分支：
   - 新特性：`feat/<short-topic>`
   - 缺陷修复：`fix/<short-topic>`
   - 文档完善：`docs/<short-topic>`
   - 测试与工程化：`chore/<short-topic>` 或 `test/<short-topic>`
2. 遵循 **[Conventional Commits](https://www.conventionalcommits.org/)** 编写提交信息：
   ```text
   <type>(<scope>): <concise summary>

   示例：
   feat(driver): support custom websocket ping_interval in client drivers
   fix(lifespan): roll back shutdown hooks when startup fails inside async with
   docs(adapter): add streaming HTTP client and MockInvokeResult guide
   test(dependencies): cover async generator cleanup on handler error
   ```
3. 重大架构改动或公共 API 变更，建议先提交 **Feature Request / RFC Issue** 对齐设计边界后再编码。

---

## 四、代码质量与安全门禁

本项目使用 [ruff.toml](ruff.toml) 统一管理格式化、Lint 与静态安全检查（包含 Bandit `S` 规则集）：

```bash
# 自动格式化代码
ruff format src tests examples

# 运行全量 Lint + 安全规则检查（零告警才可通过 CI）
ruff check src tests examples
```

### 安全编码要求

- **禁止在生产代码 `src/opsmesh/` 中使用 `assert` 执行运行时校验**（`python -O` 会剥离 `assert`，且会触发 `S101`）；请抛出明确的 `ValueError`、`TypeError` 或 `RuntimeError`。
- **禁止静默吞咽异常**（`except Exception: pass` 会触发 `S110`）；若确实需要忽略非致命异常，请至少记录 `logger.debug` 或使用有明确说明的 `contextlib.suppress(...)`。
- 修改 `src/opsmesh/plugin/on.py` 时，必须同步更新对应的类型存根 [src/opsmesh/plugin/on.pyi](src/opsmesh/plugin/on.pyi)。

---

## 五、单元测试编写规范

测试位于 `tests/unit/`，使用 `pytest` + `anyio`：

1. **严格状态隔离**：测试中动态注册的 `Matcher`、全局预/后处理器（`_event_preprocessors` 等）、`Gateway` 钩子、`TrieRule.prefix`、`plugin._plugins` / `plugin._managers` 及 `sys.modules` 必须在 `try ... finally` 或 `fixture` 清理阶段完整还原，防止污染其他测试用例。
2. **禁止依赖外网或真实端口**：HTTP/ASGI 集成测试优先使用 `httpx.ASGITransport(app=driver.asgi)` 在进程内直连测试。
3. **跨平台兼容**：避免使用 POSIX 专属路径或信号假设，所有测试需同时通过 CI 中的 `ubuntu-latest` 与 `windows-latest`。

---

## 六、提交 Pull Request 检查清单

提交 PR 前请逐项核对：

- [ ] 已在本地通过 `ruff format --check src tests examples` 与 `ruff check src tests examples`
- [ ] 已在本地通过 `pytest -v` 且为本次变更补充了对应单元测试
- [ ] 如涉及公共 API、配置项或行为变更，已同步更新 `README.md`、`docs/` 与 `CHANGELOG.md`（Unreleased 节）
- [ ] 如涉及目录或包结构调整，已验证 `python -m build` 产出的 `.whl` 包含全部必要文件（含 `py.typed` 与 `.pyi`）
- [ ] PR 描述中清晰说明了变更动机、实现方案与验证结果

---

## 七、安全漏洞上报

如发现安全隐患，请勿通过公开 Issue 或 PR 披露，请查阅 [SECURITY.md](SECURITY.md) 通过 GitHub Security Advisory 私密上报。
