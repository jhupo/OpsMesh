# OpsMesh Backend

Backend service for OpsMesh: the API, orchestration layer, worker runtime, marketplace, approvals, files, audit, and operations control plane.

Production API and worker processes run on a VPS through systemd and a release-local virtual environment. Docker is not used to deploy the backend services in production; it is only the worker-managed substrate for isolated dangerous-task runtimes.

## Local Development

Install dependencies:

```bash
uv sync --dev
```

Run the API:

```bash
uv run uvicorn backend.app.main:create_app --factory --reload --host 0.0.0.0 --port 8000
```

Run a worker:

```bash
uv run python -m backend.app.workers.cli
```

Health check:

```bash
curl http://localhost:8000/api/v1/health
```

Quality checks:

```bash
uv run ruff check backend/app backend/tests
uv run pytest
```

Run database migrations:

```bash
uv run alembic upgrade head
```

Run the worker queue tests:

```bash
uv run pytest backend/tests/test_redis_queue.py
```

Run the runtime and team execution regression set:

```bash
uv run pytest backend/tests/test_runtime_manager.py backend/tests/test_worker_runner.py backend/tests/test_worker_run_execution.py -q
```

## Deployment Assets

- `deploy/server/systemd/` contains the production systemd units.
- `deploy/server/env.example` contains the VPS environment template.
- `scripts/server-update.sh` installs GitHub release bundles by tag.
- `scripts/server-smoke-test.sh` verifies API, worker, migrations, and optional worker-user Docker runtime access.

Local `docker compose up --build` remains available for development and CI checks only.
