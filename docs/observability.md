# Execution Diagnostics

This document describes this branch's diagnostic contract. Deployment and verification
status are recorded in the [architecture document](architecture.md).

OpsMesh retains business call records in PostgreSQL, emits bounded structured service logs,
and uses the configured OpenTelemetry exporter for distributed traces and log export.
An external OTLP receiver is optional. No additional container is required for diagnostics.

## Correlation and Timing

Service JSON logs include request, workspace, task, Run, Worker, Runtime, conversation,
SDK tool call, operation and error identifiers when they are available at that boundary.
Temporal supplies Workflow, Activity and attempt identifiers, execution history and
scheduled/started/completed timestamps. OpsMesh Run events and native SDK lifecycle
events provide model, tool and response progress. Measure submission, Outbox delivery,
native queue waiting, model start, first token and result persistence separately.
Overlapping durations cannot be added together. Product timestamps use PostgreSQL
`timestamptz`; migration preserves historical instants using the prior database timezone.
SDK-owned Session tables retain the SDK schema.

The call chain uses native OpenTelemetry context and W3C Trace Context. One distributed
trace shares a 128-bit `trace_id`; each SDK span has its own 64-bit `span_id`. Structured
logs, PostgreSQL audits and execution evidence read the current SDK context. OpsMesh does
not generate another span tree or maintain separate trace/span ContextVars. HTTP exposes
the current identifiers and `traceparent`; incoming parenting uses standard W3C headers.

`execution_id` and the business Run/Task identifiers correlate the full product lifecycle,
including later approval, cancellation and conversation requests that can have separate
traces. They do not replace `trace_id`. A small Temporal Activity interceptor binds the
authorized execution references and native Workflow/Activity identifiers to business
logging context, clears inherited fields, and restores scope in `finally`. It does not
create spans, authorize operations or emit implicit business audits.

MCP invocation metadata is carried through the SDK's public `call_tool(..., meta=...)`
interface. The trusted Worker binds workspace and Run identity; an MCP server cannot
override it by returning identity fields in a diagnostic notification.

## Error Contract

Native MCP `isError` responses remain tool failures. A server may return diagnostic fields
in `CallToolResult._meta["com.opsmesh/diagnostic"]`, or emit a standard MCP logging
notification with a dictionary containing these fields. The Activity subscribes using the
native SDK `message_handler`. It accepts only:

- `category`: `network`, `authentication`, `rate_limit`, `http`, `protocol`, `execution`, `cancelled`.
- `phase`: `login`, `business_request`, `connect`, `tool`, `control`, `discovery`.
- `exception_type`: a bounded identifier, without the exception message.
- `http_status`: an actual HTTP response status, or null when there was no response.
- `duration_ms`: monotonic phase duration in milliseconds.
- `retryable`: diagnostic information; this field does not initiate retries.

The Worker adds the authoritative operation identifier and uses the MCP call log UUID
as `error_id`. The same error identifier appears in Run events, tool results and logs.
Missing upstream details are represented by `detail_available=false`; OpsMesh does not
infer HTTP codes from human-readable error text or replay ambiguous tool failures.

## Private Evidence and Access

Native failure evidence contains at most eight exception types. Exception messages,
source lines, locals and paths are omitted because they can contain credentials.
SDK exception groups and causal chains must be interpreted as transport
evidence, not proof that an upstream business operation did not execute.

MCP notifications use the SDK's public `message_handler`. Only validated diagnostic
fields are retained, with limits of 128 notifications per connection and 32 per call.
A notification belongs to a tool call only when its ID matches the active call within
the trusted workspace, Run and MCP server. Otherwise it describes the connection.
Untrusted text and raw project stderr are discarded. Stdout remains reserved for the
native MCP protocol; there is no platform stderr parser or custom protocol transport.

Bounded private evidence is stored in the existing MCP call record's `error.diagnostic`.
Normal call-log responses, SDK tool results, task messages and SSE Run events omit it.
Workspace owners and administrators can read it through:

`GET /api/v1/workspaces/{workspace_id}/capabilities/mcp-tool-call-logs/{log_id}/diagnostic`

Connection notifications have a separate owner/admin endpoint:

`GET /api/v1/workspaces/{workspace_id}/capabilities/mcp-diagnostic-notifications/{event_id}`

