# Knowledge

`opsmesh-knowledge` provides plugin-owned PostgreSQL knowledge bases, local BGE
retrieval, and native Agents SDK tools. It requires `database` and `filesystem`.
It has no HTTP listener, channels dependency, or execution dependency.

Install the package and its separate model runtime on the target Python ABI.
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

Run the existing database migration command for the knowledge plugin before
starting the host. Metadata declares the independent `knowledge_0001` head.
Settings belong to the knowledge plugin record, not global host settings.

```python
from agents import Agent
from opsmesh import on_ready, require

knowledge = require("knowledge")

agent: Agent | None = None


@on_ready
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
Call these APIs from `on_ready` or normal handlers. During the host's global
`on_startup` phase, providers have not yet entered framework READY admission.
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

Default tools are read-only and use unique library-ID prefixes. Supply
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

Unit tests use a deterministic encoder and compile PostgreSQL queries without
model weights. Optional PostgreSQL tests use `OPSMESH_TEST_DSN` and
create a disposable schema. Real BGE accuracy, device builds, and HNSW filtered
recall must also be validated with the deployed model and pgvector runtime.
