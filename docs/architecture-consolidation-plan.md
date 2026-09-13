# OpsMesh architecture and code-organization consolidation plan

## Purpose

This document records the repository architecture review against mature open-source projects and
defines the implementation plan for reducing accidental package depth without collapsing real
domain or lifecycle boundaries. It is a migration plan, not a claim that the target layout is
already implemented.

## Findings

The application already has the correct five top-level boundaries:

```text
backend/app/
├── api/             # transport and request/response contracts
├── core/            # technical foundations and infrastructure ports
├── domains/         # product-owned business aggregates
├── runtime/         # execution resources, workers and self-hosted control
└── observability/   # audit, tracing, metrics, cost and notification evidence
```

The remaining problem is implementation depth inside these boundaries. Several packages are split
by DTO shape or helper role instead of an independently owned lifecycle, persistence boundary or
extension point. The largest areas are agent runtime/provider code, orchestration tasks and plans,
runtime environment/operations, and project file staging.

## External architecture references

The following projects were selected because they expose stable contracts and execution boundaries,
not merely because they have large repositories:

- n8n separates workflow contracts, the execution core, transport/CLI and node extensions.
- LangGraph separates graph runtime, prebuilt capabilities, checkpoint contracts and concrete
  Postgres/SQLite persistence implementations.
- Temporal separates control-plane history/matching/persistence from SDK clients and workers.
- OpenHands separates agent/controller/event-stream state from the runtime and Docker sandbox.
- PydanticAI keeps a provider-neutral agent core and uses capabilities/toolsets, including dynamic
  MCP toolsets, as its main extension mechanism.
- Prefect separates the SDK, client, server, CLI and integrations, with the server owning final
  state-transition decisions.

These projects support four rules for OpsMesh: contracts before implementations, control plane
before data plane, one owner for each state machine, and extension packages only where replacement
or independent lifecycle is real.

## Target ownership model

### API

`api` validates transport input, resolves the authenticated workspace context, invokes an
application/domain service, and maps typed errors to the public envelope. Routes must not own SQL,
provider SDK calls, Docker lifecycle, or business state transitions.

### Core

`core` owns configuration, authentication, security, database/Redis composition, secrets and
technical ports such as clocks, IDs, event envelopes and redaction. It is not a business `utils`
dumping ground. Domain-specific helpers remain with their owning domain.

### Domains

`domains` owns product aggregates and their commands/queries:

```text
domains/
├── agents/
│   ├── models.py service.py profiles.py memory.py
│   ├── runtime/{contracts.py,execution.py,sessions.py,tools.py,events.py}
│   └── providers/{registry.py,openai.py,claude.py}
├── capabilities/
│   ├── models.py catalog.py governance.py tools.py mcp.py marketplace.py
├── orchestration/
│   ├── definitions.py planning.py runs.py tasks.py approvals.py execution.py events.py
└── workspace/
    ├── workspaces.py projects/{models.py,imports/,exports/} files.py teams.py storage.py reviews.py
```

The exact filenames may be split further when a module owns a real state machine or adapter, but
`payloads`, `summary`, `support`, and similar names are not package boundaries by themselves.

### Runtime

`runtime` owns actual execution resources and worker lifecycle:

```text
runtime/
├── execution/
│   ├── contracts.py manager.py backends.py pool.py policies.py workspace.py
│   └── backends/{docker.py,hosted.py,self_hosted.py}
├── workers/{queue.py,dispatch.py,handlers.py,scheduling.py,recovery.py}
└── operations/{health.py,metrics.py,cleanup.py,timeline.py}
```

Agent SDK adapters may request a runtime through the contract, but they do not own Docker leases or
workspace staging. Project services own project files and artifacts; runtime receives a typed stage
request and never becomes a second project repository.

The runtime contract explicitly supports `none`, `isolated`, `pooled`, and `persistent` modes. `none`
creates no runtime lease and denies shell, stdio MCP and local filesystem execution. Other modes
acquire a concrete backend through the runtime registry. OpenAI-native sandbox support, Claude
execution, Docker and future providers all map through this same product contract.

### Observability

`observability` owns durable audit evidence and read models for traces, metrics, costs and
notifications. Domains emit one typed event envelope; they do not each implement a private audit or
cost writer. Runtime operations may produce health and capacity facts, but observability owns their
externalized evidence.

## Dependency direction

```text
API ───────┐
Workers ───┼──> domain/application services ──> product contracts/ports
SDK adapters ───────────────────────────────────┘
Runtime backends ───────────────────────────────┘

contracts/ports ──> PostgreSQL, Redis, OpenAI SDK, Claude SDK, Docker, OpenTelemetry
domain/runtime events ──> observability
```

Domain services own their stable contracts and may not import API routes or API application
services. Runtime workers do not import API schemas for execution inputs; transport-only response
models are migrated separately when they are shared read models. Provider adapters implement the
Agent Runtime contract, while runtime backends implement the execution contract. Concrete
infrastructure is assembled once at the application/worker composition root.

## Migration sequence

1. **Inventory and ownership matrix** — capture every package, import edge, state enum, policy,
   repository and duplicate helper. This step is read-only.
2. **Freeze contracts and import rules** — define the Agent Runtime, Runtime Execution, Capability,
   Task/Run, Workspace File and Audit Event contracts; add architecture checks before moving files.
3. **Consolidate leaf packages** — merge DTO-only and one-line forwarding packages into their owning
   feature modules. Update all imports and delete superseded sources; no compatibility aliases.
4. **Remove cross-domain duplicates** — centralize task/run transitions, capability authorization,
   project staging, audit writing and cost accounting under one owner each.
5. **Complete adapter composition** — provider, runtime, storage and telemetry registries are
   injected at the composition root; domain code never imports vendor objects.
6. **Verify and document** — run focused contract, isolation, recovery and redaction tests; update
   architecture docs and enforce import rules in CI. Full pytest remains a release-tag gate.

Each migration batch is a coherent functional point and receives its own commit. Source moves must
not change public behavior, database identities or workspace isolation semantics.

## Acceptance criteria

- Five top-level application boundaries remain stable.
- Ordinary feature code is at most two package levels deep.
- A subpackage exists only for an independent lifecycle, persistence boundary, security boundary or
  replaceable adapter.
- Each state machine, policy evaluator, file boundary, authorization path and audit writer has one
  implementation owner.
- SDK adapters and runtime backends are replaceable through typed contracts.
- No API-to-infrastructure imports, circular imports, empty packages or compatibility shims remain.
- Focused architecture and behavior tests pass, with no full-suite run during normal development.
