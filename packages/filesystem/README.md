# opsmesh-filesystem

**Current runtime: 2026-11-20.** This independent plugin owns ordinary
per-plugin files and directories. Its managed wheel exports `filesystem`.

The configured root contains plugin-owned data, cache and configuration paths. Public operations cover file reads/writes, deletion, validated ZIP extraction, export and publishing a new directory. The plugin does not maintain skill/tool metadata, a database index, installation state or Python loading.

## Installation and configuration

Install matching framework and plugin wheels in a regular Python environment. For local development from the repository root:

```bash
python -m pip install -e . -e packages/filesystem
```

The default root is `/var/lib/opsmesh/files`. Set
`OPSMESH_FILESYSTEM_ROOT` in the service environment when deploying to another
writable location. Restart the service after changing it.

A consumer declares `filesystem = require("filesystem")`. Directory ownership
comes from the loaded module that directly calls the API. Requests cannot
supply an owner.

## Behavior

File paths remain below the consumer's owned roots. ZIP extraction checks paths, links, count and size limits. File creation with replacement disabled uses an atomic no-clobber operation. Directory publication only creates a previously absent destination; it does not delete and replace an existing tree.

Each independent plugin uses its full canonical module name as one directory segment: `data/demo` and `data/demo.worker` are siblings, with matching `cache` and `config` directories. Ordinary Python submodules use their owning plugin's space.

Knowledge retains its existing `opsmesh.plugins.knowledge` directory segment
when using the new wheel. Other pre-migration layouts are not moved implicitly.

Linux directory publication requires `renameat2(RENAME_NOREPLACE)`. Unsupported environments fail explicitly; Windows and older compatibility kernels cannot validate that behavior. The release workflow validates supported Linux environments.

Source skill directories and mutable sandbox workspaces should remain separate. The filesystem plugin does not turn directory ownership checks into a sandbox for untrusted Python code.

## License

MIT; see the package's included license files and third-party notices.
