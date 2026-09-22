# Repository Instructions

This file is the single repository-wide source for project mission, architecture invariants,
dependency policy, development workflow, and validation requirements.

## Mission

OpsMesh is an open-source enterprise agent framework. It provides the control plane around agent
SDKs: workspaces, authorization, agent teams, durable tasks, workers, capabilities, isolated
execution, credentials, approvals, artifacts, operations, and auditability.

The backend is the active product surface. Frontend work begins only when explicitly requested and
must remain contract-driven; do not couple backend domains to a UI framework.

## Read First

Use these current documents as the project map, while treating code, migrations and focused tests
as the final evidence of implemented behavior:

- `README.md`: product position, capabilities, target architecture, and near-term goals.
- `docs/architecture.md`: system boundaries and durable-state model.
- `docs/backend-service-architecture.md`: service and dependency boundaries.
- `docs/agent-runtime-contract.md`: supported provider Agent SDK boundary.
- `docs/capabilities-and-runtime.md`: skill, tool, MCP, and runtime model.
- `docs/isolation-and-security.md`: tenant and runtime isolation rules.
- `docs/threat-model.md`: threats and required mitigations.
- `docs/open-source-sdk-strategy.md`: dependency adoption and replacement strategy.
- `docs/platform-productionization-plan.md`: productionization status and remaining gates.

Documents containing “should”, “planned”, or “future” describe intent and are not proof of current
support.

## Non-negotiable invariants

1. A workspace is the tenant boundary for data, agents, tasks, files, memory, tools, and runtimes.
2. Never authorize or load workspace-owned resources by resource ID alone; include workspace scope
   in API, service, query, worker, cache, storage, and tool paths.
3. Postgres is the durable source of truth. Redis owns queues, locks, pub-sub, idempotency windows,
   and short-lived derived data.
4. API requests persist intent and enqueue long-running work; they do not execute Agent work inline.
5. User-controlled or Agent-controlled code never runs in the API or worker host process. Use an
   approved hosted sandbox, Docker runtime, or trusted self-hosted isolated runtime.
6. Models propose plans and actions; product services validate authorization, policy, state
   transitions, quotas, and side effects.
7. Decrypt credentials only at the narrow execution boundary. Never log, serialize, return, or
   persist raw secrets, complete authorization headers, or secret-bearing provider URLs.
8. Risky actions fail closed and create approval, audit, or security evidence as appropriate.
9. Product audit evidence remains durable and independent of model-provider tracing.
10. Public marketplace resources require review. Private workspace resources remain private and
    workspace-scoped by default.

## Platform, plugin, and business boundaries

- OpsMesh owns reusable platform domain logic: identity and authorization, generic message and
  attachment contracts, private storage, approvals, durable orchestration, knowledge and memory
  services, and plugin installation, deployment, isolation, audit and lifecycle management.
  Platform domain logic is expected; customer-specific business workflows are not.
- Keep channel-specific implementations in the independent plugin center under
  `plugins/<name>/`: vendor SDK dependencies, message transport, channel membership queries,
  native card templates, vendor callbacks and protocol translation. Do not place DingTalk or
  another channel's implementation, templates or SDK dependencies in OpsMesh or the shared SDK.
- The plugin center's `sdk/` owns provider-neutral plugin contracts and platform clients. Plugins
  use these public interfaces; they must not import platform source or bypass platform services
  with direct database access. The platform must not import plugin implementation code.
- Users configure business roles, instructions, experts, tools, skills and workflows through
  platform resources. Do not hardcode order-number extraction, order processing, log-analysis
  experts, a particular team leader, business prompts or fixed knowledge/memory sequences into
  platform routes, services, workers, migrations or production defaults.
- When a business example exposes a missing capability, implement the reusable capability in
  OpsMesh and keep the specific integration in its plugin or user configuration. Examples and
  product-flow fixtures may illustrate a scenario, but must remain explicitly opt-in and must not
  become production policy or mandatory seeded resources.
- Plugins authenticate their external channel, while OpsMesh remains the authority for workspace
  membership, resource access, tool invocation and approvals. Channel user IDs, roles, levels or
  group membership never grant platform permissions by themselves.

## Current code-quality rules

