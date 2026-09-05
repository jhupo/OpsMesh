# Backend Next Task Table

This table is the active execution checklist for the next backend phase. It keeps the work ordered, testable, and scoped to backend commercial-software reliability.

Frontend remains out of scope. Billing remains out of scope.

## Execution Rules

- Finish one functional point at a time.
- Run targeted pytest files for the touched module.
- Run targeted `ruff check`.
- Do not run the full pytest suite unless preparing a release tag.
- Run `git diff --check`.
- Commit and push each completed functional point.
- Keep API responses metadata-only and redacted.
- Do not include mockups or frontend assets in backend commits.

## Completed Reliability Phase

| Order | Priority | Task | Current Status | Acceptance Evidence |
| --- | --- | --- | --- | --- |
| 1 | P0 | Scheduler blocked-step explain API with stable reason codes | Done | `/operations/blocked-steps`, scheduler blocked reason `code/message/resource_key`, tests in `test_operations_api.py` |
| 2 | P0 | Scheduler blocked-step unblock API | Done | `/operations/blocked-steps/unblock`, audit event, filter-required guard, tests in `test_operations_api.py` |
| 3 | P0 | Runtime-space diagnostics view | Done | `/runtime-spaces/{id}/diagnostics` returns quota usage, active reservations, blocked steps, linked runtime metadata, and redacts secrets/container IDs |
| 4 | P0 | Runtime-space operator resolution workflow | Done | Reset now returns outcome diagnostics, releases active reservations, clears runtime-space blocked steps, and records affected runtimes; force-release also reports cleared blocked steps and links the resolution in runtime-space events |
| 5 | P0 | Scheduler quota and concurrency hardening | Done | workspace/runtime-space quota increments are atomic; concurrent session tests prove one reservation wins and the other is blocked without oversell |
| 6 | P0 | Worker long-task recovery | Done | `/operations/stale-runs` diagnoses stale queued/running/waiting_runtime runs with lease metadata; `/operations/stale-runs/recover` requeues stale queued runs, fails closed stale running/waiting_runtime runs, expires linked worker leases, and audits the action |
| 7 | P1 | Self-hosted machine operations hardening | Done | Machine-level quarantine/resume/revoke endpoints, stale job-claim cleanup, reconnect-aware resume state, and trust-view policy diagnostics are covered by `test_self_hosted_runtime.py` |
| 8 | P1 | Memory indexing abstraction | Done | Memory search now uses a pluggable backend protocol with lexical fallback, Postgres full-text adapter support, backend metadata in results, and workspace-scoped tests proving no cross-workspace leakage |
| 9 | P1 | MCP/OpenAI Agents SDK-native continuation migration hook | Done | OpenAI raw output now persists stable `sdk_continuation` metadata for future SDK-native migration, and MCP/self-hosted continuation tests cover completed and failed tool results |
| 10 | P1 | Continuous redaction audit | Done for current phase | New metadata surfaces from this phase are covered: runtime/stale/self-hosted diagnostics avoid sensitive fields, policy diagnostics redact nested metadata, and run API output redacts `sdk_continuation` token/base_url/header values |
| 11 | P0 | Queue governance and DB/Redis reconciliation | Done | `/operations/queue-governance` diagnoses orphaned/non-runnable/duplicate/old queued jobs, missing queued runs, dead-letter pressure, and scan truncation; `/operations/queue-governance/reconcile` can force requeue missing runs and remove stale queue jobs with audit coverage |

## Next Phase Goal Table

