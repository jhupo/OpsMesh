# Cloud Control Plane And Runtime Spaces

## Purpose

This document defines the backend control plane that must exist before ChainCloud Agent Team can feel like a mature hosted product.

The product is still personal-workspace first. There is no billing or company account layer in scope. The cloud platform still needs strong operator controls because it creates queues, runs workers, provisions Docker containers, stores user files, connects to model providers, and may hand work to user-owned machines.

## Core Terms

| Term | Meaning | Durable owner |
| --- | --- | --- |
| Workspace | The tenant boundary for one user's AI company, data, agents, teams, tools, files, and audit records. | Postgres |
| Team | A reusable organization inside a workspace. Tasks flow through the existing team. | Postgres |
| Worker | A backend process that consumes Redis jobs and runs orchestration, agent execution, runtime cleanup, imports, indexing, or other async work. | Process plus heartbeat state |
| Runtime space | A controlled execution area for a workspace or team. It owns policy, storage scope, quota reservations, runtime leases, and network rules. | Postgres plus storage |
| Docker runtime | A concrete isolated container used by a runtime space to execute user-controlled or agent-controlled work. | Docker plus Postgres metadata |
| Self-hosted machine | A user-owned execution node that polls the cloud backend and runs jobs under cloud-issued policy. | Postgres plus machine credential |

The worker is not a Docker container by definition. A worker is the backend executor. Docker is the sandboxed execution plane that a worker may create or attach when a job needs shell, code, file mutation, local skill helpers, or unsafe MCP work.

## Runtime Space Model

A runtime space is the product boundary between "this team can work with files and tools" and "this specific container is executing a step right now."

Recommended scopes:

- `workspace`: default space shared by the workspace's teams.
- `team`: optional space for a persistent team filesystem, stricter network policy, or stronger quotas.
- `task`: temporary space created for a long-running project and destroyed after completion.

The MVP should support `workspace` and `team` scopes in the schema, then use ephemeral Docker containers per run or task step. Persistent containers should be allowed only after quota reservation, cleanup evidence, and network controls are implemented.

### Why Not One Permanent Docker Per Team

One permanent Docker container per team feels simple, but it becomes risky:

- stale processes can keep running after a task is cancelled
- dependency installs can poison future work
- file permissions and secrets can accumulate
- cleanup and audit evidence become harder
- concurrent tasks can trample each other's temporary state

The better product model is a persistent runtime space with controlled storage and policy, plus ephemeral or task-scoped containers leased from that space. This gives the team a stable workspace without turning one container into an unmanaged server.

## Managed Objects

The cloud control plane must manage the following objects.

### Identity And Workspace

- users
- workspaces
- workspace memberships and roles
- workspace settings
- workspace safety defaults
- personal resource quota presets
- API tokens and session state
- audit and security events

Operator controls:

- disable or lock a workspace
- inspect quota usage and abuse indicators
- rotate or revoke credentials
- force-cancel active runs
- export audit evidence

### Team Organization

- teams
- departments
- team members
- reporting lines
- member responsibilities
- member availability
- member concurrency limits
- team-level runtime space binding
- team-level file and network policies

Operator controls:

- view team resource usage
- block a team runtime space after policy violations
- inspect queued, running, blocked, and failed work by team

### Agent Workforce

- agent profiles
- agent versions
- role and instruction snapshots
- model provider policy
- tool policy
- runtime policy
- memory policy
- approval policy
- installed skills
- public talent listings and hired agent copies

Operator controls:

- disable an unsafe public listing
- quarantine an agent profile
- inspect tool and runtime permissions
- view run snapshots proving what policy was used

### Tasks, Runs, And Scheduling

- tasks
- task priority
- task steps
- dependency DAGs
- work packages
- agent runs
- run events
- task messages
- corrections and revisions
- blocked scheduling reasons
- scheduler reservations

Operator controls:

- view queue depth by workspace, team, priority, and reason
- pause scheduling for one workspace or runtime space; workspace scheduler policy now supports a durable `paused` flag and runtime spaces support `paused` status to block new runs without deleting the team workspace
- cancel stuck runs
- requeue safe failed jobs
- inspect starvation and fairness metrics

### Worker Fleet

