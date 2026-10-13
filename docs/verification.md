# 后端验证与交付

代码变更、运行验收与服务器部署分别记录。`ruff`、strict mypy、架构检查和 API 导出检查只证明静态合同；数据库、并发和执行恢复使用真实 PostgreSQL 与 Temporal。

## Pull Request

`backend-ci.yml` 始终执行静态合同和关键持久执行验证。`scripts/ci_tests.py --base <commit>` 根据变更所属产品模块选择测试，包括同一调用链上的工具、原生 Session、取消、审批、MCP、插件和归档；不会将所有测试默认加入每个 PR。缺失的测试路径直接失败。测试组织重命名不等于必须执行发布全套。

产品测试 Job 提供 pgvector PostgreSQL、Redis，并设置 `OPSMESH_TEST_POSTGRES_URL` 与 `OPSMESH_TEST_TEMPORAL=1`；Temporal 测试启动官方本地开发服务器。选中原生 Docker 沙箱流程时，CI 先构建锁定依赖的 compute 镜像，再使用 `OPSMESH_TEST_NATIVE_SANDBOX=1` 和 `OPSMESH_TEST_COMPUTE_IMAGE` 执行实际 SDK 会话、MCP 和清理验证。没有基础设施而跳过的结果不算验收通过。

## 发布与安装

Tag 发布的 `release-prepare.yml` 执行完整测试套件，启用真实 PostgreSQL、Temporal 与 Docker 沙箱。外部模型推理仍使用独立 opt-in，避免把真实业务 API 或收费模型请求加入常规 CI。发布前须读取保留的测试诊断，逐项列出外部验证边界。

Compose 中的 schema 和 namespace 是一次性初始化任务，依赖使用 `service_completed_successfully`。交付入口启动这些任务后，通过 Compose 的全部容器查询定位 namespace，再使用官方 Docker SDK `container.wait(condition="not-running")` 检查退出码；初始化已退出也能验证。只有常驻服务使用 `up --wait` 健康检查。

独立服务器包、生产镜像和托管更新分别由 delivery workflows 验证。动态配置以只读文件装入 Temporal；生产数据卷不因验收或升级被替换。单机升级不等于分布式整体升级，原生队列出现非托管 Worker 时必须拒绝自动升级。

## Linux 临时验证

`scripts/remote-backend-validation.sh [pytest 参数...]` 创建独立 PostgreSQL、Redis 与测试容器，使用固定 uv 和 `uv sync --frozen --all-groups`，启用真实 Temporal 流程，并只清理本次命名的容器与网络。参数以数组传递，不执行环境变量中的 shell 字符串。此入口不挂载宿主 Docker socket，原生沙箱执行由单独的 Linux Docker 验收负责。
