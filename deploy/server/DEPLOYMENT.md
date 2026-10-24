# Backend deployment

There are two supported production deployment modes: Docker (`--mode compose`) and
native Linux installation (`--mode systemd`). Native installation uses the frozen server
distribution and requires no system Python, pip or uv. Docker uses prebuilt API/Worker
images. Both modes start application processes through `opsmesh-server`.

OpsMesh runs three application processes. The API process authenticates requests, commits
business state and Temporal dispatch intents, and serves queries and SSE. The Worker
process runs registered Temporal Workflows and Activities and publishes committed intents.
The Gateway process owns QQ/DingTalk connections, durable ingress and authorized delivery.
PostgreSQL owns product data; Temporal owns durable execution history and task queues.
Redis provides caches and short-lived coordination, not a second execution queue.

Release acceptance must cover the shipped artifacts and their upgrade/recovery paths.
Source checks do not certify a production deployment. Source responsibilities and
verification scope are documented in [the repository README](../../README.md).

## Deployment components

| Component | Deployment and responsibility |
| --- | --- |
| API | Independent API image or `opsmesh-server api` under systemd |
| Worker | Independent Worker image or `opsmesh-server worker` under systemd |
| Gateway | API image with a gateway command, or `opsmesh-server gateway` under systemd |
| Business PostgreSQL | PostgreSQL 16, managed with Alembic |
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
interchangeable distributed sandbox hosts. This topology does not provide cross-machine
sandbox failover. Model and database-only Activities can be distributed through the
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

API, Worker and Gateway must share the storage GID even when systemd assigns different UIDs.
Working-directory storage creates setgid directories and group-writable files.
Compute images require Python 3 and `setfacl` (`acl` on Debian), and workspace mounts
must support POSIX ACLs. Runtime initializes ACLs through the native SDK container
as `0:0` before making an environment ready; only the Worker has this authority.
Existing private storage needs an ownership/permission migration by its owner or
an administrator. See [resource storage](../../src/opsmesh/resources/README.md).

Source checkout commands belong to [development setup](../../CONTRIBUTING.md).
The Worker is a long-running process. Production migrations are an explicit
`opsmesh-server migrate` operation before application startup.

## VPS layout and systemd services

The installer defaults to systemd for API/Worker/Gateway. The frozen release supplies the executable
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
`opsmesh-gateway.service` runs as `opsmesh-gateway` without Docker authority.
Only the Worker receives Docker authority. The API process must not be able to control the
Docker daemon. Do not make immutable release directories writable. Host release operations
run only when an administrator invokes the independent operator CLI.

## Production Compose

`--mode compose` uses the same signed release contract and independent API, Worker and
compute image digests. `deploy/server/compose.yml` includes persistent Temporal. The
installer generates a separate random Temporal database password. Only the Worker mounts
the Docker socket. Product storage persists outside releases.

Migration `0004_native_gateway` disables old channel configurations and retains their data.
Reconfigure QQ/DingTalk credentials and verify external identities before rebuilding bindings
and routes. See [Gateway](../../src/opsmesh/gateway/README.md). Stop API, Worker and Gateway
before a coordinated backup; release operations include all three application processes.

The production health check uses `/api/v1/health/ready`; its path must match a customized
`OPSMESH_API_PREFIX`. Business PostgreSQL/Redis and Temporal have separate persistent
volumes. Temporal schema management is separate from Alembic.

## Configuration and capacity

The standard API and Worker accept `OPSMESH_AGENT_CATALOG_PATH`, pointing to one
shared JSON file based on [agent-catalog.example.json](agent-catalog.example.json).
Set the actual provider URL, protocol, model IDs and output/context bounds for the
selected model. `credential_ref` uses `env:VARIABLE`; provide that variable to both
processes through the service environment. Direct Python launches must export it
in the shell; Pydantic reading `.env` does not export arbitrary provider variables.

For managed deployments, place the catalog at
`${OPSMESH_ROOT}/data/storage/config/agents.json`, owned by the API UID and shared
service GID, with directory mode `2750` and file mode `0640`. Compose mounts that
same absolute storage path in both processes; local Compose uses
`OPSMESH_DOCKER_STORAGE_ROOT`. Backups include the file with product storage.
The file seeds missing database entries at startup; it never overwrites online
configuration changes. Manage Agent/Provider drafts, releases and rollback through
`/agent-catalog`; credentials may also use encrypted `secret:identifier` records.
Published changes take effect for new instances or admitted Runs without restarting
API or Worker. Without a seed file, configure the catalog through the API before
creating executable instances. Invalid seed declarations fail startup.

