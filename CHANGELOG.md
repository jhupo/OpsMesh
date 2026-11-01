# Changelog

## Unreleased

## 0.3.1 — 2026-11-01

- Add a dedicated `opsmesh-<VERSION>-linux.tar.gz` installation bundle containing
  the built wheel, hash-locked runtime requirements and deployment files. Exclude
  Python interpreters, virtual environments, development source trees and tests.
- Default to native systemd installation, reusing a service-accessible Python
  3.11–3.14 or downloading managed Python 3.13 when needed. Install runtime
  dependencies online into a new virtual environment. Docker remains an explicit
  `--mode docker` and installs the same wheel and locked dependencies.
- Preserve instance configuration and data across repeat installs, require
  explicit upgrades, and accept bootstrap passwords through an interactive
  prompt, stdin or an existing protected secret file without persisting them in
  long-lived services.
- Add instance isolation, service readiness checks and installation behavior
  tests from the built artifact. Publish only the HTTP port in Compose and use
  bounded HTTPS package downloads while building the application image. Keep the
  existing v0.3.0 release and its assets unchanged.

## 0.3.0 — 2026-10-31

This release replaces the architecture used by the repository's previous `0.2.x`
and earlier releases. It requires a separate installation and database; previous
configuration, plugin APIs, checkpoints and storage cannot be upgraded by running
the new `opsmesh migrate` command. Back up the old deployment before evaluating the
new one. See [release notes](docs/releases/v0.3.0.md) for installation and limitations.

The changes below describe the implementation included in this release. References
to legacy records or imports concern this implementation's development revisions,
not compatibility with the previously published architecture.

- Separate startup composition from cross-module application flows. Name isolated
  execution infrastructure `run_workers` and flatten the single embedding provider.

- Align source ownership: concrete bot protocols now live in `infrastructure/bots`,
  Run worker transports in `infrastructure/run_workers`, and command entrypoints in `entrypoints/cli`.
  Group SQL, messaging and coordination infrastructure; separate public resource contracts
  from persistence and notification internals from management. Direct implementation imports
  must use the release's new paths; documented framework APIs and the database facade
  are the extension entrypoints for this architecture.
  Add a complete source responsibility inventory and stricter import-boundary checks.

- Separate managed bot adapter contracts from platform accounts, authorization and routing.
  Add DingTalk Stream/OpenAPI and official QQ signed Webhook/OpenAPI implementations;
  move OneBot compatibility into its own adapter package with legacy-record migration.

- Fix Bot identity delegation and credential-reference authorization, including concurrent
  configuration and route changes; preserve scoped legacy secret permission aliases.
- Recover terminal Run session claims, deduplicate schedule recovery capacity accounting,
  and merge notification delivery receipts without losing concurrent inbox read state.
- Finalize obsolete memory jobs, resume workspace snapshot restoration from private staging,
  and allow historical pricing for withdrawn model versions without reactivating execution.

- Keep Bot-to-Agent bindings and durable scheduling in platform modules, independent of
  business plugin loading. Add authorized binding lookup/list/removal and resolve published
  Agent revisions inside the service for both HTTP and dependency-injected callers.
- Separate process timers into framework scheduling and durable jobs into the scheduling
  module; retain legacy scheduler imports and prevent core code from importing optional plugins.
- Implement the asynchronous NoneBot-style framework: drivers, adapters, bots, events/messages,
  matchers, dependency injection, contexts, plugin metadata, ownership and failed-load rollback.
- Add local accounts, permissions, immutable agent/model revisions, sessions, durable runs,
  approval/resume, cancellation, launch leases, capacity admission and artifact finalization.
- Integrate OpenAI Agents SDK 0.24.0 with focused modules for construction, models, tools,
  delegation, guardrails, hooks, streaming, state, sandboxes, tracing, errors and usage.
- Add tool authorization, skills, MCP, local knowledge search, workspace manifests/snapshots,
  execution providers and platform-managed two-stage file memory generation.
- Add explicit SQL migrations, transactional outbox delivery, Redis rate limits/leases,
  audit, structured logs, metrics, OpenTelemetry traces, usage accounting and budgets.
- Add CLI initialization/migration/check/server/worker commands, HTTP/SSE, signed OneBot v11
  ingress, delivery handling and persistent scheduled events.
- Establish descriptive public names, selected OpsMesh framework prefixes, src package
  boundaries, locked dependencies, CI, deployment examples and open-source contributor files.
- Add typed output/tool completion/agent input extensions, native tool ports, hosted MCP approvals,
  Responses WebSocket, metered session compaction and immutable per-run overrides.
- Add isolated child sandbox restoration, metered native memory flush, live memory updates,
  snapshot/session source authorization, retained memory pruning and reference-aware blob GC.
- Add commit-pinned Git skills, isolated script packages, hybrid/vector search, MCP SSE,
  authenticated active/reverse OneBot WebSocket and NapCat file delivery.
- Add resource tariffs, explicit FX versions, repricing, invoice reconciliation and OTLP/W3C export;
  verify PostgreSQL, local protocol services and native Linux sandboxes with actual instances.

- Document current support and test evidence separately from target architecture. Web and
  Claude remain future implementations; external provider/deployment acceptance is pending.
