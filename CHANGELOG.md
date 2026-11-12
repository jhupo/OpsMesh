# Changelog

Changes are recorded here before publication. A section in this file is not proof that a matching release has been published.

## 0.5.0 — 2026-11-12

- Host manifests only need entry plugins and configuration overrides; `require()` loads dependencies, while explicit disabling and origin checks remain enforced. Configuration namespaces default to module names.

- Flatten the core into `opsmesh`, removing the `framework`, `access` and runtime wrapper modules. Driver owns adapter registration and connected channel lookup.
- Publish independent plugin packages under `opsmesh.plugins.*`, with flat source directories and standard entry-point names such as `require("channels")`.
- Replace the custom HTTP stack with native FastAPI/Starlette. API owns its authenticated listener; QQ owns its Webhook listener; channels only coordinates lifecycle phases.
- Switch DingTalk to the official Stream SDK with bounded asynchronous requests, reconnection and cancellable connection ownership.
- Preserve framework Event/Message/Matcher/Dependent dispatch and close streaming dependencies on errors, timeouts and disconnects.
- Keep plugin settings on their owners. Make the systemd environment file optional; the installer deploys only the framework.

### Upgrade notes

Update `opsmesh.framework.*` imports to `opsmesh.*`, plugin module names to
`opsmesh.plugins.<name>`, and move API listener settings from `config.http` into
`config.api`. QQ has its own listener configuration under `config.qq` (default
port 8081). DingTalk requires Stream mode. No legacy import aliases or old HTTP
configuration parsing are retained.

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
