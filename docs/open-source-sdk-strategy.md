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

### P0: Python OpenAI Agents SDK foundation

**Current position:** `openai-agents` is already the agent execution foundation and is the only
Agent orchestration core for the current phase. The upstream project is
`openai/openai-agents-python`. OpsMesh still contains product-level adapters for run state, provider
behavior, tools, approvals, and sandboxes.

**Use upstream for:** agent turns, tools, handoffs, sessions, serializable run state, human approval
interruptions, MCP integration, and sandbox client contracts when those APIs are stable.

**Keep in OpsMesh:** workspace authorization, durable task/run models, scheduling, quotas, provider
credential policy, audit events, artifact ownership, and recovery evidence.

**Next action:** create a version support matrix against the installed and target Agents SDK versions.
Exercise tool calling, structured output, streaming, sessions, approval pause/resume, MCP, provider
selection, and serialized run restoration. Replace custom code only after behavior is equivalent.

Other model providers are deferred. When provider expansion begins, each provider must implement the
OpsMesh-owned provider contract and pass the same runtime contract tests. Do not add another Agent
orchestration framework.

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
approvals, circuit policy, call logs, run events, self-hosted dispatch, and redaction.

**Current boundary:** remote HTTP, SSE, and hosted remote connections resolve to the SDK-backed
adapter behind `McpToolAdapter`; the superseded remote JSON-RPC/SSE parser is removed. Stdio is now
also SDK-backed, but the client process is launched only inside a Docker runtime or a trusted
self-hosted connector. The control plane passes a versioned request contract and receives a
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

**Candidate:** [Docker SDK for Python](https://docs.docker.com/reference/api/engine/sdk/)

The current Docker client constructs CLI commands. The official SDK provides a typed client for
images, containers, exec, logs, and API-version negotiation.

**Adopt:** Docker daemon communication, image/container operations, exec lifecycle, log streams,
and structured daemon errors.

**Keep:** `DockerRuntimeClient` as the OpsMesh boundary, allowed-image checks, resource policies,
mount validation, capability dropping, read-only root filesystems, network restrictions, leases,
workspace labels, cleanup verification, and security evidence.

**Migration shape:** add an SDK implementation behind the existing protocol, replay all runtime
manager tests against both clients, then switch the composition root. Do not expose Docker SDK
objects outside `runtime_manager`.

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
audit events remain an independent Postgres record.

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

Use Tenacity for bounded retries around idempotent outbound transport calls. It can replace generic
backoff loops in HTTP/provider integrations.

Do not use Tenacity to replace durable worker retry state, idempotency keys, task transitions,
dead-letter handling, or circuit state that must survive a process restart.

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
