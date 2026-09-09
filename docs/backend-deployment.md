# Backend Deployment

This project ships as a backend control plane with two long-running process types:

- API process: serves workspace, task, runtime, approval, file, and operations APIs.
- Worker process: pulls queued agent runs from Redis and records durable run state in Postgres.

Packaged production deployment supports Compose or direct systemd services. Systemd uses the
self-contained release runtime, not a host Python virtual environment. Docker remains required
for isolated task runtimes and the separately managed observability stack.

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

The root Compose file is for development and CI. Production Compose uses
`deploy/server/compose.yml`; direct systemd deployment is the other managed mode.

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

Provision a Linux amd64 VPS with Postgres, Redis, Docker, systemd, GitHub CLI and PostgreSQL client
tools. The release supplies Python and application dependencies. The managed installer owns:

```text
/opt/opsmesh/.env
/opt/opsmesh/current -> /opt/opsmesh/releases/v1.2.3
/opt/opsmesh/downloads
/opt/opsmesh/releases
/opt/opsmesh/installation.json
/opt/opsmesh/updater
/opt/opsmesh/data/storage
```

Use the native CLI and [managed installation procedure](delivery-operations.md). It generates
secrets and preserves existing configuration. Systemd mode requires pre-provisioned Postgres/Redis
configuration. Do not make immutable release directories writable by application service users.

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
- `OPSMESH_STORAGE_ROOT=/opt/opsmesh/data/storage`
- `OPSMESH_RUNTIME_ALLOWED_IMAGES=["opsmesh-runtime:local"]` or a reviewed immutable runtime image
  digest

Only the worker receives Docker authority for hosted task execution, in either deployment mode.
The API process must not be able to control the Docker daemon.

## systemd Services

The installer creates reserved service identities and installs the canonical units from
`deploy/server/systemd/opsmesh-api.service` and `opsmesh-worker.service`. They run
`current/opsmesh-server api` and `current/opsmesh-server worker`; the independent root-owned updater
runs `updater/opsmesh-server updater`. Configuration remains outside immutable release contents.
The observability unit is separately root-owned because Docker manages its containers.

Put Nginx or another controlled ingress in front of `127.0.0.1:8000` before exposing the API outside the server.

Install `deploy/server/systemd/opsmesh-observability.service` with the API and worker units after
rendering `/opt/opsmesh/alertmanager.generated.yml`. Complete setup and secret-isolation steps are
in [Observability, Audit, and Cost Operations](observability-audit-and-costs.md). The monitoring
Compose file is Linux-specific: it uses host networking so Prometheus can scrape the API at
`127.0.0.1:8000`, while every monitoring listener is also pinned to `127.0.0.1`.

## Verified Releases and Online Updates

The official packaged deployment is a prebuilt GHCR backend image and a separate runtime image.
The backend image is shared by the API, worker and explicit migration job. Production Compose is
`deploy/server/compose.yml`; the root Compose file remains a source-build development environment.
Neither API startup nor worker startup runs migrations automatically.
Systemd and the independent updater use the verified self-contained runtime without downloading
Python packages at installation time. Migration is explicit: `opsmesh-server migrate`.

See [Delivery operations](delivery-operations.md) for installation, CLI commands, verification,
upgrade approval, maintenance, backup verification and offline recovery. The old shell updater and
PID-returning update endpoints have been removed. Do not use source-directory switching commands
from earlier releases.

A platform administrator submits a durable plan. A separate host updater verifies its release,
records its fingerprint and waits for explicit approval. It then drains work, stops application
services, verifies a backup by restoring it into a temporary database, applies migrations, switches
the release, checks readiness and resumes admission. An ambiguous failure remains in maintenance
and requires an explicit host recovery command. Database restoration is never an automatic image
rollback.

Existing VPS/systemd installations must perform a maintenance-window migration to the managed
installation layout and preserve their database, storage and encryption keys. This is not an
in-place adapter for the removed shell update format. Use the managed installer for the current
release/update contract.

## Smoke Checks

Run the smoke test after a deploy:

```bash
OPSMESH_ENV_FILE=/opt/opsmesh/.env /opt/opsmesh/current/scripts/server-smoke-test.sh
```

The smoke test verifies:

- `GET /api/v1/health/ready`
- `systemctl is-active opsmesh-api`
- `systemctl is-active opsmesh-worker`
- `opsmesh-server migrate current`

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

Rate limits use the `limits` fixed-window strategy with Redis storage. Authentication and platform
administration fail closed if Redis is unavailable; ordinary API traffic fails open so a cache
incident does not take down the entire service.

Hosted MCP health checks are treated as stale after `OPSMESH_MCP_HEALTH_CHECK_STALE_AFTER_SECONDS` seconds, defaulting to `86400`. Stale or missing MCP health results fail closed, so the platform will avoid using hosted MCP credentials until a fresh healthy check is recorded.

## Process Commands

API:

```bash
/opt/opsmesh/current/opsmesh-server api --host 127.0.0.1 --port 8000
```

Worker:

```bash
/opt/opsmesh/current/opsmesh-server worker
```

Run one-shot migrations:

```bash
cd /opt/opsmesh/current
./opsmesh-server migrate
```

The updater runs migrations before restarting services.

## Model Provider Dispatch Runner

The worker routes each run through the real provider-dispatching runner. OpenAI-compatible providers route through the OpenAI Agents SDK; Anthropic/Claude providers route through the Claude Agent SDK adapter.

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
