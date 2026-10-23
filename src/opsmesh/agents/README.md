# Agent Configuration And Assembly

This package wraps the public constructor surface of `openai-agents==0.22.3`.
It contains configuration values, explicit registries, and per-execution native
Agent assembly. `catalog/` owns encrypted durable configuration drafts, immutable
releases, online management routes and ownership checks. AgentManager remains the
trusted in-memory configuration API. Sessions, runs, quotas and environments have
separate owners; this package does not implement another Agent loop.

## Configuration

`AgentConfig` uses the native Agent field names. `SandboxAgentConfig` adds
`default_manifest`, `base_instructions`, `capabilities`, and `run_as`.
Model settings are JSON values validated against the installed SDK's
`ModelSettings`; unknown top-level settings are rejected.

```python
from opsmesh.agents import AgentConfig, AgentDefinition, AgentManager
from opsmesh.agents.config.tools import AgentToolRef

manager = AgentManager()
manager.register(AgentDefinition(
    id="analyst",
    config=AgentConfig(name="Log analyst", instructions="Analyze the supplied logs."),
))
manager.register(AgentDefinition(
    id="assistant",
    config=AgentConfig(
        name="Assistant",
        tools=[AgentToolRef(
            agent_id="analyst",
            tool_name="analyze_logs",
            tool_description="Analyze logs and return the findings.",
        )],
    ),
))
manager.patch("assistant", {"instructions": "Summarize findings for the user."})
```

Updates replace the submitted configuration or top-level fields. Lists replace
the old selection; an empty list clears it. Existing builds retain their snapshot;
the next build uses the updated configuration. A nested object supplied to `patch`
replaces that whole object. There is no publication/version rollback subsystem.
The manager stores validated copies and supports atomic JSON snapshot import and
JSON export; the application owns durable storage and authorization. `replace=True`
on import overwrites matching IDs, preserving other registered definitions.
The manager API is trusted, not a public user API. For a shared multi-user catalog,
use `build_agent(..., load=authorized_loader, bindings=...)` so the loader checks
every referenced Agent, including transitive subagents. Resource resolvers must
likewise enforce the caller's current access to models, tools, skills and MCP.

## Build And Resource Ownership

```python
from agents import Runner
from opsmesh.agents import AssemblyBindings

async def run(message: str) -> object:
    bindings = AssemblyBindings()  # Supply the current caller's authorized resources.
    async with manager.build("assistant", bindings) as build:
        result = await Runner.run(build.agent, message)
        return result.final_output
```

With no explicit model selection, the SDK's configured default model is used.
For an explicit `ModelRef`, register `ProviderConfig` and `ModelInfo` in
`bindings.providers` and inject an asynchronous `credentials(reference)` returning
`SecretStr`. Alternatively, inject `model_resolver(reference, lifespan)` returning
a native `Model` and optional `ModelSettings`. This also accommodates execution-time
model proxies without importing an execution engine. The built-in factory supports
Responses and Chat Completions with explicit provider URLs and credential references.
It never stores a plaintext API key in the Agent definition. Model descriptions
advertise capabilities; the application still owns user grants, limits and metering.

Every build creates a fresh graph. Graph limits and cycles are checked before
resource creation. The build context closes owned model clients on success, error
and cancellation. Run the Agent inside that context. Do not store built Agents in
the manager or reuse them across unrelated user sessions.

## Tools, MCP And Callbacks

Installed plugins enter these registries through the host's
`PluginRegistry.agent_bindings(invoke_tool=dispatcher, plugin_ids=...)` or
`agents.plugins.bindings.register_agent_plugins`. The dispatcher is mandatory and
reauthorizes each tool invocation; the Agent package never loads plugin code.
Tools use `<plugin_id>/tools/<name>` references, Skills use
`<plugin_id>/skills/<directory>`, and lifecycle hook groups use `<plugin_id>/hooks`.
`opsmesh.plugins.hooks` selects the combined lifecycle hooks in that caller view.
Set `hooks=CallbackRef(callback_id="demo-agent-plugin/hooks")` to select one group.
Plugin Skill directories retain their complete content and install under
`.opsmesh/skills/<plugin_id>__<directory>/`; this also avoids same-name collisions.
The [plugin SDK guide](../../../sdk/python/README.md) describes setup and callback contracts.

