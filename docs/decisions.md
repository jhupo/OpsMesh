# Decisions

This document records important product and architecture decisions. These decisions can change, but changes should be intentional and documented.

## Product Decisions

### D001: Workspace Is The Tenant Boundary

Every user, agent, task, file, artifact, memory entry, runtime, and audit event is scoped to a workspace.

Reason:

Workspace isolation keeps the product safe for multi-user usage where each user or account owns an isolated AI workforce.

### D002: No Billing In Scope

The product will not include billing in the current plan.

Reason:

The product is focused on isolated workspaces, agent orchestration, security, and runtime execution.

### D003: Agents Are Modeled As Digital Employees

The user experience should present agents as employees, not low-level graph nodes.

Reason:

This supports a clearer product narrative: users hire, staff, supervise, and review AI workers.

## Architecture Decisions

### D004: OpenAI Agents SDK Is The Agent Runtime Foundation

The system uses `openai-agents-python` for agent execution primitives.

Reason:

It provides Agent, Runner, tools, handoffs, guardrails, sessions, tracing, sandbox, and realtime foundations.

### D005: Postgres Is The Source Of Truth

Durable state belongs in Postgres.

Includes:

- users
- workspaces
- agents
- teams
- tasks
- runs
- events
- approvals
- files
- artifacts
- runtimes
- audit logs

### D006: Redis Is Coordination Infrastructure

Redis is used for queues, locks, pub/sub, progress cache, idempotency windows, and short-lived cache.

Redis must not be the only place where important durable state exists.

### D007: API Does Not Execute Long-Running Agent Work

The API layer validates requests, writes state, and enqueues jobs. Workers execute long-running work.

Reason:

This keeps API requests fast, recoverable, and horizontally scalable.

### D008: Workers Are Required

Worker processes consume jobs, run agents, call the runtime manager, collect artifacts, and update state.

Reason:

Agent runs, Docker work, approvals, retries, artifact collection, and memory consolidation are asynchronous workflows.

### D009: Dangerous Execution Never Runs On The Application Host

User-controlled or agent-controlled commands, scripts, code execution, local skill helpers, and untrusted tool operations must run inside an isolated runtime.

Allowed runtimes:

- Docker container
- microVM
- dedicated VM
- trusted hosted tool environment
- user self-hosted isolated runtime

### D010: Backend Manages Docker As A Control Plane

The backend creates, starts, stops, limits, monitors, and deletes Docker runtimes.

Reason:

The backend owns workspace policy and runtime security. Docker is execution infrastructure.

### D011: Self-Hosted Runtimes Use Outbound Worker Connection

User-owned machines should run a local worker that connects outbound to the platform.

Reason:

This avoids platform SSH access, simplifies firewalls, and lets users keep data local.

### D012: Runtime Files Are Temporary Until Collected

Files inside Docker or self-hosted runtimes are not durable product data until collected as artifacts or workspace files.

Reason:

This keeps product state explicit and recoverable.

### D013: Resource Access Requires Workspace Scope

Application code must not authorize or load workspace-owned resources by ID alone.

Required pattern:

```text
resource_id + workspace_id
```

Reason:

This prevents cross-workspace data access.

## Security Decisions

### D014: MCP, Tools, Skills, And Connectors Are Permissioned

Agents only see and use tools granted by their profile and workspace policy.

Reason:

Tools are high-risk because they connect models to real actions.

### D015: Write-Capable Tools Require Approval Policy

Tools that write files, call external systems, send messages, mutate data, or execute code should support approval.

Reason:

Human accountability is a core product principle.

### D016: File Downloads Are Authorized Product Actions

Downloads must check workspace membership and permission.

Reason:

Storage paths and URLs must not become implicit authorization.

### D017: Audit Logs Are Append-Only Product Records

Sensitive actions write audit events.

Reason:

Users need accountability and traceability.

## Implementation Decisions

### D018: Start Backend-First

Frontend can wait. The first implementation should focus on backend APIs, models, workers, and runtime control.

Reason:

The hardest risks are orchestration, security, runtime isolation, and persistence.

### D019: Start With Cloud Docker Runtime

Initial executable runtime should be Docker managed by backend.

Reason:

Docker gives a practical local execution boundary before adding self-hosted workers or remote providers.

### D020: Design For Self-Hosted But Implement Later

The database and architecture should leave room for self-hosted runtimes, but it does not need to ship first.

Reason:

Self-hosted runtime adds protocol, worker distribution, credential, and local security complexity.
