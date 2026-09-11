# Backend application organization audit

## Scope and acceptance

This consolidation covers package ownership throughout backend/app. It does not claim that every
feature in the product roadmap is complete. Source moves must update production imports, test
imports, dynamic patch targets, architecture gates and documentation together, without aliases.

## Current ownership

| Area | Current owner | Change |
| --- | --- | --- |
| Runtime models, backends, pools and lifecycle | runtime | Former runtime_manager and runtimes share one owner. |
| Placement quotas and reservations | runtime/spaces | Former runtime_spaces; retained as a cohesive reservation lifecycle. |
| Audit, costs, traces and notification delivery | observability | Direct modules, with explicit audit_, cost_ and notification_ names. |
| Object storage, file metadata and artifact persistence | storage | Former files and artifacts; one byte-storage boundary. |
| Project snapshots, staging policy and export metadata | projects | Project-domain policy remains separate from storage drivers. |
| Provider SDK implementation and helpers | agent_runtime/providers | OpenAI helpers and Claude runner no longer have separate sibling packages. |
| Vendor-neutral execution environment | agent_runtime/runtime | Former sandbox; distinct from the infrastructure runtime resource owner. |
| Run execution and state application | orchestration/runs | Includes runtime authorization and execution state helpers. |
| Plan validation, scheduling and step lifecycle | orchestration/workflows | Former planning, policies, steps and scheduler micro-packages consolidated. |
| Authorized request construction and provider gateway | orchestration/requests | Former run_request and models_layer; these are services, not database models. |
| Orchestration database entities | orchestration/models.py | Kept as a module; creating a one-file models package would add needless depth. |

## Retained boundaries, reviewed rather than flattened blindly

- teams/project_space has twelve implementation modules for assembling team projects, staffing,
  resource matching and governance. It remains a cohesive application-service boundary.
- workers/queue has nine implementation modules for queue contracts, Redis scripts, leases,
  retries and consumption. It is an independently testable queue lifecycle.
- tools/product_tools has six implementation modules for authorized product-tool dispatch.
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
Focused mypy still reports five existing typing defects in the provider RunConfig/sandbox
boundary and request metadata dictionaries; this directory-only migration does not claim a
clean type-check or repair those runtime contracts.
