# Knowledge

**Current runtime: 2026-11-20.** The Linux service loads the independent
`knowledge`, `database` and `filesystem` wheels in dependency order. The
framework and the service do not depend on these plugins.

`opsmesh-knowledge` provides plugin-owned PostgreSQL knowledge bases, local BGE
retrieval, and native Agents SDK tools. It requires `database` and `filesystem`.
It has no HTTP listener, channels dependency, or execution dependency.

Install the package and its separate model runtime on the target Python ABI.
For managed deployment, use the [Linux service instructions](../../docs/linux-plugin-service.md)
to install runtime dependencies into the service venv, then install the three
plugin wheels through the management socket. Enable `database` and
`filesystem` before enabling `knowledge`. Supply a private service
environment with `OPSMESH_DATABASE_DSN`. Set
`OPSMESH_KNOWLEDGE_MODEL_PATH` to an existing immutable model directory, or
use the cache layout below.
Before running migrations, provision PostgreSQL with the `vector` extension.
HNSW mode requires pgvector 0.8 or newer; exact mode explicitly disables index
scans for its retrieval transaction. Deploy and verify a specific extension
version, for example 0.8.2. No request attempts to install extensions.

Prepare the model explicitly on a connected build machine:

```bash
python packages/knowledge/provision_model.py /opt/opsmesh-models/bge-small-zh-v1.5
```

The helper downloads `BAAI/bge-small-zh-v1.5` at commit
`7999e1d3359715c523056ef9478215996d62a620` and writes an SHA-256 manifest for
every file. Copy the entire directory into the runtime resource package and
set knowledge's `model_path` to it. Without `model_path`, provision the same
directory below knowledge's filesystem cache at `models/<revision>`.
Startup verifies the manifest and loads with `local_files_only=True` and
`trust_remote_code=False`. It never downloads weights. Keep the snapshot's
license with the distributed model resource; the BGE model is MIT licensed.

Apply the knowledge plugin's database migration before using it. Metadata
declares the independent `knowledge_0001` head. Settings belong to the plugin.

```python
from agents import Agent
from opsmesh import get_driver, require

knowledge = require("knowledge")

agent: Agent | None = None


@get_driver().on_startup
async def prepare() -> None:
    """Resolve knowledge after required plugins become ready."""
    global agent
    base = await knowledge.create(
        "Support", description="Approved support documents"
    )
    await base.documents.add("Support hours: 09:00-18:00.", name="hours.md")
    agent = Agent(name="Support", tools=base.tools)
```

`create`, `get`, and `list` use the trusted calling plugin's ownership scope.
Persist the returned base ID in the consuming plugin's configuration or data;
use `get(id)` on subsequent activations. It never implicitly creates a base.
Call these APIs from a loaded consuming module's Driver startup hook after
declaring `require("knowledge")`, or from its handlers. The provider checks
the schema and model before opening admission. It drains accepted calls and
the indexing worker before its dependencies shut down.
Handles bind the selected library and owner; SDK tools need no extra context.
Applications choose which handles a user may access.

`base.documents` exposes `add`, `import_file`, `get`, `list`, `update`, `delete`,
`read`, and `reindex`. `import_file` takes bounded UTF-8 bytes or an opened
binary stream plus a `.txt`/`.md` display name. It never accepts arbitrary
local paths. Markdown is plain text. Original bytes are atomically saved in
knowledge's own file space; normalized text retains citation offsets.

Writes return pending metadata. The worker uses short `SKIP LOCKED` claims,
renewable leases, bounded retries, and publication checks. Replacing content
immediately removes old excerpts from retrieval. `read(id, index_key=...)`
rejects stale citations. Lists, reads, source sizes, encoding admission, and
search text are bounded by settings. Calls that omit a page or result limit
use a safe default capped by the configured maximum, so lowering a maximum
does not make the no-argument native tools invalid. HNSW scan budgets may return fewer than
the requested number of hits and do not guarantee exact top-k. Search returns excerpts and cosine
distances, not answers or confidence probabilities.

Encoder, tokenizer, checksum, normalization, and chunk settings form the
profile. Changed profiles require `await base.reindex()`. Whole-base rebuilds
remain unavailable until every current document has the target profile and
epoch. Retrying a failed rebuild preserves that target epoch. New/updated
documents join the current target under the same library lock. Worker restart
recovers pending work and expired leases; old workers cannot publish results.

## Unified Agent tools

From a trusted consuming plugin scope after knowledge has started, pass
`knowledge.build_tools()` to `Agent.tools`. It returns native SDK function tools
bound to the constructing plugin. Both library lookup and document operations
check that scope; a model cannot set an owner or access another plugin's data by
guessing a UUID. Give the full suite only to Agents authorized for the consuming
plugin's entire collection. For a single library, use its existing read tools.

