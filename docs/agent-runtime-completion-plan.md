# Agent Runtime Completion Plan

## Purpose

This is the active completion plan for the OpsMesh agent-runtime phase. It is based on code-path
inspection rather than the presence of models, routes, or tests alone. A capability is complete only
when its durable execution and recovery loop satisfies the acceptance gate in this document.

Earlier roadmap items marked `Done` describe the foundation delivered at that time. They do not
override an incomplete production closure identified here.

## Execution Rules

- Complete and commit one functional point at a time, in the order below unless a documented blocker
  requires a dependency-first change.
- Use the OpenAI Agents SDK and Claude Agent SDK as the provider execution cores. Keep OpsMesh-owned
  authorization, policy, durable state, redaction, quotas, and audit at the adapter boundary.
- Do not add compatibility shims or a general-purpose agent framework alongside the provider SDKs.
- Run targeted tests and targeted lint for each functional point. Do not run the full test suite
  during normal implementation; reserve it for the release gate.
- Update status only after the acceptance gate works across API, database, queue, worker, and runtime
  boundaries as applicable.
- Preserve workspace isolation, idempotency, secret redaction, and restart recovery in every phase.

## Status Legend

- `Pending`: the production loop is not complete.
- `In progress`: implementation has started but the acceptance gate is not yet satisfied.
- `Done`: implementation, focused validation, documentation, and the acceptance gate are complete.

## Phase 1: Approval And Resumable Execution

Goal: make every approval path durable, idempotent, and recoverable from the exact interruption.

| Order | Priority | Functional point | Status | Commit | Acceptance evidence |
| --- | --- | --- | --- | --- | --- |
| 1.1 | P0 | Inject the worker queue into approval decisions and reliably schedule approved runs | Done | `fix-approval-run-rescheduling` | Public approve API queues one eligible resume job and the queue idempotency key prevents duplicate jobs |
| 1.2 | P0 | Persist pending tool invocation state, including call ID, tool, arguments, policy decision, and idempotency key | Done | `add-pending-tool-invocation-state` | Encrypted arguments survive restart; workspace scope and unique idempotency binding are enforced |
| 1.3 | P0 | Persist and restore SDK interruption and `RunState` through the adapter | Done | `add-sdk-run-state-resume` | Encrypted SDK state is stored outside public output and restored through `RunState.from_json()` with current authorization context |
| 1.4 | P0 | Resume the approved tool invocation exactly once and continue the original agent run | Done | `complete-approved-tool-resume` | Approve executes the original call once, supplies its result to the SDK run, and reaches a valid terminal state |
| 1.5 | P0 | Complete reject, timeout, cancellation, duplicate decision, and worker-restart behavior | Done | `complete-approval-failure-lifecycle` | Every branch has a valid run/task transition and durable audit evidence |
| 1.6 | P0 | Unify automatic allow, human approval, and deny policy across product, MCP, model, and runtime actions | Done | `complete-approval-policy-engine` | Identical policy inputs produce the same decision and high-risk actions fail closed |

Phase acceptance gate:

`run -> tool approval -> worker restart -> approve -> execute once -> resume SDK state -> complete`
works end to end. Rejection and timeout terminate or recover according to policy without replaying a
side effect.

Verification: `test_phase_one_approval_flow_survives_worker_restart_and_executes_once` exercises the
complete durable path with a recreated database session and a duplicate execution attempt. Focused
approval lifecycle tests cover rejection, timeout, cancellation, and unknown worker outcomes.

## Phase 2: Provider Agent SDK Adapter Closure

Goal: expose the supported Agents SDK orchestration surface through vendor-neutral OpsMesh contracts.