- Submit changes on a feature branch through a pull request targeting `master`. Do not push
  directly to `master`, bypass branch protection, or merge without the required checks/review.

- Complete the entire user-authorized task, not an arbitrarily selected slice. Break work into
  internal steps and coherent commits, but continue through the agreed acceptance criteria without
  requiring repeated "continue" prompts. Do not move to the next roadmap stage with required
  work unfinished or silently reduce the requested scope to a demo, scaffold or happy path.
- Never claim completion while required implementation or validation remains. Report implemented,
  verified, unverified, and blocked items distinctly; passing static checks, a mock flow, a build,
  or a commit is not proof of a working external integration.
- Reconcile every acceptance criterion with the implementation, affected callers, configuration,
  assets, migrations, packaging and documentation as applicable. A completed subtask or a disclosed
  blocker does not make the overall task complete. For example, parseable card JSON does not prove
  it can be imported, published, rendered or used for callbacks by the vendor.
- When credentials, external setup, a material user choice or new authority are missing, finish
  unaffected work and identify the exact remaining work and prerequisite. Never invent evidence,
  conceal gaps or bypass authorization. Retain the focused verification policy below; these rules
  do not require per-file tests, repeated checks or full-suite testing.

- The source of truth is the current code, migrations, focused tests, and architecture checks. Do
  not use an old plan, release note, or review snapshot as proof that the current checkout supports
  a behavior.
- Keep the dependency direction `api -> domains/application -> infrastructure`. Routes validate
  transport input and call services; they do not own business policy or long-running execution.
- Keep one owner for each contract, state machine, query, redaction rule, and evidence writer.
  Remove obsolete modules and update all callers directly; never add compatibility aliases,
  deprecated wrappers, re-export shims, version branches, or silent fallbacks.
- Split entrypoints such as `build`, `resolve`, `import`, and `run` into clear stages when they
  combine query, policy, serialization, and side effects. Keep cohesive state machines together;
  do not create one-line forwarding modules just to reduce line count.
- Add a package or file only for a stable contract, independent lifecycle, security boundary,
  persistence model, or replaceable adapter. Domain-specific helpers stay in their owning domain;
  `core/utils.py` is limited to small provider-neutral primitives.
- Runtime execution is explicit: `none`, `isolated`, `pooled`, or `persistent`. `none` never means
  host execution and cannot provide shell, stdio MCP, project files, or local code execution.
  Provider SDK adapters map the sandbox contract; `runtime` owns Docker/self-hosted lifecycle,
  pools, leases, staging, and cleanup.
- Documentation must describe the current tree and status/date. Delete superseded plans and stale
  paths, then update `README.md` and `docs/index.md` in the same change.
- Tests are product-flow tests: exercise the complete path from an accepted request, through
  enqueueing and durable orchestration (including team/project creation where applicable), to
  execution and the user-visible output. Keep only cross-boundary rejection, retry/recovery,
  workspace-isolation, idempotency, and redaction scenarios that are observable in that flow.
  Do not add tests for individual files, classes, dataclasses, serializers, registries, or helper
  functions, and do not create a test file solely for one implementation class. Extend an existing
  flow test when possible; remove redundant single-point tests instead of adding another layer.
- For code that is not covered by a product flow, use Ruff, type checking, import/compile checks,
  architecture checks, and `git diff --check` to catch syntax and structural regressions. For each
  functional point run only the affected flow tests plus those static checks. The complete pytest
  suite is a release-tag gate only.
- During architecture or code-organization refactors, do not add or repeatedly run tests when
  runtime behavior is unchanged. Update an existing flow test only when a changed contract makes
  it stale; otherwise rely on static checks and leave the functional-flow suite untouched.

## Open-source first policy

Do not implement a protocol, infrastructure client, retry engine, telemetry format, authorization
engine, workflow engine, parser, or storage driver until maintained libraries and official SDKs
have been evaluated.

Before adding infrastructure code:

1. Search the current dependency graph and official upstream SDKs.
2. Compare protocol coverage, security model, maintenance, license, Python support, async support,
   typing, observability, failure semantics, and operational cost.
3. Record the decision and rejected alternatives when a dependency changes an architecture boundary.
4. Wrap third-party SDKs behind a small OpsMesh-owned infrastructure protocol. Domain services must
   not depend on vendor response objects.
