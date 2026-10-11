# Execution Diagnostics

OpsMesh retains business call records in PostgreSQL, emits bounded structured service logs,
and uses the configured OpenTelemetry exporter for distributed traces and log export.
An external OTLP receiver is optional. No additional container is required for diagnostics.

## Correlation and Timing

Service JSON logs include request, workspace, task, Run, Worker, Runtime, conversation,
SDK tool call, operation and error identifiers when they are available at that boundary.
Worker lease metadata includes `queue_wait_ms`, measured from the original job creation;
on retry this includes time since the original submission. Runtime MCP operation logs
include `duration_ms`. Existing Run events and SDK lifecycle events provide execution,
model and tool timestamps. These measurements describe different stages and must not be
added together when their intervals overlap.

MCP invocation metadata is carried through the SDK's public `call_tool(..., meta=...)`
interface. The trusted Worker binds workspace and Run identity; an MCP server cannot
override it by returning identity fields in a diagnostic notification.

## Error Contract

Native MCP `isError` responses remain tool failures. A server may return diagnostic fields
in `CallToolResult._meta["com.opsmesh/diagnostic"]`, or emit a standard MCP logging
notification with a dictionary containing these fields. Runtime subscribes using the
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

Exception evidence contains at most eight exception types, with at most sixteen location
fingerprints and line numbers each. Fingerprints are SHA-256 of `filename:function`.
Exception messages, source lines, locals and paths are omitted because they can contain
credentials. SDK exception groups and causal chains must be interpreted as transport
evidence, not proof that an upstream business operation did not execute.

Project stderr is asynchronously drained through the SDK's public `errlog` parameter.
Only allowlisted JSON diagnostic fields are retained. Other text is discarded; its byte
count, SHA-256 and discarded record count remain available. Lines larger than 4096 bytes
are discarded, and the collector keeps at most 32 records. Raw stderr is never written
to a file or host log. Stdout remains reserved for the MCP protocol.

Bounded private evidence is stored in the existing MCP call record's `error.diagnostic`.
Normal call-log responses, SDK tool results, task messages and SSE Run events omit it.
Workspace owners and administrators can read it through:

`GET /api/v1/workspaces/{workspace_id}/capabilities/mcp-tool-call-logs/{log_id}/diagnostic`

The existing list endpoint additionally filters by `agent_run_id`, `tool_call_id` and
`error_id`, as well as trace, request, server, tool and status. Every query remains scoped
to the authenticated workspace and applicable resource permissions.

Worker maintenance removes only expired `error.diagnostic` data. Configure its lifetime
with `OPSMESH_MCP_DIAGNOSTIC_RETENTION_DAYS` (default 7). The error identity, summary,
status and business/audit records remain. Expired records expose `diagnostic_expired=true`.

## Service and Egress Logs

Python's standard `QueueHandler`/`QueueListener` keep stdout I/O off the request path.
The queue is bounded by `OPSMESH_LOG_QUEUE_CAPACITY` (default 2048). Overflow drops
service log records and increments `opsmesh_log_records_dropped_total{sink="stdout"}`.
The existing secret filter runs before enqueue and OTLP export. Records exceeding 16 KiB
replace their message/details with a size marker and increment
`opsmesh_log_records_truncated_total{sink="service"}`. Durable Run and audit writes do
not use this lossy service-log queue. OpenTelemetry uses its native bounded batch processors.

Docker API/Worker and newly created Runtime containers rotate JSON logs at 10 MiB with
three files. Existing containers need recreation to adopt the logging configuration.
For native deployments, journald owns service-log storage and retention.

Runtime's built-in Squid records only failed egress responses, using execution port,
proxy result, status and duration. It never logs URLs, query strings, headers or bodies.
Match the execution port to the Runtime allocation UID when investigating a denial.
Runtime container logs are infrastructure diagnostics and are not exposed through Chat.

The Uhaozu MCP project emits safe login/business-request diagnostics before rendering its
friendly error message. Its upstream requests are tested with an offline HTTP transport;
no business API request is needed to validate this contract.

## Verification

Focused tests cover native SDK logging notifications, stderr flooding and secret canaries,
transport cleanup/cancellation, public/private serialization, workspace query isolation,
and diagnostic expiry against real PostgreSQL. Business API and model endpoints are not
part of this offline validation. Historical errors whose details were discarded cannot
be reconstructed after upgrading.

Managed MCP lifecycle failures write the safe exception summary to the existing audit
stream as `mcp_process.failed`. Its `error.error_id` matches the structured Worker log;
cleanup failures preserve their exception type separately from the original cause.
Lifecycle auditing follows the existing workspace audit access and retention policy.
