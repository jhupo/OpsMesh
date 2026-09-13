# Backend application organization audit

## Scope and acceptance

This consolidation covers package ownership throughout backend/app. It does not claim that every
feature in the product roadmap is complete. Source moves must update production imports, test
imports, dynamic patch targets, architecture gates and documentation together, without aliases.

## Current ownership

| Area | Current owner | Change |
| --- | --- | --- |
| Runtime models, backends, pools and lifecycle | runtime/environment | Former runtime_manager and runtimes share one owner. |
| Placement quotas and reservations | runtime/environment/spaces | Former runtime_spaces and runtime/spaces, now direct modules. |
| Audit, costs, traces and notification delivery | observability | Direct modules, with explicit audit_, cost_ and notification_ names. |
| Object storage, file metadata and artifact persistence | domains/workspace/storage | Former files and artifacts; one byte-storage boundary. |
| Project snapshots, staging policy and export metadata | domains/workspace/projects | Project-domain policy remains separate from storage drivers. |
| Provider SDK implementation and helpers | domains/agents/runtime/providers | OpenAI helpers and Claude runner no longer have separate sibling packages. |
| Vendor-neutral execution environment | domains/agents/runtime/sandbox | Former sandbox; distinct from the infrastructure runtime resource owner. |
| Run execution and state application | domains/orchestration/runs | Includes runtime authorization and execution state helpers. |
| Plan validation, scheduling and step lifecycle | domains/orchestration/workflows | Former planning, policies, steps and scheduler micro-packages consolidated. |
| Authorized request construction and provider gateway | domains/orchestration/requests | Former run_request and models_layer; these are services, not database models. |
| Orchestration database entities | domains/orchestration/models.py | Kept as a module; creating a one-file models package would add needless depth. |
| Planning attempts, feasibility, ownership and project plans | domains/orchestration/workflows/plan_* | Former top-level planning; one orchestration owner for automatic and user-authored work. |

### Platform foundations and shared helpers

`backend/app/core/common` is the single owner for cross-domain, provider-neutral foundations:
configuration, logging, metrics, request/trace context, pagination contracts, typed value
normalization, resource sizing, executors, and maintenance primitives. These modules deliberately
remain small when they define a stable contract used by several domains; they are not a generic
catch-all package. There is no root-level `utils.py`. Helpers belong in the narrowest typed module
that owns their behavior (for example `common/typing.py` for value coercion and
`common/trace_context.py` for propagation). This keeps imports discoverable and prevents unrelated
business logic from accumulating in a dumping ground.

Core persistence and security adapters remain nested under `core/db`, `core/redis`,
`core/security`, `core/secrets`, `core/auth`, `core/identity`, `core/admin`, and
`core/integrations/webhooks`. They are infrastructure boundaries, not application-level utility
folders.

Task orchestration follows the same ownership rule. `domains/orchestration/tasks` keeps durable
task models, status transitions, the event bus, and public state services at its root. Control,
collaboration, delivery, execution, management, observation, and operator actions live in their
corresponding nested packages; filename prefixes such as `control_*` and `observation_*` are not
used to simulate package boundaries.

## Edge-domain consolidation

- Team project views are direct teams/project_* modules. Resource summaries now live with the
  response assembler and small record helpers with project_types. The package re-export is gone.
- Queue implementation is directly owned by workers: redis_queue, queue_contracts, queue_leases,
  queue_retries and the other queue_* modules. Filtering lives with queue queries, not a standalone
  nineteen-line module. Queue storage and worker-run leases remain distinct lifecycle concepts.
- Product-tool dispatch and file, memory, mailbox and event implementations are direct tools/product_*
  modules. Authorization contexts remain explicit; they are not merged into workspace CRUD services.
- Scheduled jobs are owned by workers/scheduled_jobs.py, scheduled_models.py, scheduled_types.py
  and schedules.py. Six service mixins and their internal plumbing protocol were removed in favor
  of a concrete service. Scheduling calculation remains pure and separate from transaction handling.
- Team project views and dashboards import canonical run statuses directly; duplicate status aliases
  were removed. Operations still reads scheduling/queue evidence but does not own execution logic.
- This batch consolidates 43 old source files into 28 direct domain modules and removes all four
  former directories. Public API paths, database table identities and tenant-scoped queries are
  unchanged. Workspaces remains the tenant owner; team project assembly is not a second tenant service.

## Retained boundaries

- domains/capabilities/mcp is an SDK protocol/execution boundary.
- API routes and schema groups retain their authentication and transport grouping.
- auth, identity, security, secrets, db, runtime/self_hosted and domains/workspace are separate
  security or lifecycle owners; the target tree was not an instruction to erase these domains.

## File-level review policy