| Order | Priority | Goal | Current Status | Acceptance Evidence |
| --- | --- | --- | --- | --- |
| 1 | P0 | VPS systemd deployment assets and smoke test for API/worker on shared Postgres/Redis | Done | `deploy/server/systemd/`, `deploy/server/env.example`, `scripts/server-smoke-test.sh`, deployment asset tests, and server health verified on a staging VPS |
| 2 | P0 | Workspace/team Docker runtime isolation closure | Done | Runtime containers now carry workspace/runtime/runtime-space/team identity, a dedicated `/workspace` Docker volume, network-policy metadata, managed volume cleanup, team/runtime-space policy resolution, and explicit isolation diagnostics |
| 3 | P0 | Agent model configuration closure | Done | Workspace default provider, per-agent override, cloud-downloaded base URL/key/model, fail-closed provider behavior, and redacted audit coverage are covered by model provider service and runtime tests |
| 4 | P0 | Multi-agent project execution loop | In progress | Reusable team org chart API, team execution overview API with staffing-gap recommendations, task risk scoring, team-level recommended actions, delivery-health scoring, team bottleneck diagnostics, metadata-only intervention plans, team command center API, command-center action-plan dry-run/apply with source/action filtering and de-duplicated team operator actions, optional team-scoped run enqueue after command-center apply, execution-loop task finalization dry-run/apply for PM-approved tasks, manual execution-loop enqueue action, unified execution-loop iteration endpoint that finalizes PM-approved work before applying remaining team actions and enqueueing runs, handoff/manager-queue team action payload plans, team-level manager-review/requeue/downstream operator actions, task execution diagnostics with step-level handoff state, workspace/team-filtered handoff queue, project-plan quality diagnostics, correction lifecycle diagnostics, task execution timeline API, manager handoff/acceptance diagnostics, workspace/team-filtered manager queue API, and domain-specific observation status cards for AIGC/novel/research/software tasks are implemented; operator actions now requeue blocked steps, schedule downstream handoff work, automatically emit `reassign_step` for specialist reassignment, and request manager review steps; task message/event-stream regressions cover DB-only append, Redis replay, SSE redaction, stable `event_id`/`outbox_id` dedupe semantics, and worker maintenance count rollups |
| 5 | P1 | MCP skill lifecycle and tool permission hardening | In progress | Private/public skill install lifecycle and version snapshots exist; skill installs now support impact analysis, upgrade history, rollback, disable, availability checks, per-agent tool-policy diagnostics, workspace-level tool-policy matrix, MCP tool-call execution audit boundary checks, configurable stale MCP health-check blocking, execution fail-closed for stale MCP health, health-check refresh reporting, per-run/hourly MCP tool call limits with catalog policy summaries, workspace capability governance summaries with recommended operator actions, dry-run/apply governance remediation, explicit MCP connection reconfiguration, and encrypted/external credential rotation without exposing secrets; remote HTTP/SSE and isolated stdio now use the official MCP Python SDK, while runtime image packaging and live server validation remain |
| 6 | P1 | Self-hosted worker install and upgrade channel | Done | Registration token, connector bootstrap manifest, heartbeat/version policy diagnostics, job slots, upgrade/drain/quarantine/revoke flow, and trust diagnostics are implemented |
| 7 | P1 | Operations control plane expansion | Done | Runtime/container quota dashboard API, backlog/failure/latency aggregates, blocked-step explanations, and metadata-only operator responses are covered by operations API and tests |
| 8 | P1 | Workspace data lifecycle | In progress | Import/export restore path, artifact versioning, archive job download, archive integrity verification, and file access audit exist; lifecycle diagnostics reports backup/retention/restore-drill readiness, backup schedule due/overdue state, and automation visibility for latest scheduled jobs, active exports, recent lifecycle events, skipped reasons, retention auto-apply status, and scheduled restore-drill status; worker maintenance now enqueues due scheduled archive backups, can run explicitly opted-in retention auto-apply, and can execute due restore drills against the latest successful archive with audit evidence; recovery readiness summarizes archive export/import health, backup integrity checks, backup freshness, backup coverage scoring, restore test history, restore drill evidence, import conflict preview history, overdue backup schedule warnings, and restore recommendations; recovery-readiness actions now support dry-run/apply archive export remediation, restore-import-test remediation, and integrity verification remediation with active-job dedupe, audit evidence, and redacted metadata; retention preview/apply identifies scoped candidates, returns recommended operator actions, soft-deletes expired files, requires successful backups by default, and audits operator actions |

## Execution Rules For This Phase

1. Implement one backend goal at a time.
2. Validate locally with targeted tests and lint.
3. Validate runtime behavior on a staging VPS before committing.
4. Commit and push after each verified goal.
5. Keep frontend and billing out of scope.

## Active Productization Phase

This phase starts after the persistent team runtime operations layer. The backend now has
team runtime state, mailbox/session recovery, operations-console metadata, worker
maintenance enqueue, model-provider protocol selection, and SDK tracing provenance.
The remaining work is to turn those pieces into production-grade operating loops.

