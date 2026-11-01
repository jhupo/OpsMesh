# Deployment

Deploy Linux with the dedicated `opsmesh-VERSION-linux.tar.gz` installation bundle. Replace `VERSION` with the actual release version and run from the extracted directory:

```sh
tar -xzf opsmesh-VERSION-linux.tar.gz
cd opsmesh-VERSION-linux
sha256sum --check SHA256SUMS
sudo bash install.sh
sudo systemctl status opsmesh.service --no-pager
sudo journalctl -u opsmesh.service -f
```

The default is native installation under systemd, using SQLite and one service for the HTTP API and background coordinator. Configuration and persistent data live in `/etc/opsmesh/opsmesh.env` and `/var/lib/opsmesh`. Select Docker explicitly with `sudo bash install.sh --mode docker`. This installer is Linux-only; it installs no Windows service. See the [Linux guide](linux.md) for requirements, upgrades, configuration and maintenance commands.

## Installation artifact

The bundle contains the application wheel under `packages/`, `requirements.lock` with dependency versions and hashes, installation helpers, systemd units, Compose/Dockerfile, `VERSION`, `SHA256SUMS`, a README and the license. It excludes Python interpreters, `.venv`, the development `src/` tree, tests, Git metadata and development configuration. A wheel is a standard Python installation artifact that can contain Python modules, not a standalone native executable or a source-hiding mechanism.

The native installer first checks for Python 3.11–3.14 that the service account can access. It reuses a suitable interpreter; otherwise it downloads managed Python 3.13. It creates a separate virtual environment, installs dependencies online using the hash-locked requirements and installs the included wheel. The bundle is not an offline dependency distribution and does not replace the system Python.

The original `v0.3.0` release has no Linux installation bundle; its historical assets remain unchanged. Developers can build the current bundle from the repository:

```sh
uv run python scripts/build_linux_release.py --output-dir dist/linux
```

Transfer the resulting `dist/linux/opsmesh-VERSION-linux.tar.gz` to the Linux host. The source checkout is a development/build input, not the production deployment payload. Native and Docker integration checks exercise the assembled bundle; consult the corresponding CI run for actual results.

## Optional Docker deployment

From the extracted bundle:

```sh
sudo bash install.sh --mode docker
```

Docker Engine and Docker Compose must already be installed. The image installs the wheel and locked dependencies from the bundle instead of copying the repository source. Building requires network access for base images and dependencies. Compose manages API, Worker, PostgreSQL and Redis with instance-specific configuration and persistent volumes. Only the API is published, by default at `127.0.0.1:8000`; PostgreSQL and Redis have no host port bindings. `OPSMESH_BIND_HOST` and `OPSMESH_PORT` control the host API binding. The application container runs as UID/GID 10001.

Do not put `OPSMESH_ADMIN_PASSWORD` in the persistent environment file. Interactive initialization is the default. For automation, pass one password line directly from a secret manager through `--admin-password-stdin`; an existing protected file is also supported through `--admin-password-file`. These options are mutually exclusive. The password is passed only to the initialization process, not to long-lived services.

Repeated installation preserves configuration and data. Use `--upgrade` from the new bundle for explicit schema migration after backing up and reviewing the change. The installer stops writing services for migration and verifies the schema before starting the replacement. Ordinary Compose shutdown retains volumes; do not use `down --volumes` for upgrades. Exact Compose management commands with the installed project/configuration paths are in the [Linux guide](linux.md).

Configure an authenticated reverse proxy/TLS for external use. Pin reviewed base-image digests in your release pipeline. Kubernetes remains optional and is not installed by either mode; its deployment examples are [separate](kubernetes/README.md).

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
