# Backend Completion Plan

This document tracks what is still incomplete or only implemented as a basic backend foundation. It is intentionally implementation-focused: each item describes the current gap, the production behavior to build, the main API/data changes, the tests required, and the acceptance criteria.

Frontend remains out of scope. The goal is to make the backend feel like a mature commercial control plane for a personal AI company workspace.

## Current Completion Snapshot

Completed foundation:

- user/workspace isolation and RBAC
- machine-aware core resource recommendations for DB pool, Redis pool, thread pool, and worker concurrency defaults
- application lifecycle cleanup for owned Redis clients and blocking executors
- structured log context for request, workspace, user, task, run, and worker IDs
- split health checks for live, ready, and startup probes with DB, Redis, storage, and worker queue readiness
- reusable maintenance runner for registered sweeper jobs with intervals, duration logging, and failure capture
- lightweight metrics registry and `/metrics` endpoint with HTTP request counters and duration buckets
- core domain error hierarchy with API error-envelope mapping
- production configuration guardrails and redacted settings summary
- Redis token-safe distributed lock helper used by worker run locks
- structured idempotency states for in-progress, succeeded, and failed reservations with legacy value compatibility
- core feature flag service with defaults, global settings, workspace overrides, safe disabled unknowns, and redacted enabled-flag summary
- reusable Redis JSON cache abstraction with namespaced keys, TTLs, get-or-set loading, corrupt-value healing, and namespace invalidation
- platform-admin system configuration endpoint exposing redacted settings plus recommended/configured core resource sizing deltas
- blocking executor observability snapshot for configured workers, initialized state, active threads, and queued work items
- database pool observability snapshot for backend, pool class, size, checked-in/out, overflow, max overflow, and raw pool status
- Redis connection pool observability snapshot for max, created, available, and in-use connections
- workspace agents, teams, members, tasks, steps, runs, events, approvals, files, artifacts, exports, and audit records
- worker queue, retries, dead letters, idempotency, heartbeats, and rate limits
- OpenAI Agents runtime contract and fake/runtime adapters
- Docker runtime manager foundation and self-hosted runtime enrollment/job flow
- talent marketplace for public agent listing and hiring
- persistent organization orchestration with PM planning, work packages, parallel dependency execution, PM review, and task messages
- workspace metadata/archive export and import, including task messages
- account-scoped private/public MCP catalog and hosted credential storage

Incomplete or basic-only areas:

- Cloud control plane and runtime spaces are now documented, but the data model, admin APIs, runtime-space reservations, and worker fleet controls are not implemented yet.
- MCP skills are cataloged and authorized, but not yet fully connected to the runtime execution path.
- Skills can be visible as private/public, but public skill copy/provenance and install workflows are incomplete.
- Task observation now has a stable backend API with generic and domain-specific sections for AIGC, novel writing, research, and software tasks. The first implementation composes existing task, step, message, run event, and artifact data; richer domain persistence can be added behind the same response shape.
- Correction/revision is supported through a generic user-facing endpoint that targets tasks, steps, agents, artifacts, or final output and records follow-up work plus task messages.
- Scheduling supports per-member concurrency, workspace active run quotas, per-tick resource-limit prechecks, durable workspace usage reservations, blocked reasons, cross-task priority ordering, and starvation prevention. Docker/self-hosted execution slot usage still needs deeper completion.
- Docker runtime cleanup now records container and managed host-resource evidence and emits critical security events on cleanup failure. Self-hosted runtimes enforce worker concurrency/artifact limits, revocation evidence, and workspace-visible machine trust snapshots; broader manual remediation workflows still need completion.
- Import preview now returns a structured conflict plan for existing names, skipped dependencies, and archive byte limits; preview-token resolution workflows are still pending.
- Model provider selection exists. Queued runs now freeze per-agent/default provider resolution
  metadata without storing secrets, worker execution can use a workspace-scoped fallback policy,
  and provider credentials track health state plus last success/failure details.
- Operations APIs expose short-cached overview, capacity, scheduler, outcomes, runtime capacity,
  MCP job, and unified control-plane health aggregates.
- Workspace memory now has explicit durable memory entries plus workspace-scoped lexical search
  across operational data; full-text/vector indexing remains a future upgrade.
- Core lifecycle, Redis pooling, machine-aware defaults, database transaction retry helpers, structured log context, split health probes, reusable maintenance runner, HTTP metrics, domain error mapping, production config guardrails, Redis distributed locks, structured idempotency states, feature flags, Redis cache abstraction, admin-visible core configuration summaries, blocking executor snapshots, database pool snapshots, and Redis pool snapshots are implemented.

## P0: Cloud Control Plane And Runtime Spaces

Current state:

