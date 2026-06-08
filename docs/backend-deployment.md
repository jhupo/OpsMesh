# Backend Deployment

This project ships as a backend control plane with two long-running process types:

- API process: serves workspace, task, runtime, approval, file, and operations APIs.
- Worker process: pulls queued agent runs from Redis and records durable run state in Postgres.

Postgres remains the source of truth. Redis is used for queues, locks, pub/sub, and short-lived cache. User-controlled execution must still happen in Docker runtimes or self-hosted isolated machines, not in the API container.

## Local Container Stack

Create an environment file from the template:

```bash
cp .env.example .env
```

For local development, the defaults are enough to boot the stack:

```bash
docker compose up --build
```

The API is exposed at `http://localhost:8000`. Health checks are available at:

- `GET /api/v1/health`
- `GET /api/v1/health/ready`

## Server Test Stack

The server test stack reuses a shared Postgres/Redis runtime network instead of creating a
second database pair. Use it after provisioning the database services and creating a release
symlink such as `/opt/chaincloud-app/current`:

```bash
cp deploy/server/env.example /opt/chaincloud-app/.env
docker compose -f deploy/server/docker-compose.backend.yml --env-file /opt/chaincloud-app/.env build
docker compose -f deploy/server/docker-compose.backend.yml --env-file /opt/chaincloud-app/.env up -d
CHAINCLOUD_COMPOSE_FILE=deploy/server/docker-compose.backend.yml scripts/server-smoke-test.sh
```

By default the API binds to `127.0.0.1:8000`. Put Nginx or another controlled ingress in front
of it before exposing it outside the server.

The same compose file also includes a minimal monitoring stack:

- Prometheus scrapes `api:8000/api/v1/metrics` and loads `deploy/server/monitoring/alert-rules.yml`.
- Alertmanager loads `deploy/server/monitoring/alertmanager.yml`; the checked-in receiver keeps alerts visible in the UI until an operator adds email, Slack, or webhook routing.
- Grafana provisions the Prometheus datasource and the `ChainCloud Control Plane` dashboard from `deploy/server/monitoring/grafana`.

Prometheus, Alertmanager, and Grafana bind to `127.0.0.1` by default:

```bash
CHAINCLOUD_MONITORING_DIR=/opt/chaincloud-app/current/deploy/server/monitoring
CHAINCLOUD_PROMETHEUS_PORT=9090
CHAINCLOUD_ALERTMANAGER_PORT=9093
CHAINCLOUD_GRAFANA_PORT=3000
CHAINCLOUD_GRAFANA_ADMIN_PASSWORD=replace-with-random-password
```

Run the smoke test with monitoring checks after the stack starts:

```bash
CHAINCLOUD_SMOKE_MONITORING=true CHAINCLOUD_COMPOSE_FILE=deploy/server/docker-compose.backend.yml scripts/server-smoke-test.sh
```

Keep Grafana and Alertmanager behind SSH tunneling, VPN, or authenticated ingress unless a production SSO/auth layer is configured.

## Isolated Remote Backend Validation

For backend closure checks on a remote host, use the isolated validation script instead of
connecting to an existing server database:

```bash
scripts/remote-backend-validation.sh
```

The script creates a disposable Docker network, one temporary Postgres container, one temporary
Redis container, and one temporary Python test container. It installs the backend package plus
`pytest`, `fakeredis`, and `ruff`, runs targeted pytest/ruff checks, and then removes the
containers/network with a shell trap. It does not connect to `chaincloud-postgres`.

Override the targeted checks when needed:

```bash
CHAINCLOUD_REMOTE_PYTEST_ARGS="backend/tests/test_operations_api.py -k operations_overview" \
CHAINCLOUD_REMOTE_RUFF_ARGS="backend/app/operations/service.py backend/tests/test_operations_api.py" \
scripts/remote-backend-validation.sh
```

## Production Settings

Before running with `CHAINCLOUD_ENVIRONMENT=production`, set strong values for:

- `CHAINCLOUD_INTERNAL_API_TOKEN`
- `CHAINCLOUD_TOKEN_HASH_PEPPER`
- `CHAINCLOUD_POSTGRES_PASSWORD`
- `CHAINCLOUD_ENABLE_API_DOCS=false`
- `CHAINCLOUD_CREDENTIAL_ENCRYPTION_SECRET`
- `CHAINCLOUD_GRAFANA_ADMIN_PASSWORD`

The application refuses to boot in production when default internal secrets are used or API docs are still enabled.
The credential encryption secret protects hosted MCP credentials, model provider keys, and webhook signing secrets stored by the platform. Rotate it by setting a new `CHAINCLOUD_CREDENTIAL_ENCRYPTION_SECRET` and `CHAINCLOUD_CREDENTIAL_ENCRYPTION_KEY_ID`, while keeping old key material in `CHAINCLOUD_CREDENTIAL_ENCRYPTION_PREVIOUS_SECRETS` as a JSON object keyed by old key ID. Once old encrypted rows have been re-encrypted under the current key, remove the retired key from the previous-secret keyring.
External vault references can be configured through `CHAINCLOUD_SECRET_VAULT_PROVIDERS` as JSON provider metadata. Admin/configuration responses redact provider URLs to host-only summaries and redact tokens/headers.

API rate limiting is disabled by default for local development. Enable it in shared or production environments:

```bash
CHAINCLOUD_API_RATE_LIMIT_ENABLED=true
CHAINCLOUD_API_RATE_LIMIT_REQUESTS=600
CHAINCLOUD_API_RATE_LIMIT_WINDOW_SECONDS=60
```

Rate limits use Redis fixed windows and fail open if Redis is temporarily unavailable, so cache instability does not take down the API.

Hosted MCP health checks are treated as stale after `CHAINCLOUD_MCP_HEALTH_CHECK_STALE_AFTER_SECONDS` seconds, defaulting to `86400`. Stale or missing MCP health results fail closed, so the platform will avoid using hosted MCP credentials until a fresh healthy check is recorded.

## Process Commands

API:

```bash
uvicorn backend.app.main:create_app --factory --host 0.0.0.0 --port 8000
```

Worker:

```bash
python -m backend.app.workers.cli
```

Run one-shot migrations:

```bash
alembic upgrade head
```

The Docker entrypoint runs migrations by default. Set `CHAINCLOUD_RUN_MIGRATIONS=false` for worker-only containers or when migrations are managed by an external release job.

## Model Provider Dispatch Runner

The worker routes each run through the real provider-dispatching runner. OpenAI-compatible
providers route through the OpenAI Agents SDK; Anthropic/Claude providers route through the
native messages runner.

Model provider keys should be stored through the workspace API, not raw environment
variables:

- `POST /api/v1/workspaces/{workspace_id}/model-provider-credentials`
- stores encrypted `api_key`, optional `base_url`, and a `default_model`
- returns only a fingerprint and never returns the secret
- agents can reference a credential through `model_provider_credential_id`
- agents can set `model` to a concrete model or `workspace-default` to use the credential default
If an agent has no credential reference, the worker resolves the workspace default model provider
credential when one exists.

Run a real OpenAI-compatible gateway smoke only after explicitly authorizing the external
provider call and exporting a temporary API key in the shell:

```bash
export OPENAI_API_KEY
export OPENAI_SMOKE_BASE_URL=https://dash.ovload.com/
python scripts/openai-gateway-smoke.py --dry-run
python scripts/openai-gateway-smoke.py --allow-external-provider-call
```

The smoke script reads the key from the process environment, never stores it in the repo, and
normalizes root OpenAI-compatible URLs to `/v1` before running the `openai_smoke` pytest marker.
The dry run prints only redacted configuration and does not make an external provider call. Use
`OPENAI_SMOKE_MODEL` to override the default smoke model.

The product orchestration layer should continue to talk through the internal agent runtime contract rather than importing provider-specific SDK behavior into API routes.