| Order | Priority | Goal | Current Status | Acceptance Evidence |
| --- | --- | --- | --- | --- |
| 1 | P0 | Multi-agent project execution E2E closure | Done | `test_multi_agent_handoff_survives_worker_restart_and_manager_approval` proves manager planning, specialist handoff, worker restart recovery, manager approval, downstream completion, lease cleanup, and redacted collaboration state; `test_worker_executes_openai_agents_runner_through_control_plane` covers the control-plane to OpenAI Agents SDK runner path without network access |
| 2 | P0 | Provider/model operations closure | Pending | Each agent can declare provider/model/protocol; operations APIs expose readiness, fallback, budget/limit metadata, failure reasons, and redacted provenance for OpenAI, OpenAI-compatible, and Anthropic paths |
| 3 | P1 | MCP tool permission production closure | In progress | Workspace/team/agent tool-policy matrix can explain, dry-run, apply, and audit permission remediation; explicit MCP connection reconfiguration resets health and is audited, credential rotation supports hosted encryption and external references, and remote Streamable HTTP/SSE plus isolated stdio execution delegate to the official MCP Python SDK; runtime image packaging and live server validation remain |
| 4 | P1 | Workspace data lifecycle production closure | Pending | Backup, restore drill, retention, import conflict preview, integrity verification, and recovery recommendations form an auditable closed loop with scheduled-job evidence |
| 5 | P1 | Long-running reliability proof | Pending | Targeted local tests plus isolated staging Postgres/Redis validation cover restart recovery, stale heartbeat handling, queue dedupe, provider-readiness blocking, and redaction |

## Immediate Backend Work Queue

| Order | Priority | Task | Status | Notes |
| --- | --- | --- | --- | --- |
| 1 | P0 | Add a team execution readiness/recovery summary that combines runtime health, mailbox backlog, handoff queue, manager queue, provider readiness, and recommended next operator action | Done | Operations console now returns readiness status, stall state, queue/mailbox/blocking counts, and the next redacted operator action; repeated no-progress loop iterations mark the runtime stalled |
| 2 | P0 | Add provider/model operation diagnostics per team member and per queued run | Done | Operations console provider management returns per-member bindings plus active/queued run diagnostics with frozen run provider metadata, protocol, credential reference, readiness reasons, warnings, and redaction |
| 3 | P1 | Add MCP permission remediation evidence to operations/timeline APIs | Done | Capability governance apply records redacted MCP health refresh evidence, team runtime timeline surfaces relevant MCP governance remediation events, governance apply can remove unallowed agent MCP tool references and re-enable disabled MCP allowlist entries with audit evidence, and explicit `PATCH`/`rotate` APIs now cover MCP connection and credential repair with health invalidation, encrypted/external secret handling, and audit evidence |
| 4 | P1 | Add lifecycle recovery-action rollup to workspace operations responses | Done | `/operations/overview` now includes redacted `data_lifecycle` status, latest backup, latest restore drill, retention safety, import conflict preview, recommended actions, and next safe action with empty-workspace and ready-with-warning coverage |
| 5 | P1 | Add one isolated remote validation script/table entry for the above closures | Done | `scripts/remote-backend-validation.sh` creates disposable Docker network/Postgres/Redis/test containers, runs targeted pytest/ruff with overrideable arguments, documents the workflow, and avoids `opsmesh-postgres` |

## MCP Runtime Delivery Queue

| Order | Priority | Task | Status | Acceptance Evidence |
| --- | --- | --- | --- | --- |
| 1 | P0 | Dedicated isolated runtime image and MCP SDK capability probe | In progress | `Dockerfile.runtime` installs only `opsmesh-runtime` plus MCP SDK, runs non-root, uses `/workspace`, and exposes `python -m opsmesh_runtime.mcp_stdio_client --check`; Docker adapter fails closed before launching a server |
| 2 | P0 | Official SDK stdio integration proof | In progress | A real FastMCP fixture verifies SDK initialization and tool execution locally; Docker image execution still requires environment validation |
| 3 | P0 | Self-hosted MCP connector execution loop | Done | `opsmesh-self-hosted-worker` heartbeats, polls, reclaims, executes through the official SDK, durably records results before completion, and fails closed on invalid v1 contracts or interrupted execution |
| 4 | P1 | MCP runtime security and recovery evidence | In progress | Targeted tests cover full-session timeout, process failure, duplicate completion, claimed-job rediscovery, result replay, interrupted execution, response/credential redaction, plaintext remote URL denial, and workspace denial; a packaged connector against a deployed Postgres control plane remains |
