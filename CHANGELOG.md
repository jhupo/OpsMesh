# Changelog

Changes are recorded here before publication. A section in this file is not proof that a matching release has been published.

## 0.5.8 — 2026-11-18

- Split the process, CLI, local control socket, and SQLite plugin catalog
  into the `opsmesh-service` distribution. The core exposes an explicit
  driver and protocol-neutral events; the channels plugin owns connections,
  adapters, transport events, and channel hooks.
- Allow online status, load, start, stop, and restart for eligible plugins.
  Installing or replacing plugin code and removing it from disk still require
  a service restart. The Linux service reports ready only after its local
  control socket is available.
- Upgrade the framework, service, and official plugins together. Plugins
  using `opsmesh.adapters` or the global framework driver must migrate before
  deployment; the removed APIs are not retained as aliases.

## 0.5.7 — 2026-11-17

- Unify API, QQ and DingTalk under one consumer-owned connection model. Every
  protocol now dispatches `MessageEvent` values to ordinary framework
  `on_message`, `on_command` and `on_notice` matchers. Remove `MessageEntrances`,
  handler binding, `installation_id` and the HTTP identity resolver; each
  connection owns its SDK client, queue and token state.
- Require a `user` profile in the HTTP `MessageRequest` body and remove the
  `X-OpsMesh-User-*` headers. The profile is converted to `UserContext` but is
  not authenticated; consumers decide whether and how to verify requests.
- Encode session scopes from the connection, conversation and scoped user ID.
  Behavior and identifier changes are breaking within 0.5.x: there is no
  compatibility path for old bindings, identity headers, scopes or saved
  selections.

- Move HTTP listener settings to each consumer plugin's
  `dependencies.channels.api.<name>` section. Channels owns transport,
  protocol conversion and framework dispatch, while consumer plugins own
  business permissions.
- Create API connections directly from dependency configuration. Consumers no
  longer bind HTTP handlers or provide an identity callback.
- Remove obsolete API route, authentication and routing modules. HTTP, QQ and
  DingTalk inputs now use the same framework Event, Message and command path.
- Release the channel lifecycle cleanup and response ownership fixes, including
  correct hot restart ownership removal and independent response state.

## 0.5.6 — 2026-11-16

- Add typed multimodal replies, Markdown, native DingTalk card streaming, and delivery observations. Agent plugins select and persist output files; channels transport supported message types.
- Move output formatting and fallback decisions into conversation examples; remove implicit channel rendering and old API transport module paths.
- Separate HTTP dispatch resources from response ownership, bound JSON/SSE execution, and prevent busy bot conversations from occupying all queue workers.
- Cache DingTalk access tokens per connection and preserve safe platform error details.
- Follow bounded, validated HTTPS media redirects for incoming attachments; record safe DingTalk attachment failures without download codes or signed URLs.

## 0.5.5 — 2026-11-16

- Support PluginMetadata.config_file for first-start JSON configuration import and explicit re-import. Preserve saved values across restarts.
- Migrate host records to schema 3, retaining existing configuration, including empty objects.
- Configure consumer-owned QQ and DingTalk accounts through dependencies.channels, without duplicate Settings declarations or manual connection calls.
- Add native group Rules and a split-file example for multiple agents on one robot.
- Fix disabled QQ credential handling, metadata type inspection, configuration snapshot isolation, and connection lifecycle cleanup.

## 0.5.4 — 2026-11-16

- Replace the QQ protocol implementation with Tencent qqbot-agent-sdk 1.2.2.
- Use native token management, gateway lifecycle, message parsing, and message/media requests.
- Preserve configured intents, complete notice frames, incremental message identities, safe operation errors, and plugin shutdown cleanup.
- Configure QQ endpoints with QQ_API_BASE and QQ_TOKEN_URL before host startup. Remove obsolete QQ endpoint, reconnect-delay, and sandbox configuration fields.
- Pin the SDK in development and Linux plugin runtime bundles.

## 0.5.3 — 2026-11-15

