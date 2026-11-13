# opsmesh-filesystem

Independent OpsMesh plugin for ordinary per-plugin files and directories. The import module is `opsmesh.plugins.filesystem`.

The configured root contains plugin-owned data, cache and configuration paths. Public operations cover file reads/writes, deletion, validated ZIP extraction, export and publishing a new directory. The plugin does not maintain skill/tool metadata, a database index, installation state or Python loading.

## Installation and configuration

Install matching framework and plugin wheels in a regular Python environment. For local development from the repository root:

```bash
python -m pip install -e . -e packages/filesystem
```

For a Linux executable host, use the separate official plugin runtime archive. Configure a root writable by the service account:

```json
{
  "module": "opsmesh.plugins.filesystem",
  "required": true,
  "config": {"root": "/var/lib/opsmesh/files"}
}
```

A consumer declares `filesystem = require("filesystem")`. Directory ownership comes from the framework plugin context; incoming requests must not choose another plugin's identity.

## Behavior

File paths remain below the consumer's owned roots. ZIP extraction checks paths, links, count and size limits. File creation with replacement disabled uses an atomic no-clobber operation. Directory publication only creates a previously absent destination; it does not delete and replace an existing tree.

Each independent plugin uses its full canonical module name as one directory segment: `data/demo` and `data/demo.worker` are siblings, with matching `cache` and `config` directories. Ordinary Python submodules use their owning plugin's space.

To migrate an existing nested directory layout, stop the host and use the repository maintenance tool with an explicit `{"plugins": ["demo", "demo.worker"]}` inventory:

```bash
python -m tools.migrate_plugin_paths /var/lib/opsmesh/files plugins.json --state-dir /var/lib/opsmesh
python -m tools.migrate_plugin_paths /var/lib/opsmesh/files plugins.json --state-dir /var/lib/opsmesh --apply
```

The first command prints moves and unregistered entries. Application requires exclusive host state-directory ownership, checks every target before moving, processes independent children first, and retains a journal for interrupted moves. Existing targets are never merged or overwritten. Unregistered entries remain untouched.

Linux directory publication requires `renameat2(RENAME_NOREPLACE)`. Unsupported environments fail explicitly; Windows and older compatibility kernels cannot validate that behavior. The release workflow validates supported Linux environments.

Source skill directories and mutable sandbox workspaces should remain separate. The filesystem plugin does not turn directory ownership checks into a sandbox for untrusted Python code.

## License

MIT; see the package's included license files and third-party notices.
