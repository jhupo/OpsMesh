# Deployment

Linux installation defaults to a native Python environment and a systemd service. From a source checkout containing [install.sh](install.sh), run:

```sh
sudo bash deploy/install.sh
sudo systemctl status opsmesh.service --no-pager
sudo journalctl -u opsmesh.service -f
```

The default uses SQLite and one service for the HTTP API and background coordinator. Configuration and persistent data live in `/etc/opsmesh/opsmesh.env` and `/var/lib/opsmesh`. Docker is an explicit alternative: `sudo bash deploy/install.sh --mode docker`. The installer requires Linux and does not install Windows services. See the [Linux guide](linux.md) for requirements, flags, upgrades, configuration and maintenance commands. The original `v0.3.0` archive predates this installer.

`Dockerfile` and `compose.yaml` provide API/Worker/PostgreSQL/Redis services. A separate Linux test host has validated actual Docker isolation/cancellation, mutual-TLS remote sandboxes and per-run Jobs on K3s; those historical checks do not replace verification of the installer. Kubernetes setup and RBAC are documented in [kubernetes](kubernetes/README.md).

Kubernetes is optional. A single workstation does not need a cluster: start with `opsmesh serve`, or use Compose when PostgreSQL and Redis are wanted. Use the Kubernetes examples only when deploying to an existing cluster or needing per-Run Jobs; multiple services alone do not require Kubernetes.

## Manual Compose deployment

For a manual deployment without the installer, copy `.env.example` to `.env`, then configure a database password and an async URL with the Compose hostname `postgres`. Percent-encode special characters in URL credentials; the PostgreSQL password remains its original value. The `init` command prompts for a first administrator password (12+ characters).

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

Do not put `OPSMESH_ADMIN_PASSWORD` in the persistent environment file. For unattended initialization, pass the inherited variable by name only to a one-off container: `docker compose ... run --rm -e OPSMESH_ADMIN_PASSWORD api init`, then unset it. The installer handles this lifecycle when given a protected password file. PostgreSQL and workspace data use named volumes; ordinary `down` retains them. Migrations are explicit: after backing up and reviewing an upgrade, stop API and Worker, then run `docker compose ... run --rm --no-deps api migrate` before starting the new application version. Startup validates schema and does not silently modify it.

The container runs as UID/GID 10001. Only the API is published, by default at `127.0.0.1:8000`; PostgreSQL and Redis have no host port bindings. `OPSMESH_BIND_HOST` and `OPSMESH_PORT` control the host API binding. Configure an authenticated reverse proxy/TLS and suitable access controls for external use. The image installs dependencies from uv.lock with frozen resolution, including the optional OpenAI SDK. Pin reviewed base-image digests in your release pipeline.

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
