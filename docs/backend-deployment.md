# Backend Deployment

This project ships as a backend control plane with two long-running process types:

- API process: serves workspace, task, runtime, approval, file, and operations APIs.
- Worker process: pulls queued agent runs from Redis and records durable run state in Postgres.

Production server deployments run the API and worker directly on the VPS through systemd and a release-local Python virtual environment. Docker is still required on the host for dangerous task runtimes and the pinned observability stack; it is not used to run the backend API or worker.

Postgres remains the source of truth. Redis is used for queues, locks, pub/sub, and short-lived cache. User-controlled execution must still happen in Docker runtimes or self-hosted isolated machines, never inside the API or worker process.

## Local Container Stack

Create an environment file from the template:

```bash
cp .env.example .env
```

For local development, the defaults are enough to boot the local compose stack:

```bash
docker compose up --build
```

The API is exposed at `http://localhost:8000`. Health checks are available at:

- `GET /api/v1/health`
- `GET /api/v1/health/ready`

The local compose stack is only for development and CI checks. Production backend processes are managed by systemd.

Build the dedicated isolated runtime image before enabling Docker-backed agent or stdio MCP
execution:

```bash
docker build -f Dockerfile.runtime -t opsmesh-runtime:local .
docker run --rm opsmesh-runtime:local \
  python -m opsmesh_runtime.mcp_stdio_client --check
```

The runtime image contains the small `opsmesh-runtime` package and the pinned official MCP Python
SDK, not the API application or database clients. It runs as `opsmesh-runtime`, uses `/workspace`
as its working directory, and reports SDK readiness before a stdio server is launched. Keep
`OPSMESH_RUNTIME_ALLOWED_IMAGES` restricted to reviewed runtime image tags or immutable digests.

## Self-Hosted MCP Connector

Create an enrollment token as a workspace owner, register the machine through
`POST /api/v1/self-hosted/register`, and retain the returned runtime credential in the machine's
secret manager. Install the connector from the runtime package:

```bash
python -m pip install ./runtime
```

Run the connector with the API prefix and credential in environment variables. The credential is
intentionally not accepted as a command-line argument because process arguments are commonly
visible to other local users:

```bash
export OPSMESH_API_URL=https://opsmesh.example.com/api/v1
export OPSMESH_RUNTIME_CREDENTIAL=ccwc_replace_with_runtime_credential
opsmesh-self-hosted-worker --state-path /var/lib/opsmesh-connector/state.sqlite3
```

Use `--once` for a health or packaging smoke. `--check` validates the installed MCP SDK without
requiring credentials. Remote API URLs must use HTTPS; plaintext HTTP is accepted only for loopback
development. Run exactly one connector process per state file. A `filelock` guard enforces that
rule, while SQLite stores claimed requests, execution phase, and result-ready completion payloads.

Configure the connector as a service with its credential supplied by the host secret manager and a
private, persistent state directory. A restart reposts a recorded result without rerunning the MCP
tool. Execution interrupted before a result was durably recorded is completed as a sanitized
failure because automatically repeating a side-effecting tool would be unsafe. An empty heartbeat
capability object preserves the capabilities established at registration; pass
`--capabilities-file` only when supplying the complete replacement capability object.

## VPS Layout

Provision a VPS with Python 3.11+, `uv`, Postgres, Redis, Docker, and systemd. Keep release assets under `/opt/opsmesh`:

```text
/opt/opsmesh/.env
/opt/opsmesh/current -> /opt/opsmesh/releases/v1.2.3
/opt/opsmesh/downloads
/opt/opsmesh/releases
/opt/opsmesh/release-state.env
/var/lib/opsmesh/storage
```

Create the deploy environment from the server template:

```bash
sudo mkdir -p /opt/opsmesh/releases /opt/opsmesh/downloads /var/lib/opsmesh/storage
sudo cp deploy/server/env.example /opt/opsmesh/.env
sudo chmod 600 /opt/opsmesh/.env
```

