# Backend Architecture Gates

Status: implemented, 2026-09-10. This is the quality-baseline stage after accepted rc9 delivery;
it does not reopen deployment acceptance or introduce another release tag.

## Ownership changes

`core.pagination.PageParams` is the single transport-independent pagination input for repositories,
application services, and routes. HTTP query validation and response envelopes remain in
`api.pagination`. All callers use the new owner directly; the former API import is not re-exported.
Limits, offsets, response fields, authorization queries, and database behavior are unchanged.

HTTP request context, security headers, and rate limiting now belong to `api.middleware`, composed
by `main`. Shared core no longer imports HTTP error responses. The old core module is removed.

## Enforced contracts

| Contract | Enforcement |
| --- | --- |
| HTTP transport ownership | Only API modules and the API entry point may directly import routes or middleware |
| Shared infrastructure | Core, database, and Redis cannot reach API modules, including indirect imports |
| Pagination input | No HTTP framework, API, or SQLAlchemy dependency |
| Production code | No direct or indirect pytest dependency |
| Docker SDK | Only the managed Docker adapter may directly import the SDK |
| S3 SDK | Only the storage adapter may directly import Boto3 |
| Agent runtime contract | No direct or indirect provider SDK or API dependency, including type-checking imports |

Configuration lives in `pyproject.toml`. There are no ignored-import exemptions or compatibility
modules. Package markers in `runtime_manager` and `api/services` ensure those modules are included
in the static graph. A test requires every application Python directory to remain a regular package.

## Dependency decision

Adopt [Import Linter](https://import-linter.readthedocs.io/en/stable/) as a **development-only**
dependency (`>=2.11,<3`, locked to 2.15). Its BSD-licensed upstream implements static import-graph
analysis through Grimp, with protected and forbidden contracts and support for the project's Python
versions. No web UI extra is installed. There is no runtime service, credential, database, or network
requirement to run the check, and production images/server dependency installs exclude it.

Ruff remains responsible for local import style; it does not replace transitive architecture rules.
A custom AST/regex dependency scanner was rejected because it would duplicate graph discovery,
relative import resolution, indirect paths, and type-checking semantics. A general architecture
framework or external service would add operational cost without helping these concrete contracts.
OpsMesh owns the policy configuration and acceptance tests, not the graph algorithm.

## Validation and operation

```bash
uv sync --frozen --all-groups
uv run lint-imports --no-cache
uv run pytest backend/tests/test_architecture.py backend/tests/test_pagination.py
```

Backend CI checks every master push and pull request; the release gate runs the same command before
the complete suite and artifact build. Violations fail the job without `continue-on-error`.

Tests invoke the installed upstream CLI on a temporary copy of actual application source. Eight
intentional violations cover each rule, indirect imports, and type-checking-only imports. The real
working tree is never modified by those negative tests. Pagination tests cover unchanged HTTP
validation/defaults and scoped SQL pagination/counting; middleware behavior is covered by health,
rate-limit, metrics, and telemetry tests. No schema or PostgreSQL-specific behavior changes here.

## Explicit limits and next stage

Static imports cannot prove runtime authorization, prevent dynamic `importlib` lookups, or establish
safe command execution by themselves. Existing tenant-denial and isolated-runtime tests remain
required. Protected SDK contracts constrain direct SDK imports, not every product service calling
an adapter through its public interface.

Some application services still consume `api.schemas`, and export/read application services remain
under `api/services`. This stage does **not** assert universal domain independence, acyclic service
dependencies, or complete facade consolidation. Moving every DTO/service is a distinct architectural
migration, not an ignored-import workaround.

After this bounded gate is accepted, the next product subsystem is knowledge-source registration,
durable ingestion, permission-aware retrieval, and citations, reusing existing workspace files,
memory indexing, pgvector, worker recovery, resource grants, and audit. Those features remain planned.
