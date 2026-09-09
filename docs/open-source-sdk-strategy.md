# Open-Source SDK Strategy

## Purpose

OpsMesh differentiates itself through its enterprise agent control plane: tenant boundaries,
durable product state, governance, runtime policy, approvals, operations, and auditability. It
should reuse maintained open-source projects for standard protocols and infrastructure mechanics.

This document identifies the strongest current candidates, the code they may replace, and the
boundaries OpsMesh must continue to own.

## Native SDK Interface Rule

When an adopted SDK already provides a required capability, OpsMesh must call the SDK's documented
public interface directly. We do not duplicate protocol, transport, execution, serialization,
retry, or telemetry implementations. An OpsMesh adapter is appropriate only for translating a
product-owned contract and enforcing workspace authorization, redaction, limits, approvals, or
audit evidence around the SDK call.

## Decision Criteria

An SDK is not adopted only because it reduces line count. Evaluate:

- protocol and feature coverage;
- license compatibility with the MIT-licensed project;
- maintenance activity and security response;
- Python 3.11+ support, async support, and typing quality;
- predictable timeout, cancellation, retry, and error semantics;
- secret handling and observability hooks;
- versioning and migration policy;
- testability without a live external service;
- deployment and operational cost;
- whether it can remain behind an OpsMesh-owned contract.

For a high-impact dependency, use a small proof of concept and an architecture decision record. A
successful proof must include failure and security cases, not only a happy-path demo.

## Recommended Adoption Order

### P0: Agent SDK foundations

**Current position:** OpsMesh supports two vendor SDKs behind one product-owned runtime contract.
`openai-agents` is the OpenAI execution core and `claude-agent-sdk` is the Anthropic execution core.
The upstream projects are `openai/openai-agents-python` and
`anthropics/claude-agent-sdk-python`. `ProviderAgentRuntimeRegistry` selects a concrete adapter;
`AgentRuntimeExecutor`, `AgentRunRequest`, and `AgentRunResult` remain vendor-neutral. SDK-backed
adapters share `BaseSDKAgentRuntimeAdapter` for product execution invariants and implement
provider-specific construction, invocation, and result mapping as subclasses.

**Use upstream for:** agent turns, tools, handoffs, sessions, context compaction, serializable run
state, human approval interruptions, MCP integration, and sandbox client contracts when those APIs
are stable.

**Keep in OpsMesh:** workspace authorization, durable task/run models, scheduling, quotas, provider
credential policy, audit events, artifact ownership, and recovery evidence.

**Claude SDK use:** `ClaudeSDKClient`/`ClaudeAgentOptions` own the interactive model turn and expose
stream consumption plus `interrupt()`; `create_sdk_mcp_server()` and `tool()` own the in-process MCP
bridge; `SessionStore` mirrors the transcript into the existing Postgres-backed session;
`output_format` and `ResultMessage.structured_output` own structured output; SDK hooks plus
`deferred_tool_use` own provider-side lifecycle and approval pause state; Claude owns automatic
context compaction. The adapter retains product
authorization, input/output guardrail policy, approval records, redaction, durable retry policy,
audit events, usage normalization, and workspace scope. A provider adapter never replays a complete
Agent SDK run in memory because model turns and tools may already have produced side effects.

The Claude wheel bundles the Claude Code CLI and is materially larger than a protocol-only client
(about 99 MB on Windows for the currently pinned release). That footprint is accepted because the
user explicitly requested the official SDK; no additional general agent framework is added.

**Support matrix:** contract tests cover tool registration, policy review, structured output,
product guardrails, ordered stream events, lifecycle hooks, session mirroring, deferred approval
state, rejection without resume, provider selection, SDK cancellation, usage normalization, and
secret-safe raw output. Unsupported semantics are rejected explicitly: OpenAI-style handoff
descriptors and per-subagent MCP execution contexts.

Other model providers remain deferred. A future provider must implement the OpsMesh-owned provider
contract and pass the same runtime contract tests; it must not add a third orchestration framework.
The workspace model-capability API publishes the runtime adapter matrix. The provider registry
derives requirements from every request before SDK invocation, so a future adapter cannot silently
ignore tools, handoffs, nested agents, structured output, streaming, resume, guardrails, sessions,
or cancellation.

### SDK Adoption Audit (2026-09)

