# Backend Task Breakdown

## Purpose

This document breaks the backend roadmap into actionable engineering tasks, from the basic framework to complete backend capabilities.

Use it as the implementation checklist after the planning phase.

## Phase 1: Backend Project Foundation

Goal: create a clean backend skeleton that can run locally.

Tasks:

- [x] Choose backend framework and project layout.
- [x] Create backend application entrypoint.
- [x] Add configuration management.
- [x] Add environment variable loading.
- [x] Add structured logging.
- [x] Add request ID / correlation ID middleware.
- [x] Add health check endpoint.
- [x] Add local development settings.
- [x] Add dependency management.
- [x] Add test runner.
- [x] Add lint/format/type-check commands.

Deliverables:

- backend starts locally
- health endpoint works
- tests can run
- configuration is documented

## Phase 2: Postgres And Migrations

Goal: establish durable state.

Tasks:

- [x] Add Postgres connection.
- [x] Add database session lifecycle.
- [x] Add migration tool.
- [x] Create base migration.
- [x] Add `users` table.
- [x] Add `workspaces` table.
- [x] Add `workspace_members` table.
- [x] Add common timestamp fields.
- [x] Add soft-delete/status conventions.
- [x] Add database test setup.

Deliverables:

- migrations run from empty database
- workspace tables exist
- tests can use isolated test database

## Phase 3: Redis, Queue, And Locks

Goal: support async worker execution.

Tasks:

- [x] Add Redis connection.
- [x] Define Redis key naming convention.
- [x] Implement queue abstraction.
- [x] Implement job payload schema.
- [x] Implement enqueue function.
- [x] Implement worker consume loop.
- [x] Implement run lock.
- [x] Implement job idempotency key.
- [x] Implement retry policy.
- [x] Implement dead-letter handling.

Deliverables:

- API can enqueue a job
- worker can consume a job
- locks prevent duplicate run execution

## Phase 4: Authentication And Workspace Authorization

Goal: enforce multi-user workspace isolation.

Tasks:

- [x] Implement authentication strategy.
- [x] Implement current user resolution.
- [x] Implement workspace membership checks.
- [x] Implement role constants.
- [x] Implement permission helper.
- [x] Scope all workspace APIs by `workspace_id`.
- [x] Add tests for cross-workspace reads.
- [x] Add tests for cross-workspace writes.
- [x] Add tests for worker workspace mismatch rejection.

Deliverables:

- user can access only their workspace data
- resource ID alone is never enough for authorization

## Phase 5: Core Domain Models

Goal: represent agents, teams, tasks, runs, and events.

Tasks:

- [x] Add `agent_profiles` table.
- [x] Add `agent_teams` table.
- [x] Add `agent_team_members` table.
- [x] Add `tasks` table.
- [x] Add `task_steps` table.
- [x] Add `agent_runs` table.
- [x] Add `run_events` table.
- [x] Add `audit_events` table.
- [x] Add domain model validation.
- [x] Add status transition helpers.

Deliverables:

- can create agent profile
- can create team
- can create task
- can create run
- can append run events

## Phase 6: Workspace API Surface

Goal: expose the basic backend API.

Tasks:

- [x] Implement workspace CRUD endpoints.
- [x] Implement workspace member endpoints.
- [x] Implement agent profile endpoints.
- [x] Implement team endpoints.
- [x] Implement task endpoints.
- [x] Implement run read endpoints.
- [x] Implement run event endpoint.
- [x] Implement audit event endpoint.
- [x] Add pagination.
- [x] Add filtering by status.
- [x] Add API error format.

Deliverables:

- API supports basic workspace, agent, team, task, and run operations
- no frontend required

## Phase 7: Worker Run Execution Skeleton

Goal: move run execution out of API.

Tasks:

- [x] Add `agent.run` job type.
- [x] Create run when task starts.
- [x] Enqueue `agent.run`.
- [x] Worker loads workspace, task, team, agent, and policy.
- [x] Worker marks run `running`.
- [x] Worker writes `run.started`.
- [x] Worker handles success.
- [x] Worker handles failure.
- [x] Worker writes terminal run event.
- [x] Worker releases lock.