Set production values in `/opt/opsmesh/.env`, especially:

- `OPSMESH_INTERNAL_API_TOKEN`
- `OPSMESH_PLATFORM_ADMIN_TOKEN`
- `OPSMESH_WORKER_HEARTBEAT_TOKEN`
- `OPSMESH_TOKEN_HASH_PEPPER`
- `OPSMESH_POSTGRES_PASSWORD` or the full `OPSMESH_DATABASE_URL`
- `OPSMESH_REDIS_URL`
- `OPSMESH_ENABLE_API_DOCS=false`
- `OPSMESH_READINESS_WORKER_CHECK_ENABLED=true`
- `OPSMESH_CREDENTIAL_ENCRYPTION_SECRET`
- `OPSMESH_STORAGE_ROOT=/var/lib/opsmesh/storage`
- `OPSMESH_RUNTIME_ALLOWED_IMAGES=["opsmesh-runtime:local"]` or a reviewed immutable runtime image
  digest

Install Docker on the VPS and leave the daemon available only to the worker service user if hosted runtime execution is enabled. The backend API and worker are not Compose services. Docker runs isolated task containers and the separately managed observability stack; the API process must not be able to control the Docker daemon.

## systemd Services

Create separate unprivileged service users and install systemd units for the API and worker. The
observability unit is root-owned because Docker manages its containers; the Collector receives
application telemetry over the loopback OTLP endpoint and does not mount the host filesystem:

```bash
sudo groupadd --system opsmesh
sudo useradd --system --home /opt/opsmesh --shell /usr/sbin/nologin --gid opsmesh opsmesh-api
sudo useradd --system --home /opt/opsmesh --shell /usr/sbin/nologin --gid opsmesh opsmesh-worker
sudo chown -R opsmesh-api:opsmesh /opt/opsmesh
sudo chown -R opsmesh-worker:opsmesh /var/lib/opsmesh
sudo usermod -aG docker opsmesh-worker
```

`/etc/systemd/system/opsmesh-api.service`:

```ini
[Unit]
Description=OpsMesh API
After=network-online.target postgresql.service redis-server.service
Wants=network-online.target

[Service]
User=opsmesh-api
Group=opsmesh
WorkingDirectory=/opt/opsmesh/current
EnvironmentFile=/opt/opsmesh/.env
ExecStart=/bin/sh -c 'exec /opt/opsmesh/current/.venv/bin/uvicorn backend.app.main:create_app --factory --host "${OPSMESH_API_BIND:-127.0.0.1}" --port "${OPSMESH_API_PORT:-8000}"'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/opsmesh-worker.service`:

```ini
[Unit]
Description=OpsMesh Worker
After=network-online.target postgresql.service redis-server.service docker.service
Wants=network-online.target docker.service

[Service]
User=opsmesh-worker
Group=opsmesh
WorkingDirectory=/opt/opsmesh/current
EnvironmentFile=/opt/opsmesh/.env
ExecStart=/opt/opsmesh/current/.venv/bin/python -m backend.app.workers.cli
Restart=always
RestartSec=5
SupplementaryGroups=docker

[Install]
WantedBy=multi-user.target
```

Enable the units after the first release is installed:

```bash
sudo systemctl daemon-reload
sudo systemctl enable opsmesh-api opsmesh-worker
```

Put Nginx or another controlled ingress in front of `127.0.0.1:8000` before exposing the API outside the server.

Install `deploy/server/systemd/opsmesh-observability.service` with the API and worker units after
rendering `/opt/opsmesh/alertmanager.generated.yml`. Complete setup and secret-isolation steps are
in [Observability, Audit, and Cost Operations](observability-audit-and-costs.md). The monitoring
Compose file is Linux-specific: it uses host networking so Prometheus can scrape the API at
`127.0.0.1:8000`, while every monitoring listener is also pinned to `127.0.0.1`.

