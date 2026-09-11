# Backend application organization audit

## Scope and acceptance

This consolidation covers package ownership throughout backend/app. It does not claim that every
feature in the product roadmap is complete. Source moves must update production imports, test
imports, dynamic patch targets, architecture gates and documentation together, without aliases.

## Current ownership

| Area | Current owner | Change |
| --- | --- | --- |
| Runtime models, backends, pools and lifecycle | runtime | Former runtime_manager and runtimes share one owner. |
| Placement quotas and reservations | runtime/space_* | Former runtime_spaces and runtime/spaces, now direct modules. |
| Audit, costs, traces and notification delivery | observability | Direct modules, with explicit audit_, cost_ and notification_ names. |
| Object storage, file metadata and artifact persistence | storage | Former files and artifacts; one byte-storage boundary. |
| Project snapshots, staging policy and export metadata | projects | Project-domain policy remains separate from storage drivers. |
| Provider SDK implementation and helpers | agent_runtime/providers | OpenAI helpers and Claude runner no longer have separate sibling packages. |
| Vendor-neutral execution environment | agent_runtime/runtime | Former sandbox; distinct from the infrastructure runtime resource owner. |
| Run execution and state application | orchestration/runs | Includes runtime authorization and execution state helpers. |
| Plan validation, scheduling and step lifecycle | orchestration/workflows | Former planning, policies, steps and scheduler micro-packages consolidated. |
| Authorized request construction and provider gateway | orchestration/requests | Former run_request and models_layer; these are services, not database models. |
| Orchestration database entities | orchestration/models.py | Kept as a module; creating a one-file models package would add needless depth. |
| Planning attempts, feasibility, ownership and project plans | orchestration/workflows/plan_* | Former top-level planning; one orchestration owner for automatic and user-authored work. |

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

## Final plan acceptance

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
