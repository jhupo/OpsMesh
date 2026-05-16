# Roadmap

## Purpose

This roadmap converts the planning docs into an implementation sequence. It is intentionally backend-first because runtime isolation, workspace security, workers, and data persistence are the hardest foundations.

## Phase 0: Planning Foundation

Status: in progress.

Deliverables:

- backend scope summary
- architecture overview
- backend service architecture
- runtime control plane plan
- workspace isolation plan
- data management plan
- capability/skill/tool model
- self-hosted runtime plan
- decisions log

Exit criteria:

- core product shape is clear
- security boundaries are clear
- MVP scope is clear
- backend implementation sequence is clear

## Phase 1: Backend Foundation

Goal:

Create the backend shell and durable workspace model.

Deliverables:

- Python backend project structure
- configuration management
- Postgres connection
- Redis connection
- migrations
- user model
- workspace model
- workspace membership model
- role checks
- audit event writer
- basic health checks

Exit criteria:

- can create users and workspaces
- can enforce workspace membership
- can write audit events
- can connect to Postgres and Redis

## Phase 2: Agent And Task Core

Goal:

Represent digital employees, teams, and tasks in the database.

Deliverables:

- agent profile model
- agent team model
- task model
- task step model
- agent run model
- run event model
- basic task status machine
- default curated team templates

Exit criteria:

- can create an agent profile
- can create a team
- can create a task
- can create a run record
- can append run events

## Phase 3: Worker And Queue

Goal:

Move execution out of the API layer.

Deliverables:

- Redis queue abstraction
- worker process
- job payload schema
- run locks
- retry policy
- run progress events
- failure event persistence

Exit criteria:

- API can enqueue an agent run
- worker can claim and lock the run
- worker can update run status
- worker can recover from simple failures

## Phase 4: OpenAI Agents Runtime Integration

Goal:

Run the first real agent through OpenAI Agents SDK.

Deliverables:

- agent factory from `agent_profiles`
- Runner wrapper
- model settings mapping
- basic function tool mapping
- run event capture
- final output persistence
- basic manager/specialist example

Exit criteria:

- worker can run one agent
- final output is stored
- run events are visible in Postgres
- no long-running work executes in API request

## Phase 5: Docker Runtime Manager

Goal:

Execute risky work inside managed Docker runtimes.

Deliverables:

- runtime template model
- workspace runtime model
- runtime event model
- Docker client wrapper
- resource limits
- network policy
- workspace runtime directory
- command execution inside container
- stdout/stderr capture
- cleanup job

Exit criteria:

- backend can create a Docker runtime
- worker can execute a command inside Docker
- command output is persisted
- container is scoped to workspace
- no agent command runs on host

## Phase 6: Workspace Files And Artifacts

Goal:

Let users upload data and download results.

Deliverables:

- workspace file model
- artifact model
- file access event model
- local storage adapter
- upload endpoint
- download endpoint
- runtime file staging
- artifact collection from Docker

Exit criteria:

- user can upload a workspace file
- worker can stage approved file into runtime
- runtime can create artifact
- user can download authorized artifact

## Phase 7: Approvals And Governance

Goal:

Pause sensitive actions and require human decisions.

Deliverables:

- approval model
- approval API
- approval-required tool policy
- pause run on approval
- resume run after approval
- approval audit events

Exit criteria:

- write-capable or risky action can pause execution
- user can approve or reject
- worker can resume or fail the run

## Phase 8: Capabilities, Skills, And MCP

Goal:

Move from raw tools to user-facing capabilities and skills.

Deliverables:

- capability catalog
- skill registry
- workspace skill install
- tool group catalog
- MCP registry
- MCP tool permissioning
- agent profile capability mapping

Exit criteria:

- user can assign capabilities to agent profiles
- agent sees only allowed tools
- MCP tools are workspace-scoped and logged
- skills can affect agent instructions and runtime files

## Phase 9: Self-Hosted Runtime

Goal:

Let user-owned machines execute workspace tasks.

Deliverables:

- runtime enrollment token
- self-hosted worker prototype
- heartbeat
- polling job protocol
- local Docker execution
- local file allowlist
- selected artifact upload

Exit criteria:

- user can register a self-hosted runtime
- platform can assign a job
- worker can execute locally and report progress
- data-local mode works for basic tasks

## MVP Cut

The first demo-quality MVP can stop at Phase 7:

- workspace
- agents
- teams
- tasks
- worker
- OpenAI Agents SDK run
- Docker runtime
- files/artifacts
- approvals

Phase 8 adds the capability layer. Phase 9 expands trust and power for advanced users.