Plugin discovery and Agent assembly share `tools.ToolInfo`, including input/output
schemas, effects, approval, administrator restriction and timeout metadata. Plugin
authorization selects the current registration by plugin ID and tool name, with an
optional expected package version; it does not depend on the former capability
configuration types. Skill ZIP construction belongs to `skills/packages.py` and
checks paths, file kinds, entry counts and compressed/expanded size limits before
returning a deterministic archive.

- `ToolRegistry.register_function` uses native `function_tool`. Configured or
  stateful tools use `register(info, factory)`; factories return fresh SDK tools.
  Catalog metadata and implementation registration have one owner.
- Native hosted tools use `NativeToolConfig`. Stateful computer, shell and patch
  executors use trusted registered factories. Uploaded code is never imported here.
- `AgentToolRef` invokes native `as_tool`; `HandoffRef` invokes native `handoff`.
  Sessions, conversation IDs, input and per-run overrides are supplied by the
  execution owner, rather than persisted in reusable Agent-as-tool bindings.
- `mcp_resolver(reference, options, lifespan)` must return a connected native
  `MCPServer`. It must enforce the supplied native filter/approval options without
  mutating another Agent's borrowed connection. Register owned cleanup on lifespan;
  borrowed connections remain owned by the connection manager.
- `CallbackRegistry` registers synchronous factories by contract kind and ID.
  Factories return SDK guardrail/hook objects, Python types or callable handlers;
  the handlers themselves can be asynchronous. No dynamic import path is accepted.
  Temporal callback registrations also require an immutable `revision`; direct
  assembly does not. See the [execution adapter contracts](../execution/README.md)
  for supported callback kinds and Activity copy semantics.

## Sandbox And Skills

`config/manifest.py` declares the default OpsMesh workspace. Assembly converts the
configuration to the SDK's native `Manifest`; the sandbox client materializes it.
There are no shell commands for creating this layout.

```text
/workspace/
|-- files/                 # Input and working files
|-- outputs/               # Deliverables
|-- tmp/                   # Temporary working files
`-- .opsmesh/
    |-- skills/            # Installed Skills, each with its own SKILL.md
    |-- session/           # Reserved for runtime-owned SQLiteSession storage
    |-- memory/            # Optional native Memory summaries
    `-- rollouts/          # Optional native Memory rollout artifacts
```

Each `SandboxAgentConfig` gets a fresh default declaration. Replace
`default_manifest` to customize the root and entries, use `ManifestConfig()` for an
empty declaration, or set `default_manifest=None` to delegate to the SDK. To extend
the default layout, call `default_workspace_manifest()` and construct a new
`ManifestConfig` with its entries and the additional entries. Paths selected for
Skills and Memory must match the customized capability configuration.

No SQLite database is created by this package. The execution owner binds the
database under `.opsmesh/session/` and selects the current user's private Session
ID; a shared database file does not mean shared conversation history. Memory is
opt-in; its default directories above apply only when that capability is selected,
and `options.layout` can override them. Resetting SQLiteSession history and
clearing generated Memory files are separate runtime operations.

Expert instructions remain in `instructions`. An optional `AGENTS.md` can be
declared as a File entry for workspace conventions; adding it does not implicitly
load its content into the Agent prompt. Initialization does not clear an existing
directory or reset memory when a configuration is updated.

Sandbox capability options are validated against native Filesystem, Shell, Memory
and Compaction models. Omitted capabilities preserve SDK defaults; `[]` disables
them all. Skills select catalog IDs, an authorized directory `EntryRef`, or a
registered lazy source. `skills_path` follows the SDK: a skill named `log-analysis`
with `skills_path=".opsmesh/skills"` lives at `.opsmesh/skills/log-analysis/SKILL.md`.
OpsMesh uses `.opsmesh/skills` by default; the unmodified SDK default is `.agents`.

Skills are native `Skills` capabilities, not prompt text pretending that files
exist. The SDK materializes declarations when preparing a new sandbox session.
`build.skill_installations` additionally exposes the selected file trees for an
external runtime to synchronize into an existing persistent environment. That owner
must implement updates/removals, authorization and shared-directory concurrency;
assembly itself does not write files or silently remove user content.

Inline manifests support File, Dir and GitRepo declarations. Local host sources,
mounts and other entries use authorized `EntryRef` resolution. A trusted manifest
template can supply host grants and secret environment values. The package creates
no sandbox, process or MCP connection of its own. Sandbox client/session options,
Session memory and `max_turns` belong to Runner/RunConfig at execution time.