Their private fields are also excluded from ordinary Run event responses, Task/Team
timelines and exported event summaries. An MCP notification never grants access to a Run.

The existing list endpoint additionally filters by `agent_run_id`, `tool_call_id` and
`error_id`, as well as trace, request, server, tool and status. Every query remains scoped
to the authenticated workspace and applicable resource permissions.

A native Temporal maintenance Schedule removes expired private call evidence from
`error.diagnostic`, diagnostic result metadata and private Run notification evidence.
Configure its lifetime with
`OPSMESH_MCP_DIAGNOSTIC_RETENTION_DAYS` (default 7). The error identity, summary,
status and business/audit records remain. Expired error evidence exposes
`diagnostic_expired=true`.

## Service Logs and Traces

Python's standard `QueueHandler`/`QueueListener` keep stdout I/O off the request path.
`opentelemetry-instrumentation-logging` injects native trace identifiers at log creation.
Its public LoggingHandler replaces the deprecated SDK handler for OTLP export. Product
filters retain business references, redact sensitive fields and enforce size limits.
The queue is bounded by `OPSMESH_LOG_QUEUE_CAPACITY` (default 2048). Overflow drops
service log records and increments `opsmesh_log_records_dropped_total{sink="stdout"}`.
The existing secret filter runs before enqueue and OTLP export. Records exceeding 16 KiB
replace their message/details with a size marker and increment
`opsmesh_log_records_truncated_total{sink="service"}`. Durable Run and audit writes do
not use this lossy service-log queue. OpenTelemetry uses its native bounded batch processors.

Docker API and Worker logs rotate at 10 MiB with three files. For native installations,
journald owns service-log storage and retention. User code runs through the selected
native SDK sandbox backend. OpsMesh no longer supplies Runtime RPC, Squid, execution
UID rules or an egress-log collector.

SDK tracing uses the official Temporal/OpenAI OTel integration. The global provider comes
from Temporal's public `create_tracer_provider()` for deterministic identifiers and
replay-safe export. `OpenTelemetryPlugin(add_temporal_spans=True)` is registered once on
the Client; all Workers inherit it. The Agent Worker enables
`OpenAIAgentsPlugin(use_otel_instrumentation=True, add_temporal_spans=False)` so the
Temporal plugin owns Workflow/Activity spans and the Agents plugin owns SDK spans.
Both official OTel integrations are Public Preview in the locked SDKs.

The provider, instrumentation and official plugins remain active with export disabled.
`OPSMESH_TRACING_ENABLED` controls trace export; `OPSMESH_OTEL_LOGS_ENABLED` independently
controls log export. With no trace exporter, new root traces are unsampled and still have
valid SDK identifiers. Incoming native sampling flags are preserved. Configuration is
applied at process startup. Disabling export does not remove context, logs or durable audits.

The PostgreSQL Outbox stores the submitting context's W3C `traceparent` and `tracestate`, including its
sampling flags. Delivery restores that context before native Client calls and resets it
in `finally`. A repeated business command retains its first parent; tracing does not change
the command's idempotency identity. Commands submitted without context begin a new trace.
Service log and audit correlation use the active native span. Product tool spans use the
OTel API directly and inherit that context. HTTP responses preserve native sampling flags,
including an unsampled parent. No product fallback reconstructs or invents child identifiers.
Temporal payloads and failure attributes
use the configured encryption codec. Credentials are resolved in authorized Activities,
and are never intentionally placed in Workflow arguments or diagnostic output.

### SDK ownership and product audit

