# Self-Hosted Runtimes

## Goal

Some users will not want sensitive data stored or processed in the cloud. Others may want to use their own GPUs, larger machines, local files, private networks, or existing development environments.

OpsMesh should support self-hosted runtimes: user-owned machines that run a controlled worker/runtime process and execute approved workspace tasks locally.

The platform remains the control plane. The user's machine becomes an execution plane.

## Use Cases

- User wants files to stay on their own machine.
- User wants to use a powerful local workstation or GPU server.
- Company data is inside a private network.
- User wants agents to operate on local repositories.
- User wants to connect to internal services without exposing them publicly.
- User wants lower cloud compute cost.

## Product Model

A self-hosted runtime is a workspace-owned runtime connected to a user's machine.

It should appear in the product next to cloud Docker runtimes:

- Cloud Runtime
- Persistent Workspace Runtime
- Self-Hosted Runtime

Each self-hosted runtime belongs to exactly one workspace unless an explicit multi-workspace sharing feature is added later.

## Recommended Architecture

Use an outbound connector model.

The user runs a local worker on their own machine. The worker connects outbound to the platform and pulls assigned jobs. The platform should not SSH into the user's machine by default.

```text
Platform Backend
    |
    | HTTPS/WebSocket outbound connection initiated by user machine
    v
Self-Hosted Worker
    |
    +--> Local Docker or sandbox
    +--> Local filesystem allowlist
    +--> Local tools
    +--> Local MCP servers
    +--> Local network resources
```

Benefits:

- no inbound firewall opening required
- platform does not need SSH credentials
- user can stop the worker at any time
- local data can stay local
- execution can use local hardware

## Local Worker Responsibilities

The self-hosted worker should:

- authenticate to the platform
- register machine capabilities
- heartbeat health status
- poll or subscribe for assigned jobs
- enforce workspace binding
- enforce local resource limits
- execute tasks in a local Docker container or sandbox
- stage allowed local files
- call local tools or MCP servers
- stream progress events
- upload selected artifacts if policy allows
- keep sensitive data local when configured
- support cancellation

The worker should not:

- execute jobs from unbound workspaces
- expose the entire filesystem to agents
- run commands directly on the host unless explicitly configured and warned
- leak local secrets to the platform
- accept arbitrary inbound commands outside the job protocol

## Data Residency Modes

### Metadata Cloud, Data Local

The platform stores:

- task metadata
- run status
- event summaries
- approvals
- artifact references

The user machine stores:

- original files
- raw tool outputs
- local runtime working files
- sensitive artifacts

Downloads happen from the user's machine when it is online, or through a user-approved upload/export.

### Hybrid

The platform stores selected files and artifacts, while sensitive inputs remain local.

Use for:

- final reports
- non-sensitive generated assets
- logs redacted by the worker

### Cloud Sync

The self-hosted worker uploads selected files/artifacts back to the platform.

Use for:

- users that want normal cloud sync and access from multiple devices
- non-sensitive workflows
- backup and sharing

## Registration Flow

1. User creates a self-hosted runtime in a workspace.
2. Platform generates a one-time enrollment token.
3. User installs and starts the local worker.
4. Worker exchanges enrollment token for a scoped runtime credential.
5. Worker registers machine capabilities.
6. Platform marks runtime `online`.

Runtime credential should be scoped to:

- workspace id
- runtime id
- allowed job types
- expiration/rotation policy

## Capability Advertisement

The worker should report:

- OS
- architecture
- CPU count
- memory
- available disk
- Docker availability
- GPU availability
- supported tools
- installed skills
- local MCP servers
- allowed filesystem roots
- network mode

The platform uses these capabilities when assigning tasks.

## File Access

Self-hosted runtimes need explicit local file scopes.

Example:

```yaml
allowed_paths:
  - /Users/alice/projects/client-a
  - /Users/alice/Documents/research
```

Rules:

- Agents can only access allowlisted paths.
- File access should go through worker-controlled tools.
- The worker should block path traversal.
- The platform should store references, not raw local paths, when data-local mode is enabled.
- Users should be able to revoke paths.

## Execution Model

Even on a user-owned machine, the safest default is still local Docker or sandbox execution.

Recommended:

- worker process runs as a normal user
- worker creates local Docker containers for agent tasks
- containers mount only approved local paths
- resource limits are applied
- network policy is configurable
- no Docker socket inside task containers

Host execution can exist as an advanced local-only mode, but it must be clearly marked dangerous.

## Job Assignment

Jobs should include:

- `workspace_id`
- `runtime_id`
- `agent_run_id`
- required capabilities
- file references
- tool permissions
- approval policy
- timeout

The worker must reject a job if:

- workspace id does not match
- runtime id does not match
- required capabilities are unavailable
- requested file scope is not allowed
- job signature or token is invalid
- policy version is outdated

## Communication Protocol

MVP can use HTTPS polling. Later versions can use WebSocket.

Worker to platform:

- register
- heartbeat
- poll jobs
- claim job
- stream events
- request approval
- upload artifact metadata
- complete job
- fail job

Platform to worker:

- job assignment
- cancellation
- policy update
- credential rotation

## Artifact Handling

Artifact policy options:

- keep local only
- upload selected artifacts
- upload redacted artifacts
- upload all artifacts

If artifacts stay local, the platform stores:

- artifact id
- runtime id
- local reference
- metadata
- availability status

Users can download local-only artifacts only when the self-hosted runtime is online.

## Security Requirements

- enrollment token is one-time use
- runtime credential is scoped and revocable
- worker runs with least privilege
- local paths are allowlisted
- jobs are signed or authenticated
- worker validates workspace and runtime ids
- no platform-initiated arbitrary shell
- no inbound open port required by default
- all command execution is logged locally
- sensitive logs can be redacted before upload

## Database Additions

Extend `workspace_runtimes`:

- `runtime_provider`: `cloud_docker`, `self_hosted`
- `connection_status`: `online`, `offline`, `degraded`
- `last_heartbeat_at`
- `capabilities`
- `data_residency_mode`
- `artifact_policy`

Add `runtime_credentials`:

- `id`
- `workspace_id`
- `runtime_id`
- `credential_hash`
- `status`
- `expires_at`
- `created_at`
- `revoked_at`

Add `self_hosted_runtime_paths`:

- `id`
- `workspace_id`
- `runtime_id`
- `path_label`
- `path_ref`
- `access_mode`
- `created_at`

## MVP Scope

Not required for the first backend build, but architecture should leave room for it.

Recommended first self-hosted MVP:

- worker registration
- heartbeat
- HTTPS polling
- run simple agent task
- local Docker execution
- local file allowlist
- progress event upload
- selected artifact upload

Later:

- WebSocket streaming
- local-only artifact browsing
- local MCP server registry
- GPU capability scheduling
- offline queueing
- signed job bundles
- worker auto-update

## Product Positioning

Self-hosted runtime should be positioned as:

- more private
- more powerful
- more configurable
- user's responsibility for local machine security

Cloud runtime should remain the easiest default. Self-hosted runtime is for users who need stronger data control or local compute.
