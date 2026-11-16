# Knowledge deployment — 2026-11-16

## Installed runtime

- Server: `192.168.232.129`; systemd service: `opsmesh`.
- Core and knowledge plugin: `0.5.5`. Existing frozen core executable retained.
- Plugin import root: `/var/lib/opsmesh/plugins/official-0.5.5-knowledge-cpu/site`.
- Model: `BAAI/bge-small-zh-v1.5`, revision `7999e1d3359715c523056ef9478215996d62a620`, 512 dimensions.
- Model directory: `/opt/opsmesh-models/bge-small-zh-v1.5`; manifest and file hashes verified by the plugin before loading.
- PostgreSQL database: `opsmesh_host`; pgvector extension: `0.8.6`; migration: `knowledge_0001`.
- Configuration: CPU, one encoding thread, batch size 8, exact vector search. Knowledge is enabled, autoloaded and required, but not protected.

The published knowledge wheel was checked against release checksums. A separate plugin directory extends the existing official plugin bundle with compatible CPython 3.12 Linux dependencies and official PyTorch `2.15.0+cpu`. The CUDA dependency set was not installed. Dependency metadata was checked against existing host packages. This server-specific CPU bundle is not a new published release artifact.

Existing plugin records and configuration were preserved. The model downloads happened during provisioning; runtime model loading uses local files. The framework executable was not rebuilt, and no application source code was changed.

## Verification

An isolated maintenance host used the deployed executable, staged plugin directory, actual PostgreSQL database and pinned model. Its temporary knowledge base was removed after the checks.

| Check | Result |
|---|---|
| CPU model load and normalized Chinese embedding | Passed, 512 dimensions |
| Import and asynchronously index three Chinese TXT/Markdown documents | Passed |
| Semantic search and original document reading | Passed |
| Invoke the native SDK search tool with SDK ToolContext | Passed |
| Read tools and approval flags on management tools | Passed, 3 read tools and 4 approval-required management tools |
| Update content and reject the old citation | Passed |
| Search updated content | Passed |
| Delete document and rebuild another document's index | Passed |
| Production service startup, `/healthz`, `/readyz` | Passed |

The first tool invocation attempt used an obsolete test fixture (`RunContextWrapper`); replacing it with SDK `ToolContext` fixed the test. No plugin implementation changes were needed.

Startup emitted a non-fatal sentence-transformers method-rename warning for `get_sentence_embedding_dimension`. This was not patched into the published wheel. No error appeared in the inspected production startup log. These checks do not establish large-corpus throughput or sustained-load capacity on this 3.3 GiB server.

## Operation and recovery

Consumers use `require("knowledge")`, create or open their owned knowledge base, and attach its native tools to their Agent. Installing the plugin does not independently expose a new HTTP endpoint.

Deployment evidence and downloaded wheel inventory are under `/root/opsmesh-knowledge-deploy` on the server. The pre-migration database dump is `before-knowledge.pgdump`; the immediately preceding host configuration snapshot is `pre-enable-state.sqlite3`. Those files contain operational data and must remain root-readable only. The old plugin directory remains available for rollback.

Do not restore the full configuration snapshot after subsequent configuration changes without reconciling those changes. A rollback to the former plugin directory must first disable knowledge and address any consumers that now require it.
