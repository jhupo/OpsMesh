# OpsMesh 文档

## 使用与部署

| 文档 | 内容 |
| --- | --- |
| [快速开始](getting-started.md) | 发行包安装、管理员登录、模型配置和第一条运行 |
| [部署手册](../deploy/server/DEPLOYMENT.md) | Docker / 本机安装、配置、权限和基础设施 |
| [主机操作](../deploy/server/delivery-operations.md) | 升级、备份、恢复与故障处理 |
| [发行格式](../deploy/DISTRIBUTIONS.md) | 原生包、镜像、平台矩阵和校验合同 |
| [观测](../deploy/server/observability.md) | 日志、指标、追踪和告警 |
| [平台管理与自动化](platform-management.md) | 模型配置、重试、事件、调度、评测与预算 |
| [0.1.0 发行说明](releases/0.1.0.md) | 功能范围、升级注意事项和已知限制 |

HTTP API 合同来自运行中服务的 `/openapi.json`，不维护手写或生成后入库的副本。
生产环境默认关闭文档路由；按需在受控网络启用 `OPSMESH_ENABLE_API_DOCS`。

## 开发与扩展

| 文档 | 内容 |
| --- | --- |
| [贡献指南](../CONTRIBUTING.md) | 开发环境、检查与提交约定 |
| [插件 SDK](../sdk/python/README.md) | 插件注册、打包和安装，不提供业务 API 客户端 |
| [Agents](../src/opsmesh/agents/README.md) | 配置和原生 SDK 组装 |
| [Execution](../src/opsmesh/execution/README.md) | 持久实例、Session、环境和 Run |
| [Workflows](../src/opsmesh/workflows/README.md) | 编排定义、绑定、图执行和引擎边界 |
| [Plugins](../src/opsmesh/plugins/README.md) | 安装、适配器和热更新 |
| [Resources](../src/opsmesh/resources/README.md) | 存储接口、目录权限和资源所有权 |
| [Gateway](../src/opsmesh/gateway/README.md) | 通道、外部身份绑定和投递 |
| [测试范围](../tests/README.md) | 自动检查、外部依赖和覆盖限制 |
| [工程脚本](../scripts/README.md) | 构建、发行和验收入口 |

## 支持与安全

问题报告使用 [GitHub Issues](https://github.com/jhupo/OpsMesh/issues)。提交日志前移除凭据、
Session、用户输入和内部地址。漏洞通过[安全策略](../SECURITY.md)中的私密渠道报告。