Upgrade Alembic to `0005_platform_management` before starting this release's API
and Worker. Native Temporal Schedules and evaluation experiments require the
Worker and transactional Outbox dispatcher. Configuration, retry deadlines, SSE,
webhook signing, evaluation adapters and monetary budgets are described in
[platform management](../../docs/platform-management.md).

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
| `OPSMESH_TEMPORAL_DISPATCH_CONCURRENCY` | 16 | Concurrent Outbox deliveries |
| `OPSMESH_TEMPORAL_OUTBOX_DATABASE_POOL_SIZE` | 4 | Short-transaction async dispatch pool |
| `OPSMESH_TEMPORAL_SHUTDOWN_GRACE_SECONDS` | 120 | Native Worker graceful shutdown |
| `OPSMESH_MODEL_REQUEST_TIMEOUT_SECONDS` | 600 | One model request, independent of MCP |
| `OPSMESH_MCP_TOOL_TIMEOUT_SECONDS` | 30 | MCP operation timeout |

Async Activities run as asyncio tasks; blocking Activities use the bounded thread executor.
External channel adapters and business integrations call the public HTTP API.
Trusted administrator-installed system plugins use `opsmesh-sdk` declarations and the
shared plugin host's generation reload. The API needs write access to the package root;
Workers need read access. All hosts must share the package/data roots and encryption keys.
Core APIs provide Agent/Workflow submission, private sessions, run status/results and
tool/MCP/Skill publication. Plugins do not restore channel inbox/delivery business tables.
Durable Workflow waiting holds no Activity slot. Tune using model rate limits, database
pools, tool latency and measured workloads. Multiple machines register compatible Workers
on the same queue; there is no custom Worker lease or heartbeat table. Unknown external
write outcomes require explicit reconciliation.

Keep each host's `OPSMESH_TEMPORAL_WORKER_IDENTITY` unique. The installer generates it once.
Stop the whole fleet before distributed backups, schema changes or restoration. A new remote
Worker can register during an operator action, and stopped pollers may remain visible briefly
in Temporal metadata.

## Migrations, shutdown and recovery

Apply `opsmesh-server migrate` before starting the release. The immutable migration chain
includes the initial schema and subsequent execution/workflow instance migrations. Do not run incompatible
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
`deploy/server/check.sh` checks native installation; optional monitoring requires `jq` and uses
`OPSMESH_SMOKE_MONITORING=true`.

`scripts/check_runtime_image.py` checks SDK lifecycle, file I/O and an actual MCP stdio
round trip on a disposable Docker host. Native and managed delivery checks exercise a
published workflow, its binding instance and a Run through the current API. These are
focused checks; complete Agent/MCP/approval acceptance coverage remains incomplete.
See [current test coverage](../../tests/README.md). Skipped external tests do not prove a real model call.

### Credential encryption keys and proxy trust

`OPSMESH_CREDENTIAL_ENCRYPTION_KEY` contains a URL-safe base64 encoded random 32-byte
Fernet key. Generate a new installation key with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
`OPSMESH_CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS` maps key IDs to native Fernet keys.
Keep historical keys until their ciphertexts have been explicitly re-encrypted.

Model credentials belong to the configured credential resolver; no old provider-table
rotation command is exposed. `opsmesh-server cleanup-login-sessions --before 2026-10-21T00:00:00+00:00`
removes expired logins before a timezone-aware cutoff. Each batch writes its audit in the
same transaction; a failed batch can be retried by invoking the command again.
Keep old keys for unresolved encrypted payloads, Temporal history and retained backups.
Finishing these batches does not authorize deleting all historical keys.

Forwarded client IPs require both `OPSMESH_TRUSTED_PROXY_HOPS` and
`OPSMESH_TRUSTED_PROXY_NETWORKS` (a JSON list of exact proxy IPs/CIDRs).
Only include actual reverse proxy addresses. A direct peer or intermediary outside
those networks makes the application ignore the forwarded chain.
Uvicorn proxy-header rewriting is disabled so the application validates the original
socket peer itself.

### Plugin Packages and Sandbox Environments

The plugin host installs immutable ZIP versions and validates registered tools, MCP and
Skills through resource adapters. Installation validation executes trusted setup code;
archive-only checks are available through `opsmesh-plugin check`. MCP factories connect
when selected by an Agent runtime. Operators configure approved sandbox images
with `OPSMESH_EXECUTION_SANDBOX_IMAGES`; an empty list refuses environment creation.

Only the Worker needs access to Docker. Keep the daemon on a dedicated execution host;
uploaded tools and MCP processes receive no Docker socket, model Key or login Session.
Sandbox networking uses the underlying default behavior; core does not configure network
rules, DNS pinning or target allowlists.

Host backup/release maintenance requires closed MCP/sandbox environments and terminal,
reconciled tasks. It refuses to snapshot unresolved external effects. External adapters
must coordinate their own shutdown and persistence before a platform snapshot.