- Docker runtimes, self-hosted runtimes, queue metrics, worker heartbeats, security events, and scheduler limits exist as separate foundations.
- The platform control plane objects are now specified in [Cloud Control Plane And Runtime Spaces](cloud-control-plane-and-runtime-spaces.md).
- Runtime spaces are now represented as first-class records with workspace/team/task scopes, quota tables, reservation tables, event logs, and workspace-scoped APIs.
- Teams, tasks, task steps, runs, Docker runtimes, runtime commands, and runtime events now carry `runtime_space_id` where applicable.
- Worker node tracking, worker leases, drain requests, and worker/lease operations APIs are now implemented.
- Platform admin APIs now expose global overview, workspaces, workers, worker drain, runtime spaces, runtime-space quarantine, worker leases, queues, dead-letter requeue, runtimes, runtime force-stop, platform policies, and security events behind a separate platform admin token.
- Global risky-execution policy now applies to Docker runtime network access, runtime shell commands, self-hosted runtime registration/job polling, and high-risk MCP tools. Platform admins set global guardrails; task approvals remain workspace-owner approvals, not platform-admin approvals.
- Runtime-space scheduler reservations now enforce `active_runs` capacity for team task steps before run enqueue and release the reservation on run completion, failure, stale-run recovery, or cancellation.
- Runtime-space reservations now merge runtime-space, agent, and task-step resource requirements for CPU, memory, storage, Docker runtime slots, self-hosted job slots, and artifact/log style quota keys before run creation.
- Docker cleanup evidence is now recorded on runtime delete/stale cleanup success and failure, including managed host-resource cleanup for temp directories, staged files, and tracked Docker volumes.

Build:

- [x] Add runtime spaces with workspace, team, and task scopes.
- [x] Bind teams, tasks, task steps, runs, Docker runtimes, and runtime commands to a runtime space where applicable.
- [x] Add runtime space quota records and reservation records for active runs, Docker runtimes, self-hosted jobs, CPU, memory, storage, logs, and artifacts.
- [x] Add worker node records, worker leases, drain status, worker version, and capacity reporting.
- [x] Extend scheduler decisions to reserve runtime-space `active_runs` capacity before enqueueing team task steps.
- [x] Extend runtime-space reservations to Docker runtimes, self-hosted jobs, CPU, memory, storage, logs, and artifacts.
- [x] Add cleanup evidence for Docker runtime delete and stale cleanup.
- [x] Add cleanup evidence for runtime-space temporary storage and staged files declared by a runtime.
- [x] Add operator APIs for workers, runtime spaces, worker leases, workspaces, queues, runtimes, platform policies, and security events.
- [x] Enforce global risky-execution controls inside Docker runtime, self-hosted runtime, and MCP execution paths.
- [x] Keep workspace-owner approval separate from platform-admin policy controls for high-risk tool use.
- [x] Keep admin APIs metadata-only: no raw secrets, raw file contents, or cross-workspace data leakage.

API/data changes:

- [x] Add `runtime_spaces`, `runtime_space_bindings`, `runtime_space_quotas`, `runtime_space_reservations`, and `runtime_space_events`.
- Add `runtime_leases`, `worker_nodes`, `worker_leases`, `scheduler_decisions`, `egress_policy_rules`, and `egress_events` as needed.
- [x] Add `runtime_space_id` to teams, tasks, task steps, runs, workspace runtimes, runtime commands, and runtime events.
- Add `runtime_space_id` to runtime-origin file metadata where applicable.
- [x] Add workspace APIs for runtime spaces.
- [x] Add workspace APIs for operations aggregates.
- [x] Add admin APIs under `/api/v1/admin/...` with platform-operator authentication.
- [x] Add `platform_policies` and `platform_policy_events` for auditable operator policy changes.

Tests:

- runtime space access is workspace-scoped
- a team-bound runtime space cannot be used by another workspace
- team task scheduling cannot reserve beyond runtime-space `active_runs` quota
- cancellation, completion, failure, and stale-run recovery release run-capacity reservations
- timeout and cleanup paths release runtime-space reservations once Docker lease cleanup is implemented
- worker drain prevents new leases but does not corrupt running leases
- admin APIs redact secrets and file contents
- Docker lease cleanup records success or failure evidence

Acceptance:

- a user can operate a persistent team execution space while Docker containers remain controlled and disposable
- platform operators can see and control workers, queues, runtime spaces, quotas, Docker leases, self-hosted trust state, and security events
- platform operators can see self-hosted MCP tool job backlog by status, tool, and oldest queued age
- concurrent multi-task execution is bounded by durable reservations instead of best-effort checks

## P0: MCP Runtime Execution Path

Current state:

- MCP servers, tools, credentials, visibility, and account-scoped authorization are represented in the backend.
- Tool call logging exists, and hosted credentials can be encrypted or referenced through an external vault.
- The execution service now resolves an MCP server/tool from the run authorization snapshot, injects workspace-owned credential references into an adapter, enforces payload policy, and records call logs, run events, task messages, and security events.
- MCP execution also re-checks the runtime context tool set, sends explicitly approval-required tools into workspace approval, and writes authorization snapshot metadata into tool audit records.
- Worker-built agent requests now carry a backend tool executor, and the OpenAI Agents runner registers allowed MCP tools as SDK function tools that call back into the backend execution service.
- HTTP JSON-RPC, remote SSE, and remote hosted MCP servers can now be executed through the adapter resolver. Stdio MCP has a Docker-runtime-only adapter, but it is not selected by the default resolver; callers must explicitly bind it to a workspace runtime so the API host never executes stdio commands directly. Self-hosted runtimes now have an auditable MCP job queue for poll, claim, and completion callbacks, and OpenAI tool calls can submit stdio jobs with an explicit `waiting_self_hosted` result. Completed self-hosted MCP jobs now move waiting runs back to queued, persist pending tool results, and resume through structured tool continuations rendered at the OpenAI runtime boundary.
- A workspace MCP catalog API now summarizes each server's visibility, allowed tools, credential readiness, execution mode, connection summary, and agent-scoped availability.

Build:

- [x] Add an MCP execution service that resolves a requested server/tool from the run authorization snapshot.
- [x] Add a workspace MCP catalog endpoint for product and control-plane views.
- [x] Support stdio, HTTP/SSE, and hosted MCP adapters behind one internal interface.
  - [x] Add HTTP JSON-RPC adapter with credential header injection and sanitized remote errors.
  - [x] Add SSE adapter.
  - [x] Add hosted MCP adapter for declared remote HTTP/SSE transports.
  - [x] Add Docker-runtime stdio adapter that is only usable through explicit runtime binding, not the default API/worker resolver.
  - [x] Route bound Docker-runtime stdio MCP calls through `RuntimeManager.execute_command`.
  - [x] Add self-hosted stdio MCP job dispatch foundation with worker poll, claim, and completion APIs.
  - [x] Wire self-hosted MCP job dispatch into the runtime tool adapter with pending-result semantics for OpenAI tool calls.
  - [x] Add waiting-runtime run state and self-hosted MCP result handoff back to queued runs.
  - [x] Resume completed self-hosted MCP results through structured OpenAI tool continuations instead of mutating orchestration input text.
  - [ ] Replace runner-level continuation rendering with SDK-native tool-call continuation when the OpenAI Agents SDK exposes a stable API for it.
  - [x] Add operations visibility for self-hosted MCP job backlog and tool distribution.
- [x] Inject only the credentials that belong to the current workspace and selected tool.
- [x] Enforce timeout, payload size, response size, allowlisted tool names, and network policy.
- [x] Persist `tool.called`, `tool.completed`, `tool.failed`, and `tool.blocked` run events and task messages.
- [x] Normalize MCP errors without leaking secrets.
- [x] Wire MCP execution into worker/OpenAI tool invocation through runtime tool executor and OpenAI function-tool bridge.
- [x] Add optional approval hooks for high-risk and explicitly approval-required MCP tools.

API/data changes:

- Add `mcp_tool_executions` or extend existing tool call logs with run, task, step, agent, server, tool, latency, status, error code, and payload hash.
- Add runtime policy fields for MCP network mode, timeout, max input bytes, and max output bytes.
- Add an internal worker handler for MCP tool execution jobs.

Tests:

- authorized private MCP tool executes for the owning workspace
- public MCP tool cannot be invoked until copied/installed into the workspace
- cross-workspace MCP server/tool/credential calls are rejected
- blocked tool emits a durable security/audit event
- timeout, oversized input, oversized output, and adapter failure are recorded correctly

Acceptance:

- an agent run can call an approved MCP tool and receive its result through the runtime contract
- every MCP call is workspace-scoped, auditable, and replayable from run events
- secrets never appear in API responses, logs, events, or task messages

## P0: Public Skill Install, Copy, And Provenance

Current state:

- Skills have private/public visibility.
- Public agents can be hired into a workspace as isolated copies.
- Public skill installs now persist a workspace-local install snapshot with installed key, name, version, capability keys, manifest, source visibility, and source checksum.
- Run authorization snapshots include installed skill provenance for active workspace-local skill installs referenced by the agent profile.
- Installed skills can now be upgraded to a newer installable source skill and disabled for future runs without mutating historical run snapshots.

Build:

- [x] Add a skill install flow that copies a public skill into the target workspace.
- [x] Preserve immutable source metadata: source skill ID, source owner, source version, source checksum, install time, installed by user.
- [x] Let users upgrade an installed skill to a newer public version without rewriting historical runs.
- [x] Make agents reference installed workspace-local skills, not remote public source rows.
- [x] Add uninstall/disable behavior that blocks future runs while keeping historical run snapshots intact.

API/data changes:

- `POST /api/v1/workspaces/{workspace_id}/skills/{public_skill_id}/install`
- [x] `POST /api/v1/workspaces/{workspace_id}/capabilities/workspace-skills/{installed_skill_id}/upgrade`
- [x] `POST /api/v1/workspaces/{workspace_id}/capabilities/workspace-skills/{installed_skill_id}/disable`
- Add `installed_from_skill_id`, `installed_version`, `source_checksum`, `installed_by_user_id`, and `disabled_at` fields if not already represented.
- [x] Extend run authorization snapshots with installed skill IDs, versions, and source checksums.
- [x] Extend run authorization snapshots with installed skill MCP tool allowlists and
  credential references, excluding secret payloads and external refs.

Tests:

- install public skill copies data without copying source workspace secrets
- installed skill can be attached to a local agent
- disabling a skill blocks new runs but does not mutate old run snapshots
- source owner updates do not silently change installed skill behavior
- cross-workspace direct use of public source skill is rejected

Acceptance:

- users can safely consume public skills as local workspace copies
- every run can prove exactly which installed skill version and tool allowlist was used

## P0: Workspace Quotas And Multi-Task Priority Scheduling

Current state:

- Per-member max concurrency exists.
- Runtime capacity is considered during member matching.
- Workspace-level active run limits and cross-task priority scheduling are now enforced by the workspace scheduler.
- Blocked queued steps record a scheduling status and blocked reason in step dependencies for observation and operations.
- Workspace active run, CPU, memory, storage-style quota usage, Docker runtime slots, and
  self-hosted job slots have durable reservation and release at run lifecycle boundaries.
- Workspace quota limits are now manageable through authenticated workspace APIs, including
  active run, Docker runtime, self-hosted job, CPU, memory, and storage-style quota keys.

Build:

- [x] Add a central scheduler service that selects eligible steps across all queued tasks in a workspace.
- [x] Enforce workspace-level limits for active tasks, active runs, Docker runtimes, self-hosted jobs, CPU, memory, and storage.
  - [x] Enforce active run limits from `workspace.settings.scheduler.max_active_runs`.
  - [x] Enforce per-tick scheduler resource limits from `workspace.settings.scheduler.resource_limits`.
  - [x] Enforce durable workspace active run, CPU, memory, and storage-style usage reservations in scheduler decisions.
  - [x] Enforce durable Docker runtime and self-hosted job slot usage reservations in scheduler decisions.
- [x] Support priority ordering.
- [x] Add starvation prevention for long-waiting lower-priority work.
- [x] Reserve workspace and runtime-space capacity before enqueueing a run.
- [x] Release workspace and runtime-space capacity on completion, failure, cancellation, timeout, or cleanup.
- [x] Add scheduling reason codes for blocked work.

API/data changes:

- [x] Add workspace quota settings and current usage counters.
- Add `scheduling_status`, `blocked_reason`, `scheduled_at`, and `priority_score` to task steps or a scheduling table.
- Add operations endpoints for queue depth by priority and blocked scheduling reasons.

Tests:

- high-priority task runs before lower-priority queued work
- per-member and workspace-level concurrency are both enforced
- blocked steps resume when capacity is released
- cancellation releases reservations
- scheduler does not double-start the same step under concurrent workers

Acceptance:

- multiple tasks can be submitted at once and are executed predictably under workspace quotas
- users can understand why work is waiting

## P0: Task Observation Views For Different Team Types

Current state:

- Tasks, steps, messages, artifacts, and domain state are persisted.
- Generic task state is visible, and domain task extensions exist.
- `GET /api/v1/workspaces/{workspace_id}/tasks/{task_id}/observation` now returns stable `view_type`, `summary`, `sections`, and typed `cards`.
- Generic, AIGC, novel, research, and software views are composed from existing durable records and degrade gracefully when optional domain data is missing.
- Task message payloads are sanitized before entering the observation response.
- Observation now includes a quality section with revision history and risk flag cards composed from correction metadata, PM revision decisions, scheduler blockers, and approval/review payloads.
- Worker run completion can now merge structured agent progress output into `Task.input`, `Task.generic_state`, and `Task.domain_state`, then emit a `task.progress.updated` message for observation.

Build:

- [x] Add a task observation service that composes status from task, steps, messages, artifacts, domain state, and run events.
- [x] Define reusable observation sections: timeline, current blockers, active agents, step progress, produced artifacts, and review state.
- [x] Add domain-specific views:
  - [x] AIGC: prompt, variants, selected asset, model/settings, review notes, asset versions through artifact cards.
  - [x] Novel writing: outline, chapters, scenes, characters, continuity notes, word count, editorial review.
  - [x] Research: source list, claims, confidence, citations, extracted notes, report sections.
  - [x] Software: requirements, design tasks, branches/patches, tests, build status, review comments.
- [x] Keep the API schema stable by returning `view_type`, `sections`, and typed `cards`.
- [x] Add richer persisted domain-specific progress writers from worker outputs.
- [x] Add revision history and risk flag cards from correction commands, PM revision decisions, scheduler blockers, and review payload risk metadata.