[Temporal](https://docs.temporal.io/develop/python/platform/observability) supplies Workflow
and Activity logs, execution history, Visibility APIs and optional Core metrics. The
[Agents SDK](https://openai.github.io/openai-agents-python/zh/tracing/) creates model, tool,
handoff and guardrail spans. OpsMesh uses those public facilities instead of implementing
another execution tracing engine. Product lifecycle hooks also enforce authorization,
observe usage and collect artifacts; their product responsibilities remain necessary.

PostgreSQL audit records retain the workspace, actor, action and target in the business
transaction, including operations that never enter an Agent Workflow. SDK tracing can be
disabled, sampled or fail to export, so it does not replace these records. Audit hash-chain
verification detects inconsistent retained records; it does not prevent a database
administrator from rewriting the database and chain.

Current export boundaries for the locked SDKs are explicit:

- The configured OTLP receiver receives service, Temporal and Agent SDK spans, and service
  logs when enabled. No OTLP endpoint or disabled tracing disables trace export while native
  context propagation remains active; log export can be enabled independently. There is one trace export
  pipeline, without a second hosted OpenAI exporter or a separate tracing API key.
- Native model/tool input and output collection remains disabled. A public SpanExporter
  adapter applies product confidentiality policy before OTLP serialization: it omits
  prompts, outputs, tool schemas, exception messages/stacks and full URLs, redacts known
  secret strings, and preserves identifiers, numeric usage, timestamps and error status.
  The adapter does not create spans, reconstruct call graphs or control SDK execution.
- The official [OTel integration](https://docs.temporal.io/develop/python/integrations/openai-agents#tracing)
  supplies Agent-internal spans. Export follows native sampling and bounded batch queues;
  it is operational evidence rather than a durable business audit record.
- Temporal Runtime Core metrics are not configured. The existing Outbox metrics describe
  PostgreSQL delivery intent, not native poll/task latency or retry metrics.

### Model failure evidence

At the authorized model Activity boundary, an OpenAI SDK failure receives an `error_id`
shared by its structured log, Activity span and safe Temporal failure details. Logs retain
workspace, Run, Agent, Workflow/Activity/attempt, exception type, HTTP status and a validated
`provider_request_id`. This identifier is separate from the API `request_id`.
The code stack contains at most 16 module/function/line locations, excluding exception
text, source lines, filesystem paths and local values. Raw provider bodies, headers, URLs
and credentials are not retained. The native client does not add its own retries; Temporal
owns Activity retries and each attempt has separate evidence.

The terminal failure Activity preserves safe metadata in `AgentRun.error.metadata` and
the `run.failed` event. The last attempt's `error_id` locates its operator log and span;
stack locations stay out of business API responses. Earlier discarded evidence cannot
be reconstructed. Business audits remain explicit transactional PostgreSQL writes.

## Verification

The observability acceptance uses a real local Temporal server, native OpenAI/Agents SDK
with HTTP MockTransport, and a real gRPC OTLP receiver. It verifies an inherited API parent,
model retry, a tool Activity, positive Workflow duration, confidential-data exclusion and
native history replay without new requests or duplicate exports. Separate real PostgreSQL
tests verify Outbox parent persistence, idempotency and sampling preservation, production
Worker composition, and terminal model failure metadata. This proves local integration;
paid providers and the deployment's external Collector remain unverified.

The disabled-export acceptance submits concurrent HTTP requests in two workspaces,
commits Outbox parents to real PostgreSQL, delivers them through the production Client
plugins to a real Temporal Worker, and writes transactional audit records in Activities.
It checks `00`/`01` sampling flags, `tracestate`, request/execution/workspace identity,
audit/log span identity, native logging injection, secret redaction, valid audit hash chains
and context cleanup. No exporter or in-memory replacement proves those database boundaries.

Focused tests cover native SDK logging notifications and secret canaries,
transport cleanup/cancellation, public/private serialization, workspace query isolation,
and diagnostic expiry against real PostgreSQL. Business API and model endpoints are not
part of this offline validation. Historical errors whose details were discarded cannot
be reconstructed after upgrading.

Run the native compute image acceptance independently of business and model endpoints:

```bash
uv run python scripts/check_runtime_image.py --image opsmesh-compute:<commit>
```

This uses DockerSandboxClient to start a disposable native sandbox, exchange real MCP
stdio messages, serialize/resume its session and read back a file. The script removes
its sandbox in `finally`. It is compute-backend evidence, not full product deployment
or cross-machine failover evidence.

The opt-in product test additionally checks PostgreSQL sandbox references, encrypted
native state, stop/resume, workspace rejection, independent cleanup and the production
managed stdio adapter on Linux:

```bash
OPSMESH_TEST_NATIVE_SANDBOX=1 OPSMESH_TEST_COMPUTE_IMAGE=opsmesh-compute:<commit> \
  OPSMESH_TEST_POSTGRES_URL=postgresql+psycopg://... \
  uv run pytest tests/test_native_sandbox_docker_postgres.py
```
