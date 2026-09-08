# OpsMesh Agent Guide

## Mission

OpsMesh is an open-source enterprise agent framework. It provides the control plane around agent
SDKs: workspaces, authorization, agent teams, durable tasks, workers, capabilities, isolated
execution, credentials, approvals, artifacts, operations, and auditability.

The backend is the active product surface. The frontend is intentionally absent while its product
flows and design system remain undecided. Do not scaffold a frontend, select a frontend stack, or
add UI-specific backend coupling unless the user explicitly requests frontend work.

## Read First

Use these documents as the project map instead of guessing:

- `README.md`: product position, current capabilities, target architecture, and near-term goals.
- `docs/architecture.md`: system boundaries and durable-state model.
- `docs/backend-service-architecture.md`: service and dependency boundaries.
- `docs/agent-runtime-contract.md`: boundary around the supported provider Agent SDKs.
- `docs/capabilities-and-runtime.md`: skill, tool, MCP, and runtime model.
- `docs/isolation-and-security.md`: mandatory tenant and runtime isolation rules.
- `docs/threat-model.md`: threats and required mitigations.
- `docs/open-source-sdk-strategy.md`: dependency adoption and replacement strategy.
- `docs/backend-completion-plan.md`: detailed implementation status, not a substitute for code.

Treat code, migrations, and tests as evidence of current behavior. Documents containing words such
as "should", "planned", or "future" describe intent and must not be represented as implemented.

## Non-Negotiable Invariants

1. A workspace is the tenant boundary for data, agents, tasks, files, memory, tools, and runtimes.
2. Never authorize or load a workspace-owned resource by resource ID alone. Include workspace scope
   in API, service, query, worker, cache, storage, and tool paths.
3. Postgres is the source of truth for durable state. Redis is for queues, locks, pub-sub,
   idempotency windows, and short-lived derived data.
4. API requests must not execute long-running agent work inline. Persist intent and enqueue work.
5. User-controlled or agent-controlled code must never run in the API or worker host process. Use
   Docker, an approved hosted sandbox, or a trusted self-hosted isolated runtime.
6. Models propose plans and actions. Product services validate authorization, policy, state
   transitions, quotas, and side effects.
7. Credentials are decrypted only at the narrow execution boundary. Never log, serialize, return,
   or persist raw secrets, complete authorization headers, or secret-bearing provider URLs.
8. Risky actions fail closed and produce approval, audit, or security evidence as appropriate.
9. Product audit events remain durable and independent of model-provider tracing.
10. Public marketplace resources require review. Private workspace resources remain private and
    workspace-scoped by default.

## Open-Source First Policy

Do not implement a protocol, infrastructure client, retry engine, telemetry format, authorization
engine, workflow engine, parser, or storage driver until existing maintained libraries have been
evaluated.

Before adding custom infrastructure code:

1. Search the current dependency graph and official upstream SDKs.
2. Compare protocol coverage, security model, maintenance, license, Python support, async support,
   typing quality, observability, failure semantics, and operational cost.
3. Record the decision and the rejected alternatives in the relevant design document or ADR when
   the dependency changes an architectural boundary.
4. Wrap third-party SDKs behind a small OpsMesh-owned protocol or adapter at the infrastructure
   boundary. Domain services must not depend on vendor-specific response objects.
5. Add contract tests for success, timeout, cancellation, malformed responses, secret redaction,
   tenant isolation, and provider/version behavior.
6. Delete superseded custom code after migration. Do not leave two default implementations without
   a narrowly scoped migration step.

When an adopted SDK already exposes the required capability, call its documented public interface
directly. Do not recreate equivalent protocol, transport, execution, serialization, retry, or
telemetry logic. An OpsMesh adapter may translate product-owned contracts, enforce authorization,
redaction, limits, and audit evidence, but it must delegate the underlying capability to the SDK.

Compatibility code is prohibited. When a contract changes, update its callers, tests, migrations,
and documentation in the same change. Do not add legacy aliases, deprecated endpoint shims,
version branches, silent fallbacks, duplicate implementations, or adapters whose only purpose is
to preserve an old internal behavior.

Preferred candidates and their adoption order are documented in
`docs/open-source-sdk-strategy.md`. In particular:

- Use the Python OpenAI Agents SDK (`openai-agents`, upstream `openai/openai-agents-python`) and the
  official Claude Agent SDK (`claude-agent-sdk`, upstream `anthropics/claude-agent-sdk-python`) as
  provider execution cores. Use their public APIs for agent turns, tools, sessions, run state, and
  HITL where their stable contracts satisfy product requirements.
- Prefer the official MCP Python SDK and Agents SDK MCP integrations over custom JSON-RPC and
  transport implementations.
- Prefer Docker SDK for Python over constructing Docker CLI commands.
- Prefer OpenTelemetry and the official Prometheus client over custom telemetry protocols.
- Prefer pgvector with existing Postgres full-text search before introducing another vector store.
- Use Authlib for future OAuth/OIDC integration rather than building an identity provider.
- Evaluate Temporal with a real OpsMesh workflow before replacing durable orchestration.
- Evaluate OpenFGA or OPA only against concrete authorization or policy requirements.
- Defer Any-LLM and LiteLLM Agents SDK integrations. Add other model providers only through the
  product-owned provider contract after an SDK path is stable and contract-tested.

