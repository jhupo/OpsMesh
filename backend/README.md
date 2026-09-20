# OpsMesh Backend

Backend service for OpsMesh: the API, orchestration layer, worker runtime, marketplace, approvals, files, audit, and operations control plane.

Production API and worker processes use the managed release package in either Compose (default) or
systemd mode. Docker also remains the worker-managed substrate for isolated runtime workloads; the
API never receives Docker authority.

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
uv run python -m backend.app.runtime.workers.cli
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

- `deploy/install.sh` bootstraps a fixed GitHub Release tag without cloning or building source.
- `deploy/server/compose.yml` is the default packaged topology.
- `deploy/server/systemd/` contains the alternative production systemd units.
- `deploy/server/env.example` documents VPS configuration.
- `scripts/server-smoke-test.sh` verifies API, worker, migrations, and optional worker-user Docker runtime access.

Local `docker compose -f deploy/local/compose.yml up --build` remains available for development and CI checks only.