- Add QQ ordinary-group operations for group information, bot state, member lookup, member mute changes, and join-request approval.
- Separate QQ guild operations from ordinary-group operations and keep kick, blacklist, and automatic approval strategy APIs unexposed.
- Subscribe DingTalk Stream connections to native `EVENT` frames alongside chatbot callbacks.

## 0.5.2 — 2026-11-15

- Replace QQ webhook delivery with the official Bot API v2 Gateway WebSocket.
- Add Gateway heartbeat acknowledgement checks, bounded reconnect backoff and fatal close handling.
- Parse QQ notice events from standard Gateway envelopes without requiring an outer event ID.
- Refresh the synchronized framework and official plugin package versions.

## 0.5.1 — 2026-11-14

- Unify HTTP message requests, ordered replies, structured data and SSE with framework Matcher commands and continuations. Migrate chat, sandbox and the interactive client together.
- Preserve admitted robot messages and same-conversation ordering during shutdown. Hold synchronous worker dependencies until the actual thread exits on cancellation.
- Add independent knowledge and cron packages: pinned local BGE plus pgvector, and PostgreSQL schedule definitions dispatched by native APScheduler into framework Matchers.
- Retain Langfuse cleanup resources across timeout and cancellation; expose aggregate actual exporter delivery results without sensitive span content.
- Build all plugin distributions separately. Keep local embedding dependencies and weights outside the standard host and base plugin runtime.

## 0.5.0 — 2026-11-12

- Host manifests only need entry plugins and configuration overrides; `require()` loads dependencies, while explicit disabling and origin checks remain enforced. Configuration namespaces default to module names.

- Flatten the core into `opsmesh`, removing the `framework`, `access` and runtime wrapper modules. Driver owns adapter registration and connected channel lookup.
- Publish independent plugin packages under `opsmesh.plugins.*`, with flat source directories and standard entry-point names such as `require("channels")`.
- Replace the custom HTTP stack with native FastAPI/Starlette. API owns its authenticated listener; QQ owns its Webhook listener; channels only coordinates lifecycle phases.
- Switch DingTalk to the official Stream SDK with bounded asynchronous requests, reconnection and cancellable connection ownership.
- Preserve framework Event/Message/Matcher/Dependent dispatch and close streaming dependencies on errors, timeouts and disconnects.
- Keep plugin settings on their owners. Make the systemd environment file optional; the installer deploys only the framework.

### Upgrade notes

Update `opsmesh.framework.*` imports to `opsmesh.*` and plugin module names to
`opsmesh.plugins.<name>`. HTTP settings now belong to the consuming plugin's
`dependencies.channels.api.<name>` configuration. There are no legacy import
aliases or old HTTP configuration paths.

## 0.4.0 — 2026-11-12

### Changed

- Split the framework and official channels, execution, filesystem and database plugins into separate Python distributions.
- Replace `opsmesh.plugins.*` imports and configuration namespaces with `opsmesh.plugins.channels`, `opsmesh.plugins.execution`, `opsmesh.plugins.filesystem` and `opsmesh.plugins.database`.
- Publish plugin wheels, source distributions and optional Linux plugin runtime archives alongside the framework release.
- Make the Linux installer deploy only the framework service, with an empty initial plugin manifest and no automatic database checks or migrations.
- Keep execution focused on native Runner calls, cleanup, framework callbacks and tracing; move business usage persistence outside its responsibilities.
- Isolate database migration histories by plugin and retain an explicit conversion path for older shared histories.
- Consolidate documentation around the current package and runtime contracts.

### Upgrade notes

- This architecture replaces the previous v0.3.2 platform layout. Existing deployments require an explicit configuration and plugin migration; copying the new files is not an in-place data migration.

- Update plugin imports, `require()` calls, host module names and configuration namespaces. No legacy import aliases are supplied.
- Official plugin runtime IDs now follow their standalone module names. User plugin IDs such as `example_chat` are unchanged. Do not rename historical migration owners to match package names.
- Preserve existing plugin files and database contents when replacing framework binaries. Plugin upgrades and data migrations are separate administrative operations.
- Older execution observation data is not deleted automatically; the new execution plugin does not maintain those tables.