- worker process heartbeats
- worker type: API-adjacent background, agent runner, runtime cleanup, import/export, indexing, self-hosted dispatch
- worker version
- worker capacity
- assigned queue names
- current leases
- drain status
- last error

Operator controls:

- drain a worker
- disable a worker version
- rebalance queue assignment
- inspect active leases and stuck jobs
- detect duplicate execution attempts

### Queues And Locks

- Redis queues
- priority buckets
- delayed retries
- dead letters
- idempotency keys
- run locks
- scheduler locks
- runtime lease locks

Operator controls:

- inspect queue latency percentiles
- inspect dead-letter causes
- replay or discard dead-letter jobs
- detect lock leaks
- expire abandoned reservations safely

### Runtime Spaces

- runtime space records
- scope: workspace, team, or task
- bound workspace and optional team or task
- storage root
- runtime policy
- network policy
- default Docker template
- quota limits
- current reserved usage
- cleanup policy
- trust state

Operator controls:

- create, disable, reset, or archive a runtime space
- view CPU, memory, disk, and active run reservations
- view network policy and egress evidence
- view cleanup evidence
- quarantine a space after suspicious execution

### Docker Runtime Plane

- runtime templates
- allowed images
- Docker containers
- container labels
- workspace volumes
- mounted staged files
- runtime commands
- runtime events
- runtime leases
- cleanup evidence

Operator controls:

- manage image allowlist
- set default CPU, memory, process, disk, timeout, log, and artifact limits
- disable network by default
- configure allowed egress domains
- kill a container by workspace, runtime space, task, or run
- run leak cleanup and record evidence

### Self-Hosted Machines

- enrollment tokens
- machine identities
- machine credentials
- heartbeat state
- machine trust state: active, degraded, quarantined, revoked
- capability policy
- max concurrent jobs
- supported runtimes and tools
- artifact upload policy
- revocation evidence

Operator controls:

- revoke a machine
- quarantine stale or suspicious machines
- restrict job assignment by capability and trust
- view affected jobs after revocation
- force credential rotation

### Files, Artifacts, And Storage

- workspace files
- task input files
- runtime staged files
- artifacts
- artifact versions
- archive export jobs
- archive import jobs
- checksums
- storage quota usage

Operator controls:

- enforce upload, download, staging, artifact, and archive size limits
- inspect storage growth by workspace and runtime space
- block oversized exports
- verify artifact collection from runtimes
- delete quarantined transient runtime files

### Network And Egress

- default network mode
- allowed domains
- blocked domains
- connector-specific network permissions
- egress proxy policy
- DNS/IP block policy
- network events

Operator controls:

- set global default to no network
- approve explicit allowlist policies
- block private address ranges for cloud Docker runtimes
- inspect egress attempts and denials
- quarantine runtime spaces with suspicious network activity

### Model Providers And Secrets

- provider credentials
- base URL aliases
- model allowlists
- workspace default provider
- per-agent provider overrides
- fallback policies
- key references
- external vault references
- provider health state

Operator controls:

- disable a provider
- rotate encrypted hosted credentials
- inspect provider fallback decisions without exposing secrets
- enforce per-agent and workspace model allowlists
- prevent fallback across workspace boundaries

### MCP, Skills, And Tools

- MCP server catalog
- MCP tool allowlists
- MCP credentials
- skill catalog
- workspace-local skill installs
- public skill provenance
- installed skill versions
- tool execution logs
- tool approval rules

Operator controls:

- disable a public or private MCP server
- disable a tool or installed skill
- inspect tool call failure rates
- view credential usage without seeing secret values
- require approvals for high-risk tool classes

### Approvals, Audit, And Security

- approval requests
- approval decisions
- security events
- policy snapshots
- denied actions
- cleanup failures
- cross-workspace denial evidence

Operator controls:

- inspect pending approvals
- inspect security event streams
- define high-risk action policies
- export evidence for a workspace

### Operations Aggregates

- active tasks
- active runs
- queue latency
- worker capacity
- runtime saturation
- Docker leak counts
- self-hosted stale heartbeat counts
- storage usage
- tool error rates
- approval backlog

Operator controls:

- dashboard-level summaries
- drill-down by workspace, team, runtime space, queue, worker, and provider
- alert thresholds
- maintenance mode
- global kill switches for risky execution classes

