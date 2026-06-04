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

## Production Settings

Before running with `CHAINCLOUD_ENVIRONMENT=production`, set strong values for:

- `CHAINCLOUD_INTERNAL_API_TOKEN`
- `CHAINCLOUD_TOKEN_HASH_PEPPER`
- `CHAINCLOUD_POSTGRES_PASSWORD`
- `CHAINCLOUD_ENABLE_API_DOCS=false`
- `CHAINCLOUD_CREDENTIAL_ENCRYPTION_SECRET`

The application refuses to boot in production when default internal secrets are used or API docs are still enabled.
The credential encryption secret protects hosted MCP credentials stored by the platform. Rotate it by introducing a new `CHAINCLOUD_CREDENTIAL_ENCRYPTION_KEY_ID` and re-encrypting existing hosted secrets before retiring the old key.

API rate limiting is disabled by default for local development. Enable it in shared or production environments:

```bash
CHAINCLOUD_API_RATE_LIMIT_ENABLED=true
CHAINCLOUD_API_RATE_LIMIT_REQUESTS=600
CHAINCLOUD_API_RATE_LIMIT_WINDOW_SECONDS=60
```

Rate limits use Redis fixed windows and fail open if Redis is temporarily unavailable, so cache instability does not take down the API.

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

## OpenAI Agents Runner

Deployments always use the OpenAI Agents SDK runner. Unit tests may inject a
deterministic test runner directly, but deployed API and worker processes should
not be switched into simulated execution by environment configuration.

Model provider keys should be stored through the workspace API, not raw environment
variables:

- `POST /api/v1/workspaces/{workspace_id}/model-provider-credentials`
- stores encrypted `api_key`, optional `base_url`, and a `default_model`
- returns only a fingerprint and never returns the secret
- agents can reference a credential through `model_provider_credential_id`
- agents can set `model` to a concrete model or `workspace-default` to use the credential default

If an agent has no credential reference, the worker resolves the workspace default model provider
credential when one exists.

The product orchestration layer should continue to talk through the internal agent runtime contract rather than importing provider-specific SDK behavior into API routes.
