# Contributing

先阅读 [设计入口](docs/README.md)、[开发约定](docs/development.md)、[源码边界](docs/design/source-layout.md) 和 [实现路线](docs/roadmap.md)。当前 `0.3.x` 已包含可运行的框架与平台服务；实现范围以代码、覆盖表和实际验证记录为准。新增功能不要仅通过目录或接口声明标记为完成。

## Development

```sh
uv sync --locked --dev --extra openai
uv run pre-commit install
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run python scripts/check_repository.py
uv build
uv run python scripts/check_sdist.py
```

SDK表面检查：`uv run --extra openai python scripts/check_sdk_surface.py`，无需API key且不调用模型。平台核心的部署安装仍可省略 `openai` extra；完整源码的类型检查和SDK测试需要安装它。实际模型、Docker、Kubernetes和其他外部服务测试必须显式启用。

## Changes

每次修改围绕一个完整行为。提交公开合同、实现、必要测试和文档；涉及跨模块边界或恢复格式时先补ADR。不要用返回成功的空函数模拟未实现能力。跨模块只使用api/events，原生SDK类型限制在后端适配包。更改依赖后执行`uv lock`并提交锁文件。

PR说明问题、最终行为、验证及兼容性影响。发布版本更新 `_version.py`、锁文件中的项目版本、CHANGELOG及相关部署示例；GitHub Release附上对应提交构建的发行包，不复用其他提交的测试结果。暂未要求DCO或CLA；不得提交无权授权的代码。导入上游代码时遵守其许可证并更新第三方声明。