| Order | Priority | Functional point | Status | Commit | Acceptance evidence |
| --- | --- | --- | --- | --- | --- |
| 2.1 | P0 | Expand typed contracts for agents, handoffs, interruptions, streaming, structured results, and capabilities | Done | `expand-agent-runtime-contracts` | Product-owned session, handoff, interruption, stream, structured-output, and capability contracts are mapped at the adapter boundary; orchestration has no SDK result/state imports; unsupported request features fail explicitly |
| 2.2 | P0 | Implement SDK handoffs and handoff input filtering | Done | `add-openai-agent-handoffs` | SDK handoff objects are created only for unique same-workspace authorized targets; filtered context and source/target metadata are returned, persisted, and emitted as redacted runtime events |
| 2.3 | P0 | Implement agents-as-tools with nested run provenance and limits | Done | `add-openai-agents-as-tools` | OpenAI `Agent.as_tool()` receives only frozen same-team targets; every nested level intersects tool, resource, and file scope, enforces graph, depth, and turn limits, and persists source, target, call, and usage provenance |
| 2.4 | P0 | Add structured output plus agent input/output guardrails | Done | `add-agent-output-and-guardrails` | Versioned JSON Schemas and bounded guardrail policies are validated at profile write, frozen in run authorization, restored by workers, enforced by both adapters, and persisted as redacted results or non-retryable policy failures |
| 2.5 | P1 | Add lifecycle hooks, streaming events, cancellation propagation, and usage capture | Done | `add-agent-runtime-streaming-hooks` | Both SDK adapters share one execution observer and template lifecycle; ordered redacted stream events and normalized usage are persisted, while durable run cancellation interrupts the active SDK client and tool executor without provider fallback |
| 2.6 | P1 | Publish and enforce the provider adapter capability matrix | Done | `add-agent-adapter-capability-matrix` | A workspace-readable API publishes every adapter feature, limit, and unsupported reason; the provider registry derives required capabilities from each request and rejects gaps as non-retryable policy failures without cross-provider fallback |

Phase acceptance gate:

Single-agent turns, handoffs, agents-as-tools, structured results, streaming, cancellation, and paused
run restoration all execute through the same OpsMesh runner contract.

Verification: focused adapter tests cover both provider SDK implementations, ordered streaming,
SDK/tool cancellation, lifecycle events, normalized usage, and capability rejection. The worker
integration path proves SDK stream events reach durable run history, while the workspace capability
API publishes the same matrix enforced by the registry.

## Phase 3: Workspace Projects, Files, Configuration, And Outputs

Goal: give each run a real, authorized project input and output lifecycle.

| Order | Priority | Functional point | Status | Commit | Acceptance evidence |
| --- | --- | --- | --- | --- | --- |
| 3.1 | P0 | Read authorized workspace file content through object storage with type and size limits | Done | `complete-workspace-file-reading` | `read_workspace_file` intersects the frozen file scope with active resource grants, performs bounded Local/S3 reads, verifies workspace storage prefix, actual size and SHA-256, accepts configured UTF-8 text MIME types only, and returns content marked as untrusted input; denied, unsupported, missing, corrupt, and oversized files fail with stable evidence |
| 3.2 | P0 | Write artifact bytes to object storage with transactional compensation | Done | `complete-artifact-object-storage` | Model tools, runtime collection, and archive restore write bytes under a unique workspace/artifact ID key before committing metadata; storage failures roll back rows, database failures delete newly written objects, partial writes are cleaned up, and committed artifacts are downloadable with their recorded bytes |
| 3.3 | P0 | Model project paths, project configuration, input files, and output locations | Done | `add-workspace-project-layout` | Workspace project APIs persist non-overlapping paths, secret-free configuration, scoped input bindings, bounded output declarations, and optional task ownership; traversal and foreign file/project IDs are rejected |
| 3.4 | P1 | Add configuration versions, run input snapshots, file versions, and diffs | Done | `add-workspace-file-versioning` | Immutable checksummed configuration versions and per-path file chains expose scoped history/diffs; ordinary runs, team-step runs, and task resumes freeze exact project manifests, while failed-run retries clone the original snapshot and public reads redact storage locators |
| 3.5 | P0 | Stage authorized inputs at run start and harvest declared outputs at completion | Done | `add-run-file-staging-and-harvesting` | Managed Docker and self-hosted runs consume the exact checksummed snapshot through a storage-key-redacted project contract; only declared outputs can be collected, required outputs block completion when absent, artifact writes are versioned and idempotent with storage compensation, and durable I/O state, file-access events, run events, and audit evidence record the lifecycle |
| 3.6 | P0 | Enforce file grants, path policy, capacity, sensitive-file rules, and access evidence | Done | `harden-workspace-file-boundaries` | Before any object bytes are read, managed and self-hosted execution verify the v2 authorization fingerprint, project-snapshot binding, workspace/task/runtime scope, exact active capability-resource versions and locators, current file identity, explicit runtime-access policy, sensitive filename denylist, aggregate input/output limits, and reported runtime capacity; denials create stable run, audit, and security evidence without filenames, storage locators, or content |