Avoid adding LangChain, LlamaIndex, or another general agent framework alongside the provider SDKs.
A focused library is acceptable when it fills a documented gap without duplicating those SDKs and
the OpsMesh control plane.

## Architecture And Code Quality

- Keep dependencies directed from API routes to application/domain services to infrastructure
  adapters. Infrastructure must not import API route modules.
- Keep FastAPI routes thin: validate transport input, resolve authenticated context, call a service,
  and map the result. Business rules belong in services or domain helpers.
- Define explicit typed contracts at module boundaries. Prefer Pydantic models, dataclasses,
  protocols, and enums over unstructured dictionaries when the shape is stable.
- Keep database transactions and state transitions visible. Do not hide commits across unrelated
  helper layers or make external calls while holding a transaction unless required and documented.
- Separate pure policy/decision logic from I/O. Pure logic should be deterministic and easy to test.
- Prefer cohesive modules with clear ownership. Split a module when responsibilities diverge, but do
  not create long chains of one-line pass-through wrappers or generic `utils` dumping grounds.
- Reuse an existing domain service, repository, schema, error type, redaction helper, and event
  writer before introducing a parallel abstraction.
- Use an adopted SDK's existing public interface directly when it provides the required capability;
  keep custom code limited to product-specific policy, contract translation, and evidence.
- Avoid circular imports and import-time side effects. Construct infrastructure dependencies at the
  application or worker composition boundary.
- Do not write compatibility code. Replace obsolete contracts directly and update all in-repository
  callers in the same change; never hide an incomplete refactor behind aliases or fallback paths.
- Use Alembic for every schema change. Preserve downgrade behavior and cross-workspace constraints.
- Keep public API error envelopes stable and free of internal exception details or secrets.
- Add comments only for decisions and non-obvious invariants, not narration of straightforward code.
- Keep changes narrowly scoped. Do not mix feature work, architecture cleanup, and formatting churn
  unless they are required for one coherent migration.

## Repository Map

- `backend/app/api`: API routes, schemas, dependencies, and transport-facing services.
- `backend/app/agent_runtime`: Agents SDK adapters, sessions, tools, and runtime contracts.
- `backend/app/orchestration`: run construction, authorization snapshots, and lifecycle.
- `backend/app/agents`, `backend/app/teams`, `backend/app/tasks`: core product domains.
- `backend/app/capabilities`: skills, MCP, credentials, policy, diagnostics, and execution.
- `backend/app/runtime_manager`: Docker runtime lifecycle and host-resource cleanup.
- `backend/app/runtime_spaces`: placement, quotas, reservations, and leases.
- `backend/app/self_hosted`: user-owned runtime protocol, trust, and job lifecycle.
- `backend/app/workers`: queues, handlers, worker lifecycle, and maintenance.
- `backend/migrations`: Alembic migrations.
- `backend/tests`: unit and integration-style tests.
- `deploy`: VPS/systemd and monitoring assets.
- `docs`: architecture and operating documentation.

## Development Workflow

1. Inspect the affected route, service, model, migration, worker path, and tests before editing.
2. Establish the current test baseline. Do not attribute pre-existing failures to the new change.
3. State the invariant and ownership boundary the change affects.
4. For a significant dependency, complete the open-source evaluation described above.
5. Implement the smallest coherent change through the existing architecture.
6. Add tests proportional to risk, including negative authorization and redaction cases.
7. Run targeted checks for the affected modules. Expand checks only when the change crosses a
   documented boundary or has a concrete integration risk.
8. Update README or architecture documents when behavior, boundaries, setup, or roadmap changes.
9. After completing a functional point, return to the active roadmap and select the highest-priority
   remaining product goal. Prefer moving to a different major subsystem once the current subsystem's
   stated acceptance criteria are met; do not create an open-ended sequence of local refinements.
10. Continue work in the same subsystem only for a concrete security or reliability defect, failed
    acceptance criterion, release blocker, or dependency that blocks the next roadmap goal. Record
    that reason in the task table or relevant design document instead of treating optional polish as
    the default next phase.

Roadmap breadth is evaluated across completed functional points, not within a single change. Keep
each implementation and commit coherent, then deliberately advance the broader control plane,
capability plane, runtime, operations, and data-lifecycle goals rather than repeatedly optimizing
one feature area.

## Validation

The supported development baseline is Python 3.11 or newer with dependencies managed by `uv`.

```bash
uv sync --all-groups
uv run ruff check .
uv run mypy
uv run pytest backend/tests/test_health.py
```

Run focused pytest targets for the affected module or behavior. Non-essential full-suite tests are
prohibited during normal development and pull requests. Expand to a small set of integration tests
only when the changed boundary requires it. Run the complete `uv run pytest` suite only as a release
gate immediately before creating a release tag, and report any baseline failure precisely.

For database behavior that depends on PostgreSQL semantics, do not rely only on SQLite-based tests.
Use the Postgres integration path or Docker Compose where appropriate. Never require live provider
credentials in the default test suite.

## Definition Of Done

A change is complete only when:

- ownership and dependency direction remain clear;
- workspace isolation and denial paths are tested;
- retries and side effects are idempotent where required;
- secrets and sensitive payloads are redacted;
- durable state can recover after worker or process interruption;
- runtime cleanup and failure evidence are preserved for execution changes;
- the relevant quality checks pass or known baseline failures are explicitly reported;
- documentation describes the behavior as current or planned accurately;
- superseded custom code is removed after an SDK migration.
