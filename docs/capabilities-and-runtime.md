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
SSE transport. Stdio MCP uses the same SDK's `stdio_client` and `ClientSession`, launched through
`python -m opsmesh_runtime.mcp_stdio_client` inside an isolated Docker runtime or by a
trusted self-hosted connector. OpsMesh owns the adapter boundary for workspace authorization,
credentials, egress, timeouts, retry/circuit policy, redaction, and audit evidence; it does not
reimplement MCP framing or JSON-RPC parsing. The runtime image or connector must provide the pinned
MCP Python SDK and return a serialized SDK `CallToolResult`; the API and worker hosts never execute
user-controlled stdio processes.

Docker stdio credentials use hosted encrypted payloads with an `env` string mapping. The worker
decrypts them only at invocation and streams the request through `docker exec` stdin, so raw values
are not stored in runtime command records or process arguments. Self-hosted stdio credentials use
provider `self_hosted_env` with `env:VARIABLE_NAME` references; the connector resolves the value
from its local environment immediately before process launch and persists only the variable name.

The self-hosted connector uses the control-plane-issued runtime credential to heartbeat, poll,
claim, and complete workspace-scoped MCP jobs. A local SQLite recovery store records the claimed
request, the execution phase, and the serialized result before completion is posted. On restart,
recorded results are posted again through the idempotent completion endpoint. If the connector was
terminated while the MCP tool was executing, the job fails as interrupted instead of automatically
repeating a possibly side-effecting action. The control plane returns a worker's existing claimed
job before offering new work, closing the crash window between the claim response and local state
persistence.

## Tool Catalog

The product exposes a workspace-scoped dynamic catalog for product tools, allowed MCP tools, and
configured resources. Tool entries include their source, description, JSON Schema input contract,
risk level, approval requirement, MCP provenance when applicable, and sanitized policy. Resource
entries are versioned and use typed locators for file collections, memory collections, MCP
resources, runtimes, and external services.

Resource locators are validated against the current workspace when they reference files, MCP
servers, credential references, runtime spaces, or workspace runtimes. Parameter defaults must
validate against a Draft 2020-12 JSON Schema. Raw secrets and secret-shaped schema fields are
rejected; external authority must be represented by a workspace-owned credential reference.

The catalog endpoint returns concrete tool names because the Agent runtime needs an executable
manifest. Tool groups remain a product-facing grouping layer over those concrete definitions.

### Effective Agent Catalog

The raw workspace catalog is not an execution grant. OpsMesh computes a separate effective catalog
for an active Agent profile, optionally in an active team context:

1. The Agent profile selects concrete tool names and resource IDs.
2. The team's versioned capability policy limits those selections.
3. A policy keyed by the member's existing `department` label may narrow them further.
4. Team, department, and Agent parameter values are merged in that order; locked upper-level
   parameters cannot be overridden.
5. JSON Schema validation, active-state checks, workspace ownership, and unique tool-name checks
   remove invalid or ambiguous entries before execution.
6. Product tools that require file or memory access are omitted unless an effective resource grant
   provides the required access mode.

The result contains only executable tool descriptors and resource grants, plus denials, policy
provenance, Agent/team/member identifiers, configuration versions, and a canonical SHA-256
fingerprint. The department label is an Agent-team policy selector only. It does not create users,
human organizational units, or a second authorization hierarchy alongside workspace membership
RBAC.

Changing a team capability policy increments its policy version and writes a durable audit event.
Workspace metadata import resets team capability policies because resource IDs belong to the source
workspace and must be explicitly rebound in the target workspace.

### Frozen Run Manifest And Execution Gateway

Creating a run freezes the effective catalog into authorization snapshot version 2. The snapshot
contains the exact product/MCP tool descriptors, input schemas, merged defaults, locked parameters,
resource grants, gateway-only file scope, MCP server and allowlist provenance, runtime placement,
network requirements, and canonical fingerprints. Later Agent or team configuration changes affect
new runs only.

Both OpenAI Agents SDK function tools and Claude Agent SDK MCP tools are generated from the
frozen descriptors. Every backend tool call then crosses the Agent tool gateway, which:

1. requires one exact tool definition from the run manifest;
2. merges frozen defaults and rejects locked-parameter overrides;
3. validates the final arguments against the frozen JSON Schema;
4. intersects file and memory operations with frozen resource and step scopes;
5. re-checks that referenced resources, MCP servers, and MCP allowlist entries are still active;
6. routes the prepared call to the product or MCP executor; and
7. records durable `tool.blocked` run evidence plus a security event for gateway denials.

Disabling a capability resource or MCP entry is therefore an immediate emergency stop for existing
runs. Ordinary policy edits remain frozen for reproducibility, while active-state checks fail closed
at the side-effect boundary.

The frozen `runtime_binding` records the exact workspace runtime and runtime space, the runtime
resources that authorized the placement, the effective network restriction, and allowed file IDs.
Worker preflight and stdio MCP routing compare this binding with current workspace-scoped state.
Removing the binding or changing/revoking its runtime, space, resource, or file scope blocks the run
and records runtime/security evidence.

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

The current orchestrator resolves runtime placement from executable runtime resources, the team's
bound workspace runtime, task/step runtime-space selection, and the concrete runtime's space. It
rejects ambiguous or conflicting placement and requires scoped spaces to have a current active
binding to the relevant team or task.

Remote MCP and product tools do not require a concrete runtime. stdio MCP does: scheduling fails if
the effective catalog exposes a stdio tool without one active, online authorized runtime. The
scheduler reserves the resolved runtime space before creating the run, and the worker revalidates
the frozen binding before execution.

## Docker Strategy

Docker is useful, but should be treated as an execution backend and security boundary. For stronger tenant isolation, evaluate microVMs or managed sandbox providers later.

Current managed-runtime controls:

- Use Docker only for explicitly bound runtime execution.
- Use a dedicated Docker volume; do not mount workspace object storage into Agent stdio execution.
- Keep workspace file reads behind the product tool gateway and frozen file-ID scope.
- Collect artifacts only through explicit authorized workflows.
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

Persistent managed runtime lifecycle is recorded through queued/provisioning, created, running,
stopped, failed/cleanup-failed, and deleted states. Connection status is tracked independently.

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
