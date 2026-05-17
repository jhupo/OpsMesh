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

- [ ] Add `agent.run` job type.
- [ ] Create run when task starts.
- [ ] Enqueue `agent.run`.
- [ ] Worker loads workspace, task, team, agent, and policy.
- [ ] Worker marks run `running`.
- [ ] Worker writes `run.started`.
- [ ] Worker handles success.
- [ ] Worker handles failure.
- [ ] Worker writes terminal run event.
- [ ] Worker releases lock.

Deliverables:

- task start creates a queued run
- worker can complete a fake run end to end

## Phase 8: OpenAI Agents SDK Integration

Goal: run a real SDK agent.

Tasks:

- [ ] Add OpenAI Agents SDK dependency.
- [ ] Implement agent factory from `agent_profiles`.
- [ ] Map model settings.
- [ ] Map instructions.
- [ ] Add Runner wrapper.
- [ ] Add runtime context object.
- [ ] Capture final output.
- [ ] Capture basic events.
- [ ] Persist model/tool/run events.
- [ ] Add safe error normalization.

Deliverables:

- worker can run one OpenAI Agents SDK agent
- output is stored in `agent_runs`
- run events are persisted

## Phase 9: Product Tool Layer

Goal: expose safe backend tools to agents.

Tasks:

- [ ] Define product tool interface.
- [ ] Implement tool permission checks.
- [ ] Implement `list_workspace_files`.
- [ ] Implement `read_workspace_file`.
- [ ] Implement `write_artifact`.
- [ ] Implement `search_workspace_memory` placeholder.
- [ ] Add tool event logging.
- [ ] Add tool error formatter.
- [ ] Add tests for tool workspace isolation.

Deliverables:

- agents can call product tools through SDK
- tools enforce workspace context

## Phase 10: Docker Runtime Manager

Goal: execute risky work in isolated containers.

Tasks:

- [ ] Add Docker client wrapper.
- [ ] Add `runtime_templates` table.
- [ ] Add `workspace_runtimes` table.
- [ ] Add `runtime_events` table.
- [ ] Add `runtime_commands` table.
- [ ] Implement create runtime.
- [ ] Implement start runtime.
- [ ] Implement stop runtime.
- [ ] Implement delete runtime.
- [ ] Implement command execution inside container.
- [ ] Add CPU limit.
- [ ] Add memory limit.
- [ ] Add disk/workdir policy.
- [ ] Add network policy.
- [ ] Add container labels.
- [ ] Add cleanup job.
- [ ] Add tests that host execution is never used.

Deliverables:

- worker can execute a command inside Docker
- command output is stored
- runtime is workspace-scoped

## Phase 11: Runtime Tool Integration

Goal: let agents use Docker-backed tools safely.

Tasks:

- [ ] Implement shell/runtime tool wrapper.
- [ ] Route shell calls to Runtime Manager.
- [ ] Enforce runtime policy.
- [ ] Enforce command timeout.
- [ ] Capture stdout/stderr.
- [ ] Persist `tool.called` and `tool.completed`.
- [ ] Support command failure.
- [ ] Add approval hook for risky commands.

Deliverables:

- agent can request shell execution
- execution happens inside Docker
- events are persisted

## Phase 12: Workspace Files And Artifacts

Goal: support user data and generated outputs.

Tasks:

- [ ] Add `workspace_files` table.
- [ ] Add `artifacts` table.
- [ ] Add `file_access_events` table.
- [ ] Implement local storage adapter.
- [ ] Implement file upload endpoint.
- [ ] Implement file download endpoint.
- [ ] Implement artifact download endpoint.
- [ ] Implement runtime file staging.
- [ ] Implement artifact collection from runtime.
- [ ] Add checksum calculation.
- [ ] Add file size limits.
- [ ] Add tests for file authorization.

Deliverables:

- user can upload file
- worker can stage approved file into runtime
- runtime output can become artifact
- user can download artifact

## Phase 13: Approvals And Resume

Goal: pause sensitive actions and resume after human decision.

Tasks:

- [ ] Add `approvals` table.
- [ ] Implement approval creation.
- [ ] Implement approval list endpoint.
- [ ] Implement approve endpoint.
- [ ] Implement reject endpoint.
- [ ] Add approval policy to agent profile.
- [ ] Pause run on approval.
- [ ] Enqueue resume job after approval.
- [ ] Resume or fail run based on decision.
- [ ] Add audit events.

Deliverables:

- risky action can pause run
- user can approve/reject
- worker can resume or fail safely

## Phase 14: Domain Task Extensions

Goal: support team-specific task data and correction flows.

Tasks:

- [ ] Add `team_type` to agent teams.
- [ ] Add `domain_type` to tasks.
- [ ] Add `generic_state` to tasks.
- [ ] Add `domain_state` to tasks.
- [ ] Add `domain_projects` table.
- [ ] Add `domain_items` table.
- [ ] Add `review_comments` table.
- [ ] Add `revision_requests` table.
- [ ] Implement task view endpoint.
- [ ] Implement review comment endpoint.
- [ ] Implement revision request endpoint.
- [ ] Route revision request to worker.
- [ ] Add tests for domain item workspace isolation.

Deliverables:

- task view endpoint returns generic plus domain payload
- user correction becomes structured backend state
- worker can execute a revision request

## Phase 15: Capabilities, Skills, And MCP

Goal: manage what agents are allowed to do.

Tasks:

- [ ] Add capability catalog.
- [ ] Add skill registry.
- [ ] Add workspace skill install table.
- [ ] Add tool group catalog.
- [ ] Add MCP server registry.
- [ ] Add MCP tool allowlist.
- [ ] Add MCP credential reference model.
- [ ] Add MCP health status.
- [ ] Map allowed MCP tools to SDK tools.
- [ ] Log MCP tool calls.
- [ ] Add approval policy for write-capable MCP tools.

Deliverables:

- agents see only allowed capabilities/tools
- MCP tools are workspace-scoped
- skills can affect instructions/runtime files without bypassing permissions

## Phase 16: Self-Hosted Runtime

Goal: allow user-owned machines to execute tasks.

Tasks:

- [ ] Add runtime provider field.
- [ ] Add runtime enrollment token.
- [ ] Add runtime credential model.
- [ ] Add self-hosted worker registration endpoint.
- [ ] Add heartbeat endpoint.
- [ ] Add job polling endpoint.
- [ ] Add job claim endpoint.
- [ ] Add progress event upload.
- [ ] Add selected artifact upload.
- [ ] Add local file reference model.
- [ ] Add credential revocation.

Deliverables:

- self-hosted worker can register
- platform can assign a job
- worker can report progress
- artifacts can be uploaded selectively

## Phase 17: Observability And Operations

Goal: make backend runs inspectable and operable.

Tasks:

- [ ] Add structured logs.
- [ ] Add run event viewer endpoint.
- [ ] Add runtime event endpoint.
- [ ] Add worker heartbeat.
- [ ] Add queue metrics endpoint.
- [ ] Add runtime cleanup scheduler.
- [ ] Add orphan container cleanup.
- [ ] Add failed job inspection.
- [ ] Add audit event filters.

Deliverables:

- operators can inspect failed runs
- stale runtime resources are cleaned
- audit trail is usable

## Phase 18: Backend Test Coverage

Goal: protect critical safety and orchestration paths.

Tasks:

- [ ] Test workspace isolation.
- [ ] Test role permissions.
- [ ] Test worker job workspace mismatch.
- [ ] Test run lock behavior.
- [ ] Test file download authorization.
- [ ] Test runtime command path.
- [ ] Test approval pause/resume.
- [ ] Test tool allowlist.
- [ ] Test MCP tool allowlist.
- [ ] Test artifact collection.
- [ ] Test domain revision flow.

Deliverables:

- core backend safety has automated tests
- regressions are caught early

## Suggested MVP Cut

Minimum backend demo:

- Phase 1 through Phase 13.

Differentiated backend demo:

- Phase 1 through Phase 15.

Advanced trust demo:

- Phase 1 through Phase 16.

Frontend can wait until the backend demo is stable.
