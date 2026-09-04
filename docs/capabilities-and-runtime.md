# Capabilities And Runtime

## Goal

Agents need capabilities: drawing, writing, coding, browsing, calling APIs, reading files, using MCP servers, and running skills. The product should let users configure what an agent can do without forcing them to understand every runtime detail.

The key design principle is to separate capability configuration from execution environment.

Users define agent abilities. The system chooses the safest and most appropriate runtime for each task.

Security rule: user-controlled or agent-controlled execution must never run directly on the application server host. Shell commands, scripts, untrusted MCP tools, code execution, file mutation, and skill helper programs must run inside an isolated runtime such as Docker, a microVM, a jailed sandbox, or a trusted hosted tool environment.

## Core Concepts

### Capability

A capability is a product-level ability assigned to an agent.

Examples:

- generate images
- edit images
- search workspace files
- browse the web
- run Python analysis
- edit code
- prepare documents
- use a CRM connector
- call an internal API
- run a design review skill

Capabilities are what users understand.

### Skill

A skill is a packaged instruction set, playbook, or tool bundle that teaches an agent how to perform a specific class of work.

Examples:

- chart designer
- report writer
- CSV analyst
- interface reviewer
- competitor researcher
- code reviewer
- image prompt engineer

Skills may include:

- instructions
- examples
- schemas
- small helper scripts
- allowed tools
- required files
- runtime requirements

Skills should be versioned and workspace-installable.

### Tool

A tool is an executable capability exposed to the model.

Examples:

- image generation tool
- file search tool
- shell tool
- apply patch tool
- code interpreter
- workspace memory search
- document renderer
- email sender

Tools may be native OpenAI hosted tools, local runtime tools, MCP tools, or product-defined tools.

### MCP Server

MCP is a way to expose external tools and context to agents through a standard protocol.

MCP servers should be treated as connector providers. A workspace can install and configure MCP servers, and agents can be granted access to specific MCP tools.

### Runtime

A runtime is where tools execute.

Examples:

- application backend process
- OpenAI hosted tool environment
- per-run Docker sandbox
- persistent workspace sandbox
- external MCP server
- remote sandbox provider such as E2B, Daytona, Modal, or Runloop

Runtime is a system decision, not something most users should manage directly.

## Recommendation: Persistent Isolation, Not Bare Server Execution

If the product needs advanced persistent environments, use persistent isolated runtimes. Do not run agent workloads directly on the server that hosts the API, workers, database clients, or secrets.

Creating a Docker container immediately when a user creates a team is still not the best default for every team, but any team that is granted code execution, shell execution, local skill helpers, filesystem mutation, or untrusted MCP execution must be backed by an isolated runtime before those capabilities can run.

Reasons:

- Many teams may never run a task that needs Docker.
- Idle always-on containers waste resources.
- Long-lived containers increase isolation and cleanup risk.
- Team configuration changes should not require container lifecycle management.
- A team is a product object, while a container is an execution resource.
- Multiple tasks in the same team may require different environments.

Recommended default:

- Create teams and agents as database configuration.
- Create or attach an isolated runtime when a task/run actually needs executable capabilities.
- Prefer short-lived per-run sandboxes for general safety and predictability.
- Offer persistent workspace runtimes only as an explicit advanced mode.
- Persist outputs, memory, artifacts, and optional snapshots outside the container.
- Reuse or resume sandboxes only when the task explicitly requires continuity.
- Never fall back to executing user or agent commands on the host server.

## Runtime Modes

### Mode 1: No Sandbox

Use for normal reasoning, planning, simple tool calls, MCP calls, and hosted OpenAI tools.

Best for:

- writing
- planning
- summarization
- web search
- file search
- lightweight API calls
- image generation through hosted tools

### Mode 2: Per-Run Sandbox

Create an isolated Docker or provider sandbox for one agent run or task step.

Best for:

- running shell commands
- executing scripts
- transforming files
- rendering documents
- testing code
- generating artifacts
- using local skill helper scripts

This should be the default for risky or filesystem-heavy work.

### Mode 3: Task-Scoped Sandbox

Keep one sandbox alive for the duration of a task.

Best for:

- multi-step coding tasks
- iterative data analysis
- workflows where several specialist agents need the same temporary filesystem

The sandbox still ends when the task completes, unless a snapshot is saved.

### Mode 4: Workspace Sandbox Snapshot

Persist a prepared environment as a snapshot, but do not keep it running.

Best for:

- large dependencies
- repeated workspace workflows
- preloaded repositories
- reusable analysis environments

Snapshots are safer and cheaper than always-on containers.

### Mode 5: Persistent Workspace Runtime

Keep a long-lived workspace runtime.

Best for:

- advanced enterprise workflows
- expensive environment warmup
- continuously scheduled automation
- stateful development environments

This should not be MVP default. It needs quotas, cleanup, monitoring, and stronger security controls.

If enabled, this runtime must still be isolated from the application host. It should run in a container, microVM, dedicated VM, or external sandbox provider. It must not share host secrets, broad filesystem mounts, Docker socket access, or unrestricted network access.

## Capability Assignment Model

Agent profile should declare:

- capabilities
- installed skills
- allowed tool groups
- allowed MCP servers
- allowed MCP tools
- runtime policy
- approval policy
- file access policy
- network policy
- memory policy

Example:

```yaml
agent: Design Agent
capabilities:
  - image_generation
  - image_editing
  - brand_asset_review
skills:
  - image_prompt_engineer@1
  - visual_design_reviewer@1
tools:
  - openai.image_generation
  - workspace.file_read
  - workspace.artifact_write
runtime_policy:
  sandbox: on_demand
  network: restricted
approval_policy:
  external_publish: required
```

