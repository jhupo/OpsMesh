# Backend Architecture Gates

状态：2026-09-18。架构规则由 pyproject.toml 的 Import Linter 配置执行，
不使用断言目录名、源码字符串或类形状的 pytest 作为架构门禁。

## 当前规则

| 合同 | 静态约束 |
| --- | --- |
| HTTP transport | 只有 API 和应用入口可以直接导入 routes/middleware |
| Shared infrastructure | core 不能直接或间接依赖 API |
| Pagination | 共享 PageParams 不依赖 HTTP/SQLAlchemy/API |
| Production code | 不依赖 pytest |
| Docker SDK | 只能由 Docker backend adapter 直接调用 |
| S3 SDK | 只能由 storage adapter 直接调用 |
| Agent runtime contract | 不依赖厂商 SDK 或 API，包含类型检查导入 |
| Domain/application | domain/runtime 不导入 api.services |

HTTP 参数校验属于 api.pagination，共享分页输入属于 core.pagination，数据库查询由领域使用
core.db.pagination。SDK 包的唯一源码在独立仓库，平台不再通过 mypy_path 指向本地副本。

## 校验

```sh
uv sync --frozen --all-groups
uv run ruff check backend/app scripts
uv run mypy
uv run lint-imports --no-cache
git diff --check
```

CI/master 和 release tag 使用同一份 Import Linter 规则。
Import Linter 是开发依赖，不进入生产镜像。规则位于配置中，无兼容模块或忽略导入豁免。
运行时授权、恢复和跨租户隔离不能靠静态导入图证明，继续由产品流程测试覆盖。

## 测试清理范围

本次删除 10 个非产品流程测试文件：adapter_registries、agent_runtime_contracts、
architecture、core_domain_models、database_models、pagination、runtime_event_messages、
self_hosted_policy_values、shared_redaction、worker_dependencies（文件名前缀均为 test_）。

它们直接检查注册表类型、dataclass/serializer/helper、表结构、构造器或目录布局，
不构成从接受请求到执行和输出的产品流程。保留 user_orchestration、marketplace_api、
agent_runtime_critical_e2e、agent_runtime_recovery_e2e、workspace_projects、workspace_export_api、
tenant_isolation_matrix、worker_runner 等流程及其拒绝/恢复分支。
此次没有宣称剩余每个历史测试都是完整端到端测试；包含混合场景的文件不整文件误删。

后续新增测试只扩展已有产品流程；组织重构默认只跑静态、导入和构建检查。
全量 pytest 仍仅在 release-tag 门禁执行。过期测试与文档可从 Git 历史恢复。
