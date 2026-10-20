# Backend deployment

OpsMesh runs two application processes. The API process authenticates requests, commits
business state and Temporal dispatch intents, and serves queries and SSE. The Worker
process runs registered Temporal Workflows and Activities and publishes committed intents.
PostgreSQL owns product data; Temporal owns durable execution history and task queues.
Redis provides caches and short-lived coordination, not a second execution queue.

The Temporal refactor is undergoing integration acceptance on this branch. Code changes,
local verification and production deployment are distinct states. Source responsibilities
and verification commands are documented in [the repository README](../../README.md).

## Deployment components

| Component | Deployment and responsibility |
| --- | --- |
| API | Independent API image or `opsmesh-server api` under systemd |
| Worker | Independent Worker image or `opsmesh-server worker` under systemd |
| Business PostgreSQL | pgvector-enabled PostgreSQL, managed with Alembic |
| Redis | Caches, rate limits and short-lived derived state |
| Temporal | Persistent Temporal Server and namespace; native schema tools |
| Compute image | Environment used by the SDK Docker sandbox backend; no OpsMesh RPC daemon |

API/Worker are built from `deploy/images/Dockerfile` using the `api` and `worker` targets.
`deploy/images/Dockerfile.runtime` builds the compute environment. There is no mandatory
model gateway, local registry or permanent OpsMesh Runtime service. Native SDK sandboxes
are backend instances with their own lifecycle; this does not promise one container for all
execution. Creating an Agent profile does not create a container.

The installed backend is the SDK Docker provider. Its container state belongs to a Docker
daemon. Workers sharing sandbox Activities must use the same reachable daemon; distributed
snapshots require shared object storage. Independent local Docker daemons are not
interchangeable distributed sandbox hosts. Cross-machine sandbox failover has not been
accepted on this branch. Model and database-only Activities can be distributed through the
same Temporal task queue without a sandbox.

## Local development

```sh
uv sync --frozen --all-groups
cp deploy/local/env.example .env
docker build -f deploy/images/Dockerfile.runtime -t opsmesh-runtime:local .
docker compose -f deploy/local/compose.yml up --build
```

The Compose includes `deploy/temporal/compose.yml`: persistent Temporal PostgreSQL,
one-shot native schema and namespace initialization, then application services. API defaults
to port 8000; Temporal gRPC is bound to loopback port 7233. Initialization jobs exit after
completion. Runtime records select a native backend and validated options; they do not hold
old connector credentials, allocation slots or pool metadata.

Local Compose initializes `OPSMESH_DOCKER_STORAGE_ROOT` (default
`/var/lib/opsmesh-local/storage`) for UID/GID 10001. API and Worker mount this directory at
the same absolute host path, allowing native sandbox containers to bind it through the
Docker daemon. Server Compose applies the same rule to `${OPSMESH_ROOT}/data/storage`.

To run code directly, provision PostgreSQL, Redis and persistent Temporal first:

```sh
uv run alembic upgrade head
uv run uvicorn opsmesh.bootstrap.api:create_app --factory --no-proxy-headers --host 127.0.0.1 --port 8000
uv run python -m opsmesh.bootstrap.worker_cli
```

The Worker is a long-running process. Retired `worker --once`, Redis consumers and
self-hosted connector commands are removed.

## VPS layout and systemd services

The installer defaults to systemd for API/Worker. The self-contained release supplies Python
and locked dependencies. Before installation, provision the business database, Redis,
persistent Temporal and its namespace, Docker for the installed SDK backend, `pg_dump`,
`pg_restore` and systemd. Use an HTTPS reverse proxy and bind the API to loopback.

```sh
sudo opsmesh --root /opt/opsmesh install --version <release-tag> --origin https://agents.example.com --mode systemd
```

| Path | Purpose |
| --- | --- |
| `/opt/opsmesh/.env` | Persistent service configuration and secrets |
| `/opt/opsmesh/releases/` | Immutable, attested server releases |
| `/opt/opsmesh/current` | Atomically selected release |
| `/opt/opsmesh/data/storage` | Persistent product files |

`opsmesh-api.service` runs as `opsmesh-api`; `opsmesh-worker.service` runs as `opsmesh-worker`.
Only the Worker receives Docker authority. The API process must not be able to control the
Docker daemon. Do not make immutable release directories writable. Host release operations
run only when an administrator invokes the independent operator CLI.

## Production Compose

