---
name: temporal-python-sdk
description: Implement or review OpsMesh Temporal Workflows, Activities, Workers, replay, cancellation, and official OpenAI Agents integration. Consult locked SDK APIs and official documentation before changing execution or sandbox boundaries.
---

# Temporal Python SDK in OpsMesh

Use this skill for changes to durable execution, SDK integration, task queues, approvals, replay, or sandbox Activities. Read the relevant official pages and the installed package's public API before implementation; `uv.lock` defines the version in use.

Current validated baseline: `temporalio==1.34.0`, `temporalio-openai-agents==1.1.0`, `openai-agents==0.22.3`, `openai==3.28.0`, `mcp==2.3.0`.

## Integration boundary

- Configure `OpenAIAgentsPlugin` on the Worker; Clients use the same official `OpenAIPayloadConverter`, with the OpsMesh encryption codec. Run native `Agent`, `SandboxAgent`, and `Runner` in Workflows. The plugin owns model Activities and replay behavior.
- Use `activity_as_tool` for statically defined Python Activities, `temporal_mcp_server` for MCP, and `temporal_sandbox_client` with a registered `SandboxClientProvider` for sandbox operations. Dynamic product tools may use the public `FunctionTool` callback to delegate an Activity when their user-defined JSON schema and approval identity do not match a Python Activity signature; document this concrete gap. Do not copy SDK runners, Activity stubs, converters, retries, or MCP transport.
- Keep product authorization, credential references, tenancy, cost, audit, and side-effect receipts in OpsMesh Activities. Resolve secrets only there. `ModelProvider` is a public extension for these responsibilities, not another inference engine.
- Native Session persistence runs in an Activity because Workflow code cannot access the database. The Session protocol bridge delegates to SDK storage; it does not maintain another transcript or compaction policy.
- Temporal Workflow Sandbox enforces replay determinism; it does not isolate untrusted code. Current code execution uses the SDK `DockerSandboxClient`. The native backend owns creation, resume, files, commands, and cleanup; OpsMesh owns authorized environment/session references.
- In the locked `temporalio-openai-agents==1.1.0`, `SandboxRunConfig.session` is rejected even for a Temporal proxy. Use the supported `session_state` resume contract and public Worker interceptors for product authorization and persistent environment ownership. Keep native shutdown and dependency cleanup; do not access plugin caches or alter private Runner code. Verify this boundary on real Temporal and Docker before claiming continuation or cancellation works.
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

Start at `runtime/temporal/worker.py`, `agents/execution/providers/openai/workflow.py`, `runtime/sandboxes/factory.py`, and `agents/sessions/gateway.py`. Validate the affected API → transaction/outbox → Workflow → Activity → SDK → product result flow. Use the relevant `tests/test_temporal_*`, native routing, sandbox, and migration tests with their opt-in infrastructure. A skipped integration test is unverified.
