# Deployment

The default local application needs only Python and SQLite. `Dockerfile` and `compose.yaml` also provide API/Worker/PostgreSQL/Redis services. These are deployment examples. A separate Linux test host has validated actual Docker isolation/cancellation, mutual-TLS remote sandboxes and per-run Jobs on K3s. Kubernetes setup and RBAC are documented in [kubernetes](kubernetes/README.md).

## Local Compose

Copy `.env.example` to `.env`, then configure a database password and an async URL with the Compose hostname `postgres`. Percent-encode special characters in URL credentials; the PostgreSQL password remains its original value. Set a first administrator password (12+ characters) or allow `init` to prompt interactively.

```text
OPSMESH_POSTGRES_PASSWORD=<your-password>
OPSMESH_DATABASE_URL=postgresql+asyncpg://opsmesh:<url-encoded-password>@postgres:5432/opsmesh
```

```sh
docker compose --env-file .env -f deploy/compose.yaml build
docker compose --env-file .env -f deploy/compose.yaml run --rm api init
docker compose --env-file .env -f deploy/compose.yaml up -d
docker compose --env-file .env -f deploy/compose.yaml down
```

Remove `OPSMESH_ADMIN_PASSWORD` from the deployment environment after initialization. PostgreSQL and workspace data use named volumes; ordinary `down` retains them. Migrations are explicit: after backing up and reviewing an upgrade, run `docker compose ... run --rm api migrate` before starting the new application version. Startup validates schema and does not silently modify it.

The container runs as UID/GID 10001. API port 8000, PostgreSQL and Redis are bound to loopback in this development example. Configure an authenticated reverse proxy/TLS and suitable access controls for external use. The image installs dependencies from uv.lock with frozen resolution, including the optional OpenAI SDK. Pin reviewed base-image digests in your release pipeline.

## Run Worker isolation

`OPSMESH_RUN_WORKER_MODE=inline` executes runs as tasks in the worker process. `subprocess` launches one Python process per Run. `docker` uses a configured image and constrains CPU, memory, PID count, network, capabilities and root filesystem. The latter requires a host coordinator with access to a Docker daemon; the Compose example does not mount a host Docker socket.

```text
OPSMESH_RUN_WORKER_MODE=subprocess
OPSMESH_RUN_WORKER_ENVIRONMENT_ALLOWLIST=OPENAI_API_KEY
```

For Docker mode configure `OPSMESH_RUN_WORKER_IMAGE`, `OPSMESH_RUN_WORKER_CPU`, `OPSMESH_RUN_WORKER_MEMORY_MB`, `OPSMESH_RUN_WORKER_PIDS_LIMIT` and `OPSMESH_RUN_WORKER_NETWORK`. The image must contain the same opsmesh version and deployed plugins. Only explicitly allowed environment variables and base OS settings are inherited. Database URLs and data paths are passed as worker settings; they must be reachable inside the chosen network.

A full worker process and the SDK's sandbox are different boundaries. Do not assume Docker-inside-Docker works: a containerized worker needs a deliberately chosen SDK sandbox client. Trusted local SDK execution is opt-in, Unix-only, and intended for an already isolated worker. A Python subprocess alone is not an OS security sandbox.

## Operations

See [operations](../docs/operations.md), [HTTP guide](../docs/runtime-api.md), [migrations](../migrations/README.md) and [coverage](../docs/design/sdk-coverage.md). Use PostgreSQL for multiple processes, shared workspace storage, identical plugin versions, synchronized clocks, per-run idempotency keys, schema backups and artifact backups. Tests verify real SQLite transactions and a temporary PostgreSQL 17.11 server, including competing workers, outbox rollback, leases and application restart/approval recovery. A real PostgreSQL16/S3 offline backup and restore has also passed. Multi-node production topology and failover require deployment-specific validation.
