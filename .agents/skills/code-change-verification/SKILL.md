---
name: code-change-verification
description: Run the focused verification commands required by an OpsMesh change.
---

# OpsMesh 变更校验

修改完成后先确认受影响的服务端、插件 SDK、部署 CLI、脚本和测试路径，再运行最小充分的静态检查。只有用户明确要求时才运行测试、验收脚本或测试环境联调；不得以默认技能流程代替用户授权。这个技能不决定功能是否完成，也不替代用户要求。

## 常用检查

在仓库根目录执行：

```text
uv sync --frozen --all-groups
uv run ruff format --check <受影响的 Python 路径>
uv run ruff check <受影响路径>
uv run mypy
uv run lint-imports --no-cache
uv run python scripts/audit_app_layout.py --architecture-check
```

`uv sync` 只在依赖文件变化、全新环境或解析失败时执行；文档和技能目录变更不需要运行后端套件。HTTP API 合同由当前服务路由生成 `/openapi.json`，不维护独立业务客户端或代码生成器。

## 范围规则

- 按实际改动选择上述静态检查，覆盖格式、Ruff、类型和依赖边界。
- 改动 `sdk/python` 时，在其目录独立执行 Ruff、mypy、import-linter。
- 用户明确要求测试时，按受影响功能运行 `uv run pytest tests/<实际路径>`；SDK 测试在 `sdk/python` 运行，涉及打包时增加干净环境安装/导入验证。
- 用户明确要求部署或发布验收时，按改动选择 standalone、runtime image、managed delivery 或 release 检查；需要声明的 Docker/外部服务但环境缺失时，标为 `unverified`。
- 用户明确要求 Temporal Workflow、Activity、Sandbox 或 MCP 验收时，使用 `temporal-python-sdk` 的真实重放、取消、恢复和资源释放场景；静态检查不能代替真实基础设施证据。

## 报告

记录命令、退出码和范围。区分 `implemented`、`verified`、`unverified` 与 `blocked`；未运行的真实 PostgreSQL、Temporal、Docker、模型或外部 MCP 验收不得写成通过。
