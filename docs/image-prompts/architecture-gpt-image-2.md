# GPT Image 2 Prompt: Backend Architecture Diagram

Use case: infographic-diagram
Asset type: README architecture image
Primary request: Create a polished product architecture diagram for "ChainCloud Agent Team", a backend platform where one user acts as a boss, creates an AI company workspace, hires public AI agents from a talent marketplace, organizes them into teams and departments, and executes work through OpenAI Agents, Docker runtimes, and user-owned self-hosted machines.
Style/medium: clean enterprise SaaS architecture infographic, crisp vector-like layout, white and soft blue background, readable labels, no decorative characters.
Composition/framing: wide 16:10 landscape diagram with clear left-to-right flow and grouped system layers.
Required labels:
- Boss User
- Workspace = AI Company
- Teams and Departments
- HR Agent
- Manager Agent
- Specialist Agents
- Talent Market
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
- Boss User creates a workspace and teams.
- Talent Market publishes public agent listings.
- Hiring copies a public agent into the boss workspace without copying source data or credentials.
- Product API sends long-running work to workers and queues.
- Workers invoke OpenAI Agents Runtime.
- Agents can use skills, MCP tools, files, and artifacts.
- Risky work routes to Cloud Docker Runtime or Self-hosted Machines.
- Postgres stores durable business state; Redis handles queues, locks, rate limits, and cache.
- Approvals, audit, and security events guard sensitive actions.
Constraints: professional, readable, no tiny illegible text, no random logos, no stock-photo people, no decorative blobs, no watermark.
