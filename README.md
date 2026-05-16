# ChainCloud Agent Team

ChainCloud Agent Team is a backend-first, multi-user platform for operating isolated AI agent workspaces. The backend uses the OpenAI Agents SDK as the agent execution foundation and adds the control plane needed for real work: workspaces, agents, task orchestration, workers, Docker runtimes, approvals, files, artifacts, security, and auditability.

Frontend work is intentionally not the current focus. The project is currently planning and building the backend foundation.

## Core Stack

- Python backend with OpenAI Agents SDK
- Postgres as the source of truth
- Redis for queues, locks, pub/sub, and short-lived cache
- Worker processes for async agent execution
- Docker runtime manager for isolated executable work
- Workspace-scoped permissions, files, tools, MCP, skills, and runtimes

## Backend Goals

- Enforce strict user and workspace isolation.
- Manage workspace-scoped agents, teams, tasks, runs, files, artifacts, and approvals.
- Run long-running agent work through workers, not API requests.
- Execute dangerous work only inside Docker or self-hosted isolated runtimes.
- Integrate OpenAI Agents SDK behind a product-owned runtime contract.
- Keep all durable state and audit records in Postgres.

## Documentation

- [Backend Documentation Index](docs/index.md)
- [Backend Task Breakdown](docs/backend-task-breakdown.md)
- [Architecture](docs/architecture.md)
- [Backend Service Architecture](docs/backend-service-architecture.md)
- [Domain Model](docs/domain-model.md)
- [API Design](docs/api-design.md)
- [Domain Task Extensions](docs/domain-task-extensions.md)
- [Backend Runtime Control Plane](docs/backend-runtime-control-plane.md)
- [Agent Runtime Contract](docs/agent-runtime-contract.md)
- [Capabilities And Runtime](docs/capabilities-and-runtime.md)
- [Workspace Data Management](docs/workspace-data-management.md)
- [Isolation And Security](docs/isolation-and-security.md)
- [Threat Model](docs/threat-model.md)
- [Self-Hosted Runtimes](docs/self-hosted-runtimes.md)
- [MVP Spec](docs/mvp-spec.md)
- [Roadmap](docs/roadmap.md)
- [Decisions](docs/decisions.md)
