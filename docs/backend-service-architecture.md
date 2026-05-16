# Backend Service Architecture

## Goal

The backend should be split into clear responsibility domains. The API service should not directly execute long-running agent work. Workers should execute runs asynchronously, communicate through queues and persisted state, and use the runtime manager for Docker-backed execution.

## Three Backend Domains

### 1. OpenAI Agents Runtime Layer

This layer adapts `openai-agents-python` into our product.

Responsibilities:

- build SDK `Agent` objects from database `agent_profiles`
- build `Runner` configuration
- map product tools to SDK tools
- map skills into agent instructions and runtime files
- configure handoffs and agents-as-tools
- configure guardrails
- configure sessions and memory
- capture SDK run events
- translate SDK outputs into product run events and artifacts

This layer should not decide workspace permissions by itself. It receives already validated runtime context from the orchestration layer.

### 2. Agent Management And Orchestration Layer

This is the brain of the product backend.

Responsibilities:

- manage agent profiles
- manage teams
- manage tasks and task steps
- plan runs
- assign tasks to agents
- enforce workspace policies
- enforce agent permissions
- choose runtime mode
- request approvals
- pause and resume runs
- enqueue worker jobs
- persist run state
- write audit events

This layer decides what should happen and whether it is allowed.

### 3. Product Backend Service Layer

This is the API and application layer used by frontend, API clients, and admins.

Responsibilities:

- authentication
- workspace membership
- user-facing REST/API endpoints
- validation
- CRUD for workspaces, agents, teams, tasks, approvals, files, and runtimes
- websocket/SSE progress streams
- admin operations
- product settings

This layer should be fast and request/response oriented. It should enqueue long-running work instead of executing it inline.

## Worker Layer

Workers are required. They are not optional.

Workers execute asynchronous work that should not happen inside the API request lifecycle.

Worker responsibilities:

- consume queued task/run jobs
- load workspace, task, agent, and policy state from Postgres
- acquire Redis locks
- build runtime context
- call the OpenAI Agents Runtime Layer
- route tool execution through Runtime Manager
- write run events
- update task and run status
- collect artifacts
- publish progress events
- handle retries and failures

Workers communicate with the API service through Postgres and Redis, not direct in-memory calls.

## Runtime Manager

Runtime Manager is a backend service module used by workers and admin APIs.

Responsibilities:

- create Docker containers
- start and stop runtimes
- enforce resource limits
- enforce network policy
- stage files into runtime directories
- execute commands inside containers
- collect stdout, stderr, logs, and artifacts
- run health checks
- cleanup stale resources

Runtime Manager is not the same as the OpenAI Agents Runtime Layer. It executes system-level work inside isolated containers.

## Communication Flow

```text
Frontend or API Client
    |
    v
Product Backend API
    |
    +--> Postgres: create task/run records
    +--> Redis Queue: enqueue run job
    |
    v
Worker
    |
    +--> Postgres: load workspace/task/agent/policy
    +--> Redis Lock: lock run execution
    +--> OpenAI Agents Runtime Layer
    |        |
    |        v
    |   openai-agents-python Runner
    |
    +--> Runtime Manager
             |
             v
        Docker Runtime
```

## What Executes Where

### API Service Executes

- request validation
- authentication
- workspace authorization
- simple CRUD
- enqueueing jobs
- reading progress
- returning results

### Worker Executes

- task planning
- agent runs
- retries
- approvals resume
- artifact collection
- memory consolidation
- cleanup tasks

### Docker Runtime Executes

- shell commands
- user/agent-controlled scripts
- local skill helper scripts
- code execution
- file mutation
- tests
- renderers and converters
- unsafe or untrusted tool operations

### OpenAI Hosted Tools Execute

- OpenAI-managed web search
- file search
- code interpreter when using hosted mode
- image generation
- hosted MCP when configured

### External MCP Server Executes

- MCP tool implementation on the MCP server side
- connector-specific actions

The backend still authorizes and logs MCP calls before exposing them to agents.

## Job Types

Initial worker jobs:

- `task.plan`
- `task.run_step`
- `agent.run`
- `agent.resume_after_approval`
- `runtime.cleanup`
- `memory.consolidate`
- `artifact.collect`

Future jobs:

- `workspace.index_files`
- `connector.sync`
- `scheduled_task.run`
- `runtime.snapshot`
- `runtime.health_check`

## Queue Design

Redis can be used as the initial queue backend.

Suggested queues:

- `queue:agent_runs`
- `queue:runtime`
- `queue:memory`
- `queue:maintenance`

Each job payload must include:

- `workspace_id`
- job type
- resource id
- idempotency key
- requested by user or agent
- created timestamp

Workers must re-load all resource data from Postgres. Job payloads are not trusted as source of truth.

## Run Execution Flow

1. API receives task creation request.
2. API validates membership and creates `tasks` row.
3. Orchestration layer creates initial `agent_runs` row.
4. API enqueues `agent.run` job in Redis.
5. Worker consumes job.
6. Worker acquires run lock.
7. Worker loads workspace, task, team, agent profile, tools, skills, and policy.
8. Worker chooses runtime mode.
9. Worker asks Runtime Manager to create or attach runtime if needed.
10. Worker builds SDK Agent and Runner config.
11. Worker starts OpenAI Agents SDK run.
12. Tool calls are routed to hosted tools, MCP, product tools, or Docker runtime.
13. Worker streams and persists run events.
14. If approval is needed, worker marks run `waiting_approval` and exits.
15. If complete, worker stores final output and artifacts.
16. Worker updates task state and releases lock.

## Approval Resume Flow

1. User approves or rejects an approval request through API.
2. API validates workspace role and writes decision.
3. API enqueues `agent.resume_after_approval`.
4. Worker reloads run state.
5. Worker resumes the SDK run or creates a follow-up run depending on saved state.
6. Worker persists events and final outcome.

## State Ownership

Postgres owns durable state:

- tasks
- task steps
- agent runs
- approvals
- run events
- runtime records
- artifacts
- audit events

Redis owns coordination:

- queues
- locks
- progress cache
- pub/sub events
- short-lived idempotency keys

Docker owns temporary execution state:

- working directory
- intermediate files
- command process state

Docker must not be the only place where important final state exists.

## Module Boundary Proposal

```text
backend/
  app/
    api/                    # Product Backend Service Layer
    auth/
    workspaces/
    agents/                 # Agent management CRUD
    teams/
    tasks/
    approvals/
    runtimes/
    orchestration/          # Agent Management And Orchestration Layer
    agent_runtime/          # OpenAI Agents Runtime Layer
    runtime_manager/        # Docker control plane
    workers/
    db/
    redis/
    events/
```

## Key Rule

The API layer answers user requests. The orchestration layer decides what should happen. The OpenAI Agents layer runs model workflows. The worker layer performs long-running execution. The runtime manager executes dangerous work inside Docker.

These boundaries should stay separate even if they start in one deployable backend process.
