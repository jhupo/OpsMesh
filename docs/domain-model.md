# Domain Model

## Purpose

This document defines the core product entities and their relationships. It is not a final database schema, but it should guide migrations, APIs, workers, and runtime services.

## Entity Map

```text
User
  -> WorkspaceMember
      -> Workspace
          -> AgentProfile
          -> AgentTeam
          -> Task
              -> TaskStep
              -> AgentRun
                  -> RunEvent
                  -> Approval
                  -> Artifact
          -> WorkspaceFile
          -> MemoryEntry
          -> WorkspaceRuntime
          -> AuditEvent
```

## Users And Workspaces

### User

Represents a human account.

Key fields:

- `id`
- `email`
- `display_name`
- `status`
- `created_at`
- `updated_at`

### Workspace

Tenant and isolation boundary for one user's or one account's AI workforce.

Key fields:

- `id`
- `owner_user_id`
- `name`
- `slug`
- `status`
- `settings`
- `created_at`
- `updated_at`

### WorkspaceMember

Connects a user to a workspace and role.

Key fields:

- `id`
- `workspace_id`
- `user_id`
- `role`
- `status`
- `created_at`
- `updated_at`

Initial roles:

- `owner`
- `admin`
- `operator`
- `viewer`

## Agents And Teams

### AgentProfile

Workspace-owned digital employee definition.

Key fields:

- `id`
- `workspace_id`
- `name`
- `role`
- `description`
- `instructions`
- `model`
- `model_settings`
- `capabilities`
- `skills`
- `tool_policy`
- `runtime_policy`
- `memory_policy`
- `approval_policy`
- `version`
- `status`
- `created_at`
- `updated_at`

### AgentTeam

Workspace-owned group of agents coordinated for a type of work.

Key fields:

- `id`
- `workspace_id`
- `name`
- `team_type`
- `description`
- `manager_agent_profile_id`
- `coordination_rules`
- `default_task_policy`
- `status`
- `created_at`
- `updated_at`

### AgentTeamMember

Connects agents to a team.

Key fields:

- `id`
- `workspace_id`
- `agent_team_id`
- `agent_profile_id`
- `team_role`
- `is_required`
- `created_at`

## Tasks And Runs

### Task

User-created or agent-created work order.

Key fields:

- `id`
- `workspace_id`
- `created_by_user_id`
- `created_by_agent_run_id`
- `agent_team_id`
- `domain_type`
- `title`
- `description`
- `status`
- `priority`
- `input`
- `generic_state`
- `domain_state`
- `final_output`
- `created_at`
- `updated_at`
- `completed_at`

Statuses:

- `draft`
- `queued`
- `planning`
- `running`
- `waiting_approval`
- `blocked`
- `completed`
- `failed`
- `cancelled`

### TaskStep

Planned subtask inside a task.

Key fields:

- `id`
- `workspace_id`
- `task_id`
- `assigned_agent_profile_id`
- `title`
- `description`
- `status`
- `order_index`
- `dependencies`
- `result_summary`
- `created_at`
- `updated_at`

### AgentRun

One concrete execution of an agent.

Key fields:

- `id`
- `workspace_id`
- `task_id`
- `task_step_id`
- `agent_profile_id`
- `runtime_id`
- `status`
- `input`
- `output`
- `error`
- `model`
- `started_at`
- `completed_at`
- `created_at`
- `updated_at`

Statuses:

- `queued`
- `running`
- `waiting_approval`
- `completed`
- `failed`
- `cancelled`

### RunEvent

Append-only event stream for a run.

Key fields:

- `id`
- `workspace_id`
- `agent_run_id`
- `event_type`
- `sequence`
- `message`
- `metadata`
- `created_at`

Event types:

- `run.started`
- `model.called`
- `tool.called`
- `tool.completed`
- `handoff.started`
- `approval.requested`
- `approval.resolved`
- `artifact.created`
- `run.completed`
- `run.failed`

## Approvals

### Approval

Human decision gate.

Key fields:

- `id`
- `workspace_id`
- `task_id`
- `agent_run_id`
- `requested_by_agent_profile_id`
- `approval_type`
- `risk_level`
- `payload`
- `status`
- `decided_by_user_id`
- `decision_reason`
- `created_at`
- `decided_at`

Statuses:

- `pending`
- `approved`
- `rejected`
- `expired`

## Data And Memory

### WorkspaceFile

User-uploaded or imported input file.

Key fields:

- `id`
- `workspace_id`
- `uploaded_by_user_id`
- `filename`
- `content_type`
- `size_bytes`
- `checksum_sha256`
- `storage_key`
- `status`
- `metadata`
- `created_at`
- `updated_at`

### Artifact

Generated deliverable.

Key fields:

- `id`
- `workspace_id`
- `task_id`
- `agent_run_id`
- `artifact_type`
- `filename`
- `content_type`
- `size_bytes`
- `checksum_sha256`
- `storage_key`
- `metadata`
- `created_at`

### MemoryEntry

Durable context for future tasks.

Key fields:

- `id`
- `workspace_id`
- `agent_profile_id`
- `source_task_id`
- `source_agent_run_id`
- `memory_type`
- `content`
- `metadata`
- `created_at`
- `updated_at`

## Runtime

### RuntimeTemplate

Reusable runtime configuration.

Key fields:

- `id`
- `name`
- `image`
- `default_limits`
- `default_network_policy`
- `status`
- `created_at`

### WorkspaceRuntime

Cloud Docker or self-hosted runtime owned by one workspace.

Key fields:

- `id`
- `workspace_id`
- `runtime_template_id`
- `runtime_provider`
- `runtime_type`
- `name`
- `status`
- `connection_status`
- `docker_container_id`
- `limits`
- `network_policy`
- `capabilities`
- `last_heartbeat_at`
- `created_at`
- `updated_at`

## Audit

### AuditEvent

Append-only security and governance record.

Key fields:

- `id`
- `workspace_id`
- `actor_type`
- `actor_id`
- `user_id`
- `agent_run_id`
- `action`
- `target_type`
- `target_id`
- `metadata`
- `created_at`

## Cross-Cutting Rules

- Workspace-owned tables include `workspace_id`.
- Worker jobs include `workspace_id`.
- Runtime resources include `workspace_id`.
- Queries use resource ID plus `workspace_id`.
- Deletion should be soft-delete first.
- Run events and audit events are append-only.

## Domain Extensions

Different team types may require domain-specific task state. The core task/run model remains shared, but the backend should support domain projects, domain items, review comments, and revision requests.

### DomainProject

Workspace-scoped domain work container.

Key fields:

- `id`
- `workspace_id`
- `domain_type`
- `name`
- `description`
- `status`
- `metadata`
- `created_at`
- `updated_at`

### DomainItem

Domain object produced or used by tasks.

Key fields:

- `id`
- `workspace_id`
- `domain_project_id`
- `task_id`
- `item_type`
- `title`
- `status`
- `content`
- `metadata`
- `created_at`
- `updated_at`

### ReviewComment

Structured comment on a task, artifact, or domain item.

Key fields:

- `id`
- `workspace_id`
- `task_id`
- `domain_project_id`
- `domain_item_id`
- `artifact_id`
- `author_user_id`
- `author_agent_run_id`
- `target_type`
- `target_id`
- `comment`
- `status`
- `created_at`
- `resolved_at`

### RevisionRequest

Structured request to revise work.

Key fields:

- `id`
- `workspace_id`
- `task_id`
- `domain_project_id`
- `domain_item_id`
- `artifact_id`
- `requested_by_user_id`
- `assigned_agent_profile_id`
- `instruction`
- `status`
- `created_at`
- `completed_at`
