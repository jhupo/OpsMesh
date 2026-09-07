# Isolation And Security

## Goal

OpsMesh is a multi-user, multi-workspace system. User and workspace isolation is a core product requirement, not an implementation detail. The system must prevent data, tasks, files, memory, tools, and agent runs from crossing workspace boundaries unless an explicit sharing feature is designed later.

## Isolation Boundaries

The primary tenant boundary is `workspace_id`.

Every workspace-owned object must include `workspace_id`, including:

- agent profiles
- agent teams
- tasks
- task steps
- agent runs
- run events
- approvals
- files
- artifacts
- memory entries
- audit events
- tool credentials
- connector settings

User identity is separate from workspace identity. A user may belong to many workspaces, and each workspace membership has its own role.

## Access Model

Every request must resolve:

1. authenticated user
2. target workspace
3. membership in that workspace
4. workspace role
5. requested resource
6. resource `workspace_id`
7. action permission

Access is allowed only when the user has active membership in the target workspace and the resource belongs to that workspace.

User API tokens may additionally restrict the workspace IDs, workspace actions, and account actions
available to the caller. Token scope is an upper bound on the user's current workspace role: it
cannot grant membership, exceed that role, create an unrestricted child token, or delegate an
action that the calling token does not hold. Password-login tokens and the internal service-token
path are explicitly unrestricted; custom automation tokens should use the narrowest practical
scope. Workspace listing is filtered to the token's workspace IDs, and every workspace dependency
rechecks both token scope and current membership.

## Required Authorization Rule

Application code must never authorize access by resource ID alone.

Bad:

```text
SELECT * FROM tasks WHERE id = :task_id
```

Good:

```text
SELECT * FROM tasks
WHERE id = :task_id
  AND workspace_id = :workspace_id
```

The same rule applies to updates and deletes.

## Postgres Isolation

Postgres is the source of truth for isolation.

Requirements:

- All workspace-owned tables include `workspace_id`.
- Foreign keys should preserve workspace ownership where practical.
- All queries are scoped by `workspace_id`.
- Unique constraints should usually include `workspace_id`.
- Audit events record both `user_id` and `workspace_id`.
- Background workers must load jobs with workspace scope.

Recommended future hardening:

- Postgres Row Level Security for workspace-owned tables.
- Per-request database setting such as `app.current_workspace_id`.
- Automated tests that attempt cross-workspace reads and writes.

## Redis Isolation

Redis must not use unscoped keys for workspace data.

Key format:

```text
workspace:{workspace_id}:...
user:{user_id}:...
run:{workspace_id}:{run_id}:...
lock:{workspace_id}:{resource_type}:{resource_id}
queue:{workspace_id}:...
```

Rules:

- No workspace data in global keys.
- Locks include `workspace_id`.
- Pub/sub channels include `workspace_id`.
- Cached permission results include `user_id` and `workspace_id`.
- Redis values must be treated as disposable cache or coordination state, not the source of truth.

## File And Artifact Isolation

Files and artifacts must be workspace-scoped.

Recommended object path format:

```text
workspaces/{workspace_id}/files/{file_id}/{filename}
workspaces/{workspace_id}/artifacts/{artifact_id}/{filename}
workspaces/{workspace_id}/runs/{run_id}/outputs/{filename}
```

Rules:

- Never infer workspace access from a file URL alone.
- File metadata lives in Postgres with `workspace_id`.
- Downloads check membership before issuing a signed URL or stream.
- Agent tools receive only workspace-authorized file references.
- Generated artifacts inherit the run's `workspace_id`.

## Agent Runtime Isolation

Agent runs must always carry an explicit workspace context.

Runtime context should include:

- `workspace_id`
- `user_id`
- `task_id`
- `run_id`
- allowed tools
- allowed file scopes
- approval policy
- memory policy

Rules:

- An agent can only read memory from its workspace.
- An agent can only write memory to its workspace.
- An agent can only access tools allowed by its profile and workspace policy.
- An agent cannot call another workspace's agent.
- Handoffs and agents-as-tools are limited to agents in the same workspace unless a future cross-workspace sharing feature exists.
- Runtime-generated tasks inherit the current workspace and must pass policy checks.