API/data changes:

- [x] `GET /api/v1/workspaces/{workspace_id}/tasks/{task_id}/observation`
- [x] optional `?view_type=auto|generic|aigc|novel|research|software`
- [x] Add domain observation schema models with versioned card types.

Tests:

- [x] generic observation works for every task
- [x] each domain view returns deterministic sections from seeded state
- [x] cross-workspace observation is rejected
- [x] missing optional domain data degrades gracefully
- [x] artifacts are included only when authorized by workspace scope

Acceptance:

- the frontend can render very different AI company workflows from one backend observation API
- users can see what is happening and what needs correction without reading raw run logs

## P0: Generic User-Facing Correction Endpoint

Current state:

- PM decisions can create revision or missing-work follow-up steps.
- Some domain correction flows exist.
- A generic endpoint lets users correct a task, step, agent output, artifact, or final output from one consistent API.

Build:

- [x] Add a correction command service with targets: task, step, agent, artifact, and final output.
- [x] Support correction modes: revise, regenerate, add_missing_work, replace_artifact, and stop_work.
- [x] Convert correction commands into new queued correction work packages and manager review steps.
- [x] Preserve original outputs and link corrections through follow-up step metadata.
- [x] Add task messages and audit events for every user correction and resulting assignment.

API/data changes:

- [x] `POST /api/v1/workspaces/{workspace_id}/tasks/{task_id}/corrections`
- [x] Add correction request/response schemas.
- [x] Add correction metadata to `TaskStep.dependencies`; a dedicated `task_corrections` table can be added later if query needs grow.

Tests:

- [x] correcting one step creates a new revision step only for that scope
- [x] correcting final output creates PM reconciliation work
- [x] correcting an artifact creates replacement work and preserves old artifact
- [x] invalid/cross-workspace target is rejected
- [x] correction event appears in task messages

Acceptance:

- users can steer an AI team after observing partial work without restarting the entire task

## P1: Import Conflict Preview

Current state:

- Workspace metadata and archives can be imported.
- Dry-run counts created/skipped resources and returns a structured `conflict_plan`.
- Dedicated metadata/archive preview endpoints run as dry-runs and do not write database rows or storage blobs.
- Existing-name conflicts, missing dependency skips, missing archive bytes, oversized objects, and archive total byte limits are reported with collection, source ID, field, severity, and strategy.

Build:

- [x] Add an import preview service that validates the archive and builds a resource-by-resource conflict plan.
- [x] Detect name conflicts, missing dependencies, missing bytes, oversized objects, and archive total byte limits.
- [x] Return suggested skip/reject strategies for currently supported conflict types.
- Detect disabled skills, missing runtime policies, unsupported format versions, checksum mismatches, and quota violations.
- Return richer suggested resolutions: rename, replace, install dependency, or reject with required user action.
- Let committed import accept a preview token or explicit resolution map.

API/data changes:

- [x] `POST /api/v1/workspaces/{workspace_id}/exports/metadata/import/preview`
- [x] `POST /api/v1/workspaces/{workspace_id}/exports/archive/import/preview`
- [x] Add `conflict_plan` response schema with `collection`, `source_id`, `field`, `source_value`, `target_value`, `strategy`, `severity`, and `message`.
- Add richer preview response sections: `resources`, `estimated_counts`, and `required_resolutions`.

Tests:

- [x] duplicate agent/team/task names generate skip-existing conflict items
- missing task mapping blocks dependent artifact unless skipped
- [x] oversized blobs are reported before commit
- [x] preview does not write database rows or storage blobs
- committed import honors selected resolutions

Acceptance:

- users can see exactly what an upload will create, skip, or rename before committing data import

## P1: Docker Runtime Quotas, Cleanup Verification, And Evidence

Current state:

- Docker runtime management and safety defaults exist.
- Image allowlist and disabled network defaults exist.
- Quota enforcement exists at runtime creation, and cleanup success/failure now records structured runtime evidence.

Build:

- Enforce CPU, memory, process, disk, network, timeout, and output limits at container creation.
- Track runtime lease lifecycle from reservation to cleanup.
- [x] Verify container cleanup action and record structured success/failure evidence.
- [x] Verify volume, temp directory, and staged file cleanup for managed runtime resources.
- [x] Emit security events when cleanup fails.
- Emit security events when a container exceeds policy.
- Add periodic sweeper for abandoned containers and stale runtime leases.

API/data changes:

- Add runtime lease records or extend runtime events with lease IDs.
- [x] Add cleanup status and evidence metadata to runtime events.
- Add operations endpoint for leaked/stale runtime resources.

Tests:

- over-limit runtime request is rejected before container creation
- timed-out command is killed and marked failed
- cleanup success records evidence
- [x] simulated cleanup failure emits a security event
- sweeper cleans stale leases idempotently

