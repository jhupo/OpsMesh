---
name: temporal-python-sdk
description: Implement or review OpsMesh Temporal Workflows, Activities, Workers, replay, cancellation, and official OpenAI Agents integration. Consult locked SDK APIs and official documentation before changing execution or sandbox boundaries.
---

# Temporal Python SDK in OpsMesh

Use this skill for changes to durable execution, SDK integration, task queues, approvals, replay, or sandbox Activities. Read the relevant official pages and the installed package's public API before implementation; `uv.lock` defines the version in use.

Current validated baseline: `temporalio==1.34.0`, `temporalio-openai-agents==1.1.0`, `openai-agents==0.22.3`, `openai==3.28.0`, `mcp==2.3.0`.

## Integration boundary

- Configure `OpenAIAgentsPlugin` on the Worker; Clients use the same official `OpenAIPayloadConverter`, with the OpsMesh encryption codec. Run native `Agent` and `Runner` in Workflows. The plugin owns model Activities and replay behavior.
- Use `activity_as_tool` for statically defined Python Activities and `temporal_mcp_server` for MCP. Product sandbox tools use public `FunctionTool` callbacks delegating authorized Temporal Activities and the official `DockerSandboxClient`. Their dynamic schemas, approval identities and independently shared environment lifecycle belong to the product contract. Do not copy SDK runners, Activity stubs, converters, retries, or MCP transport.
- Keep product authorization, credential references, tenancy, cost, audit, and side-effect receipts in OpsMesh Activities. Resolve secrets only there. `ModelProvider` is a public extension for these responsibilities, not another inference engine.
- Native Session persistence runs in an Activity because Workflow code cannot access the database. The Session protocol bridge delegates to SDK storage; it does not maintain another transcript or compaction policy.
- Temporal Workflow Sandbox enforces replay determinism; it does not isolate untrusted code. Current code execution uses the SDK `DockerSandboxClient`. `execution.runtime` owns explicit creation, resume, authorization, operation receipts and release; the official client performs the corresponding backend effects. Run completion or cancellation must not delete an independently shared environment.
- In the locked plugin, `SandboxRunConfig.session` is rejected; use the public `session_state` path. `AgentSandboxClient` implements public `BaseSandboxClient`/`BaseSandboxSession` contracts as a borrowed reference to an explicitly created environment. SDK cleanup closes that reference; only the runtime environment owner deletes the actual Docker container. The state contains opaque environment/Run references, and every Activity reauthorizes access. Use a workspace-relative `cwd`. Do not suppress SDK cleanup, access plugin caches or alter private Runner code. Verify continuation, approval, cancellation and container persistence on real Temporal/Docker.
- Preserve the native `RunState` when applying approvals inside a Workflow. Rebuilding an Agent from a serialized state can discard the plugin's model proxies. Durability comes from Workflow replay over recorded Activities; persisted encrypted snapshots are separate from the live Workflow-local state.
- Outbox signal delivery prepends `dispatch_id` to the declared signal arguments. Handlers must accept that identity, check it against their command and deduplicate delivery. Test the serialized wire arguments and real Worker restart, not just the in-memory mailbox.
- Cancellation, approval waits, and uncertain writes must follow the product's documented state contracts. Verify actual replay and failure behavior on real Temporal and PostgreSQL.

## Official documentation

- [Python setup](https://docs.temporal.io/develop/python) / [中文入口](https://docs.temporal.org.cn/develop/python/set-up-your-local-python)
- [AI integrations](https://docs.temporal.io/develop/python/integrations) / [中文入口](https://docs.temporal.org.cn/develop/python/integrations)
- [OpenAI Agents integration](https://docs.temporal.io/develop/python/integrations/openai-agents)
- [Workflow Sandbox](https://docs.temporal.io/develop/python/best-practices/python-sdk-sandbox)
- [Python SDK API](https://python.temporal.io/) / [SDK source](https://github.com/temporalio/sdk-python)
- [Official AI integration source](https://github.com/temporalio/ai-integrations/tree/main/python/openai_agents)
- [OpenAI sandbox clients](https://openai.github.io/openai-agents-python/sandbox/clients/)

The Chinese entry pages are navigation aids. If they lag behind, check the English official page and locked installed source. Link to authoritative pages instead of copying a mutable documentation snapshot.

## Repository evidence

Read `.agents/references/workflow-engine-boundary.md` and start at `bootstrap/worker.py`, `workflows/engines/temporal/`, `execution/runs/driver.py`, `execution/runtime/service.py`, and `execution/sessions/storage.py`. Validate the affected API → transaction/outbox → Workflow → Activity → SDK → product result flow. Current tests and coverage gaps are listed in `tests/README.md`; obsolete provider/revision acceptance scripts have been removed. Run tests, acceptance scripts, infrastructure and model calls only when explicitly requested by the user. A skipped integration test is unverified.
