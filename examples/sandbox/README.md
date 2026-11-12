# Native sandbox conversation plugin

A native `SandboxAgent` with persistent SDK history and a workspace per
conversation. Requests and commands use framework Event, Message, Context,
Matcher and Depends. The plugin passes the native agent, `SQLAlchemySession`
and `SandboxRunConfig` directly to `execution.run`.

## Loading

Install the independent channels, execution, database and filesystem packages first. A frozen Linux host loads them from the matching official plugin runtime archive's `site/` directory, which must also appear in `plugin_paths`. The framework installer does not install them.

Add `examples/common` and `examples/sandbox` to the host's `plugin_paths`.
Allow `conversation_support`, `opsmesh_channels`,
`opsmesh_execution`, `opsmesh_database` and
`opsmesh_filesystem` in the host manifest. Load `example_sandbox`
with its own configuration namespace:

```json
{
  "module": "example_sandbox",
  "required": true,
  "config_namespace": "example_sandbox",
  "config": {
    "model": "your-model-name",
    "reasoning": {"effort": "medium"},
    "max_turns": 8
  }
}
```

Configure authenticated API identities in the channels plugin and PostgreSQL
in the database plugin. The native SDK reads `OPENAI_API_KEY` and optional
`OPENAI_BASE_URL`. Execution owns tracing, including optional Langfuse.

Before starting the host, apply migrations using its existing manifest:

```sh
opsmesh --manifest /etc/opsmesh/host.json \
  --command opsmesh_database.commands:migrate
```

`DatabaseSchema` in plugin metadata declares the ORM index and migration
directory. The framework load hook registers them automatically. Startup
checks the expected migration before initializing the conversation store.
Loading the plugin alone does not connect to PostgreSQL or start a sandbox.

## Conversation API and commands

All endpoints require an authenticated caller and enforce index ownership.
Paths are relative to `/plugins/example_sandbox`.

| Method | Path | Result |
|---|---|---|
| POST | `/sessions` | Create an empty index with optional `name` |
| GET | `/sessions?limit=50&offset=0` | List owned conversations |
| GET | `/sessions/{id}` | Read conversation metadata |
| GET | `/sessions/{id}/messages` | Read recent native SDK history |
| DELETE | `/sessions/{id}/messages` | Clear history; retain files and ID |
| DELETE | `/sessions/{id}/workspace` | Reset files and sandbox state; retain history and ID |
| DELETE | `/sessions/{id}` | Delete history, workspace, state and index |
| POST | `/chat` | Run a turn or a declared command |

Send `{"message": "your task", "session_id": "<id>"}` to `/chat`.
The response is `{"text": "answer", "session_id": "<id>"}`. Omitting
`session_id` creates a conversation. Reuse the returned ID for later turns.

`/chat <text>`, `/help`, `/session`, `/history`, `/clear` and
`/reset_workspace` use framework `on_command` matchers. Management commands
do not call a model. Unknown slash commands return 400. This example exposes
HTTP; it does not configure bot connections.

Reuse the standalone chat client:

```sh
python examples/chat/client.py --plugin example_sandbox --new
```

Set `--base-url` or `OPSMESH_URL`; enter the API token at the hidden prompt
or set `OPSMESH_API_TOKEN`. Terminal `/new`, `/sessions`, `/use <id>` and
`/delete <id>` manage selection. `/clear` and `/reset_workspace` operate on
the selected conversation. Saved selection is separated by server, plugin
and credential fingerprint; the client never saves tokens or history.

## Persistence and lifetime

The database holds `example_sandbox_conversations` and the SDK's native
`example_sandbox_sdk_sessions` / `example_sandbox_sdk_messages` tables.
`conversation_support` shares ownership, index and locking logic with chat;
each example owns its models, tables and migrations.

The filesystem plugin allocates this plugin's data directory. Under it:

```text
workspaces/<conversation-id>/     # Native working directory
states/<conversation-id>.json    # Native serialized SandboxSessionState
```

Each turn resumes SDK session state, preserves workspace files, and closes
live processes and I/O afterward, including on cancellation. A host restart
can reopen that workspace. This is workspace persistence, not a resumable
`RunState`: interrupted agent runs and pending approvals are not continued.
Approval interruptions return 409. Streaming is not exposed by this example.

Run, history, clear, reset and delete share a per-conversation scope with a
PostgreSQL NOWAIT row lock. Busy operations return 409. Deletion records its
intent before cleanup; retry DELETE if interrupted. Workspaces require the
same filesystem root after restart. This local example is intended for one
host; database locking alone does not replicate files to other hosts.

`UnixLocalSandboxClient` runs on Linux and provides no OS isolation there.
Use trusted input or external isolation. For untrusted workloads, choose a
native Docker or hosted client and review its resource persistence semantics.

## Code and verification

- `agent.py`, `config.py`: native SDK agent and plugin options.
- `handlers.py`, `commands.py`, `conversation.py`: API, matchers and turn scope.
- `models.py`, `sessions.py`, `migrations/`: owned index and history handles.
- `workspace.py`, `lifecycle.py`: native workspace state and resource lifetime.
- `../common/conversation_support/`: shared example conversation behavior.

Affected tests exercise framework dispatch, native SDK history, multi-turn
input, access isolation, busy operations, clear/reset/delete, restart and
cancellation. Linux integration checks also use actual PostgreSQL connections
and the native sandbox `exec_command` tool. Model responses are deterministic
`ScriptedModel` outputs; no remote model provider is required.
