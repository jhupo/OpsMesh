# Unified workflow backend

The accepted scope is backend-only. Team definitions describe participants and authorization;
workflow definitions describe execution order and data flow. Human editing and agent planning must
share the canonical node contract, admission policy, durable execution and mutation path.

| Deliverable | Status | Acceptance |
| --- | --- | --- |
| Shared agent node contract | Done | Authored, agent-generated, deterministic and mutated plans share WorkflowNode and graph validation; conditions, MCP identity and joins are retained |
| Branch correctness | Done | Explicit skipped state, skip propagation, selected-branch join, reference cycle denial; empty selection completes without a run |
| Immutable publication | Done | Historical versions remain executable and queryable; draft edits use optimistic concurrency; application locks task |
| Typed executable node catalog | In progress | Agent, direct tool/MCP, control and approval nodes use the shared contract and existing SDK/tool/approval boundaries; subworkflow execution remains the next bounded item |
| Structured data flow | Pending | Typed inputs/outputs and references, bounded payloads, schema validation and scoped evidence |
| Human/AI editing policy | Pending | Locked nodes and edges cannot be modified outside the authorized region |
| Execution inspection | Pending | Persisted per-node results, attempts, approval state and diagnostics usable by a future canvas |
| PostgreSQL integration | Done for revisions | Real PostgreSQL 18: migration round trip with existing data, concurrent publication/application, Alembic metadata check |

No frontend, new provider framework, or second queue is introduced. Existing provider SDK adapters,
tool gateway, approvals, Postgres task records and worker recovery remain execution boundaries.
The data-only predicate language is product policy; no general-purpose expression interpreter or
arbitrary user code is run on the API/worker host. Graph cycle validation uses Python graphlib.
Temporal remains subject to the existing representative-workflow evaluation before replacement of
the durable engine; this change does not introduce a parallel workflow engine.
