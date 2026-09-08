# Agent Runtime Contract

## Purpose

This document defines the boundary between the OpsMesh product layer and the vendor Agent SDK
runtime adapters. OpenAI Agents SDK and Anthropic Claude Agent SDK are the two supported provider
orchestration cores. Both implement the same OpsMesh-owned runtime contract; neither provider SDK
crosses into product authorization, durable state, audit, quotas, or isolation.

The product layer owns users, workspaces, tasks, permissions, runtimes, approvals, files, artifacts, and audit logs. The OpenAI adapter delegates agent turns, tools, handoffs, sessions, run state, guardrail execution, structured output, and tracing to `openai-agents`; the Claude adapter delegates turns, MCP tools, streaming, structured output, session transcript mirroring, and deferred tool state to `claude-agent-sdk`.

## Product-Owned Runtime Contracts

The adapter boundary is defined by `backend.app.agent_runtime.contracts`; orchestration code does not
accept SDK result, state, session, or stream-event objects. The contract includes:

- `AgentRuntimeSession` for durable conversation history
- `AgentRuntimeHandoff` and `AgentRuntimeHandoffResult` for authorized control transfer
- `AgentRuntimeInterruption` for approval and resumable pauses
- `AgentRuntimeStructuredOutput` and `AgentRuntimeOutputSchema` for typed results
- `AgentRuntimeStreamEvent` for ordered, redacted stream data
- `AgentRuntimeCapabilities` for explicit feature support and limits

SDK objects are created, restored, and mapped only inside the provider adapter. A request that asks
for an adapter feature which is not implemented is rejected explicitly; it is never silently ignored.

## Runtime Inputs

The orchestration layer provides an `AgentRunRequest`.

Required fields:

- `workspace_id`
- `task_id`
- `task_step_id`
- `agent_run_id`
- `agent_profile_id`
- `user_id`
- `input`
- `runtime_context`
- `tool_policy`
- `approval_policy`
- `memory_policy`
- `file_scope`

The runtime layer must not infer workspace scope from model input.

## Runtime Context

Runtime context is passed through the SDK run context and product tools.

Fields:

- `workspace_id`
- `user_id`
- `task_id`
- `task_step_id`
- `agent_run_id`
- `agent_profile_id`
- `runtime_id`
- `allowed_tools`
- `tool_definitions`
- `resource_grants`
- `file_scope_ids`
- authorization and capability-catalog fingerprints in runtime metadata

All product tools receive runtime context.

## Agent Profile To SDK Agent

Product `AgentProfile` maps to SDK `Agent`.

Mapping:

```text
AgentProfile.name           -> Agent.name
AgentProfile.instructions   -> Agent.instructions
AgentProfile.model          -> Agent.model
AgentProfile.model_provider_credential_id -> request-scoped OpenAIProvider
AgentProfile.model_settings -> Agent.model_settings
AgentProfile.tools          -> Agent.tools
request.handoffs             -> Agent.handoffs (when the adapter advertises `handoffs`)
request.guardrails           -> Agent input/output guardrails (when advertised)
request.output_schema        -> Agent.output_type (when advertised)
```

Rules:

- only tools in the frozen effective catalog are attached
- SDK tool names, descriptions, and JSON Schemas come from the frozen descriptor
- only same-workspace handoff targets are attached after capability and authorization checks
- every product and MCP tool crosses the Agent tool gateway before execution
- frozen defaults are merged and locked parameters cannot be overridden
- referenced resources and MCP allowlist provenance are rechecked as active at execution time
- dangerous tools use approval-aware wrappers
- skills may augment instructions or runtime files, but cannot bypass permissions
- provider API keys are resolved by the worker from encrypted workspace credentials
- provider secrets are not placed in prompts, run events, or API responses

### Handoff Authorization

`AgentRunRequest.handoffs` contains only product-owned target references. Each reference must resolve
to one `handoff_agents` definition whose `workspace_id` matches the current runtime context. Missing,
duplicate, or cross-workspace targets fail before `Runner.run()` is called.