## Skill Registry

The product should maintain a skill registry.

Skill metadata:

- skill id
- name
- description
- version
- owner workspace
- visibility
- required tools
- required runtime
- required permissions
- installation status
- prompt/instruction files
- helper files

Skill visibility:

- system skill: provided by the platform
- workspace skill: created inside a workspace
- private user skill: personal draft or experiment

## MCP Registry

The product should maintain a workspace-scoped MCP registry.

MCP metadata:

- server name
- transport type
- server URL or command
- authentication method
- available tools
- enabled tools
- approval policy
- installed workspace
- health status

MCP access is granted to agents by workspace policy.

Rules:

- MCP credentials are workspace-scoped.
- Agents only see MCP tools granted to their profile.
- MCP tool calls must be logged as run events.
- Write-capable MCP calls should support approval.
- MCP tool names should be namespaced to avoid collisions.

Remote MCP execution uses the official MCP Python SDK `ClientSession` with its Streamable HTTP or
SSE transport. OpsMesh owns the adapter boundary for workspace authorization, credentials, egress,
timeouts, retry/circuit policy, redaction, and audit evidence; it does not reimplement MCP framing
or JSON-RPC parsing. Stdio MCP remains isolated inside Docker or a trusted self-hosted runtime and
must not execute in the API or worker host process.

## Tool Catalog

The product should expose a catalog of tool groups rather than raw low-level tool names.

Example groups:

- Workspace Files
- Workspace Memory
- Web Search
- Image Generation
- Code Execution
- Shell Execution
- Document Generation
- External Connectors
- MCP Tools

Each group maps to concrete OpenAI Agents SDK tools, product tools, MCP tools, or sandbox tools.

## Runtime Selection Policy

The orchestrator chooses runtime based on:

- task type
- agent runtime policy
- selected tools
- skill requirements
- file access needs
- approval policy
- workspace policy
- risk level

Suggested default:

```text
If all required tools are hosted or MCP-only:
  run without sandbox
Else if shell/filesystem/code execution is required:
  create per-run sandbox
Else if task has multiple dependent filesystem steps:
  create task-scoped sandbox
Else:
  run without sandbox
```

## Docker Strategy

Docker is useful, but should be treated as an execution backend and security boundary. For stronger tenant isolation, evaluate microVMs or managed sandbox providers later.

Recommended MVP approach:

- Use Docker only for runs that require isolated filesystem or command execution.
- Mount only workspace-approved files.
- Use a generated run workspace directory.
- Persist artifacts back to workspace storage.
- Destroy the container after the run or task.
- Save snapshots only when explicitly requested by policy.
- Run containers as non-root where possible.
- Apply CPU, memory, disk, process, and timeout limits.
- Use restricted network by default.
- Never mount the host Docker socket.
- Never mount broad host paths.
- Keep application secrets out of the sandbox environment.

Avoid:

- creating a Docker container for every team immediately
- letting multiple workspaces share one container
- storing durable state only inside the container
- mounting broad host paths
- granting unrestricted network by default
- executing agent commands directly on the application server

## Persistent Runtime Strategy

Persistent runtimes are useful for advanced users, but they should be explicit resources.

Recommended model:

- A workspace may create one or more persistent runtimes.
- A runtime belongs to exactly one workspace.
- A runtime has a template, image, resource limit, network policy, and tool policy.
- Agents can be allowed to use selected runtimes.
- Tasks can attach to a runtime when continuity is needed.
- Runtime state can be snapshotted into workspace-owned artifacts.
- Admins can stop, reset, snapshot, or delete a runtime.

Persistent runtime lifecycle:

```text
provisioning -> ready -> attached -> idle -> stopped
                         -> unhealthy
                         -> deleting
```

Minimum controls:

- workspace ownership check
- runtime-level resource limits
- no host secret access
- no host Docker socket
- no privileged containers
- restricted filesystem mounts
- restricted network policy
- activity and command audit logs
- idle timeout or manual stop
- reset to clean snapshot

## Designing Agents With Capabilities

Users should define agents like this:

1. Choose role.
2. Choose skills.
3. Choose tool groups.
4. Choose what files or knowledge the agent can access.
5. Choose approval behavior.
6. Choose runtime policy.

The product API and future UI should explain capabilities in human terms:

- "Can generate and edit images"
- "Can run code in an isolated workspace"
- "Can search workspace files"
- "Can call approved MCP tools"
- "Requires approval before sending external messages"

## Example: Image Agent

An image agent does not need a Docker container by default.

It may use:

- image generation hosted tool
- workspace file read/write
- image editing skill
- brand review skill
- artifact storage

Use Docker only if the agent needs to run local image processing scripts, batch conversion, asset packaging, or custom rendering.

## Example: Software Agent

A software agent usually benefits from sandbox execution.

It may use:

- codebase reading skill
- shell tool
- apply patch tool
- test runner
- git diff tool
- workspace artifact output

Default runtime should be per-task or per-run Docker sandbox, depending on task complexity.

## Example: Research Agent

A research agent usually does not need Docker.

It may use:

- web search
- file search
- MCP connectors
- citation skill
- report writing skill

Default runtime should be no sandbox.

## Future Product Features

- Skill registry
- Workspace-created custom skills
- Skill testing and certification
- MCP connector installation flow
- Runtime templates
- Sandbox snapshots
- Capability risk scoring
- Admin approval policies
- Tool usage analytics