`--mode compose` uses the same signed release contract and independent API, Worker and
compute image digests. `deploy/server/compose.yml` includes persistent Temporal. The
installer generates a separate random Temporal database password. Only the Worker mounts
the Docker socket. Product storage persists outside releases.

The production health check uses `/api/v1/health/ready`; its path must match a customized
`OPSMESH_API_PREFIX`. Business PostgreSQL/Redis and Temporal have separate persistent
volumes. Temporal schema management is separate from Alembic.

## Configuration and capacity

Start from [env.example](env.example). Set unique `OPSMESH_INVITATION_HASH_PEPPER` and
`OPSMESH_CREDENTIAL_ENCRYPTION_KEY`, configure CORS and keep
`OPSMESH_ENABLE_API_DOCS=false` in production. Approve the release's compute image in
Runtime policy before using sandbox-backed tools.

| Setting | Default | Meaning |
| --- | --- | --- |
| `OPSMESH_TEMPORAL_ADDRESS` | `localhost:7233` | Host Temporal endpoint; Compose uses `temporal:7233` |
| `OPSMESH_TEMPORAL_NAMESPACE` | `default` | Provisioned namespace |
| `OPSMESH_TEMPORAL_TASK_QUEUE` | `opsmesh` | Queue for compatible Workers |
| `OPSMESH_TEMPORAL_WORKER_IDENTITY` | SDK default | Explicit installation identity for each Worker |
| `OPSMESH_TEMPORAL_TLS_ENABLED` | `false` | Native Temporal TLS |
| `OPSMESH_TEMPORAL_API_KEY` | empty | Optional connection secret |
| `OPSMESH_TEMPORAL_WORKFLOW_CONCURRENCY` | 128 | Workflow task concurrency |
| `OPSMESH_TEMPORAL_ACTIVITY_CONCURRENCY` | 64 | Concurrent Activities |
| `OPSMESH_TEMPORAL_BLOCKING_ACTIVITY_WORKERS` | 64 | Lazy thread capacity; must cover the Activity concurrency budget |
| `OPSMESH_TEMPORAL_DISPATCH_CONCURRENCY` | 16 | Concurrent Outbox deliveries |
| `OPSMESH_TEMPORAL_OUTBOX_DATABASE_POOL_SIZE` | 4 | Short-transaction async dispatch pool |
| `OPSMESH_TEMPORAL_SHUTDOWN_GRACE_SECONDS` | 120 | Native Worker graceful shutdown |
| `OPSMESH_MODEL_REQUEST_TIMEOUT_SECONDS` | 600 | One model request, independent of MCP |
| `OPSMESH_MCP_TOOL_TIMEOUT_SECONDS` | 30 | MCP operation timeout |

Async Activities run as asyncio tasks; blocking Activities use the bounded thread executor.
Channel account/identity routing, durable Inbox/Delivery and delegated adapter contracts
are available. Vendor protocols come from separately installed extension-center packages.
Logical state export, verification and restore use the shared platform operation Workflow
and owner callbacks on the normal Worker queue; they have no separate job table or queue.
Durable Workflow waiting holds no Activity slot. Tune using model rate limits, database
pools, tool latency and measured workloads. Multiple machines register compatible Workers
on the same queue; there is no custom Worker lease or heartbeat table. Unknown external
write outcomes require explicit reconciliation.

Keep each host's `OPSMESH_TEMPORAL_WORKER_IDENTITY` unique. The installer generates it once.
Stop the whole fleet before distributed backups, schema changes or restoration. A new remote
Worker can register during an operator action, and stopped pollers may remain visible briefly
in Temporal metadata.

## Migrations, shutdown and recovery

Apply `opsmesh-server migrate` before starting the release. V4 creates a new product schema;
the archived reference implementation is not an upgrade source. Do not run incompatible
Workers against the new schema. Host deployment checks the release manifest's accepted
source revision before migration and verifies the resulting head. Rollback restores the
verified snapshot and previous attested release, with explicit data-loss acknowledgement.

SIGTERM stops dispatch and uses Temporal's native Worker shutdown grace period. Completed
Activity history survives restarts. Approval decisions are durable signals. SDK Session
receipts protect committed mutations from duplicate retries without storing a second history.

Host snapshots coordinate business PostgreSQL, Temporal persistence/visibility, product
files and configuration. Stop every application writer and the dedicated Temporal Server
for the snapshot boundary. Systemd recovery requires a configured dedicated Temporal unit
and its two database URLs; unmanaged shared services are rejected. See
[host operations](delivery-operations.md) for offline journals and recovery commands.

## Logs, health and optional monitoring