5. Cover observable behavior changes through an existing product-flow test, including rejection,
   recovery, tenant isolation, and redaction where relevant.
6. Delete superseded custom code after migration; do not keep parallel default implementations.

Adopt dependencies in this order:

- Use `openai-agents` and the official `claude-agent-sdk` as provider execution cores for turns,
  tools, sessions, run state, and HITL when their stable public contracts satisfy the requirement.
- Prefer the official MCP Python SDK and Agent SDK MCP integrations over custom transports or RPC.
- Prefer Docker SDK for Python over constructing Docker CLI commands.
- Prefer OpenTelemetry and the official Prometheus client over custom telemetry protocols.
- Prefer pgvector with Postgres full-text search before adding another vector store.
- Use Authlib for future OAuth/OIDC integration rather than building an identity provider.
- Evaluate Temporal, OpenFGA, or OPA only against a concrete OpsMesh requirement.
- Add other model providers only through the product-owned provider contract after an SDK path is
  stable and contract-tested.

Do not add LangChain, LlamaIndex, or another general Agent framework alongside the provider SDKs.
A focused library is acceptable when it fills a documented gap without duplicating those SDKs or
the OpsMesh control plane.

## Architecture and code quality

- Keep dependencies directed from API routes to application/domain services to infrastructure.
- Define typed contracts at module boundaries; prefer Pydantic models, dataclasses, protocols, and
  enums over unstructured dictionaries when the shape is stable.
- Keep transactions and state transitions visible. Avoid unrelated hidden commits and external I/O
  inside a transaction unless the invariant requires and documents it.
- Separate pure policy decisions from I/O. Reuse the owning service, repository, error type,
  redaction helper, and evidence writer before introducing another abstraction.
- Avoid circular imports and import-time side effects. Compose infrastructure dependencies at the
  application or worker boundary.
- Use Alembic for every schema change and preserve downgrade and cross-workspace constraints.
- Keep public API error envelopes stable and free of internal details and secrets.
- Add comments for decisions and non-obvious invariants, not narration.
- Keep a change coherent; do not mix unrelated feature work, architecture cleanup, or formatting.
- Frontend uses the `shadcn-admin`-derived Vite, React, TypeScript, TanStack Router and TanStack
  Query application skeleton. Keep route modules thin, product composition in `frontend/src/features`,
  backend transport in `frontend/src/api`, and user-visible strings in `frontend/src/i18n`.
- shadcn/ui is the single component contract. Add or refresh source-owned components through
  `frontend/components.json`, `frontend/src/components/ui`, and `pnpm dlx shadcn@latest`; use
  semantic CSS variables and the configured Lucide icon family. Do not introduce a second runtime
  component library or restore copied template demos.
- Do not hand-build, replace, remove, or simplify a component when shadcn/ui, the adopted frontend
  foundation, or an approved registry already provides it. Reuse the complete upstream component
  and its accessibility behavior first. If it cannot meet an approved product requirement, extend
  or compose it at the owning feature boundary while preserving the upstream primitive. A new
  component is the last resort and requires a documented gap. Error pages, authentication layouts,
  password inputs, navigation shells, feedback states, and other reusable application components
  are foundation code, not demo code, and must not be removed merely to reduce file count.
- Do not invent or add any user-visible UI copy unless the user explicitly requests that exact
  content as part of the current interface. This prohibition includes explanatory and functional
  prose, helper text, descriptions, notices, marketing copy, empty-state guidance, repeated
  context, labels, status text, and decorative wording. Preserve only user-approved existing copy
  and the minimum text explicitly required by the requested interaction; do not infer that a phrase
  is useful. Accessibility names that are not visually rendered remain required. Put explanations
  in documentation or in an explicitly requested help surface, never into the primary UI by
  initiative.
- Adding a feature or control does not authorize adding a new route or page. Keep the behavior in
  the existing surface unless the user explicitly requests a page, route, or navigation entry.
- Every change involving frontend layout, styling, visual hierarchy, motion, typography, color,
  theme tokens, icons, or component composition must use the applicable repository skills under
  `.agents/skills` before implementation and again for a rendered-page review. At minimum, apply
  `design-taste-frontend`, `frontend-design`, and `shadcn`; apply `web-design-guidelines` for the
  final accessibility and interface review. A lint pass or source inspection is not visual proof:
  inspect the real rendered page in both light and dark themes at desktop and mobile widths, fix
  the findings, and do not claim the UI change is complete until that review passes.
