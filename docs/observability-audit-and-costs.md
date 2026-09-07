# Observability, Audit, and Cost Operations

OpsMesh ships a single-VPS observability and governance stack. Application logs, metrics, and
traces are correlated by `trace_id`; audit evidence remains a separate durable Postgres record;
model usage and calculated cost are stored in a workspace-scoped ledger.

## Signal Flow

| Signal | Producer | Transport | Durable/query backend | Retention |
| --- | --- | --- | --- | --- |
| Logs | API and worker structured log records | OTLP gRPC, with JSON stdout fallback | Loki and Grafana Explore | 31 days |
| Metrics | API and Postgres/Redis domain collectors | official Prometheus Python client | Prometheus and Grafana | 30 days |
| Traces | FastAPI, HTTPX, SQLAlchemy, Redis, queue, and worker spans | OTLP gRPC | Tempo and Grafana | 7 days |
| Audit | Product services | SQLAlchemy transaction | Postgres `audit_events` plus integrity snapshots | WORM by default |
| Model cost | Model response usage events | orchestration transaction | Postgres `model_usage_records` | Product data retention |

The checked-in stack is under `deploy/server/monitoring`. It pins Prometheus, Alertmanager, Loki,
Tempo, OpenTelemetry Collector, and Grafana versions and uses persistent Docker volumes. On the
Linux VPS, all six containers use host networking so Prometheus can scrape the loopback-only API.
Every application, telemetry, and UI listener is explicitly bound to `127.0.0.1`; there are no
Docker-published monitoring ports and no monitoring endpoint is exposed on an external interface.

## Production Setup

Copy `deploy/server/env.example` to `/opt/opsmesh/.env`, replace every placeholder secret, and keep
the file readable only by the OpsMesh service group. Production telemetry requires:

```bash
OPSMESH_TRACING_ENABLED=true
OPSMESH_OTEL_LOGS_ENABLED=true
OPSMESH_OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4317
OPSMESH_OTEL_EXPORTER_OTLP_INSECURE=true
OPSMESH_OTEL_TRACE_SAMPLE_RATIO=1.0
```

Loopback plaintext is allowed because the collector runs on the same host. Use TLS and
`OPSMESH_OTEL_EXPORTER_OTLP_HEADERS` for any non-loopback collector. Production validation rejects
an insecure remote endpoint.

Render the Alertmanager receiver from a separate root-only environment file. Do not add the
receiver token to `/opt/opsmesh/.env`, because that file is also loaded by the API and worker.

```bash
sudo install -m 600 /dev/null /etc/opsmesh/alertmanager-render.env
sudo editor /etc/opsmesh/alertmanager-render.env
```

The file contains the real receiver URL and, when required, its bearer token:

```bash
OPSMESH_ALERT_WEBHOOK_URL=https://alerts.example.com/opsmesh
OPSMESH_ALERT_WEBHOOK_BEARER_TOKEN=replace-with-real-receiver-token
```

Render without placing the secret in shell history:

```bash
sudo sh -c '
  set -a
  . /etc/opsmesh/alertmanager-render.env
  set +a
  python3 /opt/opsmesh/current/scripts/render-alertmanager-config.py \
    --output /opt/opsmesh/alertmanager.generated.yml
'
```

The renderer requires an absolute HTTPS URL, rejects URL-embedded credentials, permits plaintext
only for loopback testing, writes atomically, and sets mode `0600` on the generated config.

Install and start all three systemd services:

```bash
sudo cp /opt/opsmesh/current/deploy/server/systemd/opsmesh-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now opsmesh-api opsmesh-worker opsmesh-observability
```

The observability service runs Docker Compose as root to manage the six containers. Application
logs are sent directly to the loopback Collector and also remain on JSON stdout for systemd
diagnostics. The containers run as their image users, the stack does not mount the host journal or
root filesystem, and the Collector does not run as root. The API remains outside the Docker group;
only the worker receives Docker access for isolated runtimes.

## Verification

The monitoring smoke verifies more than open ports. It checks that the OpsMesh API target is up in
Prometheus, governance metric families exist, a recent API log can be queried from Loki, and a
known W3C trace can be fetched from Tempo.

```bash
OPSMESH_SMOKE_MONITORING=true \
OPSMESH_ENV_FILE=/opt/opsmesh/.env \
/opt/opsmesh/current/scripts/server-smoke-test.sh
```