```python
agent = Agent(name="Support", tools=knowledge.build_tools())
```

Operation names below are suffixed to the configured alias and an underscore.
For example, the default create tool is `knowledge_create_knowledge_base`.

| Operation | Input and result | Default approval |
| --- | --- | --- |
| `create_knowledge_base` | Name and optional description; returns `knowledge_base_id`. | Required |
| `list_knowledge_bases` | Optional limit and offset; returns `knowledge_bases` with index state. | No |
| `get_knowledge_base` | Library UUID; returns metadata and index state. | No |
| `search_knowledge` | Library UUID, query and optional limit; returns `hits` with excerpts, offsets and citation keys. | No |
| `list_documents` | Library UUID and optional limit/offset; returns `documents` with index state. | No |
| `get_document` | Library and document UUIDs; returns document metadata without its text. | No |
| `read_document` | Library and document UUIDs, optional character range and citation key; returns bounded text. | No |
| `add_document` | Library UUID, document name and UTF-8 text; returns pending metadata. | Required |
| `replace_document` | Library and document UUIDs and replacement text; returns pending metadata. | Required |
| `delete_document` | Library and document UUIDs; returns `{"deleted": true}`. | Required |
| `reindex_document` | Library and document UUIDs; returns pending metadata with a new citation key. | Required |

In the knowledge plugin's settings, configure a name prefix and override only
the operations that differ from the default policy:

```json
{
  "tools": {
    "alias": "support",
    "approval": {
      "search_knowledge": true,
      "add_document": false
    }
  }
}
```

This example requires approval for `support_search_knowledge` and explicitly
allows `support_add_document` without approval. Aliases are 1 to 40 ASCII
characters, start with a letter, and allow only letters, digits and underscores.
Policy keys are the unprefixed operation names; values must be JSON booleans.
When using managed wheels, put only the inner `tools` object in
`OPSMESH_KNOWLEDGE_TOOLS` and restart the service. The environment override
replaces that settings block; it does not change the signed wheel contents.
For an individual Agent, pass `knowledge.ToolSettings(...)` explicitly to
`build_tools`. That object replaces the plugin's `tools` block, and omitted
permissions use the defaults above. Rebuild the tools for policy changes to
take effect. Use `knowledge.unique_tools(...)` to catch duplicate tool names
when composing knowledge tool groups.

The native SDK interrupts a run before an operation that needs approval. The
consuming application verifies who may approve, records an approve/reject
decision on that invocation, and resumes the run. Rejecting or leaving a call
unapproved does not execute it. Approvals do not authorize cross-plugin access
and do not apply to direct Python APIs. An approved write can still fail input
or state checks. An accepted document write is not yet searchable; read its
index status, and do not blindly retry creates or adds after an unknown outcome.
The suite does not expose raw SQL, arbitrary file paths, or library deletion.

## Existing single-library tools

The tools on an individual library handle are read-only by default and use
unique library-ID prefixes. Supply
`base.tools_with_alias("support")` for descriptive names. Compose tool groups
through `knowledge.unique_tools(...)` during Agent construction to reject
duplicate aliases. `base.management_tools` separately supplies add, replace,
delete, and document reindex tools with native SDK approval required. The
consuming application must handle native interruptions and approval decisions.
An approval is not an indexing-completion signal.

Optional command entrances are application composition: declare the desired
Matcher in its owning plugin and call these objects after checking the user's
authorization. This package does not automatically expose management commands
or bind another plugin's Matcher.

Stopping rejects new calls, stops claims, waits for accepted work and actual
encoding threads, then releases the model. Encoding uses the framework's
cancellation-safe synchronous runner under one plugin-level concurrency
limiter; it does not maintain a second thread executor or cancellation
protocol. Orphan maintenance only removes
old unreferenced sources after the configured grace interval. File cleanup
failure cannot restore deleted SQL visibility.

The focused unified-tools test runs the real SDK with isolated provider wiring
and disposable SQLite storage. It covers ownership, document operations, name
and policy validation, and approve/reject behavior without calling an external
model. It does not exercise current-service loading or pgvector retrieval.
The installed-wheel [Linux smoke test](../../tests/integration/knowledge_smoke.py)
requires an isolated PostgreSQL database with `vector`, the local BGE model,
and a writable filesystem root. It applies the schema, checks source writes,
waits for indexing, searches and reads a citation, and deletes its own test
data. Device performance and HNSW filtered recall are separate checks.
