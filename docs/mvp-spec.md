# MVP Spec

## Purpose

This document defines the first useful MVP. It should be small enough to build, but complete enough to prove the product direction.

## MVP Thesis

A user can create an isolated workspace, install a small AI workforce, create a task, let agents execute it through workers and Docker runtimes, approve sensitive actions, and download the final artifact.

## Included

### Workspace

- user can create a workspace
- user can view their workspaces
- user can switch workspace context through API
- every workspace resource is scoped by `workspace_id`

### Agents

- user can create an agent profile
- system provides default curated agents
- agent profile includes instructions, model, capabilities, tools, runtime policy, and approval policy

### Teams

- user can create an agent team
- user can add agents to a team
- default team template exists
- teams can be shown as part of a simple organization structure

### Tasks

- user can create a task
- user can start a task
- task creates an agent run
- task status is persisted
- task has final output

### Worker

- API enqueues a run job
- worker claims run job
- worker locks run
- worker updates run status
- worker writes run events

### OpenAI Agents SDK

- worker can run one SDK agent
- worker can persist final output
- worker can capture basic events

### Docker Runtime

- worker can request runtime
- backend creates Docker container with limits
- command execution happens inside Docker
- stdout/stderr are captured
- container is cleaned up

### Files And Artifacts

- user can upload file
- worker can stage approved file into runtime
- runtime can produce artifact
- artifact is stored
- user can download artifact

### Approvals

- risky action can create approval
- run can pause waiting for approval
- user can approve or reject
- worker can resume or fail run based on decision

### Audit

- sensitive actions write audit events
- file download writes access event
- runtime creation writes event

## Excluded

- frontend implementation
- billing
- complex workflow designer
- enterprise SSO
- self-hosted runtime execution
- full MCP registry
- advanced memory
- multi-user human collaboration features

## Default Demo Scenario

```text
User: Research this topic from the uploaded file and generate a short report.
```

Flow:

1. User creates workspace.
2. User uploads a source file.
3. User installs default Research Team.
4. User creates task.
5. Manager Agent plans the work.
6. Research Agent reads staged file.
7. Writer Agent creates report.
8. Runtime Manager collects report artifact.
9. User downloads artifact.

## Backend Acceptance Criteria

- all workspace-owned queries use `workspace_id`
- API does not execute long-running agent work inline
- Redis queue can enqueue and consume run job
- worker can run to completion
- Docker container has memory and CPU limits
- no command executes on application host
- artifact download checks authorization
- run events are append-only
- audit events are written for sensitive actions

## Product Acceptance Criteria

- user can understand the system as an AI workforce
- task status is inspectable
- final output is useful
- failures are visible
- approvals are understandable
- artifacts are downloadable

## Technical Acceptance Criteria

- Postgres migrations exist
- Redis queue works
- worker can be run separately from API
- Docker runtime manager works locally
- tests cover workspace isolation
- tests cover file authorization
- tests cover worker job workspace mismatch rejection

## First Non-MVP Follow-Ups

1. MCP registry.
2. Skill registry.
3. Self-hosted runtime worker prototype.
4. Frontend implementation.
