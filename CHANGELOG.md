# Changelog

All notable changes to OpsMesh will be documented here.

## 0.2.0

- Organize the native gateway into transport, channel, identity, routing, messaging, API and storage modules without compatibility import paths.
- Add durable per-conversation ordering and batching, group context policy, progress and typing events, streamed QQ text, known-subject broadcasts and QR binding.
- Share authorized model binding between direct execution and Temporal Activities; preserve per-Agent model settings and explicit network routing.
- Use the official Temporal Agents streaming integration and restrict public deltas to authorized Agents; add native message inputs for workflow Agent nodes.
- Consolidate current-user checks, graph resolution and cross-module HTTP dependencies; fix the gateway audit origin that prevented channel creation and identity binding.
- Extend plugin channel contracts and include an optional TypeSafe decision plugin example; external adapters remain separately installed.
- Add migration 0006, real PostgreSQL gateway regressions and persistent Agent execution/replay acceptance. See docs/acceptance.md for evidence and remaining coverage limits.
- Publish prebuilt native archives and Docker images through the existing tag-triggered release pipeline. Upgrade requires draining old Agent Workflows and rebuilding plugins against the 0.2 SDK.

## 0.1.0

- Native Agents SDK configuration, persistent execution instances, private Sessions, sandbox environments and Agent Runs.
- Workflow definitions, immutable publication, binding instances and graph execution through a Temporal engine adapter.
- Plugin development SDK and generic package host with tool, MCP, Skill, Hook, middleware and gateway adapters.
- User ownership, grants, quotas, encrypted execution state, effect receipts and audit records.
- Native distributions, deployment CLI, Compose/systemd topology and backup/recovery tooling.
- Consolidated process bootstrap and server commands shared by Docker and native installation; removed unused security, database, credential and telemetry helpers and obsolete deployment settings.
- Repository maintenance references and adapted upstream skills. Current verification scope and remaining integration coverage are documented in tests/README.md.

