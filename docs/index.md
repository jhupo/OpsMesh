# Backend Documentation Index

## What This Project Is

OpsMesh is a backend-first, open-source enterprise agent framework for running AI agent teams with
control, isolation, and auditability. The backend owns workspace isolation, agent orchestration,
task execution, worker queues, Docker runtime control, file/artifact management, approvals,
marketplaces, and audit evidence.

Frontend work is intentionally out of scope for now. The current priority is a reliable backend control plane.

## Backend Scope

The backend must provide:

- user and workspace isolation
- workspace-scoped agents and teams
- task and run orchestration
- async worker execution
- OpenAI Agents SDK and Claude Agent SDK integration
- Docker runtime management
- MCP, tools, and skill capability control
- file upload/download and artifact collection
- approvals and audit events
- self-hosted runtime support path

## Recommended Reading Order

1. [Architecture](architecture.md)

   High-level backend system shape.

2. [Agent Runtime Architecture](agent-runtime-architecture.md)

   Current provider-neutral run boundaries, execution modes, project I/O, and evidence flow.

3. [Backend Service Architecture](backend-service-architecture.md)

   Split between API, orchestration, provider SDK runtime, workers, and runtime environment.

4. [Domain Model](domain-model.md)

   Core backend entities and relationships.

5. [API Design](api-design.md)

   Workspace-scoped API surface for the backend.

6. [Domain Task Extensions](domain-task-extensions.md)

   Backend model for team-specific task state, task view payloads, review comments, and revision requests.

7. [Backend Runtime Control Plane](backend-runtime-control-plane.md)

   Docker runtime lifecycle, limits, files, logs, and cleanup.

8. [Cloud Control Plane And Runtime Spaces](cloud-control-plane-and-runtime-spaces.md)

   Managed backend control plane objects, runtime spaces, worker fleet controls, quotas, and operator APIs.

9. [Agent Runtime Contract](agent-runtime-contract.md)

   Boundary between product orchestration and provider SDK execution.

10. [Capabilities And Runtime](capabilities-and-runtime.md)

   Capability, skill, tool, MCP, and runtime model.

11. [Workspace Data Management](workspace-data-management.md)

   Upload, download, artifacts, runtime staging, and exports.

12. [Isolation And Security](isolation-and-security.md)

   Workspace, runtime, tool, memory, file, and worker isolation.

13. [Threat Model](threat-model.md)

    Backend security threats and mitigations.

14. [Self-Hosted Runtimes](self-hosted-runtimes.md)

    User-owned machine execution model.

15. [MVP Spec](mvp-spec.md)

    First backend MVP acceptance criteria.

16. [Roadmap](roadmap.md)

    Backend-first implementation sequence.

17. [Decisions](decisions.md)

   Stable architecture and implementation decisions.

18. [Backend Task Breakdown](backend-task-breakdown.md)

   Actionable engineering task list from backend framework setup to complete backend capabilities.

19. [Backend Deployment](backend-deployment.md)

   Production VPS/systemd deployment, release bundle updates, local Compose guidance, and environment setup.

20. [Backend Completion Plan](backend-completion-plan.md)

   Detailed list of backend areas that are incomplete or only implemented as a basic foundation, with implementation tasks and acceptance criteria.

21. [Backend Next Task Table](backend-next-task-table.md)

   Active execution checklist for the next backend phase.

22. [Open-Source SDK Strategy](open-source-sdk-strategy.md)

   Candidate SDKs, adoption order, evaluation criteria, and the boundaries OpsMesh continues to own.

23. [Observability, Audit, and Cost Operations](observability-audit-and-costs.md)

   Production signal flow, deployment, verification, audit integrity, and model cost operations.

24. [Agent Runtime Completion Plan](agent-runtime-completion-plan.md)

   Active, acceptance-driven plan for SDK adaptation, approvals, workspace I/O, context and memory,
   orchestration, and per-run isolation.

25. [Architecture Consolidation Plan](architecture-consolidation-plan.md)

   Repository-wide ownership map, dependency direction, runtime modes, migration sequence, and
   acceptance criteria for keeping the backend modular without compatibility shims.

26. [Code Organization Audit](code-organization-audit.md)

   Evidence log for completed file moves, ownership decisions, focused validation, and remaining
   boundaries that are intentionally retained for independent lifecycles or adapters.

27. [Platform Closure and Productionization Plan](platform-productionization-plan.md)

   The frozen next-phase plan for closing durable recovery, runtime isolation, SDK lifecycle,
   capability/MCP operations, data recovery, observability, release supply chain, managed delivery,
   and reliability drills. The Plugin Center is explicitly deferred to a later phase.

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