- Frontend behavior is validated through complete user flows after backend contracts are connected.
  For foundation and organization changes, run lint, type checking, and production build only;
  do not recreate the removed per-component or template-demo tests.

## Repository map

- `backend/app/api`: transport, schemas, dependencies, and application-facing services. Routes are
  grouped by functional boundary; routes validate input and call services rather than owning policy.
- `backend/app/domains/agents`: agent profiles, memory, messages, providers, and SDK runtime domains.
- `backend/app/domains/capabilities`: catalog, governance, resources, skills, marketplace, tools, and
  MCP transport/catalog/execution.
- `backend/app/domains/orchestration`: requests, runs, approvals, tasks, and workflows.
- `backend/app/runtime`: runtime resources, Docker pools, workers, operations, and self-hosted jobs.
- `backend/app/domains/workspace`: tenant lifecycle, projects, teams, storage, domains, and reviews.
- `backend/app/core`: identity, authentication, common foundations, database, Redis, secrets,
  administration, and external delivery integrations.
- `backend/app/observability`: audit, cost, trace, and notification evidence.
- `backend/migrations`: Alembic migrations.
- `backend/tests`: product-flow tests and retained release/security integration scenarios.
- `deploy`: installation, VPS/systemd, container, and monitoring assets.
- `docs`: current architecture and operating documentation.
- `.agents/skills`: repository-scoped development skills; each skill owns its complete directory,
  references, scripts, assets, and license notices.
- `frontend`: contract-driven Web Portal built from the `shadcn-admin` application skeleton;
  `features` own product slices, `routes` compose them, and `components/ui` owns shadcn/ui source.

## Development workflow

1. Inspect affected routes, services, models, migrations, worker paths, callers, and flow tests.
2. Establish only the relevant baseline when behavior changes; organization-only work uses static,
   import, and build checks.
3. State the affected invariant and ownership boundary.
4. Complete the open-source evaluation when adding a significant dependency.
5. Implement the smallest complete change through the existing architecture.
6. Extend an existing product flow only for changed observable behavior; do not add per-helper tests.
7. Run targeted checks and expand only for a concrete cross-boundary risk.
8. Update README and current architecture/operations documentation when behavior or setup changes.
9. Finish the active acceptance criteria before moving to another roadmap stage.
10. Continue within a completed subsystem only for a concrete security/reliability defect, failed
    release gate, or blocker for the next product goal.

## Validation

The supported baseline is Python 3.11 or newer with dependencies managed by `uv`.

```bash
uv sync --all-groups
uv run ruff check .
uv run mypy
uv run pytest backend/tests/test_health.py
```

Normal development uses only affected product-flow tests and relevant static checks. Architecture
refactors do not repeatedly run behavioral tests. PostgreSQL-specific behavior uses the Postgres
integration path rather than SQLite alone. The complete pytest suite runs only in the tag-triggered
release gate. Default tests never require live provider credentials.

## Definition of done

A change is complete only when ownership and dependency direction remain clear; workspace scope,
denial, idempotency, redaction, recovery, runtime cleanup, configuration, packaging, callers, and
documentation are reconciled where applicable; relevant checks pass or exact blockers are reported;
and superseded implementations are removed.

## Independent plugin SDK

The independent `opsmesh-plugin-center` repository owns `sdk/`; each plugin lives under
`plugins/<name>` with its package, templates, and deployment assets. Do not restore a platform-local
plugin SDK or source-copy fallback. Pin the external SDK to immutable release content or a full
commit and preserve license metadata.

Plugin implementations stay outside OpsMesh. Platform catalog synchronization is data-only;
catalog entries cannot grant publisher trust, auto-approve upgrades, or dynamically import external
code in API/Worker. Hosted plugin deployment is explicit, admin-approved durable intent and reuses
the isolated container lifecycle with digest-pinned templates, scoped credentials, quotas, bounded
recovery, audit, and revocation cleanup. Plugin containers are not Agent sandboxes or interactive
command targets. Configuration refresh is not in-process hot loading.
