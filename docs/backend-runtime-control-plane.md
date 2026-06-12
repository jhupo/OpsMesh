# Backend Runtime Control Plane

## Goal

The backend runs on the server and manages isolated Docker runtimes for agent work. The backend is the control plane. Docker containers are the execution plane.

The backend should never run user-controlled or agent-controlled shell commands directly on the application host. It creates, limits, monitors, and destroys containers where executable work happens.

Frontend is out of scope for the first implementation. The initial backend should expose APIs and worker services for workspace, agent, task, and runtime management.

Runtime spaces sit above Docker runtimes. A runtime space is the workspace/team execution boundary that owns quotas, storage scope, network rules, reservations, and runtime leases. Docker containers are disposable execution resources leased by a runtime space. See [Cloud Control Plane And Runtime Spaces](cloud-control-plane-and-runtime-spaces.md).

## High-Level Architecture

```text
User/API Client
    |
    v
Backend API
    |
    +--> Postgres: durable state
    +--> Redis: queues, locks, run progress, pub/sub
    +--> Runtime Manager
             |
             v
        Docker Engine
             |
             v
        Workspace Runtime Containers
```

## Backend Responsibilities

The backend owns:

- user authentication and workspace authorization
- workspace and agent configuration
- task and run orchestration
- runtime spaces and quota reservations
- worker fleet and queue visibility
- Docker runtime provisioning
- resource limits
- workspace-to-container binding
- network policy
- mounted workspace files
- runtime health checks
- command execution requests
- artifact collection
- run event persistence
- audit logging
- runtime cleanup

The backend does not own:

- arbitrary host command execution
- direct user shell access to the host
- durable state stored only inside containers
- cross-workspace container sharing

## Runtime Types

### Ephemeral Run Runtime

Created for a single run or task step. Destroyed after completion.

Use for:

- shell commands
- local skill helper scripts
- file transforms
- short data analysis
- one-off artifact generation

### Task Runtime

Created for a multi-step task. Shared by several agent runs inside the same task. Destroyed when the task completes or is cancelled.

Use for:

- coding tasks
- multi-step analysis
- workflows where multiple agents need the same temporary filesystem

### Persistent Workspace Runtime

Created explicitly for a workspace. Kept across tasks until stopped, reset, or deleted.

Use for:

- advanced users
- warm dependencies
- repeated workspace workflows
- long-running project environments

Persistent runtimes still must be isolated containers. They are not host execution.

## Runtime Lifecycle

```text
requested -> provisioning -> ready -> attached -> idle -> stopped
                         -> unhealthy
                         -> deleting
                         -> deleted
```

State meanings:

- `requested`: runtime record exists but Docker resource is not created.
- `provisioning`: backend is creating image/container/network/volume resources.
- `ready`: runtime can accept work.
- `attached`: runtime is actively used by a run or task.
- `idle`: runtime is alive but not running work.
- `stopped`: runtime exists as metadata but container is stopped.
- `unhealthy`: health check failed or policy violation detected.
- `deleting`: cleanup is in progress.
- `deleted`: runtime is no longer usable.

## Resource Limits

Each runtime must have explicit limits.

Recommended fields:

- CPU limit
- memory limit
- memory swap limit
- disk quota
- process count limit
- maximum execution time
- idle timeout
- maximum log size
- maximum artifact size
- network mode

Example tiers:

| Tier | CPU | Memory | Disk | Use |
| --- | --- | --- | --- | --- |
| small | 1 vCPU | 1 GB | 5 GB | simple tools and scripts |
| medium | 2 vCPU | 4 GB | 20 GB | coding and analysis |
| large | 4 vCPU | 8 GB | 50 GB | advanced workspace runtime |

Tiers are not billing tiers. They are operational presets.

## Docker Safety Defaults

Containers should be created with strict defaults:

- non-root user where possible
- no privileged mode
- no host PID namespace
- no host network namespace
- no Docker socket mount
- no broad host path mount
- read-only base filesystem where practical
- dedicated workspace volume or bind mount
- resource limits always set
- network denied or allowlisted by policy
- dropped Linux capabilities where practical
- secrets injected only when explicitly allowed
- container labels for workspace, task, run, and owner

Required labels:

```text
opsmesh.workspace_id
opsmesh.runtime_id
opsmesh.task_id
opsmesh.run_id
opsmesh.owner
opsmesh.created_at
```

## Workspace Filesystem Layout

Host-managed runtime data should live under a controlled root.

Example:

```text
/var/lib/opsmesh/
  workspaces/
    {workspace_id}/
      runtimes/
        {runtime_id}/
          workspace/
          artifacts/
          logs/
          tmp/
```

Rules:

- Containers only mount their assigned runtime directory.
- A runtime directory belongs to exactly one workspace.
- Artifacts are copied into durable workspace storage after a run.
- The backend never mounts repository root, home directory, Docker socket, or application secrets into the container.

## Network Policy