| Area | Current implementation | Decision |
| --- | --- | --- |
| Agent turns and tools | OpenAI Agents SDK and Claude Agent SDK adapters | Adopt official SDKs; keep only contract translation and product policy |
| Stateless semantic resource review | OpenAI Python SDK and Anthropic Python SDK structured-output parsers | Adopt provider SDK parsing; keep resource redaction, policy merge, and fail-closed activation rules |
| MCP protocol and transports | Official MCP Python SDK | Keep; no custom JSON-RPC replacement |
| Schema validation | `jsonschema` | Keep; product adds workspace/resource policy |
| Release version parsing and ordering | `packaging.Version` | Adopt; keep only the three-component release-tag policy |
| Gateway and webhook rate limits | `limits` fixed-window strategy with Redis storage | Adopt; keep gateway identity, route policy, failure mode, audit, and response semantics |
| Webhook HTTP transport | `httpx` streaming client | Adopt; keep signature generation, bounded response capture, redaction, retries, and durable delivery state |
| Cache single-flight locking | Redis Python SDK `Lock` | Adopt SDK atomic ownership and release; keep cache namespaces, JSON payloads, TTL policy, and loader semantics |
| Worker run locking | Redis Python SDK `Lock` | Adopt SDK token ownership and atomic release; keep workspace/run key scope and lease duration policy |
| Queue and idempotency state transitions | Redis server-side Lua invoked through Redis Python SDK | Keep the minimal product transaction scripts; require Lua support and never degrade to non-atomic multi-command writes |
| Tracing and metrics | OpenTelemetry and Prometheus clients | Keep; product audit remains durable Postgres state |
| Request retries | Provider SDK request retries and durable worker workflow retries | Never replay a complete agent run or MCP tool call in-process; retry only an explicitly idempotent transport or durable workflow step |
| Sessions and memory | Product Postgres session/memory services, OpenAI Responses compaction session, Claude `SessionStore` bridge | Keep product ownership of storage and durable knowledge; use provider SDK transcript and compaction extension points |
| Provider health probes | OpenAI and Anthropic SDK model listing plus minimal inference | Adopt provider SDK clients; keep probe selection, no-retry policy, stable health codes, persistence, and audit |
| Generic agent frameworks | LangChain, LlamaIndex, LiteLLM | Reject for now; they would duplicate the two SDK cores and add weight |
| Durable workflow engines | Temporal | Evaluate with a representative workflow before replacing current task/worker state |
| Runtime execution | Official Docker SDK behind the product runtime boundary and self-hosted connector contracts | Adopted for daemon access; keep product isolation, policy, leases, and evidence |

### P0: Provider SDK structured resource review