The adapter converts an authorized target to an SDK `handoff()` object. `input_filter` is an allowlist
of SDK input item `type` or `role` values; the filter is applied to the handoff input while the full
history remains available to the SDK session. The adapter records source, target, retained counts,
and filtered item types in `AgentRuntimeHandoffResult` and emits a redacted `agent.handoff` event.

### Agents As Tools

`AgentRunRequest.agent_tools` is an immutable tree of product-owned target definitions. The
authorization snapshot admits only active, task-accepting members of the task's team and freezes the
target profile, model-provider binding, capability catalog, file scope, depth, and turn limits.
Every nested level receives the intersection of its own effective catalog and the parent's already
authorized tool, resource, and file scope. Cycles, duplicate targets, cross-workspace targets,
provider-family changes, graph overflow, depth overflow, and tool-name collisions fail before model
execution.

The OpenAI adapter maps each definition to the SDK's public `Agent.as_tool()` interface. Nested tool
execution binds the frozen child `AgentRuntimeContext` instead of inheriting the manager's wider SDK
context. Completion, approval interruption, and failure produce redacted `agent.tool.*` events and
persist source, target, SDK call ID, depth, turn limit, usage, and normalized failure provenance.

Claude's `AgentDefinition` is also an agents-as-tools primitive, but its in-process MCP server is
shared by subagents and cannot enforce a distinct product execution context for each nested agent.
The Claude adapter therefore does not advertise this capability and rejects nested-agent requests
instead of weakening tool or resource scope.

### Structured Output And Guardrails

An agent profile declares its optional output contract at `model_settings.output_schema` with a
stable `name`, Draft 2020-12 `schema`, `strict` flag, and optional `version`. Input and output policy
is declared at `runtime_policy.guardrails`. Each stage is a bounded list of named rules using one of
the supported deterministic kinds: `blocked_terms`, `max_characters`, or `json_schema`. Rules are
blocking by default; a non-blocking failure is recorded as `flagged` and execution continues.

Profile writes reject malformed schemas, duplicate names, unknown policy types, and unbounded rule
configuration. The run authorization snapshot freezes both controls, and the worker reconstructs
only the frozen contract. OpenAI maps the schema to the SDK's public `AgentOutputSchemaBase` and the
rules to SDK `InputGuardrail` and `OutputGuardrail` objects. Claude maps structured output to
`ClaudeAgentOptions.output_format`; because the Claude SDK has no equivalent agent output-guardrail
contract, the Claude adapter evaluates the same product policy immediately before and after its SDK
query.

Successful and non-blocking evaluations are persisted in `AgentRunResult.guardrail_results` and as
redacted `agent.guardrail.*` events. A blocking rule or invalid output raises a non-retryable product
policy error, produces a durable `agent.guardrail.blocked` or `agent.output.invalid` event, does not
activate provider fallback, and does not penalize provider health or the circuit breaker. Evidence
contains only rule identifiers, counts, limits, and failed validator names, never the evaluated
input, output, or matched term.

## Tool Mapping

Product tools map to SDK tools in several ways:

### Function Tools

Product backend functions wrapped as SDK function tools.

Examples:

- `list_workspace_files`
- `read_workspace_file`
- `search_workspace_memory`
- `write_artifact`

### Runtime Tools

Tools that require Docker or self-hosted runtime.

Examples:

- shell command
- apply patch
- render document
- run tests

These tools call Runtime Manager. They never execute on the application host.

### Hosted OpenAI Tools

Tools managed by OpenAI.

Examples:

- web search
- file search
- image generation
- hosted MCP

### MCP Tools

Tools exposed by workspace-installed MCP servers.

Rules:

- each frozen MCP definition includes its exact server and allowlist IDs
- duplicate tool names are excluded from the effective catalog
- the agent only sees MCP tools present in the run manifest
- final arguments are validated against the frozen schema before credential resolution
- write-capable tools follow approval policy
- calls and denials are logged as durable run and security events

## Authorization Snapshot Contract