## Release Bundles and Server Updates

Pushing a tag such as `v1.2.3` runs the backend quality gate and creates a GitHub Release with VPS/systemd deployment assets:

- `opsmesh-server-v1.2.3-manifest.json`
- `opsmesh-server-v1.2.3.tar.gz`
- `opsmesh-server-v1.2.3.tar.gz.sha256`

The manifest records the bundle URL and bundle sha256. It does not reference a backend container image. The server updater downloads or reads the manifest, verifies the bundle sha256, unpacks it into `/opt/opsmesh/releases/<tag>`, switches `/opt/opsmesh/current`, runs `uv sync`, applies `alembic upgrade head`, restarts `opsmesh-api`, `opsmesh-worker`, and the enabled `opsmesh-observability` service, then runs the health smoke.

On the server, update by tag:

```bash
OPSMESH_ENV_FILE=/opt/opsmesh/.env \
/opt/opsmesh/current/scripts/server-update.sh --tag v1.2.3
```

Use a manifest URL or local manifest file when mirroring release assets:

```bash
/opt/opsmesh/current/scripts/server-update.sh \
  --manifest-url https://github.com/jhupo/OpsMesh/releases/download/v1.2.3/opsmesh-server-v1.2.3-manifest.json

/opt/opsmesh/current/scripts/server-update.sh \
  --manifest-file /opt/opsmesh/downloads/opsmesh-server-v1.2.3-manifest.json
```

Use a bundle URL or local bundle file with an explicit sha256 for direct deployment:

```bash
/opt/opsmesh/current/scripts/server-update.sh \
  --bundle-url https://github.com/jhupo/OpsMesh/releases/download/v1.2.3/opsmesh-server-v1.2.3.tar.gz \
  --bundle-sha256 "$(cut -d ' ' -f 1 /opt/opsmesh/downloads/opsmesh-server-v1.2.3.tar.gz.sha256)"

/opt/opsmesh/current/scripts/server-update.sh \
  --manifest-file /opt/opsmesh/downloads/opsmesh-server-v1.2.3-manifest.json \
  --bundle-file /opt/opsmesh/downloads/opsmesh-server-v1.2.3.tar.gz \
  --bundle-sha256 "$(cut -d ' ' -f 1 /opt/opsmesh/downloads/opsmesh-server-v1.2.3.tar.gz.sha256)"
```

Use dry-run before changing the symlink or services:

```bash
/opt/opsmesh/current/scripts/server-update.sh --tag v1.2.3 --dry-run
```

Rollback switches `/opt/opsmesh/current` back to the previously recorded release directory, runs `uv sync`, restarts the API and worker, and runs smoke checks. Restart keeps the current release and only restarts services:

```bash
/opt/opsmesh/current/scripts/server-update.sh rollback
/opt/opsmesh/current/scripts/server-update.sh restart
```

The admin API exposes a sub2api-style system updater. It requires the platform admin bearer token and defaults command endpoints to dry-run. Real online updates are disabled unless `OPSMESH_RELEASE_UPDATE_ENABLED=true` is set for the API service:

```bash
curl -fsS http://127.0.0.1:8000/api/v1/admin/system/version \
  -H "Authorization: Bearer ${OPSMESH_PLATFORM_ADMIN_TOKEN}"

curl -fsS "http://127.0.0.1:8000/api/v1/admin/system/check-updates?force=true" \
  -H "Authorization: Bearer ${OPSMESH_PLATFORM_ADMIN_TOKEN}"

curl -fsS -X POST http://127.0.0.1:8000/api/v1/admin/system/update \
  -H "Authorization: Bearer ${OPSMESH_PLATFORM_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"tag":"v1.2.3","dry_run":true}'

curl -fsS -X POST http://127.0.0.1:8000/api/v1/admin/system/rollback \
  -H "Authorization: Bearer ${OPSMESH_PLATFORM_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"dry_run":true}'

curl -fsS -X POST http://127.0.0.1:8000/api/v1/admin/system/restart \
  -H "Authorization: Bearer ${OPSMESH_PLATFORM_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"dry_run":true}'
```

