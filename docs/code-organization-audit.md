# Backend application organization audit

## Scope and acceptance

This consolidation covers package ownership throughout backend/app. It does not claim that every
feature in the product roadmap is complete. Source moves must update production imports, test
imports, dynamic patch targets, architecture gates and documentation together, without aliases.

## Current ownership

| Area | Current owner | Change |
| --- | --- | --- |
| Runtime models, backends, pools and lifecycle | execution/runtime | Former runtime_manager and runtimes share one owner. |
| Placement quotas and reservations | execution/runtime/space_* | Former runtime_spaces and runtime/spaces, now direct modules. |
| Audit, costs, traces and notification delivery | observability | Direct modules, with explicit audit_, cost_ and notification_ names. |
| Object storage, file metadata and artifact persistence | workspace/storage | Former files and artifacts; one byte-storage boundary. |
| Project snapshots, staging policy and export metadata | workspace/projects | Project-domain policy remains separate from storage drivers. |
| Provider SDK implementation and helpers | agents/runtime/providers | OpenAI helpers and Claude runner no longer have separate sibling packages. |
| Vendor-neutral execution environment | agents/runtime/runtime | Former sandbox; distinct from the infrastructure runtime resource owner. |
| Run execution and state application | orchestration/runs | Includes runtime authorization and execution state helpers. |
| Plan validation, scheduling and step lifecycle | orchestration/workflows | Former planning, policies, steps and scheduler micro-packages consolidated. |
| Authorized request construction and provider gateway | orchestration/requests | Former run_request and models_layer; these are services, not database models. |
| Orchestration database entities | orchestration/models.py | Kept as a module; creating a one-file models package would add needless depth. |
| Planning attempts, feasibility, ownership and project plans | orchestration/workflows/plan_* | Former top-level planning; one orchestration owner for automatic and user-authored work. |

### Platform foundations and shared helpers

`backend/app/platform/common` is the single owner for cross-domain, provider-neutral foundations:
configuration, logging, metrics, request/trace context, pagination contracts, typed value
normalization, resource sizing, executors, and maintenance primitives. These modules deliberately
remain small when they define a stable contract used by several domains; they are not a generic
catch-all package. There is no root-level `utils.py`. Helpers belong in the narrowest typed module
that owns their behavior (for example `common/typing.py` for value coercion and
`common/trace_context.py` for propagation). This keeps imports discoverable and prevents unrelated
business logic from accumulating in a dumping ground.

Platform persistence and security adapters remain nested under `platform/db`, `platform/redis`,
`platform/security`, `platform/secrets`, `platform/auth`, `platform/identity`, `platform/admin`,
and `platform/integrations/webhooks`. They are infrastructure boundaries, not application-level
utility folders.

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

- capabilities/mcp is an SDK protocol/execution boundary.
- API routes and schema groups retain their authentication and transport grouping.
- auth, identity, security, secrets, db, self_hosted and workspaces are separate security or
  lifecycle owners; the target tree was not an instruction to erase these domains.

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