Deliverables:

- task start creates a queued run
- worker can complete a fake run end to end

## Phase 8: OpenAI Agents SDK Integration

Goal: run a real SDK agent.

Tasks:

- [x] Add OpenAI Agents SDK dependency.
- [x] Implement agent factory from `agent_profiles`.
- [x] Map model settings.
- [x] Map instructions.
- [x] Add Runner wrapper.
- [x] Add runtime context object.
- [x] Capture final output.
- [x] Capture basic events.
- [x] Persist model/tool/run events.
- [x] Add safe error normalization.

Deliverables:

- worker can run one OpenAI Agents SDK agent
- output is stored in `agent_runs`
- run events are persisted

## Phase 9: Product Tool Layer

Goal: expose safe backend tools to agents.

Tasks:

- [x] Define product tool interface.
- [x] Implement tool permission checks.
- [x] Implement `list_workspace_files`.
- [x] Implement `read_workspace_file`.
- [x] Implement `write_artifact`.
- [x] Implement `search_workspace_memory` placeholder.
- [x] Add tool event logging.
- [x] Add tool error formatter.
- [x] Add tests for tool workspace isolation.

Deliverables:

- agents can call product tools through SDK
- tools enforce workspace context

## Phase 10: Docker Runtime Manager

Goal: execute risky work in isolated containers.

Tasks:

- [x] Add Docker client wrapper.
- [x] Add `runtime_templates` table.
- [x] Add `workspace_runtimes` table.
- [x] Add `runtime_events` table.
- [x] Add `runtime_commands` table.
- [x] Implement create runtime.
- [x] Implement start runtime.
- [x] Implement stop runtime.
- [x] Implement delete runtime.
- [x] Implement command execution inside container.
- [x] Add CPU limit.
- [x] Add memory limit.
- [x] Add disk/workdir policy.
- [x] Add network policy.
- [x] Add container labels.
- [x] Add cleanup job.
- [x] Add tests that host execution is never used.

Deliverables:

- worker can execute a command inside Docker
- command output is stored
- runtime is workspace-scoped

## Phase 11: Runtime Tool Integration

Goal: let agents use Docker-backed tools safely.

Tasks:

- [x] Implement shell/runtime tool wrapper.
- [x] Route shell calls to Runtime Manager.
- [x] Enforce runtime policy.
- [x] Enforce command timeout.
- [x] Capture stdout/stderr.
- [x] Persist `tool.called` and `tool.completed`.
- [x] Support command failure.
- [x] Add approval hook for risky commands.

Deliverables:

- agent can request shell execution
- execution happens inside Docker
- events are persisted

## Phase 12: Workspace Files And Artifacts

Goal: support user data and generated outputs.

Tasks:

- [x] Add `workspace_files` table.
- [x] Add `artifacts` table.
- [x] Add `file_access_events` table.
- [x] Implement local storage adapter.
- [x] Implement file upload endpoint.
- [x] Implement file download endpoint.
- [x] Implement artifact download endpoint.
- [x] Implement runtime file staging.
- [x] Implement artifact collection from runtime.
- [x] Add checksum calculation.
- [x] Add file size limits.
- [x] Add tests for file authorization.

Deliverables:

- user can upload file
- worker can stage approved file into runtime
- runtime output can become artifact
- user can download artifact

## Phase 13: Approvals And Resume

Goal: pause sensitive actions and resume after human decision.

Tasks:

- [x] Add `approvals` table.
- [x] Implement approval creation.
- [x] Implement approval list endpoint.
- [x] Implement approve endpoint.
- [x] Implement reject endpoint.
- [x] Add approval policy for risky runtime shell commands.
- [x] Pause run on approval.
- [x] Enqueue resume job after approval.
- [x] Resume or fail run based on decision.
- [x] Add audit events.

Deliverables:

- risky action can pause run
- user can approve/reject
- worker can resume or fail safely

## Phase 14: Domain Task Extensions

Goal: support team-specific task data and correction flows.

Tasks:

- [x] Add `team_type` to agent teams.
- [x] Add `domain_type` to tasks.
- [x] Add `generic_state` to tasks.
- [x] Add `domain_state` to tasks.
- [x] Add `domain_projects` table.
- [x] Add `domain_items` table.
- [x] Add `review_comments` table.
- [x] Add `revision_requests` table.
- [x] Implement task view endpoint.
- [x] Implement review comment endpoint.
- [x] Implement revision request endpoint.
- [x] Route revision request to worker.
- [x] Add tests for domain item workspace isolation.

Deliverables:

- task view endpoint returns generic plus domain payload
- user correction becomes structured backend state
- worker can execute a revision request

## Phase 15: Capabilities, Skills, And MCP

Goal: manage what agents are allowed to do.

Tasks:

- [x] Add capability catalog.
- [x] Add skill registry.
- [x] Add workspace skill install table.
- [x] Add tool group catalog.
- [x] Add MCP server registry.
- [x] Add MCP tool allowlist.
- [x] Add MCP credential reference model.
- [x] Add MCP health status.
- [x] Map allowed MCP tools to SDK tools.
- [x] Log MCP tool calls.
- [x] Add approval policy for write-capable MCP tools.

Deliverables:

- agents see only allowed capabilities/tools
- MCP tools are workspace-scoped
- skills can affect instructions/runtime files without bypassing permissions

## Phase 16: Self-Hosted Runtime

Goal: allow user-owned machines to execute tasks.

Tasks:

- [x] Add runtime provider field.
- [x] Add runtime enrollment token.
- [x] Add runtime credential model.
- [x] Add self-hosted worker registration endpoint.
- [x] Add heartbeat endpoint.
- [x] Add job polling endpoint.
- [x] Add job claim endpoint.
- [x] Add progress event upload.
- [x] Add selected artifact upload.
- [x] Add local file reference model.
- [x] Add credential revocation.

Deliverables:

- self-hosted worker can register
- platform can assign a job
- worker can report progress
- artifacts can be uploaded selectively

## Phase 17: Observability And Operations

Goal: make backend runs inspectable and operable.

Tasks:

- [x] Add structured logs.
- [x] Add run event viewer endpoint.
- [x] Add runtime event endpoint.
- [x] Add worker heartbeat.
- [x] Add queue metrics endpoint.
- [x] Add runtime cleanup scheduler.
- [x] Add orphan container cleanup.
- [x] Add failed job inspection.
- [x] Add audit event filters.

Deliverables:

- operators can inspect failed runs
- stale runtime resources are cleaned
- audit trail is usable

## Phase 18: Backend Test Coverage

Goal: protect critical safety and orchestration paths.

Tasks:

- [x] Test workspace isolation.
- [x] Test role permissions.
- [x] Test worker job workspace mismatch.
- [x] Test run lock behavior.
- [x] Test file download authorization.
- [x] Test runtime command path.
- [x] Test approval pause/resume.
- [x] Test tool allowlist.
- [x] Test MCP tool allowlist.
- [x] Test artifact collection.
- [x] Test domain revision flow.

Deliverables:

- core backend safety has automated tests
- regressions are caught early

## Phase 19: Talent Marketplace And Hiring

Goal: let each user act as a boss who hires public agents into their own AI company.

Tasks:

- [x] Add public talent listing model.
- [x] Add workspace agent install model.
- [x] Publish a workspace agent profile to the talent market.
- [x] Search public agents by role, skill, and query.
- [x] Hire a public agent into another workspace as an isolated copy.
- [x] Optionally place the hired agent into a team role.
- [x] Record hiring and publishing audit events.
- [x] Prevent duplicate hires of the same listing into a workspace.
- [ ] Add HR agent recommendation workflow.
- [ ] Add listing version upgrades and pinned versions.
- [ ] Add public rating, usage, and review metrics.

Deliverables:

- boss can publish an agent as public talent
- another boss can hire that agent into their own workspace
- hired agents do not inherit source workspace data or credentials
- hired agents can be assigned to a department/team role

## Suggested MVP Cut

Minimum backend demo:

- Phase 1 through Phase 13.

Differentiated backend demo:

- Phase 1 through Phase 15.

Advanced trust demo:

- Phase 1 through Phase 16.

Frontend can wait until the backend demo is stable.