```sh
opsmesh --root /opt/opsmesh logs --service api
opsmesh --root /opt/opsmesh logs --service worker
curl --fail http://127.0.0.1:8000/api/v1/health/ready
```

Systemd uses journald. Compose API/Worker logs rotate at 10 MiB with three files. Readiness
checks business PostgreSQL, Redis and native Temporal service health; it does not certify a
model, MCP or sandbox execution. Use authenticated product diagnostics for those stages.

The pinned observability stack is optional and managed separately under
`deploy/server/monitoring`. OTLP tracing and logs may use an existing receiver instead.
See [logs, metrics and tracing](observability.md) for the supported metrics and alerts.
`scripts/server-smoke-test.sh` checks native installation; optional monitoring checks use
`OPSMESH_SMOKE_MONITORING=true`.

`scripts/check_runtime_image.py` checks SDK lifecycle, file I/O and an actual MCP stdio
round trip on a disposable Docker host. External inference is opt-in: inspect
`scripts/openai_gateway_smoke.py --dry-run` before `--allow-external-provider-call`.
The `openai_smoke` tests do not prove a real provider-dispatching runner when skipped.

### Credential encryption keys and proxy trust

`OPSMESH_CREDENTIAL_ENCRYPTION_KEY` contains a URL-safe base64 encoded random 32-byte
Fernet key. Generate a new installation key with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
`OPSMESH_CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS` maps key IDs to native Fernet keys.
Keep historical keys until their ciphertexts have been explicitly re-encrypted.

The platform operation owners `platform.model_connections` and `orchestration.webhooks`
support `rotate` for administrator model keys and Webhook secrets in checkpointed batches.
Keep old keys for unresolved encrypted payloads, Temporal history and retained backups.
Finishing these batches does not authorize deleting all historical keys.

Forwarded client IPs require both `OPSMESH_TRUSTED_PROXY_HOPS` and
`OPSMESH_TRUSTED_PROXY_NETWORKS` (a JSON list of exact proxy IPs/CIDRs).
Only include actual reverse proxy addresses. A direct peer or intermediary outside
those networks makes the application ignore the forwarded chain.
Uvicorn proxy-header rewriting is disabled so the application validates the original
socket peer itself.

### Extension Packages

Build the approved offline package builder from the same source and lockfile:

```bash
docker build --target extension-builder -f deploy/images/Dockerfile -t opsmesh-extension-builder:s8 .
docker image inspect opsmesh-extension-builder:s8 --format '{{.Id}}'
```

Set `OPSMESH_EXTENSION_BUILDER_IMAGE` to that approved image ID on the Worker's Docker
daemon. `OPSMESH_EXTENSION_SDK_WHEEL` points to the matching SDK wheel on the Worker;
the Worker image includes it under `/opt/opsmesh-extension-sdk`. Native host installations
must build the SDK wheel and configure its absolute path explicitly. Installation fails
when either artifact is missing. Only administrators may install executable packages.

Published packages carry standard `opsmesh.extensions` PyPA entry points and hashed offline
dependency wheels. Builds and entry point discovery run without network access as UID 65534.
Sandbox instances require an explicitly created native environment for the installed image;
managed_worker instances require an explicit resident deployment, with no environment ID.
CPU, memory and process limits are applied before invoking it. Package processes receive
no Docker socket, platform Session or database credentials. Keep the Docker daemon on a
dedicated sandbox host with the isolation policy required by the deployment.

Runtime containers and native sandboxes use their underlying default network behavior.
OpsMesh currently does not configure network rules, DNS pinning or target allowlists.

Start a managed instance with `POST /api/v1/extensions/instances/{id}/deployments`, passing
`expected_revision` and a stable `operation_id`; poll `GET /api/v1/extensions/deployments/{id}`
until ready. Stop with `POST /api/v1/extensions/deployments/{id}/stop` and `expected_revision`.
Installing, configuring or invoking an instance never implicitly starts a resident process.
The manifest bounds `execution.max_concurrency` (1–64); admission and effect receipts remain
in PostgreSQL. A lost process requires explicit stop/reconciliation and a new deployment;
it is never automatically restarted. Canceling one call does not stop other calls.

Platform drain and usage configuration close wait for resident deployments to stop. Resume does not
restart them. Uninstall also waits for stopped deployments and reconciled effects.
`host.jobs` and event subscriptions use the unique orchestration owner.
Uninstall drains unresolved calls and preserves state. Export state and request a cleanup
preview before explicitly deleting a namespace; unknown external effects require evidence.