## Smoke Checks

Run the smoke test after a deploy:

```bash
OPSMESH_ENV_FILE=/opt/opsmesh/.env /opt/opsmesh/current/scripts/server-smoke-test.sh
```

The smoke test verifies:

- `GET /api/v1/health/ready`
- `systemctl is-active opsmesh-api`
- `systemctl is-active opsmesh-worker`
- `.venv/bin/alembic current`

Docker access is checked only when hosted dangerous-task runtimes are enabled for the VPS:

```bash
OPSMESH_SMOKE_DOCKER_RUNTIME=true \
OPSMESH_DOCKER_CHECK_USER=opsmesh-worker \
OPSMESH_ENV_FILE=/opt/opsmesh/.env \
/opt/opsmesh/current/scripts/server-smoke-test.sh
```

That check verifies `docker.service` and runs `docker info` as the worker user when `sudo` is available. The API service user should not have Docker daemon access.

Run the smoke test with monitoring checks after the complete observability stack is started:

```bash
OPSMESH_SMOKE_MONITORING=true /opt/opsmesh/current/scripts/server-smoke-test.sh
```

That mode checks the observability systemd unit, Prometheus, Alertmanager, Loki, Tempo, the
OpenTelemetry Collector, Grafana, the OpsMesh Prometheus target and governance series, recent log
ingestion, and an end-to-end W3C trace. Keep Grafana and Alertmanager behind SSH tunneling, VPN, or
authenticated ingress unless a production SSO/auth layer is configured.

The release bundle carries the pinned observability stack assets under `deploy/server/monitoring`:

- Prometheus scrapes the host API and the observability services and loads `alert-rules.yml`.
- Alertmanager refuses to start without a separately rendered real webhook receiver config.
- OpenTelemetry Collector exports API/worker traces to Tempo and OTLP application logs to Loki;
  JSON stdout remains available through systemd for local diagnosis.
- Tempo emits span metrics and service graphs back to Prometheus.
- Grafana provisions Prometheus, Loki, and Tempo correlation plus the `OpsMesh Control Plane`
  dashboard.

## Isolated Remote Backend Validation

For backend closure checks on a remote host, use the isolated validation script instead of connecting to an existing server database:

```bash
scripts/remote-backend-validation.sh
```

The script creates a disposable Docker network, one temporary Postgres container, one temporary Redis container, and one temporary Python test container. It installs the backend package plus `pytest`, `fakeredis`, and `ruff`, runs targeted pytest/ruff checks, and then removes the containers/network with a shell trap. It does not connect to production Postgres or Redis.

Override the targeted checks when needed:

```bash
OPSMESH_REMOTE_PYTEST_ARGS="backend/tests/test_operations_api.py -k operations_overview" \
OPSMESH_REMOTE_RUFF_ARGS="backend/app/operations/service.py backend/tests/test_operations_api.py" \
scripts/remote-backend-validation.sh
```

## Production Settings

Before running with `OPSMESH_ENVIRONMENT=production`, set strong values for:

- `OPSMESH_INTERNAL_API_TOKEN`
- `OPSMESH_PLATFORM_ADMIN_TOKEN`
- `OPSMESH_WORKER_HEARTBEAT_TOKEN`
- `OPSMESH_TOKEN_HASH_PEPPER`
- `OPSMESH_POSTGRES_PASSWORD` or the full `OPSMESH_DATABASE_URL`
- `OPSMESH_ENABLE_API_DOCS=false`
- `OPSMESH_READINESS_WORKER_CHECK_ENABLED=true`
- `OPSMESH_CREDENTIAL_ENCRYPTION_SECRET`
- `OPSMESH_GRAFANA_ADMIN_PASSWORD`
- `OPSMESH_OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4317`
- `OPSMESH_ALERTMANAGER_CONFIG_FILE=/opt/opsmesh/alertmanager.generated.yml`