Resource review uses one product-owned `ResourceReviewProviderAdapter` contract with three concrete
protocol implementations: OpenAI Responses, OpenAI Chat Completions, and Anthropic Messages. Each
implementation calls the public structured-output parser from the
[OpenAI Python SDK](https://github.com/openai/openai-python) or
[Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python) with the same strict
Pydantic result model. Canonical OpenAI defaults to Responses; OpenAI-compatible credentials default
to Chat Completions unless their stored `model_api` explicitly selects Responses; Anthropic uses
Messages. Unknown providers and protocols fail closed instead of falling through to another vendor.

The direct `openai` and `anthropic` clients are the right boundary for this stateless classification
request. `openai-agents` and `claude-agent-sdk` remain the execution cores for stateful agent runs;
launching an agent runtime for a single resource classification would add process, session, and tool
semantics that the review does not need. OpsMesh owns input redaction, workspace credential
selection, static-policy signals, activation decisions, and durable review evidence. The SDK owns
request serialization, authentication, provider errors, JSON Schema generation, and typed response
parsing. The former manual HTTP payload and hand-written response parser are removed.

### P0: MCP Python SDK

**Adopted:** [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk)

The remote adapter uses the official `ClientSession`, Streamable HTTP transport, and SSE transport
for initialization and tool calls. Streamable HTTP receives an OpsMesh-configured `httpx` client so
the SDK owns protocol behavior while OpsMesh controls headers and timeouts. The direct dependency is constrained to `mcp>=1.27.1,<2.0.0`
because the current OpenAI Agents SDK requires MCP 1.x. The official SDK now has a 2.x line, but an
OpsMesh upgrade must move together with the Agents SDK instead of introducing a version branch or
compatibility layer.

**SDK-owned:** protocol negotiation, JSON-RPC framing and parsing, remote transport lifecycle,
session initialization, standard tool calls, and protocol result models.

**OpsMesh-owned:** server catalog, workspace credentials, allowlists, egress rules, payload limits,
approvals, call logs, run events, self-hosted dispatch, and redaction.

**Current boundary:** remote HTTP, SSE, and hosted remote connections resolve to the asynchronous
SDK-backed adapter behind `McpToolAdapter`. Agent SDK callbacks await the product tool gateway and
the gateway directly awaits the official MCP client session; no thread-owned event loop, custom
retry loop, or complete-call replay sits between them. The superseded remote JSON-RPC/SSE parser is
removed. Stdio is also SDK-backed, but the client process is launched only inside a Docker runtime
or a trusted self-hosted connector. The synchronous Docker infrastructure boundary is isolated with
`asyncio.to_thread`, while the control plane passes a versioned request contract and receives a
serialized `CallToolResult`; it never starts a user-controlled stdio process.

The Docker entrypoint is `python -m opsmesh_runtime.mcp_stdio_client`. Runtime images that enable
stdio must include the small `opsmesh-runtime` package and its pinned MCP Python SDK dependency. The
self-hosted `opsmesh-self-hosted-worker` connector receives the same v1 `request` payload and calls
the public `mcp.client.stdio.stdio_client` and `ClientSession` interfaces directly before posting
completion. Its local SQLite store and `filelock` single-instance guard own only crash recovery and
process coordination; no JSON-RPC framing or transport compatibility layer belongs in the
connector or control plane.
Prefer Streamable HTTP for new remote servers; retain SSE only for servers that require the upstream
SSE transport.

### P0: Docker SDK for Python

**Adopted:** [Docker SDK for Python](https://docs.docker.com/reference/api/engine/sdk/), constrained
to `docker>=7.2.0,<8.0.0`.

`DockerSdkRuntimeClient` is the only managed Docker implementation. It uses the official SDK for
container and volume lifecycle, command execution, and archive transfer. The former subprocess and
Docker CLI command construction path was removed; there is no dual-client or compatibility branch.

**Adopt:** Docker daemon communication, image/container operations, exec lifecycle, log streams,
and structured daemon errors.

**Keep:** `DockerRuntimeClient` as the OpsMesh boundary, allowed-image checks, resource policies,
mount validation, capability dropping, read-only root filesystems, network restrictions, leases,
workspace labels, cleanup verification, and security evidence.

Docker SDK objects do not cross `runtime_manager`. Command input bytes use the product-owned
`RuntimeCommandInputFile` contract: the Docker adapter creates an owner-scoped, read-only temporary
file in the runtime, appends only its generated path to the command, and removes it after execution.
Project archives are re-owned to the container identity before extraction so a non-root runtime can
write its declared work and output directories. Archive reads are bounded before buffering and
accept exactly one regular file; symbolic links, hard links, malformed archives, and oversized
transfers fail closed.

### Adopted: OpenTelemetry Python

**Candidates:**

- [OpenTelemetry Python](https://github.com/open-telemetry/opentelemetry-python)
- [OpenTelemetry Python instrumentation](https://opentelemetry.io/docs/languages/python/libraries/)

**Adopt:** W3C trace propagation, spans, context propagation, OTLP export, and maintained
instrumentation for FastAPI, HTTP clients, SQLAlchemy, Redis, and supported model/tool clients.

**Keep:** audit events, security events, domain event names, redaction policy, and stable
workspace/task/run correlation fields.

The active implementation exports structured API/worker logs and traces over OTLP, propagates W3C
context through HTTP and Redis jobs, and instruments FastAPI, HTTPX, SQLAlchemy, and Redis. Product
audit events remain an independent Postgres record. Trace/span identifiers and `traceparent`
extraction/injection use the OpenTelemetry ID generator and propagator; the product trace context
stores only the stable correlation fields needed by queue payloads and logs.

### Adopted: Prometheus Python client

**Candidate:** [prometheus/client_python](https://github.com/prometheus/client_python)

`backend/app/core/metrics.py` publishes process, HTTP, and domain metrics through official Counter,
Histogram, Gauge, and collector primitives. Domain collectors stay under `backend/app/operations`,
and route-template labels prevent request-ID path cardinality.

### Adopted: jsonschema

OpsMesh uses the maintained `jsonschema` package and its Draft 2020-12 validator for tool and
resource parameter contracts. The library owns JSON Schema validation semantics. OpsMesh owns the
allowed resource types, workspace reference checks, parameter default merging, secret rejection,
and policy decisions. A custom schema parser was rejected because it would duplicate a standard,
security-sensitive validation protocol and provide weaker keyword coverage.

### P1: pgvector-python

**Candidate:** [pgvector/pgvector-python](https://github.com/pgvector/pgvector-python)

OpsMesh already uses Postgres and has lexical and Postgres full-text memory search. pgvector adds
SQLAlchemy vector types, distance operators, and HNSW/IVFFlat indexes without introducing a second
database.

**Adopt:** vector column types, similarity queries, and vector index declarations.

**Keep:** knowledge-source registration, chunk ownership, workspace filters, ingestion state,
embedding-provider abstraction, citations, retention, and hybrid ranking policy.

Begin with one embedding model and reciprocal-rank fusion over Postgres full-text and vector
results. Add a separate vector database only after measured Postgres limits justify it.

### P1: Authlib

**Candidate:** [Authlib](https://authlib.org/)

Use Authlib to integrate OpsMesh with external OAuth 2.0 and OpenID Connect identity providers.
OpsMesh should be an OIDC client and protected resource server, not a new enterprise identity
provider.

**Keep:** local users, workspace membership, role mapping, token revocation metadata, audit events,
and a simple local authentication mode for development and self-hosted installations.

Do not begin this integration until issuer mapping, account-linking, workspace invitation, and role
claim semantics are specified.

### P1: Tenacity

**Candidate:** [jd/tenacity](https://github.com/jd/tenacity)

Use Tenacity only for bounded retries around explicitly idempotent outbound transport calls. It can
replace generic backoff loops in HTTP/provider integrations after the call's idempotency contract is
documented.

Do not use Tenacity to replay an agent run or MCP tool invocation, or to replace durable worker retry
state, idempotency keys, task transitions, dead-letter handling, or circuit state that must survive a
process restart.

## Architecture Spikes

### Temporal Python SDK

**Candidate:** [temporalio/sdk-python](https://github.com/temporalio/sdk-python)

Temporal can provide durable timers, retries, cancellation, signals, workflow history, and recovery
for long-running agent tasks. The Agents SDK also documents a Temporal integration path. This
overlaps substantially with the current Redis queue, worker leases, task state machines, approval
waits, and recovery services.

Do not migrate by replacing infrastructure first. Build one representative workflow containing:

1. manager planning;
2. parallel specialist work;
3. an MCP tool call;
4. a long human approval wait;
5. worker termination and recovery;
6. cancellation and artifact finalization.

Compare code size, operational dependencies, Postgres synchronization, replay constraints,
observability, local development, migration risk, and failure recovery. Postgres must remain the
product source of truth even if Temporal becomes the execution source of truth for workflow
progress.

### OpenFGA and OPA

**Candidates:**

- [OpenFGA](https://github.com/openfga/openfga) for relationship-based authorization.
- [Open Policy Agent](https://www.openpolicyagent.org/) for general policy evaluation.

The current owner/admin/operator/viewer model is intentionally simple. Do not add an external
authorization service until a concrete scenario cannot be expressed safely with local RBAC.

Evaluate OpenFGA for relationships such as organization, department, project, delegated agent,
shared artifact, and cross-workspace collaboration. Evaluate OPA for deployment-managed runtime,
network, tool-risk, and compliance policies. They solve different problems; do not deploy both by
default.

### Deferred provider expansion: Any-LLM and LiteLLM

The OpenAI Agents SDK exposes Any-LLM and LiteLLM integrations for multi-provider access. They are
outside the current phase and may be evaluated only after the native OpenAI Agents SDK path is
stable. The SDK describes these adapters as beta or best-effort.

Before adoption, run provider contract tests for:

- structured output and schema failures;
- function and MCP tool calls;
- streaming events and cancellation;
- usage and cost metadata;
- multimodal input filtering;
- error normalization and retry classification;
- per-workspace credential isolation;
- base URL and secret redaction;
- no implicit provider fallback across policy boundaries.

Keep the existing provider adapter when upstream behavior cannot meet these contracts.

## Components Not Recommended As A New Core

- Do not add LangChain or LlamaIndex as a second agent orchestration framework. Use focused parsing
  or retrieval packages only when they provide clear value behind a local contract.
- Do not introduce Celery while the project is evaluating Temporal and already owns a Redis worker
  path. Two queue semantics would increase operational and recovery complexity.
- Do not add a separate vector database before pgvector is measured under representative load.
- Do not introduce Kubernetes only to match a reference architecture. Package for Kubernetes when
  horizontal scale, scheduling, or isolation requirements justify it.
- Do not replace application audit events with vendor tracing or OpenTelemetry spans.
- Do not delegate workspace authorization, secret redaction, approval policy, or runtime safety to
  an LLM SDK.

## SDK Migration Definition Of Done

An SDK migration is complete when:

- an OpsMesh-owned interface isolates the dependency;
- behavior and security contract tests pass;
- timeouts, cancellation, retries, and shutdown are explicit;
- workspace context and trace context propagate correctly;
- no secret-bearing upstream object reaches logs or API responses;
- metrics and errors preserve stable product semantics;
- old default code is removed;
- dependency and lock files are updated intentionally;
- deployment and upgrade documentation is current;
- a rollback path exists for high-risk runtime migrations.

Compatibility shims are not an acceptable migration outcome. A dependency replacement must update
the active contract and all in-repository callers, then remove the superseded implementation.
