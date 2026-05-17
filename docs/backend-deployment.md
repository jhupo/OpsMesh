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

## Production Settings

Before running with `CHAINCLOUD_ENVIRONMENT=production`, set strong values for:

- `CHAINCLOUD_INTERNAL_API_TOKEN`
- `CHAINCLOUD_TOKEN_HASH_PEPPER`
- `CHAINCLOUD_POSTGRES_PASSWORD`
- `CHAINCLOUD_ENABLE_API_DOCS=false`

The application refuses to boot in production when default internal secrets are used or API docs are still enabled.

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

The deployment defaults to the deterministic fake runner:

```bash
CHAINCLOUD_AGENT_RUNNER_BACKEND=fake
```

Use the OpenAI Agents SDK runner after configuring provider credentials:

```bash
CHAINCLOUD_AGENT_RUNNER_BACKEND=openai
OPENAI_API_KEY=...
```

The product orchestration layer should continue to talk through the internal agent runtime contract rather than importing provider-specific SDK behavior into API routes.
