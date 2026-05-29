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

## Task Table

| Order | Priority | Task | Current Status | Acceptance Evidence |
| --- | --- | --- | --- | --- |
| 1 | P0 | Scheduler blocked-step explain API with stable reason codes | Done | `/operations/blocked-steps`, scheduler blocked reason `code/message/resource_key`, tests in `test_operations_api.py` |
| 2 | P0 | Scheduler blocked-step unblock API | Done | `/operations/blocked-steps/unblock`, audit event, filter-required guard, tests in `test_operations_api.py` |
| 3 | P0 | Runtime-space diagnostics view | Done | `/runtime-spaces/{id}/diagnostics` returns quota usage, active reservations, blocked steps, linked runtime metadata, and redacts secrets/container IDs |
| 4 | P0 | Runtime-space operator resolution workflow | Next | pause/resume/reset, force-release reservations, and diagnostics exist; next add reset outcome diagnostics and blocked-step resolution linkage |
| 5 | P0 | Scheduler quota and concurrency hardening | Done | workspace/runtime-space quota increments are atomic; concurrent session tests prove one reservation wins and the other is blocked without oversell |
| 6 | P0 | Worker long-task recovery | Done | `/operations/stale-runs` diagnoses stale queued/running/waiting_runtime runs with lease metadata; `/operations/stale-runs/recover` requeues stale queued runs, fails closed stale running/waiting_runtime runs, expires linked worker leases, and audits the action |
| 7 | P1 | Self-hosted machine operations hardening | Pending | quarantine/resume/revoke, reconnect behavior, policy diff diagnostics, stale claim cleanup |
| 8 | P1 | Memory indexing abstraction | Pending | lexical fallback remains; backend abstraction supports Postgres full-text or vector adapter; search stays workspace-scoped |
| 9 | P1 | MCP/OpenAI Agents continuation compatibility | Pending | current continuation remains; SDK response/state metadata compatibility layer is documented and tested |
| 10 | P1 | Continuous redaction audit | Ongoing | new dict/list metadata API fields have tests proving token/base_url/header/container/container_id redaction |

## Immediate Implementation Queue

1. Add self-hosted machine operations hardening.
2. Commit and push.
3. Add memory indexing abstraction.
4. Commit and push.
