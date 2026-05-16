# Threat Model

## Purpose

This document lists major threats for ChainCloud Agent Team and the default mitigations. It should be reviewed before implementing runtime, tools, MCP, file access, and self-hosted workers.

## Security Goals

- Prevent cross-workspace data access.
- Prevent user or agent code from damaging the application host.
- Prevent tools and MCP connectors from performing unauthorized actions.
- Prevent uploaded files from escaping storage or runtime boundaries.
- Keep secrets out of agent-visible contexts unless explicitly allowed.
- Preserve auditability for sensitive actions.
- Let users keep data local when using self-hosted runtimes.

## Trust Boundaries

```text
User Browser / API Client
  -> Product Backend API
    -> Postgres
    -> Redis
    -> Worker
      -> OpenAI Agents SDK
      -> Runtime Manager
        -> Docker Runtime
      -> MCP Servers
      -> External Tools
      -> Self-Hosted Worker
```

Important boundaries:

- user request to backend
- backend to worker queue
- worker to Docker runtime
- worker to MCP server
- platform to self-hosted worker
- runtime to workspace storage
- agent-generated action to real-world side effect

## Assets

- user account
- workspace data
- uploaded files
- artifacts
- memory entries
- tool credentials
- MCP connector credentials
- runtime credentials
- self-hosted worker credentials
- audit logs
- application server secrets
- Postgres data
- Redis queues and locks

## Threats And Mitigations

### T001: Cross-Workspace Data Access

Threat:

A user or worker accesses resources from another workspace by guessing IDs or using stale job payloads.

Mitigations:

- every workspace-owned table includes `workspace_id`
- every query uses resource ID plus `workspace_id`
- worker reloads resources from Postgres by workspace scope
- API request body cannot override route `workspace_id`
- isolation tests for cross-workspace reads and writes

### T002: Host Command Execution

Threat:

Agent-controlled commands execute on the application server host.

Mitigations:

- API and workers never run user/agent shell commands directly
- Runtime Manager executes commands inside Docker or isolated runtime
- no fallback to host execution
- audit command requests

### T003: Docker Escape Or Host Abuse

Threat:

Container escapes isolation or abuses host resources.

Mitigations:

- no privileged containers
- no Docker socket mount
- no broad host path mounts
- non-root user where practical
- CPU, memory, disk, process, and timeout limits
- restricted network policy
- container labels for cleanup
- cleanup orphaned containers
- consider microVMs for higher assurance later

### T004: Workspace File Exfiltration

Threat:

Agent or tool reads files outside its allowed workspace scope.

Mitigations:

- file tools enforce workspace and agent permission
- runtime mounts only explicitly staged files
- no full workspace mount by default
- storage paths are not authorization tokens
- downloads require membership and permission
- log file staging and downloads

### T005: Path Traversal In Uploads Or Archives

Threat:

Uploaded filenames or archives write outside expected storage paths.

Mitigations:

- sanitize filenames
- store by generated IDs
- reject or safely extract archives
- block `..` traversal
- enforce max size and file count
- never use raw filename as filesystem path authority

### T006: Prompt Injection Through Files Or Web Content

Threat:

Workspace files, web pages, or MCP outputs instruct agents to ignore policies or leak data.

Mitigations:

- keep policy enforcement outside model text
- tools validate permissions before action
- risky actions require approval
- retrieval tools label untrusted content
- system instructions remind agents that retrieved content is untrusted
- do not put secrets in model context unless required

### T007: MCP Tool Abuse

Threat:

MCP server exposes dangerous tools or performs unauthorized actions.

Mitigations:

- workspace-scoped MCP registry
- tool allowlist per agent
- namespaced MCP tool names
- approval policy for write-capable tools
- log MCP tool calls
- health checks and failure handling
- credentials scoped to workspace

### T008: Skill Package Abuse

Threat:

A skill includes malicious instructions or helper scripts.

Mitigations:

- skill registry with visibility and trust level
- curated system skills first
- workspace-private skills isolated to workspace
- helper scripts run only in isolated runtime
- permissions preview before install
- audit skill installation

### T009: Self-Hosted Worker Credential Leak

Threat:

Attacker obtains a self-hosted worker credential and claims jobs.

Mitigations:

- one-time enrollment token
- scoped runtime credential
- credential revocation
- workspace and runtime ID validation
- heartbeat anomaly detection
- job authentication
- rotate credentials

### T010: Self-Hosted Worker Exposes Local Files

Threat:

Agent accesses more of the user's machine than intended.

Mitigations:

- local path allowlist
- worker-controlled file tools
- local Docker sandbox by default
- no host execution unless explicitly enabled
- user can revoke paths
- local audit logs

### T011: Redis Job Tampering Or Stale Jobs

Threat:

Worker executes stale or invalid queued jobs.

Mitigations:

- jobs include workspace ID and idempotency key
- worker reloads durable state from Postgres
- run locks
- job expiration
- reject jobs with mismatched workspace/resource IDs

### T012: Secrets Leaked Into Model Context

Threat:

API keys or connector secrets are included in prompts, logs, or model context.

Mitigations:

- keep secrets server-side or runtime-side
- inject secrets only into approved tool execution
- redact logs
- do not store raw secrets in run events
- separate secret references from secret values

### T013: Artifact Poisoning

Threat:

Runtime generates malicious artifacts that users later download or execute.

Mitigations:

- content type metadata
- optional scanning
- artifact risk labels
- preview safe formats
- do not auto-execute artifacts
- audit artifact downloads

### T014: Resource Exhaustion

Threat:

Runs consume too much CPU, memory, disk, network, or model budget.

Mitigations:

- runtime resource limits
- queue concurrency limits
- per-workspace rate limits
- command timeouts
- max artifact size
- log size limits
- cancellation support

## Required Tests

- cross-workspace read is rejected
- cross-workspace update is rejected
- worker rejects mismatched workspace job
- file download requires workspace permission
- runtime command never runs on host
- Docker container cannot access host workspace root
- agent cannot see ungranted MCP tool
- approval is required for write-capable tool
- self-hosted job with mismatched runtime ID is rejected

## Open Security Questions

- Should MVP enable Postgres Row Level Security?
- Which Docker isolation profile should be default?
- Should network be disabled by default for all Docker runtimes?
- Should uploaded files be virus-scanned in MVP?
- How much run event detail should be redacted by default?
- What is the first supported secret storage backend?