Phase acceptance gate:

A user uploads project inputs, a run receives the authorized snapshot, the agent reads and modifies
files inside its runtime, and the user downloads a checksum-verified, versioned output artifact.

## Phase 4: Context Management And Three-Layer Memory

Goal: keep context bounded and make durable knowledge useful without crossing authorization scopes.

| Order | Priority | Functional point | Status | Commit | Acceptance evidence |
| --- | --- | --- | --- | --- | --- |
| 4.1 | P0 | Add token-aware context budgeting, priority classes, and deterministic truncation | Done | `add-context-budget-manager` | Every request fits its model budget and records included/excluded context provenance |
| 4.2 | P0 | Use provider-native semantic context compaction | Done | `replace-custom-session-compaction-with-provider-sdk` | OpenAI Responses sessions use `OpenAIResponsesCompactionSession`; Claude uses its SDK auto-compaction, and the superseded preview-summary API is removed |
| 4.3 | P0 | Add working memory for current run state, plan, temporary facts, and tool results | Done | `add-agent-working-memory` | Working memory is isolated to its run/session and expires or promotes explicitly |
| 4.4 | P0 | Add episodic memory for tasks, runs, decisions, failures, and human feedback | Done | `add-agent-episodic-memory` | Relevant prior episodes are searchable with task/run provenance |
| 4.5 | P0 | Add semantic memory for workspace/team knowledge, configuration, and policy | Done | `add-agent-semantic-memory` | Durable knowledge is versioned and scoped separately from transient history |
| 4.6 | P1 | Add hybrid retrieval, ranking, deduplication, promotion, decay, and archive rules | Done | `complete-memory-lifecycle` | Postgres full-text, pgvector cosine search, and lexical candidates use weighted reciprocal-rank fusion, fingerprint deduplication, deterministic importance/recency decay, and versioned workspace lifecycle policy; background embedding, retrieval, promotion, and archive decisions retain query-safe evidence |
| 4.7 | P0 | Inject authorized memory retrieval into context construction | Done | `integrate-memory-context-retrieval` | Every run request applies the default-enabled retrieval policy to its frozen `memory_collection` read grants, prefilters each grant as one conjunctive authorization scope, retrieves only enabled long-term layers, and records both selected-memory and final context-budget inclusion evidence without raw content or queries |

Phase acceptance gate:

Long sessions remain within context limits, new runs retrieve relevant authorized history, and
working, episodic, and semantic memory have distinct storage, retrieval, promotion, and retention
semantics.

Episodic capture is enabled in the strict default agent memory policy. Completed, failed, recovered,
and cancelled runs; terminal task outcomes; approval and delivery decisions; corrections; operator
decisions; and explicit human feedback are stored as redacted, expiring, idempotent events. Each
entry carries task, run, agent, source-event, and actor provenance when available, and the workspace
memory search surface returns that provenance with the matching episode.

Semantic memory now stores facts, configuration, policy, and procedures as independently scoped
workspace, team, or agent knowledge. Stable keys provide idempotent upserts, expected revisions
prevent lost updates, and every material update or archive creates an immutable, redacted version
with user/run/agent provenance. User APIs and approval-gated agent tools share the same domain
service; frozen memory policy and memory-resource grants constrain agent writes, archives, and
searches before any result is returned.

Hybrid retrieval now keeps vectors in Postgres through pgvector and calls the official OpenAI SDK
embedding API behind an OpsMesh provider boundary. Workspace-versioned policies control retrieval
weights, decay, retention, and opt-in episodic promotion. Durable worker jobs generate vectors with
generation and content-fingerprint guards, while retrieval, embedding, and lifecycle evidence stores
stable hashes, ranks, policy versions, and error codes without raw queries, credentials, or provider
exceptions.

Agent request construction now derives a bounded retrieval query from the authorized task and step,
applies episodic and semantic per-layer limits, and injects the selected results as explicitly
untrusted normal-priority context. Resource grants remain independent conjunctions, so the tag,
source, scope type, and scope ID from separate grants cannot be combined into broader access.
Workspace ownership is validated when resources are configured, authoritative memory scope fields
cannot be overridden by custom metadata, and run evidence distinguishes retrieved, rendered,
included, truncated, and excluded context without persisting query or memory content.

