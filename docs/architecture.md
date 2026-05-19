# Architecture

## Product Shape

ChainCloud Agent Team is a multi-user workspace product for operating AI agents as digital employees inside isolated user workspaces. OpenAI Agents SDK provides the execution primitives, while this application owns the business layer: users, workspaces, agent definitions, task orchestration, permissions, persistence, audit logs, and product APIs.

The core unit is a workspace. A workspace is the boundary for data, agents, tasks, tools, files, memory, and access control.

User and workspace isolation is mandatory. The product must enforce isolation across application APIs, Postgres queries, Redis keys, file storage, agent runtime context, tools, memory retrieval, background workers, and audit logs. See [Isolation And Security](isolation-and-security.md).

## System Layers

1. Application API

   Handles authentication, workspace membership, CRUD APIs, task creation, approvals, notifications, and API-facing queries.

2. Orchestration Service

   Converts workspace tasks into agent runs, manages task state transitions, coordinates manager and specialist agents, persists run events, and resumes paused runs.

3. Agent Runtime

   Uses `openai-agents-python` to run `Agent`, `Runner`, tools, handoffs, guardrails, sessions, tracing, and sandbox agents. Runtime code should be mostly stateless and driven by database configuration. User-controlled or agent-controlled executable work must run inside an isolated runtime, never directly on the application server host.

4. Persistence

   Postgres is the source of truth. Redis is used only for derived, short-lived, or coordination data.

5. Worker Layer

   Background workers execute long-running agent runs, tool calls, file indexing, memory consolidation, and webhook delivery.

6. Runtime Control Plane

   Backend services manage Docker-backed isolated runtimes for executable agent work. The backend creates containers, applies resource limits, controls mounts and network policy, executes approved commands inside containers, collects artifacts, and cleans up runtime resources. See [Backend Runtime Control Plane](backend-runtime-control-plane.md).

7. Cloud Control Plane

   Platform operator services manage runtime spaces, worker fleet health, queue pressure, quota reservations, Docker leases, self-hosted trust state, network policy, and cleanup evidence. This is still personal-workspace oriented and does not require a billing or company-account layer. See [Cloud Control Plane And Runtime Spaces](cloud-control-plane-and-runtime-spaces.md).

The backend should be organized into three major domains: the OpenAI Agents Runtime Layer, the Agent Management and Orchestration Layer, and the Product Backend Service Layer. Long-running work is executed by workers through queues and persisted state, not directly inside API requests. See [Backend Service Architecture](backend-service-architecture.md).

The runtime model should support both platform-managed cloud Docker runtimes and future self-hosted runtimes on user-owned machines. Self-hosted workers connect outbound to the platform, keep sensitive data local when configured, and execute tasks in local isolated runtimes. See [Self-Hosted Runtimes](self-hosted-runtimes.md).

## Multi-User Workspace Model

Primary entities:

- `users`: Human accounts.
- `workspaces`: Tenant boundary for one user's or one account's AI workforce and product data.
- `workspace_members`: User membership and role inside a workspace.
- `agent_profiles`: Versioned agent definitions owned by a workspace.
- `agent_teams`: Named collections of agents with coordination rules.
- `tasks`: User-created or agent-created units of work.
- `task_steps`: Planned sub-work owned by a task.
- `agent_runs`: Concrete executions of one agent against one task or step.
- `run_events`: Append-only event stream for model calls, tool calls, handoffs, approvals, errors, and final outputs.
- `approvals`: Human approval requests for sensitive actions.
- `workspace_files`: Files and artifacts available to workspace tasks.
- `memory_entries`: Workspace-scoped or agent-scoped long-term memory.
- `audit_events`: Security and governance trail.

Every workspace-owned table should include `workspace_id`. Queries should always scope by workspace membership.

Application code must never authorize or load workspace-owned data by resource ID alone. Reads, updates, deletes, background jobs, tool calls, and memory retrieval must all include workspace scope.

## Roles And Permissions

Initial workspace roles:

- `owner`: Manage workspace settings, members, agents, tools, and destructive actions.
- `admin`: Manage agents, tasks, files, and most tools.
- `operator`: Create and supervise tasks, approve allowed actions.
- `viewer`: Read-only access to tasks, runs, files, and reports.

Agent permissions are separate from human permissions. Each agent profile should declare:

- allowed tool groups
- allowed file scopes
- allowed external connectors
- approval policy
- maximum run duration
- maximum retry count
- whether it may create new tasks
- whether it may delegate to other agents

## Agent Model

An agent profile stores configuration, not arbitrary application code:

- name
- role description
- instructions
- model
- model settings
- enabled tools
- handoff targets
- guardrails
- memory policy
- sandbox policy
- version
- status

The runtime turns an `agent_profile` into an OpenAI Agents SDK `Agent` or `SandboxAgent`.

Specialist agents should be exposed in two ways:

- handoffs when the specialist should take over the conversation or task step
- agents-as-tools when a manager agent should keep orchestration control

## Task Orchestration

Task lifecycle:

```text
draft -> queued -> planning -> running -> waiting_approval -> running -> completed
                                      -> blocked
                                      -> failed
                                      -> cancelled
```

Recommended first workflow:

1. User creates a task in a workspace.
2. Orchestration service enqueues the task.
3. Manager agent reads the task and creates a plan.
4. The system persists `task_steps`.
5. Specialist agents execute steps.
6. Human approvals pause sensitive actions.
7. Manager agent synthesizes final output.
8. The task stores artifacts, run summary, and audit events.

The orchestrator, not the model, owns durable state transitions. Models may propose plans and actions, but the service validates and persists them.

## Postgres Responsibilities

Use Postgres for:

- users, workspaces, membership
- agent definitions and versions
- tasks and task steps
- run records and event logs
- approvals
- files and artifact metadata
- memory entries
- audit logs
- durable workflow state

Postgres should be treated as the only source of truth. Anything required to recover after a crash belongs in Postgres.

## Redis Responsibilities

Use Redis for:

- task queues or worker coordination
- distributed locks for task/run execution
- short-lived run progress cache
- websocket/SSE pub-sub fanout
- rate limit counters
- idempotency windows
- temporary tool output cache

Do not store irreplaceable task state only in Redis.

All Redis keys that refer to workspace data must include `workspace_id`, for example `workspace:{workspace_id}:...`, `run:{workspace_id}:{run_id}:...`, or `lock:{workspace_id}:{resource_type}:{resource_id}`.

## Agent Memory

Memory should be workspace-scoped by default, with optional agent-scoped and user-scoped memory.

Suggested memory types:

- task summary
- project fact
- user preference
- tool lesson
- failed approach
- reusable workflow pattern

Short-term conversation state can use Agents SDK sessions. Long-term memory should be explicitly written to Postgres and optionally indexed for retrieval.

## Human Approval

Actions that should require approval:

- running shell commands in privileged environments
- editing or deleting workspace files
- sending external messages
- calling write-capable third-party APIs
- creating new agents or changing agent permissions
- publishing final artifacts outside the workspace

Approval records must include requester agent, proposed action, arguments, risk level, approver, decision, and final outcome.

## Observability And Audit

The product should capture:

- task state changes
- agent run start/end
- model used
- tool calls and results
- handoffs
- guardrail results
- approvals
- errors and retries
- generated artifacts

OpenAI tracing is useful for debugging model workflows. Product audit logs should remain independent and durable inside Postgres.

## Initial Implementation Path

1. Build FastAPI backend with Postgres and Redis connections.
2. Add workspace, membership, agent profile, task, and run schemas.
3. Implement a simple manager agent and one specialist agent.
4. Add task queue worker that runs an agent task through OpenAI Agents SDK.
5. Persist run events and expose progress through APIs.
6. Add approval pause/resume.
7. Add workspace memory and file/artifact support.

Workspace files and artifacts are first-class resources. Uploads, downloads, previews, runtime staging, artifact collection, and exports must all enforce workspace authorization. See [Workspace Data Management](workspace-data-management.md).