The application refuses to boot in production when default internal secrets are used, API docs are still enabled, worker readiness is disabled, local default database credentials are configured, or OTLP logs/tracing are enabled without a valid secure or loopback collector.

The credential encryption secret protects hosted MCP credentials, model provider keys, and webhook signing secrets stored by the platform. Rotate it by setting a new `OPSMESH_CREDENTIAL_ENCRYPTION_SECRET` and `OPSMESH_CREDENTIAL_ENCRYPTION_KEY_ID`, while keeping old key material in `OPSMESH_CREDENTIAL_ENCRYPTION_PREVIOUS_SECRETS` as a JSON object keyed by old key ID. Once old encrypted rows have been re-encrypted under the current key, remove the retired key from the previous-secret keyring.

External vault references can be configured through `OPSMESH_SECRET_VAULT_PROVIDERS` as JSON provider metadata. Admin/configuration responses redact provider URLs to host-only summaries and redact tokens/headers.

API rate limiting is disabled by default for local development. Enable it in shared or production environments:

```bash
OPSMESH_API_RATE_LIMIT_ENABLED=true
OPSMESH_API_RATE_LIMIT_REQUESTS=600
OPSMESH_API_RATE_LIMIT_WINDOW_SECONDS=60
OPSMESH_AUTH_RATE_LIMIT_REQUESTS=20
OPSMESH_ADMIN_RATE_LIMIT_REQUESTS=120
OPSMESH_TRUSTED_PROXY_HOPS=1
```

Rate limits use Redis fixed windows and fail open if Redis is temporarily unavailable, so cache instability does not take down the API.

Hosted MCP health checks are treated as stale after `OPSMESH_MCP_HEALTH_CHECK_STALE_AFTER_SECONDS` seconds, defaulting to `86400`. Stale or missing MCP health results fail closed, so the platform will avoid using hosted MCP credentials until a fresh healthy check is recorded.

## Process Commands

API:

```bash
/opt/opsmesh/current/.venv/bin/uvicorn backend.app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Worker:

```bash
/opt/opsmesh/current/.venv/bin/python -m backend.app.workers.cli
```

Run one-shot migrations:

```bash
cd /opt/opsmesh/current
.venv/bin/alembic upgrade head
```

The updater runs migrations before restarting services.

## Model Provider Dispatch Runner

The worker routes each run through the real provider-dispatching runner. OpenAI-compatible providers route through the OpenAI Agents SDK; Anthropic/Claude providers route through the native messages runner.

Model provider keys should be stored through the workspace API, not raw environment variables:

- `POST /api/v1/workspaces/{workspace_id}/model-provider-credentials`
- stores encrypted `api_key`, optional `base_url`, and a `default_model`
- returns only a fingerprint and never returns the secret
- agents can reference a credential through `model_provider_credential_id`
- agents can set `model` to a concrete model or `workspace-default` to use the credential default

If an agent has no credential reference, the worker resolves the workspace default model provider credential when one exists.

Run a real OpenAI-compatible gateway smoke only after explicitly authorizing the external provider call and exporting a temporary API key in the shell:

```bash
export OPENAI_API_KEY
export OPENAI_SMOKE_BASE_URL=https://your-openai-compatible-gateway.example/
python scripts/openai-gateway-smoke.py --dry-run
python scripts/openai-gateway-smoke.py --allow-external-provider-call
```

The smoke script reads the key from the process environment, never stores it in the repo, and normalizes root OpenAI-compatible URLs to `/v1` before running the `openai_smoke` pytest marker. The dry run prints only redacted configuration and does not make an external provider call. Use `OPENAI_SMOKE_MODEL` to override the default smoke model.

The product orchestration layer should continue to talk through the internal agent runtime contract rather than importing provider-specific SDK behavior into API routes.
