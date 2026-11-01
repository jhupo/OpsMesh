# 授权测试环境验收与运行位置

目录后续修正见[全源码职责审查](design/source-layout.md)：协议现归 `infrastructure/bots`，事务消息现有真实实现 `infrastructure/messaging/outbox.py`；本文保留各历史轮次的验证记录。


更新：2026-10-31（Asia/Shanghai）。此为专用测试部署，不修改服务器原有应用或数据库。

## 验收部署范围

验收在专用 Linux 测试部署完成，网关、数据库、缓存、对象存储与 Kubernetes 命名空间均独立于已有服务。具体服务器地址、主机目录、管理员凭据、模型网关和服务凭据由测试环境维护者私下管理，不属于开源发行配置。

- PostgreSQL 16、Redis 7、SeaweedFS 4.51 S3 使用独立测试实例和本地绑定端口。
- K3s v1.34.1 在独立容器和命名空间内验收逐 Run Job；不代表跨主机高可用测试。
- 远程 Docker mTLS 使用临时代理与短期证书，验收后停止；未修改宿主 Docker 守护进程配置。
- 真实模型测试显式启用，凭据通过进程环境注入。默认测试不调用付费模型。
- 本记录描述当时的开发构建。v0.3.0 的最终提交和验证以 GitHub CI 与 Release 附件为准，不能把历史部署自动视为已升级到新版本。

## 已保存证据

`reports/`保存Linux全量测试、真实模型以外的环境测试日志、K3s生命周期、S3、原生/远程Docker、平台备份恢复与HTTP/Redis报告。实际模型测试只在本机显式运行；默认CI不调用模型。

真实K3s验收使用`opsmesh-validation:20261030`验证镜像，包含锁定依赖与实现代码。标准Dockerfile的软件源下载在测试网络停滞，停止了该构建；不能声称标准发布镜像已通过。Kubernetes仍为单节点测试拓扑，共享目录映射与UID/GID权限已设置；不是生产多节点存储方案。

平台备份演练包：`backup-bundle-b1d66198c18e`。外部保留清单SHA-256：`4fca3f259f5e6939b724f21d9200770da744927cec68b0282bbad8049c156424`。恢复到独立数据库及S3前缀后，账号登录、工作文件、只读权限、空目录、快照和blob通过验证；非空目标被拒绝。

## 目录调整

Adapter和Agent合同现直接位于`framework/adapters/`、`framework/agents/`；外部公开导入保持原名。已移除8个无实现占位包源码。自动审批检查拒绝删除本地旧目录壳，因此文件浏览器仍可能显示空目录或旧Python缓存；发行包与生成源码树不包含这些占位模块。

更多结果见[实施记录](implementation-report.md)、[SDK逐项覆盖](design/sdk-coverage.md)和[运维](operations.md)。


## 本轮平台补齐升级

Windows Python3.13与Linux Python3.14.4各通过494项测试、跳过22项显式外部集成。Linux安装来自本轮源码包，锁定依赖安装成功。已备份并迁移原隔离PostgreSQL，54张登记表验证通过；17个新增/既有管理查询接口，以及Cron计划创建/预览/删除、通知订阅创建/删除完成真实数据库验收。升级前后Run记录数一致，没有模型任务或Bot消息。

保留首次迁移前备份`reports/platform-before-upgrade.dump`，后续切换备份`reports/platform-before-upgrade-4091673.dump`及对应数据目录副本。最终验收`reports/platform-upgrade-result.json`，Linux回归`reports/platform-final-linux-tests.log`。新服务仍监听28000，原有18000服务不变。部署设置已显式固定28000端口，模型凭据继续只在进程环境内。
