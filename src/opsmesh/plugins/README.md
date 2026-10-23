# Plugin Host

`plugins` owns package installation, private module namespaces, adapter registration and
generation changes. Resource semantics remain with `agents/plugins` and `gateway/plugins.py`.
`bootstrap/plugins.py` registers those adapters and supplies the feature views.

```text
plugins/
|-- base.py        PluginAdapter and PreparedResource
|-- registry.py    Adapter dispatch and resource validation
|-- packages.py    Bounded ZIP extraction and atomic installation registry
|-- loader.py      Synchronous setup(ctx) in a private generation namespace
|-- manager.py     Prepare, publish, reload, process acknowledgements
`-- routes.py      Administrator installation/configuration/discovery endpoints
```

Package building and ZIP validation live in `opsmesh-sdk`. Each resource adapter validates
its declarations; it does not duplicate package extraction or implement MCP transport.
`kind: plugin` supports any combination of contributions. A specific kind must actually
register that resource; legacy `kind: hook` also accepts model middleware.

Packages are stored at `OPSMESH_PLUGINS_ROOT/<id>/<version>`; when unset, the root is
`OPSMESH_STORAGE_ROOT/plugin-packages`. Persistent plugin data belongs under
`OPSMESH_STORAGE_ROOT/plugins/<id>`. All API/Worker/Gateway processes in this deployment must
share both roots, the encryption key and compatible Python dependencies. This file-backed
host does not distribute packages to unrelated machines or install Python dependencies.

Online management uses the existing administrator Session and `/api/v1/plugins`:

| Method | Path | Behavior |
| --- | --- | --- |
| GET | /plugins | Installation records and process acknowledgements |
| GET | /plugins/resources | Current validated resource IDs and metadata |
| POST | /plugins/install | Raw ZIP body, disabled by default; optional enabled query |
| POST | /plugins/install-url | HTTPS URL, required SHA-256 and optional enabled |
| PATCH | /plugins/{id} | Set enabled and/or encrypted installation config |
| DELETE | /plugins/{id} | Unregister; retain immutable package files and data |
| POST | /plugins/reload | Reload the receiving process immediately |

Install and enable prepare the selected declarations before publishing. Setup can run
more than once during validation and in separate processes; it must only register resources,
not start tasks, connect to services or perform irreversible writes. MCP factories run only
when selected by an Agent. A disabled package is still imported for installation validation.
Use `opsmesh-plugin check` for archive-only inspection without executing code.

Each process polls every two seconds and publishes a complete local generation. Responses
use HTTP 202 and include `applied`, `revision`, `local_revision` and `hosts`. `applied` becomes
true only when a live API, Worker and Gateway, and every other observed live host, acknowledge the
desired revision. An absent host is not evidence of successful rollout. A failed reload
keeps the process's previous generation and reports its error class. This is eventual
cross-process propagation, not a distributed transaction. Gateway restarts affected plugin
channels when their plugin version or configuration changes; built-in channels stay active.

Old module generations and immutable versions remain available until process shutdown,
because existing Agent objects can retain factories and callbacks. Existing run authorization
and version checks still apply; a hot update does not migrate a persisted Run to a new plugin.
Removing old package files requires an explicit maintenance operation after users release them.

MCP binding and factory examples are in the [SDK README](../../../sdk/python/README.md).
Use the native Agents SDK for stdio, Streamable HTTP or SSE. Temporal callers supply a
`connect_mcp` implementation backed by the official `temporal_mcp_server`; actual factory
resolution and connection belong in Activities. The default connector is for ordinary async
Agent assembly and must not perform connections inside a Workflow.
