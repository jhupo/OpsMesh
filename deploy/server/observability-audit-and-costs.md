# 日志、指标、追踪与治理

基础部署仅需要 API、Worker、PostgreSQL、Redis 以及按授权配置的 Runtime。`deploy/server/monitoring` 是可选监控栈，不是每个 Agent 或 MCP 的额外运行容器。

## 应用日志

API 与 Worker 输出脱敏结构化日志。Compose 使用 matching release 下的 compose 文件及 `/opt/opsmesh/.env`：

```bash
docker compose -f deploy/server/compose.yml --env-file /opt/opsmesh/.env logs --tail=200 api worker
journalctl -u opsmesh-api -u opsmesh-worker -n 200 --no-pager
opsmesh --root /opt/opsmesh logs --service updater
```

前两项分别用于 Compose 和 systemd。按 request_id、workspace_id、task_id、run_id 关联 API、队列和执行事件。工具进度与终态使用 Task/Run 事件 API；不要通过输出原始 MCP stderr 或 Provider 请求内容补充日志。

## 指标与追踪

`GET /api/v1/metrics` 输出 Prometheus 格式；指标访问要求由部署配置控制，不能假定生产接口公开。可选栈包含 Prometheus、Alertmanager、Loki、Tempo、OpenTelemetry Collector 和 Grafana。其 Compose 使用 Linux host networking，监听限制在 loopback；远程访问采用受控入口或 SSH/VPN。

API/Worker 的 OTLP 目标通过 `OPSMESH_OTEL_EXPORTER_OTLP_ENDPOINT` 设置。生产开启追踪和 OTLP 日志时，需要有效的安全地址或本地 Collector。JSON stdout 与 OTLP 不互相替代。Tempo 将 span metrics 和 service graph 写入 Prometheus，Grafana 使用仓库的 provisioned datasource/dashboard。

Alertmanager 必须先用真实 webhook 配置生成私有文件：

```bash
OPSMESH_ALERT_WEBHOOK_URL=<private-webhook-url> \
  /opt/opsmesh/current/python/bin/python3 /opt/opsmesh/current/scripts/render-alertmanager-config.py \
  --output /opt/opsmesh/alertmanager.generated.yml
```

将 `OPSMESH_ALERTMANAGER_CONFIG_FILE` 指向该文件，限制文件权限，再启动 observability 服务。不要把 webhook URL 或生成配置纳入发布包或版本管理。运行 `OPSMESH_SMOKE_MONITORING=true /opt/opsmesh/current/scripts/server-smoke-test.sh` 检查服务、指标采集、日志写入和追踪；此脚本针对 systemd 管理的部署。

## 持久审计、成本与状态历史

PostgreSQL 是审计、用量账本和平台状态快照的事实来源；Redis/Prometheus 不保存唯一业务记录。审计完整性检查、陈旧 Run 恢复、平台成本汇总和历史接口见 [平台运维](../../docs/platform-operations.md) 及 [管理员 API](../../docs/api/admin.md)。

成本汇总区分有价格和未定价用量，不自动换汇。平台历史只存控制面状态数量，不是 CPU、内存或网络时序。维护采样不补造停机期间数据；业务进度不依赖监控服务或历史采样任务。
