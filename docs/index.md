# Backend Documentation Index

## What This Project Is

ChainCloud Agent Team is a backend-first, multi-user platform where each user operates an isolated AI agent workspace. The backend owns workspace isolation, agent orchestration, task execution, worker queues, Docker runtime control, file/artifact management, approvals, and auditability.

Frontend work is intentionally out of scope for now. The current priority is a reliable backend control plane.

## Backend Scope

The backend must provide:

- user and workspace isolation
- workspace-scoped agents and teams
- task and run orchestration
- async worker execution
- OpenAI Agents SDK integration
- Docker runtime management
- MCP, tools, and skill capability control
- file upload/download and artifact collection
- approvals and audit events
- self-hosted runtime support path

## Recommended Reading Order

1. [Architecture](architecture.md)

   High-level backend system shape.

2. [Backend Service Architecture](backend-service-architecture.md)

   Split between API, orchestration, OpenAI Agents runtime layer, workers, and runtime manager.

3. [Domain Model](domain-model.md)

   Core backend entities and relationships.

4. [API Design](api-design.md)

   Workspace-scoped API surface for the backend.

5. [Domain Task Extensions](domain-task-extensions.md)

   Backend model for team-specific task state, task view payloads, review comments, and revision requests.

6. [Backend Runtime Control Plane](backend-runtime-control-plane.md)

   Docker runtime lifecycle, limits, files, logs, and cleanup.

7. [Cloud Control Plane And Runtime Spaces](cloud-control-plane-and-runtime-spaces.md)

   Managed backend control plane objects, runtime spaces, worker fleet controls, quotas, and operator APIs.

8. [Agent Runtime Contract](agent-runtime-contract.md)

   Boundary between product orchestration and OpenAI Agents SDK execution.

9. [Capabilities And Runtime](capabilities-and-runtime.md)

   Capability, skill, tool, MCP, and runtime model.

10. [Workspace Data Management](workspace-data-management.md)

   Upload, download, artifacts, runtime staging, and exports.

11. [Isolation And Security](isolation-and-security.md)

   Workspace, runtime, tool, memory, file, and worker isolation.

12. [Threat Model](threat-model.md)

    Backend security threats and mitigations.

13. [Self-Hosted Runtimes](self-hosted-runtimes.md)

    User-owned machine execution model.

14. [MVP Spec](mvp-spec.md)

    First backend MVP acceptance criteria.

15. [Roadmap](roadmap.md)

    Backend-first implementation sequence.

16. [Decisions](decisions.md)

   Stable architecture and implementation decisions.

17. [Backend Task Breakdown](backend-task-breakdown.md)

   Actionable engineering task list from backend framework setup to complete backend capabilities.

18. [Backend Deployment](backend-deployment.md)

   Production-oriented backend process, Docker, Compose, and environment guidance.

19. [Backend Completion Plan](backend-completion-plan.md)

   Detailed list of backend areas that are incomplete or only implemented as a basic foundation, with implementation tasks and acceptance criteria.

## Non-Negotiable Backend Rules

- Workspace isolation is mandatory.
- Postgres is the source of truth.
- Redis is for queueing, locks, pub/sub, and short-lived cache only.
- API requests do not execute long-running agent work inline.
- Workers execute asynchronous tasks.
- User-controlled code never runs directly on the application host.
- Dangerous execution happens in Docker, hosted tools, or self-hosted isolated runtimes.
- MCP tools, skills, files, and runtimes are workspace-scoped.
- Artifacts and downloads require authorization.
- Product run events in Postgres are the durable user-visible truth.