The inventory for the current tree contains many implementation files because the product has
durable state, policy, and recovery contracts. A file is retained when it owns an independently
tested model, service, adapter, or lifecycle state machine; line count alone is not a reason to
merge it. The review removes dead modules, one-line forwarding modules, and constants that have no
independent ownership. Related behavior is grouped by function inside its domain (for example
runtime placement, queue operations, project I/O, and team execution), while large state machines
remain separate to keep transactions and failure semantics visible. API route aggregators are kept
only where they compose a real transport feature group. No compatibility aliases are used for
removed paths.

## Defects repaired during migration

The previous layout had unresolved imports into removed orchestration modules. The initial
health-test collection failed on orchestration.run_eligibility. Callers now use the actual owning
modules. Relative imports and dynamic test patch targets were updated too. The runs package's
lazy __getattr__ compatibility export was removed; callers import runs.service explicitly.

The static architecture test resolves every application import directly against source files,
without importing providers or requiring credentials. It catches removed absolute and relative
module targets. Existing import-linter rules continue to enforce SDK and infrastructure boundaries.

## Verification

Use focused architecture, health, user orchestration, runtime environment and project I/O tests
for this package migration. Provider contract and storage tests cover the other moved boundaries.
Passing these tests demonstrates the tested refactor paths, not release readiness or completion
of unrelated SDK capabilities. Full-suite release tests remain tag-only.

Validated in this change: 53 architecture/health/orchestration/runtime/project-I/O tests,
43 provider-contract/runtime/storage-boundary tests, 21 Claude/storage/notification tests,
and two final source-layout/import checks passed (119 checks in total). Ruff passes for app.
The initial five typing defects were subsequently repaired: AgentRunRequest carries a typed
product SandboxManifest, the OpenAI provider alone builds SandboxRunConfig, tracing has a valid
workflow name, and runtime metadata and Claude settings retain their appropriate boundary types.
The shared runtime package no longer imports a vendor SDK. Its unused manifest/session re-export
modules and mixed-vendor mapper were deleted. The provider-neutral import gate covers this package.

The edge-domain batch passed 60 focused queue/scheduled-job/product-tool/architecture/health tests.
Workers, teams and tools also pass focused mypy (151 source files).

## Directory consolidation acceptance (not file-level completion)

All seven consolidation directions in the original plan now have concrete owners above:
Agent providers/runtime, physical runtimes, observability, storage/projects, orchestration,
team/workspace ownership, and worker/operations ownership. Removed modules have no compatibility
aliases. The models.py file replaces the proposed one-file models package intentionally.
API route groups remain distinct transport/authentication boundaries, not arbitrary nesting.

An AST comparison of same-named functions spanning at least sixteen lines across app found four
exact duplicate groups. Runtime-space event writing now has one implementation, worker policy
creation/update has one service owner, and continuation prompt formatting belongs to the shared
Agent adapter base. The remaining TaskControl/DeliveryDecision audit helpers only adapt their
domain command to the same AuditService; keeping that adapter does not create a second audit
algorithm or persistence owner. This comparison is not a claim that all semantically similar code
is identical or should be merged.

Final verification includes full app mypy, Ruff, the seven import-linter contracts, source-path
checks, focused planner/runtime/API/provider tests, and the sandbox-manifest boundary regression.
Live-provider smoke tests remain explicitly skipped without credentials. No full pytest suite,
release tag, push, or remote publication is part of this directory-consolidation acceptance.

## File-level consolidation follow-up: 2026-09-12

The subsequent file merges were initially reported as complete too early. A cold import of the
Operations API failed: worker lifecycle buckets imported a helper back from the service, and
stale-run recovery actions imported their result contract back from the orchestrator. The pure
worker-routing helper now belongs to the bucket module; the recovery result belongs to the action
executor that produces it. Neither fix relies on a compatibility alias or deferred runtime import.

The new Teams execution-loop support module initially had no callers and duplicated four old
modules. Callers now use the consolidated module; the four superseded files are deleted. The
execution-overview member re-export module is also removed, with callers using the actual owners.

A related integration test found that QueuedRuntimeControl omitted execution_mode and pool_key
from create-job routing. It now accepts and persists these settings and includes the stored values
in the job. Worker validation remains strict; no missing-field fallback was added.

Validation for this follow-up: Operations API, team capacity, Runtime manager and existing
architecture tests passed (71 tests); four additional architecture checks cover fresh-process
imports and deleted Teams sources. The focused team execution-loop/overview selection passed
14 tests. App/test/script Ruff and app mypy passed. No full pytest suite or GitHub release run
was requested. These results cover this batch, not every possible future file consolidation.

Unrelated pre-existing workspace changes remain untouched; a successful commit does not imply a
clean workspace. The earlier clean-workspace statement was inaccurate.

## File-level consolidation follow-up: 2026-09-12 (continued)

