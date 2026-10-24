# 主机部署 CLI

此目录维护独立 `opsmesh-operator` 包及 `opsmesh` 命令，负责安装、发行校验、在线更新、
回滚、备份恢复、观测配置与管理员系统插件管理。源码不再放在仓库根目录的 operator 下。

核心 API 和 Worker 不调用主机更新器；它由管理员在部署主机显式运行。CLI 与服务端
分别构建二进制目录包，目标机器不需要 Python、pip 或 uv。

部署与命令说明见[部署手册](../server/DEPLOYMENT.md)、
[主机操作](../server/delivery-operations.md)和[发行格式](../DISTRIBUTIONS.md)。

数据库内的维护命令由服务端发行包提供：`opsmesh-server cleanup-login-sessions`。
它按页提交，并在同一事务记录审计。模型凭据由配置的 credential resolver 管理，
不再提供针对旧 provider 表的密钥轮换命令。
