# Agent Runtime Contract

## Purpose

This document defines the boundary between the ChainCloud product layer and the OpenAI Agents SDK runtime layer.

The product layer owns users, workspaces, tasks, permissions, runtimes, approvals, files, artifacts, and audit logs. The OpenAI Agents SDK layer owns agent execution primitives such as `Agent`, `Runner`, tools, handoffs, guardrails, sessions, and traces.

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
- `allowed_tool_ids`
- `allowed_file_ids`
- `approval_policy`
- `memory_policy`

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
AgentProfile.handoffs       -> Agent.handoffs
AgentProfile.guardrails     -> Agent input/output/tool guardrails
AgentProfile.output_schema  -> Agent.output_type
```

Rules:

- only enabled tools are attached
- only same-workspace handoff targets are attached
- runtime-only tools are wrapped with product permission checks
- dangerous tools use approval-aware wrappers
- skills may augment instructions or runtime files, but cannot bypass permissions
- provider API keys are resolved by the worker from encrypted workspace credentials
- provider secrets are not placed in prompts, run events, or API responses

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

- tool names are namespaced
- agent only sees allowed MCP tools
- write-capable tools follow approval policy
- calls are logged as run events

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

OpenAI tracing can be enabled for debugging. Product run events remain the durable source of user-visible truth.

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
