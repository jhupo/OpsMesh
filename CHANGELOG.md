# Changelog

Changes are recorded here before publication. A section in this file is not proof that a matching release has been published.

## 0.4.0 — 2026-11-12

### Changed

- Split the framework and official channels, execution, filesystem and database plugins into separate Python distributions.
- Replace `opsmesh.plugins.*` imports and configuration namespaces with `opsmesh_channels`, `opsmesh_execution`, `opsmesh_filesystem` and `opsmesh_database`.
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
