# 日志、指标与追踪

基础部署需要 API、Worker、PostgreSQL、Redis、Temporal，以及用户配置的原生 SDK 沙箱后端。`deploy/server/monitoring` 是可选监控栈，不是每个 Agent 或 MCP 的额外运行容器。

## 应用日志

API 与 Worker 输出脱敏结构化日志。Compose 使用 matching release 下的 compose 文件及 `/opt/opsmesh/.env`：

```bash
docker compose -f deploy/server/compose.yml --env-file /opt/opsmesh/.env logs --tail=200 api worker
journalctl -u opsmesh-api -u opsmesh-worker -n 200 --no-pager
```

前两项分别用于 Compose 和 systemd。按 request_id、session_id、run_id 关联 API、投递和运行事件。工具进度与终态使用 Run 事件 API；不要通过输出原始 MCP stderr 或 Provider 请求内容补充日志。

## 指标与追踪

Prometheus 通过独立运维监听 `127.0.0.1:9000/metrics` 采集；用 `OPSMESH_METRICS_HOST`、`OPSMESH_METRICS_PORT` 配置，未配置端口时禁用。该监听没有用户会话授权，只能绑定内部运维网络，不能经公网 API 反向代理发布。Compose 在容器内监听 9000，仅映射宿主 loopback。可选栈包含 Prometheus、Alertmanager、Loki、Tempo、OpenTelemetry Collector 和 Grafana，其 Compose 使用 Linux host networking；远程访问采用 SSH/VPN。

API/Worker 的 OTLP 目标通过 `OPSMESH_OTEL_EXPORTER_OTLP_ENDPOINT` 设置。生产开启追踪和 OTLP 日志时，需要有效的安全地址或本地 Collector。JSON stdout 与 OTLP 不互相替代。Tempo 将 span metrics 和 service graph 写入 Prometheus，Grafana 使用仓库的 provisioned datasource/dashboard。

Alertmanager 必须先用真实 webhook 配置生成私有文件：

```bash
OPSMESH_ALERT_WEBHOOK_URL=<private-webhook-url> \
  opsmesh --root /opt/opsmesh render-alertmanager-config \
  --output /opt/opsmesh/alertmanager.generated.yml
```

将 `OPSMESH_ALERTMANAGER_CONFIG_FILE` 指向该文件，限制文件权限，再启动 observability 服务。不要把 webhook URL 或生成配置纳入发布包或版本管理。安装 `jq` 后运行 `OPSMESH_SMOKE_MONITORING=true /opt/opsmesh/current/deploy/server/check.sh` 检查服务、指标采集、日志写入和追踪；此脚本针对 systemd 管理的部署。

当前应用指标以 PostgreSQL 为来源：`opsmesh_temporal_dispatches` 统计待投递/已投递事实；`opsmesh_temporal_oldest_pending_dispatch_age_seconds` 与 `opsmesh_temporal_dispatch_publication_errors` 定位投递延迟和失败。环境、Agent Run、FlowRun 按状态统计，容量按数量种类统计；未知 Token 用量保留待核对预留。采集失败只输出 `opsmesh_metrics_collection_success{source="postgres"}=0`，不输出上次业务快照；告警同时监视采集失败。不再提供货币预算、计价或费用指标。

仓库告警覆盖 API、投递、用户容量和监控链路。Temporal 内部调度延迟、Activity 并发与 Worker 可用性需要接入 [Temporal 官方监控](https://docs.temporal.io/self-hosted-guide/monitoring)，按实际部署单独配置 scrape target；当前可选栈没有自动抓取这些指标。Redis 只负责短期发布订阅，不再报告旧队列、死信或 Worker 租约指标。

## 持久事实

PostgreSQL 是审计、数量用量账本和运行状态的事实来源；Redis/Prometheus 不保存唯一业务记录。审计、模型与数量额度接口以运行中服务的 `/openapi.json` 合同为准。审计使用幂等追加回执，不提供全局哈希链验证。执行恢复使用 Temporal 持久历史，未知外部写入需要人工核对。

Grafana 仅展示当前导出的 API、Run、环境、数量预留和采集状态，不包含货币成本、预算或审计哈希链面板。业务进度不依赖监控服务。
