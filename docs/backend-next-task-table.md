# Backend Next Task Table

This table is the active execution checklist for the next backend phase. It keeps the work ordered, testable, and scoped to backend commercial-software reliability.

Frontend remains out of scope. Billing remains out of scope.

## Execution Rules

- Finish one functional point at a time.
- Run targeted pytest files for the touched module.
- Run targeted `ruff check`.
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
| 9 | P1 | MCP/OpenAI Agents continuation compatibility | Done | Runner-level continuation rendering remains; OpenAI raw output now persists stable `sdk_continuation` metadata for future SDK-native migration, and MCP/self-hosted continuation tests cover completed and failed tool results |
| 10 | P1 | Continuous redaction audit | Done for current phase | New metadata surfaces from this phase are covered: runtime/stale/self-hosted diagnostics avoid sensitive fields, policy diagnostics redact nested metadata, and run API output redacts `sdk_continuation` token/base_url/header values |

## Next Phase Goal Table

| Order | Priority | Goal | Current Status | Acceptance Evidence |
| --- | --- | --- | --- | --- |
| 1 | P0 | Server deployment assets and smoke test for API/worker on shared Postgres/Redis | Done | `deploy/server/docker-compose.backend.yml`, `deploy/server/env.example`, `scripts/server-smoke-test.sh`, deployment asset tests, and server health verified on `192.168.2.17` |
| 2 | P0 | Workspace/team Docker runtime isolation closure | Done | Runtime containers now carry workspace/runtime/runtime-space/team identity, a dedicated `/workspace` Docker volume, network-policy metadata, managed volume cleanup, team/runtime-space policy resolution, and explicit isolation diagnostics |
| 3 | P0 | Agent model configuration closure | Done | Workspace default provider, per-agent override, cloud-downloaded base URL/key/model, fallback behavior, and redacted audit coverage are covered by model provider service and runtime tests |
| 4 | P0 | Multi-agent project execution loop | In progress | Reusable team org chart API, task execution diagnostics, project-plan quality diagnostics, and correction lifecycle diagnostics are implemented; remaining closure is broader message/state timeline coverage |
| 5 | P1 | MCP skill lifecycle and tool permission hardening | In progress | Private/public skill install lifecycle and version snapshots exist; per-agent tool-policy diagnostics now explain effective MCP access, missing skill installs, missing allowlist entries, credential gaps, and blocked reasons without exposing secrets |
| 6 | P1 | Self-hosted worker install and upgrade channel | Done | Registration token, connector bootstrap manifest, heartbeat/version policy diagnostics, job slots, upgrade/drain/quarantine/revoke flow, and trust diagnostics are implemented |
| 7 | P1 | Operations control plane expansion | Done | Runtime/container quota dashboard API, backlog/failure/latency aggregates, blocked-step explanations, and metadata-only operator responses are covered by operations API and tests |
| 8 | P1 | Workspace data lifecycle | Pending | Import/export restore path, artifact versioning, backup hooks, retention policy, and file access audit |

## Execution Rules For This Phase

1. Implement one backend goal at a time.
2. Validate locally with targeted tests and lint.
3. Validate runtime behavior on `192.168.2.17` before committing.
4. Commit and push after each verified goal.
5. Keep frontend and billing out of scope.
