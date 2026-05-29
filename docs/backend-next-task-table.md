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
| 3 | P0 | Runtime-space diagnostics view | Next | API returns quota usage, active reservations, blocked steps, linked runtime/worker metadata without secrets |
| 4 | P0 | Runtime-space operator resolution workflow | In progress foundation | pause/resume/reset and force-release reservations exist; next add explain/reset diagnostics and forced reservation detail view |
| 5 | P0 | Scheduler quota and concurrency hardening | Pending | tests prove concurrent schedulers cannot oversell workspace/runtime-space quota; fairness tests cover high-priority insert and low-priority starvation boost |
| 6 | P0 | Worker long-task recovery | Pending | stale queued/running/waiting_runtime runs and worker leases can be diagnosed and recovered or failed closed |
| 7 | P1 | Self-hosted machine operations hardening | Pending | quarantine/resume/revoke, reconnect behavior, policy diff diagnostics, stale claim cleanup |
| 8 | P1 | Memory indexing abstraction | Pending | lexical fallback remains; backend abstraction supports Postgres full-text or vector adapter; search stays workspace-scoped |
| 9 | P1 | MCP/OpenAI Agents continuation compatibility | Pending | current continuation remains; SDK response/state metadata compatibility layer is documented and tested |
| 10 | P1 | Continuous redaction audit | Ongoing | new dict/list metadata API fields have tests proving token/base_url/header/container/container_id redaction |

## Immediate Implementation Queue

1. Build runtime-space diagnostics response and endpoint.
2. Add tests for quota usage, reservation details, blocked steps, and redaction.
3. Commit and push.
4. Add scheduler concurrency/fairness tests around quota release and unblock paths.
5. Commit and push.