Authorization snapshot version 2 is immutable for the lifetime of a run. It stores the effective
capability catalog and a canonical fingerprint. The catalog has its own fingerprint so callers can
verify the nested manifest independently.

The snapshot also freezes `runtime_binding`: workspace runtime, runtime space, authorizing runtime
resource IDs, effective network restriction, and gateway-only file IDs. The typed
`AgentRuntimeContext` carries the parsed binding. The worker validates it before constructing the
provider request, and the stdio MCP resolver accepts only the exact active runtime in that binding.
Product file tools use the file IDs as an authorization intersection; Agent stdio processes do not
receive a workspace storage mount.

Configuration edits do not rewrite an existing run. Resource disablement is deliberately dynamic:
the execution gateway checks current workspace-scoped active state immediately before a tool call.
This gives reproducible configuration with an emergency revocation path.

## Run Event Mapping

The runtime layer emits product `RunEvent` records.

Suggested mappings:

| SDK/runtime event | Product event |
| --- | --- |
| run starts | `run.started` |
| model request starts | `model.called` |
| model response received | `model.completed` |
| tool call requested | `tool.called` |
| tool output returned | `tool.completed` |
| handoff selected | `handoff.started` |
| handoff completed | `handoff.completed` |
| approval needed | `approval.requested` |
| artifact generated | `artifact.created` |
| final output ready | `run.completed` |
| exception raised | `run.failed` |

Rules:

- events are append-only
- events include `workspace_id`
- event sequence is monotonic per run
- sensitive values are redacted
- raw provider payloads are optional and should be controlled by debug policy
- stream events use the same redaction rules and carry a monotonic product-owned sequence

## Approval Contract

When a tool or action requires approval:

1. runtime wrapper creates `Approval`
2. runtime emits `approval.requested`
3. run status becomes `waiting_approval`
4. worker exits or pauses safely
5. API records user decision
6. worker resumes or rejects action

Approval payload should include:

- action type
- tool name
- requested arguments
- risk level
- target resources
- human-readable summary

## Artifact Contract

Tools and runtimes may produce files.

Artifact creation flow:

1. runtime produces file or output
2. Runtime Manager collects selected file
3. storage adapter stores bytes
4. product creates `Artifact`
5. runtime emits `artifact.created`
6. task references artifact

Rules:

- runtime files are not artifacts until collected
- artifact belongs to workspace
- artifact download requires permission

## Memory Contract

Short-term state may use SDK sessions. Long-term memory belongs to product storage.

Rules:

- memory retrieval filters by `workspace_id`
- memory write requires explicit product tool or post-run consolidation
- memory entries record source task/run
- agent cannot retrieve memory from other workspaces

## Error Contract

Runtime errors should be normalized.

Error categories:

- `model_error`
- `tool_error`
- `approval_rejected`
- `runtime_error`
- `permission_denied`
- `workspace_mismatch`
- `timeout`
- `cancelled`

Each error event should include:

- category
- message
- retryable
- safe details
- internal reference if needed

## Cancellation Contract

Cancellation can come from user, policy, timeout, worker shutdown, or runtime health failure.

Runtime layer should:

- stop accepting new tool calls
- cancel in-flight runtime command if possible
- update run status
- write `run.cancelled` or `run.failed`
- cleanup runtime according to policy

## Tracing Contract

OpenAI tracing can be enabled for debugging. Claude usage/cost/session metadata is mapped into the
same product events; provider tracing is optional. Product run events remain the durable source of
user-visible truth.

Rules:

- traces are useful but not required for recovery
- product events are stored in Postgres
- trace IDs may be linked from `agent_runs`
- sensitive data redaction policy applies

## Versioning

Agent runs should record:

- agent profile version
- skill versions
- tool policy version
- runtime template version
- model name
- model settings

This makes runs explainable and repeatable.

## Non-Goals

- The SDK layer does not own user permissions.
- The SDK layer does not own workspace membership.
- The SDK layer does not directly read arbitrary files.
- The SDK layer does not execute host commands.
- The SDK layer does not decide billing.