## Phase 5: Task And Agent Orchestration

Active stage (2026-09-10): continue this sequence before knowledge-source work. Agent planning now
validates the live team roster, profile versions, roles, skills, capability catalog, runtime-space
placement, scheduler limits, workspace/runtime quotas, provider readiness, and projected model cost
before any generated work step is materialized. Dynamic future-work replanning is now admitted
through an audited, task-row-locked mutation service. Ownership transfer is now durable and
version-guarded; this phase remains open until human continuation and final delivery acceptance
are complete.

Goal: move from a deterministic organization template to validated, adaptive agent planning.

| Order | Priority | Functional point | Status | Commit | Acceptance evidence |
| --- | --- | --- | --- | --- | --- |
| 5.0 | P0 | Persist user-authored orchestration definitions with expert/tool/MCP/resource selection and conditional branches | Done | `3721f4c` | Workspace APIs support draft, validation, publish, archive, versioned edits, task application, audit evidence, and a bounded data-only condition DSL; targeted orchestration tests cover admission and false/pending branches |
| 5.1 | P0 | Generate a structured task DAG with a planner agent and retain deterministic planning only as explicit fallback | Done | `638bf87` | Tool-free worker planner, SDK structured output, scoped DAG admission, duplicate-delivery denial, durable failure/cancel/retry, explicit deterministic mode; PostgreSQL and Backend CI accepted |
| 5.2 | P0 | Validate schema, cycles, authorization, capability availability, cost, and resource feasibility | Done | `1c30501` | Invalid plans cannot enqueue work; stable failure codes cover stale roster/policy, role/skill, tool/resource, runtime/workspace quota, provider, and cost gates |
| 5.3 | P0 | Replan by adding, splitting, merging, cancelling, or reassigning future work | Done | `a86f7cf` | `TaskPlanMutationService` and `POST /tasks/{task_id}/plan/mutate` apply task-row-locked, idempotent mutations; DAG/feasibility gates, step projection reconciliation, manager-summary coverage, cancellation history, and active-side-effect protection are covered by focused service/API tests and Backend CI |
| 5.4 | P0 | Transfer tasks between agents with objective, context, artifacts, state, and ownership | Done | `f69305b` | `TaskTransferService` and task transfer endpoints capture redacted objective/state/steps/messages/runs/artifact and memory references; task-row ownership/version locks, active-run denial, idempotency, audit/message evidence, platform-step reassignment, scheduler checks, and stale authorization rejection are covered by focused transfer tests |
| 5.5 | P0 | Support human pause, context correction, reassignment, and in-place continuation | Done | `caef04e` | Control, diagnostics, correction, and resume APIs persist redacted instructions/audit evidence; corrected steps are assigned and re-enter the existing scheduler |
| 5.6 | P0 | Close manager acceptance, revision, missing-work, and final integration loops | Done | `caef04e` | Delivery review/decision APIs enforce active-run, step, artifact, and review gates; explicit override requires a reason and is audited |

Phase acceptance gate:

The default planner is queued as durable intent; API task creation does not call a model. Both
provider adapters receive the existing `AgentRuntimeOutputSchema` contract named `task_plan`.
The worker checks the normalized structured result, frozen roster and DAG before admitting work,
then appends a platform-owned final acceptance step. Plain text never silently becomes a plan.
`input.planning_mode = "deterministic"` explicitly selects the existing template planner and records
that choice in planning evidence. SDK execution failures and expired workers close the attempt and
block for review; initial retry preserves failed attempts and cannot replace active work. Human
control endpoints pause/resume runs, append instructions, create target-scoped corrections, and
expose control diagnostics; corrected work is scheduled through the same workspace capacity and
authorization gates. Delivery review and decision endpoints enforce artifact and completion gates,
while a documented override reason is required for authorized exceptional finalization.