Default policy should be restrictive.

Suggested modes:

- `none`: no network access
- `allowlist`: only approved domains or endpoints
- `workspace_connectors`: only installed workspace connectors
- `open`: unrestricted network, admin-only and audited

MVP should start with `none` or `allowlist`.

## Runtime Database Model

Suggested tables:

### `runtime_templates`

Defines reusable container settings.

Fields:

- `id`
- `name`
- `image`
- `description`
- `default_cpu_limit`
- `default_memory_mb`
- `default_disk_mb`
- `default_network_policy`
- `allowed_tools`
- `created_at`

### `workspace_runtimes`

Represents a runtime owned by one workspace.

Fields:

- `id`
- `workspace_id`
- `runtime_template_id`
- `name`
- `runtime_type`
- `status`
- `docker_container_id`
- `cpu_limit`
- `memory_mb`
- `disk_mb`
- `network_policy`
- `idle_timeout_seconds`
- `created_by_user_id`
- `created_at`
- `updated_at`
- `last_used_at`

### `runtime_events`

Append-only runtime event log.

Fields:

- `id`
- `workspace_id`
- `runtime_id`
- `event_type`
- `message`
- `metadata`
- `created_at`

### `runtime_commands`

Tracks command execution inside runtimes.

Fields:

- `id`
- `workspace_id`
- `runtime_id`
- `agent_run_id`
- `command`
- `status`
- `exit_code`
- `stdout_ref`
- `stderr_ref`
- `started_at`
- `finished_at`
- `created_at`

## Backend Service Modules

Suggested backend modules:

```text
app/
  api/
    workspaces.py
    agents.py
    tasks.py
    runtimes.py
    approvals.py
  core/
    config.py
    security.py
    permissions.py
  db/
    models/
    migrations/
    session.py
  runtime/
    manager.py
    docker_client.py
    policies.py
    templates.py
    files.py
    health.py
  orchestration/
    task_service.py
    run_service.py
    agent_factory.py
    event_writer.py
  workers/
    runtime_worker.py
    agent_worker.py
```

## Runtime Manager API

Internal service methods:

- `create_runtime(workspace_id, template_id, limits, policy)`
- `start_runtime(workspace_id, runtime_id)`
- `stop_runtime(workspace_id, runtime_id)`
- `delete_runtime(workspace_id, runtime_id)`
- `attach_run(workspace_id, runtime_id, run_id)`
- `exec_command(workspace_id, runtime_id, command, timeout)`
- `copy_files_in(workspace_id, runtime_id, files)`
- `collect_artifacts(workspace_id, runtime_id, run_id)`
- `health_check(workspace_id, runtime_id)`

Every method requires `workspace_id`.

## API Endpoints For Backend MVP

Initial runtime endpoints:

```text
POST   /api/workspaces/{workspace_id}/runtimes
GET    /api/workspaces/{workspace_id}/runtimes
GET    /api/workspaces/{workspace_id}/runtimes/{runtime_id}
POST   /api/workspaces/{workspace_id}/runtimes/{runtime_id}/start
POST   /api/workspaces/{workspace_id}/runtimes/{runtime_id}/stop
DELETE /api/workspaces/{workspace_id}/runtimes/{runtime_id}
GET    /api/workspaces/{workspace_id}/runtimes/{runtime_id}/events
```

Command execution should initially be internal-only, used by agent workers, not exposed as a general user shell API.

## Worker Flow

Example task run:

1. Worker receives queued `agent_run_id` and `workspace_id`.
2. Worker loads the run, task, agent profile, and workspace policy by `workspace_id`.
3. Worker determines required runtime mode.
4. Runtime Manager creates or attaches a Docker container.
5. Worker stages approved files into the runtime.
6. Agent run executes through OpenAI Agents SDK.
7. Tool calls that need command execution are routed through Runtime Manager.
8. Runtime Manager executes commands inside the container with timeout and limits.
9. Worker persists run events and artifacts.
10. Runtime is stopped, destroyed, or returned to idle based on policy.

## Failure Handling

Failures should produce durable events.

Examples:

- Docker image pull failed
- container failed to start
- resource limit exceeded
- command timed out
- artifact collection failed
- health check failed
- runtime policy violation
- runtime deleted while run was queued

Failed runs should not leave orphaned containers.

## Cleanup Jobs

Backend should run cleanup tasks:

- stop idle ephemeral runtimes
- delete expired runtime directories
- collect orphaned Docker containers with OpsMesh labels
- truncate oversized logs
- mark unhealthy runtimes
- expire abandoned provisioning records

Cleanup must be workspace-aware and audited.

## MVP Runtime Decision

For the backend-first MVP:

- implement Docker-backed runtimes
- support ephemeral run runtimes first
- add task-scoped runtimes second
- design DB fields to support persistent runtimes
- expose persistent runtime APIs only after core safety controls exist

This gives a safe path to advanced persistent environments without allowing direct host execution.
