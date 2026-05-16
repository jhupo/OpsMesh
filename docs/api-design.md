# API Design

## Purpose

This document sketches the first backend API surface. The frontend is not being built yet, but the backend should expose clean workspace-scoped APIs from the start.

## API Principles

- Workspace scope is explicit in the URL.
- Request bodies cannot override `workspace_id`.
- Long-running work is enqueued, not executed inline.
- APIs return durable resource IDs.
- Sensitive actions create audit events.
- Downloads and runtime operations require permission checks.

## Route Shape

Preferred workspace route prefix:

```text
/api/workspaces/{workspace_id}/...
```

## Authentication

Initial endpoints:

```text
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
```

Auth implementation can be chosen later. The important rule is that every workspace route resolves an authenticated user and active membership.

## Workspaces

```text
POST /api/workspaces
GET  /api/workspaces
GET  /api/workspaces/{workspace_id}
PATCH /api/workspaces/{workspace_id}
```

Membership:

```text
GET  /api/workspaces/{workspace_id}/members
POST /api/workspaces/{workspace_id}/members
PATCH /api/workspaces/{workspace_id}/members/{member_id}
DELETE /api/workspaces/{workspace_id}/members/{member_id}
```

MVP can keep membership simple if each user primarily owns their own workspace.

## Agents

```text
POST /api/workspaces/{workspace_id}/agents
GET  /api/workspaces/{workspace_id}/agents
GET  /api/workspaces/{workspace_id}/agents/{agent_id}
PATCH /api/workspaces/{workspace_id}/agents/{agent_id}
POST /api/workspaces/{workspace_id}/agents/{agent_id}/clone
POST /api/workspaces/{workspace_id}/agents/{agent_id}/disable
```

## Teams

```text
POST /api/workspaces/{workspace_id}/teams
GET  /api/workspaces/{workspace_id}/teams
GET  /api/workspaces/{workspace_id}/teams/{team_id}
PATCH /api/workspaces/{workspace_id}/teams/{team_id}
POST /api/workspaces/{workspace_id}/teams/{team_id}/members
DELETE /api/workspaces/{workspace_id}/teams/{team_id}/members/{member_id}
```

## Tasks

```text
POST /api/workspaces/{workspace_id}/tasks
GET  /api/workspaces/{workspace_id}/tasks
GET  /api/workspaces/{workspace_id}/tasks/{task_id}
GET  /api/workspaces/{workspace_id}/tasks/{task_id}/view
PATCH /api/workspaces/{workspace_id}/tasks/{task_id}
POST /api/workspaces/{workspace_id}/tasks/{task_id}/start
POST /api/workspaces/{workspace_id}/tasks/{task_id}/cancel
GET  /api/workspaces/{workspace_id}/tasks/{task_id}/steps
GET  /api/workspaces/{workspace_id}/tasks/{task_id}/artifacts
```

Task creation should create durable state and enqueue work only when requested.

Domain task extensions:

```text
GET  /api/workspaces/{workspace_id}/domain-projects
POST /api/workspaces/{workspace_id}/domain-projects
GET  /api/workspaces/{workspace_id}/domain-projects/{project_id}
GET  /api/workspaces/{workspace_id}/domain-projects/{project_id}/items
POST /api/workspaces/{workspace_id}/tasks/{task_id}/review-comments
POST /api/workspaces/{workspace_id}/tasks/{task_id}/revision-requests
```

The task view endpoint returns generic task state plus a domain-specific payload selected by `team_type` or `domain_type`.

## Runs

```text
GET /api/workspaces/{workspace_id}/runs
GET /api/workspaces/{workspace_id}/runs/{run_id}
GET /api/workspaces/{workspace_id}/runs/{run_id}/events
```

Streaming:

```text
GET /api/workspaces/{workspace_id}/runs/{run_id}/stream
```

This can use SSE initially.

## Approvals

```text
GET  /api/workspaces/{workspace_id}/approvals
GET  /api/workspaces/{workspace_id}/approvals/{approval_id}
POST /api/workspaces/{workspace_id}/approvals/{approval_id}/approve
POST /api/workspaces/{workspace_id}/approvals/{approval_id}/reject
```

Approval decisions should enqueue resume jobs when needed.

## Files And Artifacts

Workspace files:

```text
POST /api/workspaces/{workspace_id}/files
GET  /api/workspaces/{workspace_id}/files
GET  /api/workspaces/{workspace_id}/files/{file_id}
GET  /api/workspaces/{workspace_id}/files/{file_id}/download
DELETE /api/workspaces/{workspace_id}/files/{file_id}
```

Artifacts:

```text
GET /api/workspaces/{workspace_id}/artifacts
GET /api/workspaces/{workspace_id}/artifacts/{artifact_id}
GET /api/workspaces/{workspace_id}/artifacts/{artifact_id}/download
```

## Runtimes

```text
POST   /api/workspaces/{workspace_id}/runtimes
GET    /api/workspaces/{workspace_id}/runtimes
GET    /api/workspaces/{workspace_id}/runtimes/{runtime_id}
POST   /api/workspaces/{workspace_id}/runtimes/{runtime_id}/start
POST   /api/workspaces/{workspace_id}/runtimes/{runtime_id}/stop
DELETE /api/workspaces/{workspace_id}/runtimes/{runtime_id}
GET    /api/workspaces/{workspace_id}/runtimes/{runtime_id}/events
```

Command execution should remain internal-only for MVP.

## Audit

```text
GET /api/workspaces/{workspace_id}/audit-events
```

MVP can expose audit read-only to owner/admin roles.

## Worker-Only Internal APIs

These should not be public user APIs:

- execute runtime command
- collect runtime artifacts
- stage runtime files
- mark run event
- resume internal run state

They can be service methods instead of HTTP endpoints in the first backend.

## Response Conventions

Use consistent response envelopes later if desired. For MVP, prefer simple JSON with:

- `id`
- `status`
- `created_at`
- `updated_at`
- resource fields

Errors should include:

- `error.code`
- `error.message`
- `error.details`

## Open Questions

- Auth method for first build: session cookie, JWT, or API token?
- Should self-hosted worker use separate `/api/runtime-worker/...` endpoints?
- Should streaming use SSE first or WebSocket?
- Should file upload be backend-streamed first or pre-signed first?