## Tool Isolation

Tools are one of the highest-risk isolation surfaces.

Requirements:

- Tool credentials are workspace-scoped.
- Tool execution receives workspace context.
- Tool calls validate resource ownership before execution.
- External connectors cannot be reused across workspaces unless explicitly installed in each workspace.
- Write-capable tools should support approval policies.
- Tool outputs are persisted with `workspace_id`.

Examples:

- A file search tool can only search workspace files.
- A shell tool can only mount the current workspace.
- A CRM connector can only use credentials installed for the current workspace.
- A memory retrieval tool can only retrieve current workspace memory.

## Sandbox Isolation

Sandbox agents must run with workspace-specific manifests and mounts.

User-controlled or agent-controlled code must never execute directly on the application server host. Shell commands, local skill helpers, code execution, file mutation, and untrusted MCP tools must run inside an isolated runtime such as Docker, a microVM, a dedicated VM, a jailed sandbox, or a trusted hosted tool environment.

Rules:

- Each sandbox session belongs to one workspace.
- Mounted files are limited to approved workspace files.
- Sandbox snapshots are workspace-owned artifacts.
- Sandbox output files are stored under the same workspace.
- Reusing a sandbox session requires matching `workspace_id`.
- Destructive sandbox actions should require approval based on workspace policy.
- Persistent runtimes are allowed only when they are isolated from the application host.
- Persistent runtimes must not mount host secrets, broad host paths, or the Docker socket.
- Runtime network access should be denied or allowlisted by default.
- Runtime resource limits should cover CPU, memory, disk, process count, and execution time.

## Memory Isolation

Memory is workspace-scoped by default.

Rules:

- Retrieval filters by `workspace_id`.
- Agent-scoped memory also includes `agent_profile_id`.
- User-scoped preferences include `user_id`, but should not leak into unrelated workspaces unless explicitly global.
- Memory promotion from a run should record source `task_id` and `run_id`.
- Human review can be required before long-term memory is stored.

## Audit Requirements

Every sensitive action should create an audit event with:

- `workspace_id`
- `actor_type`
- `actor_id`
- `user_id` when applicable
- `agent_run_id` when applicable
- action
- target resource type
- target resource id
- decision or outcome
- timestamp

Audit logs must be append-only from the product perspective.

## Background Worker Isolation

Queued jobs must include `workspace_id`.

Worker rules:

- Re-load resources by both ID and `workspace_id`.
- Do not trust job payloads as authorization proof.
- Validate the agent profile belongs to the workspace.
- Validate the task belongs to the workspace.
- Persist all generated events with the same workspace.
- Drop or fail the job if workspace membership or policy no longer allows execution.

## API Requirements

Every workspace API route should include or resolve workspace scope.

Preferred route shape:

```text
/api/workspaces/{workspace_id}/tasks
/api/workspaces/{workspace_id}/agents
/api/workspaces/{workspace_id}/runs
/api/workspaces/{workspace_id}/approvals
```

Rules:

- Request user must be a member of `workspace_id`.
- Request body cannot override `workspace_id`.
- Server assigns `workspace_id` from the route or current workspace context.
- Responses must not include resources from other workspaces.

## Testing Strategy

Isolation tests should be mandatory.

Test cases:

- user cannot list another workspace's tasks
- user cannot read another workspace's task by ID
- user cannot update another workspace's agent profile
- worker cannot run a task with mismatched workspace and agent IDs
- agent cannot retrieve memory from another workspace
- file download fails across workspace boundary
- Redis cache keys include workspace scope
- handoff to another workspace's agent is rejected

## Non-Goals For MVP

- Cross-workspace sharing.
- Public agent publishing or marketplace.
- Organization-wide shared memory across workspaces.
- Workspace federation.
- Per-workspace database instances.

These can be added later, but the first product should assume strict workspace isolation.