Evidence for 5.1: `test_agent_planning.py`, `test_task_dag.py`, focused worker/API regressions,
and the dedicated `test_agent_planning_postgres.py` CI job. Evidence for 5.2: the same planning
regressions cover live authorization, capability, resource, quota, provider, and cost denial paths;
`PlanFeasibilityService` reuses the existing effective catalog, provider resolution, scheduler, and
quota services. Evidence for 5.3: `test_agent_planning.py` exercises add, split, merge, cancel,
reassign, immutable completed history, and active side-effect denial; `test_workspace_api.py`
covers the workspace mutation endpoint and audit event. No new SDK/framework dependency was
introduced: schema-constrained generation remains owned by the provider SDK, while plan
authorization, mutation, and scheduling remain product policy. Human continuation and final
delivery gates are now exposed through the task control, correction, delivery review, and delivery
decision services described above.

Accepted code: `a86f7cf` (5.3), `f69305b` (5.4), and `f0ba331` (migration contract fix), on top of
`1c30501`, `638bf87`, `f455fa5` and the prerequisite fixes `e03bb48` (pgvector test fixture
initialization) and `32047c7` (dependency recheck under the scheduling lock). Backend CI
[34449010461](https://github.com/jhupo/OpsMesh/actions/runs/34449010461) passed for the transfer
implementation and [34449729516](https://github.com/jhupo/OpsMesh/actions/runs/34449729516) passed
after the migration contract fix. Delivery Integration
[34449858792](https://github.com/jhupo/OpsMesh/actions/runs/34449858792) also passed its real
PostgreSQL migration, container deployment/runtime probe, backup restoration, and tamper-denial
checks. The PostgreSQL planning test verifies concurrent planning requests produce one durable
attempt and remains separate from SQLite metadata-patching tests; the Backend job also ran the
focused planning, transfer, and workspace API regressions. Phase 5.5 and 5.6 are covered by
`test_workspace_api.py` control, correction, diagnostics, delivery review, and decision scenarios.

Evidence for 5.4: `test_task_transfer.py` covers durable package capture, response serialization,
idempotent requests, active-run denial, acceptance, owner-version increment, and platform-step
reassignment. `TaskTransferService` exposes scoped request/list/accept/reject endpoints under
`/workspaces/{workspace_id}/tasks/{task_id}/transfers`; every mutation appends a task message and
hash-chained audit event. The handoff package is redacted and reference-only for artifacts and
memory, while owner/version fields are frozen into new authorization snapshots and checked before
execution. Focused database, planning, workspace API, runtime authorization, import-linter, and
Backend CI checks passed; the real PostgreSQL and Delivery Integration gates are recorded above.

User-authored orchestration definitions are workspace-scoped durable plans. A definition is edited
as a draft, validated against active agent profiles, capability resources, MCP servers and
allowlisted tools, then published before a task can apply it. Applying a definition snapshots its
version into the task plan and planning attempt; later edits do not rewrite that task snapshot.
Nodes can select an exact expert or use the frozen team roster matcher, request product tools,
MCP allowlist entries, resources, resource limits, expected artifacts, review policy, and estimated
cost. Conditional branches use only bounded JSON data paths such as `task.input`, task state,
final output, and terminal step status/result summaries. Unsupported expressions, cycles, foreign
references, embedded secrets, and unallowlisted MCP tools are rejected before materialization.

The backend unification follow-up is tracked separately in [Workflow backend plan](workflow-backend-plan.md).
Authored and generated nodes use `package_id`, `required_mcp_tools`, and the shared `WorkflowNode`
contract. Draft PATCH requests require `expected_version`; published revisions are immutable service
records with list/detail endpoints and can be applied while a newer draft is being edited. Archiving
disables further application. Conditions include their step references in cycle validation. A false
branch becomes `skipped`, not user-cancelled. `all_success` propagates skips; `all_selected` waits for
all predecessors and runs when at least one completed. All-skipped workflows finish without a model
run. These are agent-workflow capabilities, not a claim that arbitrary typed canvas nodes exist.
The current follow-up also admits typed `agent`, direct `tool`/`mcp`, control (`condition`, `join`,
`start`, `end`) and `approval` nodes. Direct tool nodes execute through the frozen authorization
snapshot and existing `BackendToolExecutor` without resolving a model provider; approval nodes create
durable workflow approvals. Subworkflow nodes use a durable parent/child invocation boundary: the
child task is created through normal task admission, pinned to an immutable revision, queued on the
existing worker queue, and its terminal result is propagated back to the waiting parent run.

A project can be planned, validated, executed in parallel, replanned after failure, transferred
between agents, corrected by a human, reviewed by a manager, and assembled into one final delivery.

## Phase 6: Per-Run Execution Environment And Isolation

Goal: make runtime isolation enforceable by the platform for every managed run.

| Order | Priority | Functional point | Status | Commit | Acceptance evidence |
| --- | --- | --- | --- | --- | --- |
| 6.1 | P0 | Create an isolated ephemeral execution space per run with explicit persistent mounts | Done | `add-per-run-runtime-isolation` | Concurrent and sequential runs cannot read each other's undeclared files |
| 6.2 | P0 | Complete normal, failed, cancelled, and orphan runtime cleanup | Done | `complete-runtime-resource-cleanup` | Containers, volumes, leases, and reservations are reclaimed with durable evidence |
| 6.3 | P0 | Enforce domain, IP, DNS, protocol, and port egress rules through a controlled gateway | Pending | `add-runtime-egress-policy` | Direct unapproved egress fails even when code attempts to bypass application policy |
| 6.4 | P0 | Enforce non-root execution, seccomp/AppArmor policy, read-only rootfs, and minimal capabilities | Pending | `harden-runtime-sandbox` | Runtime diagnostics prove each required control is platform-enforced |
| 6.5 | P0 | Enforce CPU, memory, disk, process, wall-time, command-output, and concurrency limits | Pending | `complete-runtime-resource-limits` | Each limit has a deterministic failure state, cleanup, and bounded evidence |
| 6.6 | P0 | Complete command authorization, risk review, timeout, termination, and audit | Pending | `complete-runtime-command-security` | Shell/metacharacter and indirect execution cases cannot bypass policy |
| 6.7 | P1 | Add self-hosted worker capability attestation, revocation, and untrusted-state behavior | Pending | `harden-self-hosted-runtime-trust` | The control plane never claims host isolation it cannot verify and can revoke future work |

Phase acceptance gate:

Each managed run has isolated files, controlled egress, enforced quotas, hardened execution, reliable
cleanup, and security evidence. Self-hosted execution exposes its distinct trust boundary explicitly.

## Phase 7: Integrated Closure

| Order | Priority | Functional point | Status | Commit | Acceptance evidence |
| --- | --- | --- | --- | --- | --- |
| 7.1 | P0 | Add the critical end-to-end agent workflow | Pending | `add-agent-runtime-critical-e2e` | Plan, file read, approval, restart, resume, handoff, artifact, and manager acceptance pass together |
| 7.2 | P0 | Add focused recovery and denial scenarios | Pending | `add-agent-runtime-recovery-e2e` | Duplicate messages, timeout, network denial, quota violation, and worker loss remain safe |
| 7.3 | P1 | Integrate runtime logs, metrics, tracing, audit, and cost events | Pending | `complete-agent-runtime-operations-evidence` | Every phase transition and side effect is correlated without exposing secrets or high-cardinality data |
| 7.4 | P1 | Replace obsolete runtime prompts, flow diagrams, architecture diagrams, and status claims | Pending | `update-agent-runtime-architecture-docs` | Documentation matches the implemented code paths and labels remaining work accurately |
| 7.5 | P0 | Run the release gate and record release-readiness evidence | Pending | `complete-agent-runtime-platform` | Focused checks, migration verification, and the one release full-suite run are recorded |

## Validation Scope

For each functional-point commit:

1. Run tests for the touched service or adapter.
2. Add one relevant negative authorization, recovery, idempotency, or redaction case when the risk
   requires it.
3. Run targeted `ruff check` for changed Python paths.
4. Run `git diff --check`.
5. Do not run unrelated test modules or the full suite.

At the end of each phase, run only the small integration set needed to prove that phase's acceptance
gate. Run the complete test suite once, as the final release gate in Phase 7.

## Completion Definition

The agent-runtime phase is complete only when all rows above are `Done` and:

- approval, tool invocation, and SDK state resume survive a process restart;
- handoffs and agents-as-tools pass through the SDK adapter and OpsMesh authorization boundary;
- workspace inputs and artifacts have real storage objects, versions, checksums, and access evidence;
- context is token-bounded and three memory layers have distinct lifecycle semantics;
- agent-generated plans are validated and can be safely replanned or transferred;
- managed runs have per-run file, network, resource, command, and cleanup isolation;
- the end-to-end workflow and critical failure paths pass without live provider credentials;
- architecture and operations documentation describe actual behavior rather than intended behavior.