Workers no longer carry a one-file base protocol package: the handler protocol now lives with the
worker handler registry, and the superseded `workers/job_handlers/base.py` source is deleted.
The Teams operating-context re-export was removed; callers import the concrete context service.
Workspace export format ownership now lives in the export schema contract instead of a standalone
constants module. The API redaction re-export was also removed so application code imports the
security redaction service directly; this keeps memory and schema code from depending on an API
compatibility layer. Architecture source-path checks now validate deleted files at their actual
`backend/app` locations (the earlier check accidentally prefixed all paths with `teams`).

Validation for this follow-up: 16 architecture tests, two focused workspace redaction tests,
full app Ruff, app mypy (960 files), and a cold import of 959 application modules passed. No
compatibility aliases or full-suite test run were introduced.

## File-level consolidation follow-up: 2026-09-13

The cross-domain run query audit found two copies of the same workspace-scoped SQL query for
active tasks by agent, plus two copies of run-to-task ownership validation. Both now belong to
`domains/orchestration/runs/queries.py`; team projections and workflow scheduling consume that
owner directly, so the repository and service layers do not retain forwarding wrappers.

The same audit found repeated UTC normalization helpers in authentication, cost accounting,
operations metrics/events, self-hosted maintenance, and the three-layer memory implementation.
All callers now use `core/common/values.py` for the canonical datetime normalization and lifecycle
rollup serialization. Team execution uses the shared string-list and de-duplication primitives;
the execution contract module retains only team-specific constants and typed records.

Self-hosted worker trust evaluation follows the same rule: enrollment/trust owns the canonical
`worker_trust_state` decision, while operations only assembles its response. The former duplicate
operations implementation was removed.

Run request construction applies the same ownership rule. Task/profile lookup and workspace
authorization checks are implemented once in `domains/orchestration/requests/authorization.py`;
the model request builder and persistent-session service call those functions directly instead of
maintaining parallel validation methods.

Run-event retrieval is likewise centralized in `domains/orchestration/runs/queries.py`: latest-event
projections and full event timelines share the same workspace-scoped query owner. Observation
repositories and timeline assembly no longer carry duplicate event-loading methods.

The immutable run authorization snapshot is also read through that run query boundary. Model
request construction, self-hosted worker policy, and MCP execution checks no longer each implement
their own snapshot extraction; MCP context now owns only its audit metadata projection.

The architecture gate now asserts these ownership rules, including the absence of local `_as_utc`
implementations and the presence of the canonical run-query/value modules. Focused architecture,
authentication, memory lifecycle, cost, operations, team-capacity and execution-loop tests passed;
Ruff and full application mypy passed. No compatibility aliases, full-suite run, release tag or
remote publication were introduced.

## File-level consolidation follow-up: 2026-09-13 (memory and runtime spaces)

The memory domain no longer uses one package directory for each small implementation. Authorization,
configuration, context, indexing, lifecycle, embedding, retrieval and memory-store modules now live
directly under `domains/agents/memory` with explicit names. Episodic, semantic and working memory
remain separate files because they own different persistence and promotion semantics; only the
artificial `access`, `configuration`, `context`, `embeddings`, `indexing`, `lifecycle`, `retrieval`
and `stores` package layers were removed. All application and test imports were updated directly;
no forwarding packages remain.

Runtime-space reservation accounting follows the same rule. Attachment, capacity, release and usage
implementations now live directly under `runtime/environment/spaces` as `reservation_*.py` modules.
The reservation subpackage was removed while the reservation state machine and quota transaction
boundaries were preserved. Callers use the concrete modules directly.

The architecture gate asserts the flattened memory owner and the absence of the reservations
subpackage. Focused memory, product-tool, workspace-file, runtime-space and architecture tests pass;
Ruff passes for the changed source. No database schema or public API behavior changed.

## File-level consolidation follow-up: 2026-09-13 (worker queue boundary)

The Redis worker queue no longer uses a package of mixins, scripts, serialization helpers and a
FastAPI dependency module. Enqueueing, leasing, retry/dead-letter handling, inspection and
serialization now live in the single cohesive `runtime/workers/queue.py` execution module. The
queue's public surface is unchanged (`RedisQueue` and `consume_once`), while the superseded
`runtime/workers/queue` package was deleted without aliases.

`get_worker_queue` is an HTTP dependency, so it now belongs to `api/dependencies.py`; runtime worker
code no longer imports FastAPI. Every application and test import was updated directly to the new
owners. This keeps the queue implementation in the data plane and transport dependency resolution
in the API boundary.

The queue retains one idempotency, lease, retry and workspace-filter implementation. No queue data
format, Redis key, retry policy or public API behavior changed. Focused Redis queue, worker
dependency, worker runner and architecture tests must pass before this batch is committed.