Acceptance:

- hosted execution can be audited and trusted not to leave user code or data running on the host

## P1: Self-Hosted Runtime Policy, Quotas, And Revocation

Current state:

- Users can enroll a self-hosted runtime, heartbeat, poll jobs, update progress, upload artifacts, and revoke credentials.
- Workers enforce `max_concurrent_jobs`, artifact upload byte limits, runtime-space allowlists, allowed tools, network mode expectations, supported model lists, supported runtime lists, degraded/quarantined stale-machine transitions, and credential revocation that records actor/reason/final heartbeat/affected jobs while blocking future API use.
- Workspace runtime managers can list self-hosted worker trust snapshots with trust state, runtime/credential state, policy summary, capabilities, and last heartbeat.
- Broader manual remediation UX still needs more depth.

Build:

- Add per-machine capability policies: allowed tools, max concurrent jobs, max artifact bytes, network expectations, and supported models/runtimes.
  - [x] Enforce max concurrent jobs.
  - [x] Enforce max artifact bytes.
  - [x] Enforce runtime-space allowlists.
  - [x] Enforce allowed tools, network expectations, and supported models/runtimes.
- [x] Add machine trust state snapshots: active, degraded, quarantined, revoked, and offline.
  - [x] Mark worker/runtime revoked when credentials are revoked.
  - [x] Add degraded/quarantined trust automation.
- [x] Enforce job assignment only to compatible trusted machines.
  - [x] Block offline/revoked/quarantined workers from polling or claiming jobs.
- Record revocation reason, actor, affected jobs, credential rotation evidence, and final heartbeat state.
  - [x] Record credential revocation evidence in runtime and runtime-space events.
  - [x] Record revocation actor, reason, affected claims/runs, failed run state, and final heartbeat state.
- Add stale heartbeat quarantine and user-visible warnings.

API/data changes:

- [x] Extend self-hosted runtime policy schema through worker capability policy summaries.
- [x] Add revocation event metadata and machine trust status.
- Add operations endpoints for stale/degraded machines. (Initial cleanup endpoint now returns degraded/quarantined counts.)
- [x] Add workspace API for self-hosted machine trust snapshots.

Tests:

- [x] incompatible job is not assigned to a self-hosted runtime
- [x] revoked runtime cannot poll or upload artifacts
- [x] stale heartbeat moves machine to degraded/quarantined state
- [x] revocation audit contains actor, reason, and affected job IDs
- [x] trust snapshot exposes active, degraded, and revoked states with policy summaries

Acceptance:

- users can safely use their own machines without the cloud backend losing control of policy and auditability

## P1: Model Provider Audit And Fallback

Current state:

- Cloud base URL, key, and model can be configured globally and per agent.
- Provider credentials are stored securely.
- Queued team runs now include a run-level provider resolution snapshot and a
  `model_provider.resolved` run event.
- Worker execution supports workspace-scoped provider fallback with same-workspace credential
  enforcement.
- Provider credentials expose `health_status`, `last_success_at`, `last_failure_at`,
  `last_failure_code`, and `last_failure_message`.
- Workspaces expose a sanitized provider usage audit API for selected provider, fallback
  decisions, failed provider reference, and reason metadata.

Build:

- Resolve model provider at run creation and snapshot provider ID, base URL host, model, key
  fingerprint/reference, and source policy without raw API keys.
- Add retry/fallback policy for provider errors, rate limits, and model unavailability. (Done for
  workspace settings policy.)
- Record which provider/model actually handled each run. (Done with `model_provider.used` run
  events.)
- Prevent fallback from crossing user/workspace policy boundaries. (Done for credential
  resolution.)
- Add cost fields as optional metadata only; billing remains out of scope.

API/data changes:

- Extend run authorization snapshot and run events with provider resolution metadata. (Done for
  queued team runs.)
- Add provider health status and last failure reason. (Done.)
- Add audit action for provider selection and fallback. (Done with `model_provider.used`
  audit events that mark whether fallback was selected.)
- Add workspace usage audit endpoint for provider selection and fallback history. (Done with
  `GET /api/v1/workspaces/{workspace_id}/model-provider-credentials/usage-audit`.)

Tests:

- agent-specific provider overrides workspace default
- provider resolution snapshot excludes raw API keys and full base URLs
- failed primary provider falls back only to allowed provider
- [x] fallback event records reason without leaking key/base URL secret
- [x] provider usage audit API is workspace-scoped and redacts secrets
- [x] disallowed fallback fails the run cleanly and records sanitized fallback-unavailable
  run/audit evidence

Acceptance:

- every agent run can be traced to the exact model provider policy used, including fallback decisions

Policy shape:

```json
{
  "model_provider_fallback": {
    "enabled": true,
    "retry_error_codes": ["RuntimeError"],
    "candidates": [
      {
        "credential_id": "workspace-credential-uuid",
        "model": "backup-model"
      }
    ]
  }
}
```

