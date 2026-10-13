# Backend deployment

OpsMesh runs two application processes. The API process authenticates requests, commits
business state and Temporal dispatch intents, and serves queries and SSE. The Worker
process runs registered Temporal Workflows and Activities and publishes committed intents.
PostgreSQL owns product data; Temporal owns durable execution history and task queues.
Redis provides caches and short-lived coordination, not a second execution queue.

The Temporal refactor is undergoing integration acceptance on this branch. Code changes,
local verification and production deployment are distinct states; consult the acceptance
ledger in [the architecture document](../../docs/architecture.md).

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

To run code directly, provision PostgreSQL, Redis and persistent Temporal first:

```sh
uv run alembic upgrade head
uv run uvicorn opsmesh.main:create_app --factory --host 127.0.0.1 --port 8000
uv run python -m opsmesh.runtime.temporal.cli
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
| `/opt/opsmesh/updater` | Independently provisioned updater runtime |
| `/opt/opsmesh/updates` | Durable upgrade journals |

`opsmesh-api.service` runs as `opsmesh-api`; `opsmesh-worker.service` runs as `opsmesh-worker`.
Only the Worker receives Docker authority. The API process must not be able to control the
Docker daemon. Do not make immutable release directories writable. The root updater uses
the explicit update plan, backup and drain flow.

## Production Compose

`--mode compose` uses the same signed release contract and independent API, Worker and
compute image digests. `deploy/server/compose.yml` includes persistent Temporal. The
installer generates a separate random Temporal database password. Only the Worker mounts
the Docker socket. Product storage persists outside releases.

The production health check uses `/api/v1/health/ready`; its path must match a customized
`OPSMESH_API_PREFIX`. Business PostgreSQL/Redis and Temporal have separate persistent
volumes. Temporal schema management is separate from Alembic.

## Configuration and capacity

Start from [env.example](env.example). Set unique `OPSMESH_INTERNAL_API_TOKEN`,
`OPSMESH_PLATFORM_ADMIN_TOKEN`, `OPSMESH_TOKEN_HASH_PEPPER` and
`OPSMESH_CREDENTIAL_ENCRYPTION_SECRET`, configure CORS and keep
`OPSMESH_ENABLE_API_DOCS=false` in production. Approve the release's compute image in
Runtime policy before using sandbox-backed tools.

| Setting | Default | Meaning |
| --- | --- | --- |
| `OPSMESH_TEMPORAL_ADDRESS` | `localhost:7233` | Host Temporal endpoint; Compose uses `temporal:7233` |
| `OPSMESH_TEMPORAL_NAMESPACE` | `default` | Provisioned namespace |
| `OPSMESH_TEMPORAL_TASK_QUEUE` | `opsmesh` | Queue for compatible Workers |
| `OPSMESH_TEMPORAL_WORKER_IDENTITY` | SDK default | Explicit installation identity required for host-managed updates |
| `OPSMESH_TEMPORAL_TLS_ENABLED` | `false` | Native Temporal TLS |
| `OPSMESH_TEMPORAL_API_KEY` | empty | Optional connection secret |
| `OPSMESH_TEMPORAL_WORKFLOW_CONCURRENCY` | 128 | Workflow task concurrency |
| `OPSMESH_TEMPORAL_ACTIVITY_CONCURRENCY` | 64 | Concurrent Activities |
| `OPSMESH_TEMPORAL_ARCHIVE_ACTIVITY_CONCURRENCY` | 4 | ZIP and object storage Activities on `<queue>-archives` |
| `OPSMESH_TEMPORAL_BLOCKING_ACTIVITY_WORKERS` | 64 | Lazy thread capacity; must cover the Activity concurrency budget |
| `OPSMESH_TEMPORAL_DISPATCH_CONCURRENCY` | 16 | Concurrent Outbox deliveries |
| `OPSMESH_TEMPORAL_OUTBOX_DATABASE_POOL_SIZE` | 4 | Short-transaction async dispatch pool |
| `OPSMESH_TEMPORAL_SHUTDOWN_GRACE_SECONDS` | 120 | Native Worker graceful shutdown |
| `OPSMESH_MODEL_REQUEST_TIMEOUT_SECONDS` | 600 | One model request, independent of MCP |
| `OPSMESH_MCP_TOOL_TIMEOUT_SECONDS` | 30 | MCP operation timeout |

Async Activities run as asyncio tasks; blocking Activities use the bounded thread executor.
Channel connectors run as independently deployed remote services. OpsMesh does not host
plugin processes or reserve a separate plugin Worker queue; Temporal handles event admission
and reply delivery through the main execution Worker.
Archive export, verification and restore drills use another bounded Activity Worker in the
same process. Their authorization, dispatch and failure settlement use the main queue;
ZIP and storage work use the archive queue. Queued capacity waiting does not start the
archive Activity's execution timeout. These Workers do not add application containers.
Durable Workflow waiting holds no Activity slot. Tune using model rate limits, database
pools, tool latency and measured workloads. Multiple machines register compatible Workers
on the same queue; there is no custom Worker lease or heartbeat table. Unknown external
write outcomes require explicit reconciliation.

Keep each host's `OPSMESH_TEMPORAL_WORKER_IDENTITY` unique. The installer generates it once.
Host updates inspect native pollers on all three queues and reject unmanaged Worker identities.
This is an ownership check, not a cluster-wide write barrier: stop the whole fleet before
distributed backups, schema changes or restoration. A new remote Worker can register after
the poller query, and stopped pollers may remain visible briefly in Temporal metadata.

## Migrations, shutdown and recovery

Apply `opsmesh-server migrate` before starting the release. The execution migration requires
old active work to be settled before dropping retired tables. Do not run old Workers against
the new schema. Alembic provides explicit downgrade paths; production rollback follows an
approved release plan and backups, since downgrade can discard newly introduced data.

SIGTERM stops dispatch and uses Temporal's native Worker shutdown grace period. Completed
Activity history survives restarts. Approval decisions are durable signals. SDK Session
receipts protect committed mutations from duplicate retries without storing a second history.

The audit timestamp migration interprets old timestamps using the database's historical
`TimeZone`. Preserve that setting during migration; audit hashes are not rewritten. Validate
hashes against real historical data as well as empty-schema upgrade and downgrade.

## Logs, health and optional monitoring

```sh
opsmesh --root /opt/opsmesh logs --service api
opsmesh --root /opt/opsmesh logs --service worker
opsmesh --root /opt/opsmesh logs --service updater
curl --fail http://127.0.0.1:8000/api/v1/health/ready
```

Systemd uses journald. Compose API/Worker logs rotate at 10 MiB with three files. Readiness
checks business PostgreSQL, Redis, storage and the Temporal namespace; it does not certify a
model, MCP or sandbox execution. Use authenticated product diagnostics for those stages.

The pinned observability stack is optional and managed separately under
`deploy/server/monitoring`. OTLP tracing and logs may use an existing receiver instead.
`scripts/server-smoke-test.sh` checks native installation; optional monitoring checks use
`OPSMESH_SMOKE_MONITORING=true`.

`scripts/check_runtime_image.py` checks SDK lifecycle, file I/O and an actual MCP stdio
round trip on a disposable Docker host. External inference is opt-in: inspect
`scripts/openai_gateway_smoke.py --dry-run` before `--allow-external-provider-call`.
The `openai_smoke` tests do not prove a real provider-dispatching runner when skipped.
