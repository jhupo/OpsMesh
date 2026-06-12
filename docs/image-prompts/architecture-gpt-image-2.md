# GPT Image 2 Prompt: Backend Architecture Diagram

Use case: infographic-diagram
Asset type: README architecture image
Primary request: Create a polished product architecture diagram for "OpsMesh", a backend platform for running AI agent teams with control, isolation, and auditability. Users create isolated workspaces, publish reviewed public agents, skills, MCP servers, and plugins to markets, install public resources as isolated copies, organize teams and departments, and execute work through OpenAI Agents, Docker runtimes, and user-owned self-hosted machines.
Style/medium: clean enterprise SaaS architecture infographic, crisp vector-like layout, white and soft blue background, readable labels, no decorative characters.
Composition/framing: wide 16:10 landscape diagram with clear left-to-right flow and grouped system layers.
Required labels:
- Workspace User
- Isolated Workspace
- Teams and Departments
- HR Agent
- Manager Agent
- Specialist Agents
- Markets
- Product API
- Workers and Redis Queue
- OpenAI Agents Runtime
- Skills, MCP, Tools
- Cloud Docker Runtime
- Self-hosted Machines
- Postgres Source of Truth
- Redis Queues and Locks
- Files, Artifacts, Encrypted Credentials
- Approvals, Audit, Security Events
Key visual relationships:
- Workspace users create workspaces and teams.
- Markets publish reviewed public agents, skills, MCP servers, and plugins.
- Installing copies a public resource into the workspace without copying source data or credentials.
- Product API sends long-running work to workers and queues.
- Workers invoke OpenAI Agents Runtime.
- Agents can use skills, MCP tools, files, and artifacts.
- Risky work routes to Cloud Docker Runtime or Self-hosted Machines.
- Postgres stores durable business state; Redis handles queues, locks, rate limits, and cache.
- Approvals, audit, and security events guard sensitive actions.
Constraints: professional, readable, no tiny illegible text, no random logos, no stock-photo people, no decorative blobs, no watermark.