## Concurrency Rules

The control plane must protect every transition that can start work, reserve capacity, or mutate runtime state.

Required rules:

- Select queued steps through a central scheduler, not through each worker independently.
- Store capacity reservations in Postgres before enqueueing an executable run.
- Make reservations idempotent by workspace, task step, and run.
- Release reservations on completion, failure, cancellation, timeout, and cleanup.
- Use row locks or equivalent transactional guards when selecting and reserving queued work.
- Use Redis locks only for short-lived execution coordination, not durable truth.
- Include `workspace_id` and `runtime_space_id` in queue payloads and locks.
- Never execute a job if the loaded task, step, agent, runtime space, or tool policy no longer matches the snapshot.
- Detect duplicate worker execution and fail the later attempt safely.
- Record blocked reasons when work cannot start because of quotas, dependencies, runtime capacity, missing approvals, or degraded self-hosted machines.

## Recommended Data Additions

Suggested tables:

- `runtime_spaces`
- `runtime_space_bindings`
- `runtime_space_quotas`
- `runtime_space_reservations`
- `runtime_space_events`
- `runtime_leases`
- `worker_nodes`
- `worker_leases`
- `scheduler_decisions`
- `egress_policy_rules`
- `egress_events`

Suggested fields:

- `agent_teams.runtime_space_id`
- `tasks.runtime_space_id`
- `task_steps.runtime_space_id`
- `agent_runs.runtime_space_id`
- `workspace_runtimes.runtime_space_id`
- `runtime_commands.runtime_space_id`
- `workspace_files.runtime_space_id` for staged or runtime-origin files when applicable

## Backend APIs

Workspace-scoped user APIs:

```text
GET  /api/v1/workspaces/{workspace_id}/runtime-spaces
POST /api/v1/workspaces/{workspace_id}/runtime-spaces
GET  /api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}
PATCH /api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}
POST /api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}/reset
GET  /api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}/events
GET  /api/v1/workspaces/{workspace_id}/operations/overview
GET  /api/v1/workspaces/{workspace_id}/operations/scheduler
GET  /api/v1/workspaces/{workspace_id}/operations/runtime-capacity
```

Operator APIs should be separate from workspace user APIs and require platform operator credentials:

```text
GET  /api/v1/admin/workspaces
GET  /api/v1/admin/workers
POST /api/v1/admin/workers/{worker_id}/drain
GET  /api/v1/admin/runtime-spaces
POST /api/v1/admin/runtime-spaces/{runtime_space_id}/quarantine
GET  /api/v1/admin/docker/leases
POST /api/v1/admin/docker/leases/{lease_id}/kill
GET  /api/v1/admin/queues
GET  /api/v1/admin/security-events
```

Admin APIs must never expose secrets or raw file contents. They expose metadata, counters, policy snapshots, and evidence.

## Implementation Order

1. Add the runtime space data model and bind workspace/team/task/run records to it.
2. Move workspace quota enforcement to runtime space reservations.
3. Add worker node and worker lease tracking.
4. Extend the scheduler to reserve CPU, memory, active run, Docker, self-hosted, and storage capacity atomically.
5. Add operations aggregate APIs for queue latency, worker capacity, runtime saturation, and blocked reasons.
6. Add Docker runtime lease evidence and cleanup verification.
7. Add self-hosted trust states, policy compatibility checks, and revocation evidence.
8. Add egress policy rules and egress event logging.
9. Add admin APIs with strict platform-operator authentication.
10. Add adversarial tests for concurrent scheduling, duplicate jobs, workspace boundary violations, and leaked runtime resources.

## Acceptance Criteria

- A workspace can own one or more runtime spaces.
- A team can be bound to a runtime space without sharing files or containers across workspaces.
- Multiple tasks can run concurrently while respecting active run, Docker, self-hosted, CPU, memory, and storage quotas.
- Workers cannot double-start the same step under concurrent load.
- Operators can see which workspace, team, task, run, worker, container, tool, and provider caused each risky action.
- Cleanup success and cleanup failure are both recorded as durable evidence.
- Self-hosted machines can be quarantined or revoked without losing auditability.
- Runtime spaces make the team feel persistent while Docker containers remain controlled, inspectable, and disposable.