## P1: Operations Dashboard Aggregate APIs

Current state:

- Operations APIs expose queue metrics, failed runs, runtime events, audit filters, security events,
  dashboard capacity aggregates, scheduler backlog, outcomes, MCP job status, runtime capacity,
  and a unified control-plane health summary.

Build:

- [x] Add aggregate endpoints for worker fleet health, queue latency, queued/running counts by
  priority, runtime saturation, Docker/self-hosted capacity, failure rates, approval backlog, MCP
  jobs, and control-plane issue summaries.
- [x] Add capacity aggregate for queue age, worker slot utilization, and runtime space quota saturation.
- [x] Add scheduler aggregate for backlog, priority buckets, blocked reasons, active runs, and
  effective workspace scheduler policy.
- [x] Add outcomes aggregate for run failure rate, failure reasons, and approval backlog.
- [x] Support time windows and workspace scope.
- [x] Cache overview aggregate in Redis with short TTL and workspace-scoped cache keys.
- Cache future expensive scheduler/runtime aggregates in Redis with short TTL.
- Keep raw drill-down endpoints separate from summary endpoints.

API/data changes:

- `GET /api/v1/workspaces/{workspace_id}/operations/overview`
- `GET /api/v1/workspaces/{workspace_id}/operations/capacity`
- `GET /api/v1/workspaces/{workspace_id}/operations/scheduler`
- `GET /api/v1/workspaces/{workspace_id}/operations/outcomes`
- [x] `GET /api/v1/workspaces/{workspace_id}/operations/runtime-capacity`
- [x] `GET /api/v1/workspaces/{workspace_id}/operations/mcp-jobs`
- [x] `GET /api/v1/workspaces/{workspace_id}/operations/control-plane`

Tests:

- [x] aggregates are workspace-scoped
- empty workspace returns zeroed metrics
- [x] failed/running/queued seeded data produces expected counts
- [x] control-plane summary emits stable issue codes for capacity, scheduler, approvals, and MCP
  health
- cache key includes workspace and time window

Acceptance:

- backend can power an operations screen without frontend-specific query stitching

## P1: Real Workspace Memory Search

Current state:

- `search_workspace_memory` now performs workspace-scoped lexical search across tasks, task steps, task messages, files, artifacts, and domain items.
- Agents can write explicit durable workspace memory entries through `remember_workspace_memory`,
  and archived entries stop appearing in search results.
- It is intentionally lightweight and keeps a clean service boundary for later indexing/vector upgrades.

Build:

- [x] Define initial workspace memory sources: task summaries/final outputs, task steps, task messages, artifacts metadata, file metadata, and domain items.
- [x] Replace placeholder implementation with a workspace-scoped lexical search service.
- [x] Enforce workspace authorization at query time by filtering every source by `workspace_id`.
- Add a memory indexing service with deterministic text chunks and metadata. (Initial durable
  memory entry source is in place; chunk/index worker remains pending.)
- Start with Postgres full-text search; leave a clean interface for vector search later.
- [x] Enforce workspace, file, and task authorization at query time.
- Add freshness rules when tasks/artifacts change.

API/data changes:

- [x] Add `workspace_memory_entries` table with source type, source ID, text, metadata,
  visibility, tags, importance, and status.
- Add internal indexing jobs.
- [x] Replace placeholder implementation with real search ranked by text relevance and recency.
- [x] Add internal tool operations to create and archive explicit memory entries.

Tests:

- [x] task summary appears in workspace memory search
- [x] private workspace memory is not visible cross-workspace
- [x] archived explicit memory stops appearing
- search respects max results and source filters

Acceptance:

- agents can retrieve relevant prior workspace context without ad hoc direct database access

## P2: Dynamic Manager Planning And Planning Failure Review

Current state:

- PM planning creates structured project plans and validates them before execution.
- Failure handling is basic, and dynamic replanning is only partially represented by PM revision decisions.
- Planning attempts are queryable per task, including failed validation errors and final
  future-only regeneration snapshots.

Build:

- [x] Add planning attempts with prompt/input snapshot, output, validation errors, status, and retry count.
- [x] Retry failed planning after corrected task input or refreshed team snapshot.
- [x] If planning fails, create a human approval/review item with validation errors and suggested fixes.
- [x] Support future-only plan regeneration after hiring new team members, preserving completed work.

API/data changes:

- [x] Add `task_planning_attempts` table for queryable planning attempts.
- [x] Add `POST /api/v1/workspaces/{workspace_id}/tasks/{task_id}/plan/retry`
- [x] Add `POST /api/v1/workspaces/{workspace_id}/tasks/{task_id}/plan/regenerate`
- [x] Add `GET /api/v1/workspaces/{workspace_id}/tasks/{task_id}/planning-attempts`

Tests:

- [x] invalid PM plan records validation errors
- [x] retry can repair a failed plan
- [x] failed planning creates an approval/review item
- [x] future-only regeneration does not rewrite completed steps
- [x] planning-attempt history is workspace-scoped and exposes retry/regeneration evidence

Acceptance:

- planning failures become visible, recoverable workflow states instead of opaque run failures

## P2: Artifact Work Package Version Metadata

Current state:

- Files and artifacts are linked to tasks and runs.
- Task steps can describe expected artifacts.
- Generated artifacts are bound to producing work packages with version metadata and can be
  queried as a dedicated work-package history.

Build:

- [x] Bind produced artifacts to task ID, step ID, work package ID, agent ID, run ID, and artifact version.
- [x] Record supersedes relationships for subsequent work-package outputs.
- [x] Expose artifact version metadata in artifact list responses, task observation, workspace memory, and export/import payloads.
- [x] Expose dedicated artifact history for a work package.
- Expose dedicated artifact history for final output.

API/data changes:

- [x] Add `task_step_id`, `work_package_id`, `agent_profile_id`, `version`, `supersedes_artifact_id`, and `review_status` fields to artifacts.
- [x] Add artifact version response fields.
- [x] Add `GET /api/v1/workspaces/{workspace_id}/artifacts/history`.

Tests:

- [x] first generated artifact is version 1 for its work package
- [x] subsequent output creates version 2 and preserves version 1
- [x] artifact download/list authorization still works after versioning
- [x] artifact history endpoint is workspace-scoped and returns newest version first

Acceptance:

- users can tell which agent produced which file for which package and which version is current

## P2: OpenAI Agents Event Mapping Into Task Messages

Current state:

- Product run events and task messages exist.
- Step start/completion, PM decisions, and staffing events are persisted.
- Low-level OpenAI Agents handoff/tool/runtime-wait/fallback events are mapped into the
  user-visible task communication stream with secret redaction.

Build:

- [x] Add an event mapper from runtime event types to normalized task message types.
- [x] Filter noisy internal events.
- [x] Group related low-level events into readable milestones where appropriate.
- [x] Preserve raw run events for debugging while exposing concise task messages for users.

API/data changes:

- [x] Add message types for agent handoff, tool request, tool result summary, approval wait, and model fallback.
- [x] Add message types for blocked tools and runtime-wait/self-hosted handoff states.
- [x] Add mapper configuration to runtime contract.

Tests:

- [x] tool event creates a task message with sanitized payload
- [x] handoff event creates a message linked to source/target agents
- [x] secret-bearing event fields are redacted
- [x] blocked tool, runtime wait, and fallback event aliases create sanitized task messages
- [x] raw run events remain available separately

Acceptance:

- users can understand multi-agent collaboration without inspecting raw SDK event logs

## P2: Security Review Test Suite Expansion

Current state:

- Many workspace isolation tests exist.
- Account-scoped skill/tool invocation needs a broader adversarial test suite.
- Negative coverage now includes disabled explicit model-provider overrides and artifact-history
  foreign task IDs.

Build:

- Add dedicated security tests for MCP, skill install/provenance, files, artifacts, runtimes, model providers, self-hosted jobs, and marketplace installs.
- Add negative tests for forged IDs, stale snapshots, disabled credentials, revoked self-hosted runtimes, and public source skill misuse.
  - [x] Disabled explicit model-provider credential override is rejected.
  - [x] Artifact history does not leak artifacts when a foreign task ID is supplied.
- Add property-style tests for workspace ID mismatch where practical.

Tests:

- every workspace-scoped route rejects foreign resources
- every internal service that builds a run context rejects mismatched task/step/agent/tool/resource IDs
- every secret-bearing response is redacted
- every denied high-risk action records audit or security evidence

Acceptance:

- security regressions are caught before manual review

## Recommended Implementation Order

1. Cloud control plane and runtime spaces.
2. MCP runtime execution path.
3. Public skill install/copy/provenance.
4. Workspace quotas and multi-task priority scheduling.
5. Task observation views.
6. Generic correction endpoint.
7. Import conflict preview.
8. Docker quota cleanup verification.
9. Self-hosted runtime policy and revocation hardening.
10. Model provider audit and fallback.
11. Operations dashboard aggregate APIs.
12. Real workspace memory search.
13. Dynamic planning failure review and future-only regeneration.
14. Artifact work package version metadata.
15. OpenAI Agents event mapping into task messages.
16. Security review test suite expansion.

## Definition Of Done For Each Remaining Module

- Has workspace-scoped API contracts or internal service contracts.
- Has durable database state where behavior must survive restarts.
- Has audit/security events for user-impacting or risky actions.
- Has unit tests for success and denial paths.
- Has at least one integration-style API or worker test for the full flow.
- Does not leak credentials, file contents, model keys, base URLs, or foreign workspace IDs.
- Updates this completion plan and `backend-task-breakdown.md` when finished.