The production environment example enables both `OPSMESH_SMOKE_MONITORING` and
`OPSMESH_SMOKE_TELEMETRY`, so updates fail closed when telemetry acceptance fails. Set either to
`false` only for explicit diagnosis. Alertmanager also must load a rendered
non-placeholder webhook config. Receiver-side delivery confirmation remains part of the external
receiver's own test procedure; OpsMesh alerts on Alertmanager notification failures.

## Audit Integrity

Audit events are independently recorded, redacted, and chained per workspace with SHA-256 hashes.
Postgres migration `0055_observability_cost_audit` installs a trigger that rejects every update and
delete. Retention deletion is possible only when WORM is explicitly disabled and the application
sets `opsmesh.audit_retention_delete=on` locally inside that transaction.

The worker verifies every due workspace chain on the configured cadence and stores an
`audit_integrity_checks` snapshot. Invalid, missing, and stale states are exported to Prometheus.
Operators can inspect or queue an immediate check through:

- `GET /api/v1/workspaces/{workspace_id}/operations/audit-integrity`
- `POST /api/v1/workspaces/{workspace_id}/operations/audit-integrity/verify`
- `GET /api/v1/workspaces/{workspace_id}/operations/audit-events`

The first two endpoints require workspace admin permission. Queuing a verification is itself an
audit event. The default check interval is one hour and the stale threshold is two hours.

Database superusers can disable database triggers, so Postgres access control and encrypted,
off-host database backups remain part of the audit trust boundary. Back up the database and
observability Docker volumes before upgrades; do not treat Loki or Tempo as the audit source of
truth.

## Cost Accounting

Every completed model request writes one `model_usage_records` row in the same durable workflow as
the run events. OpenAI-style and Anthropic-style token fields are normalized; cached-input and
reasoning tokens are retained separately. Raw usage metadata is redacted before persistence.

Metering status is explicit:

- `priced`: usage matched a workspace pricing rule and includes a calculated cost snapshot.
- `unpriced`: token usage exists but no applicable rule matches provider, model, and effective time.
- `missing_usage`: the provider result contained no usable usage object.

Pricing rules are workspace-scoped and versioned. Exact model rules win over the `*` wildcard;
then the newest effective rule wins deterministically. Existing ledger rows retain the selected
pricing version, currency, component costs, and total cost, so later price changes do not rewrite
history.

Cost APIs are:

- `GET /api/v1/workspaces/{workspace_id}/costs/usage`
- `GET /api/v1/workspaces/{workspace_id}/costs/summary`
- `GET|POST /api/v1/workspaces/{workspace_id}/costs/pricing-rules`
- `POST /api/v1/workspaces/{workspace_id}/costs/pricing-rules/{rule_id}/disable`
- `GET|PUT /api/v1/workspaces/{workspace_id}/costs/budget`

Usage and summaries require read permission. Pricing and budget changes require admin permission
and write audit events. Summary aggregation runs in the database and supports provider, model,
agent, run, and UTC day grouping over a maximum 366-day range.

Budgets are monthly per currency. `warn` reports state and raises alerts; `block` also rejects new
model requests after recorded spend reaches the matching pricing currency's limit. When any
blocking budget is enabled, requests without an active pricing rule fail closed instead of bypassing
enforcement. Calls already in flight can finish, so this is an operational guardrail rather than a
prepaid billing authorization system. Unpriced and missing-usage records are visible in summaries
and alerts and must be resolved before financial reconciliation.

## Alerts and Dashboards

The provisioned Grafana datasource setup links Loki log lines to Tempo by `trace_id`, and Tempo
traces back to Loki by trace and span IDs. Tempo emits span metrics and service graphs to
Prometheus. The dashboard covers HTTP latency/errors, queues, workers, runtime capacity, audit
integrity, unpriced model usage, cost budgets, recent application logs, and trace navigation.

Alert rules cover API/backend availability, queue pressure, stale workers, runtime saturation,
audit integrity, unpriced/missing usage, exhausted budgets, observability backend availability,
domain-metrics collection failures, OTLP export failures, and Alertmanager delivery failures. Tune thresholds in
`deploy/server/monitoring/alert-rules.yml`
for the deployment's workload; do not remove the integrity or delivery-failure alerts.
