# Native sandbox conversation plugin

A native `SandboxAgent` with persistent SDK history and one workspace per
owned session. The plugin passes native `SQLAlchemySession` and
`SandboxRunConfig` objects to `execution.stream`. It consumes and sends
the native stream before closing execution, workspace and history scopes.

## Loading

Install channels, execution, database and filesystem separately. Register
`example_sandbox` with the absolute `examples/sandbox` import root and
`conversation_support` with the absolute `examples/common` import root.
Frozen hosts also register the official bundle's `site/` root; see
[Linux deployment](../../docs/linux-deployment.md).

Configure channels API authentication, PostgreSQL and the filesystem root.
Save this plugin's settings through the host plugin configuration command:

```json
{"model": "your-model-name", "reasoning": {"effort": "medium"}, "max_turns": 8}
```

The SDK reads `OPENAI_API_KEY` and optional `OPENAI_BASE_URL`. Execution owns
tracing configuration. Settings take effect when plugin resources start.
Before startup, load this plugin in the manifest and apply its migration:

```sh
opsmesh --manifest /etc/opsmesh/host.json \
  --command opsmesh.plugins.database.commands:migrate
```

Loading declarations does not start a sandbox or connect to PostgreSQL.
Startup checks the migration before initializing resources.

## Commands and transport

The only conversation endpoint is `POST /plugins/example_sandbox/messages`.
Use the common MessageRequest body with a client-generated canonical UUID:

```json
{"conversation_id":"d250759b-8d9a-4c3a-a1af-397292fc835d","message":"/new Research"}
```

All commands and JSON/SSE response fields follow the [chat example](../chat/README.md).
`/new`, `/sessions [limit] [offset]`, `/use <id>`, `/session`, `/history [limit]`,
`/clear` and `/delete <id>` share the same framework handlers. `/reset_workspace`
deletes selected files and native sandbox state while retaining history.
`/clear` retains files. Management commands never call the model and never
implicitly create a session. Ordinary text and `/chat <question>` start or
continue the selected session. Unknown and unauthorized commands never reach
the model. Expected command errors include readable text and `data.error`.

Selection is stored under the verified channel scope, independent of the SDK
session ID. Ownership is checked when selecting and operating on a session.
`/new` and `/use` expose the ID through structured `data.session_id`; later
messages only need the same channel `conversation_id`.

Reuse the standard-library terminal client:

```sh
python examples/chat/client.py --plugin example_sandbox --new --stream
python examples/chat/client.py --plugin example_sandbox --file ./report.pdf --message "Summarize this document"
```

Set `--base-url` or `OPSMESH_URL` and provide `OPSMESH_API_TOKEN` or the hidden
token prompt. Each client window generates its own channel UUID. Saved SDK
selection is separated by server, plugin and credential fingerprint.

## Persistence and lifetime

The database stores the owned conversation index and SDK-native session and
message tables. Filesystem owns the plugin data root:

```text
workspaces/<session-id>/      # Native working directory
states/<session-id>.json     # Serialized SandboxSessionState
```

Every turn resumes native sandbox state and preserves workspace files, then
closes processes and I/O, including on cancellation. Resource restart can
reopen those files, while a new window reselects its SDK session with `/use`.
Pending SDK runs and approvals are not restored. Approval continuation is
not implemented, and execution failures follow the safe channels protocol.

Run, history, clear, reset and delete share a local guard and PostgreSQL NOWAIT
row lock. Busy command errors carry status 409. Deletion records its intent;
retry `/delete <id>` after interrupted cleanup. Workspace persistence requires
the same filesystem root and one host; database locks do not replicate files.

`UnixLocalSandboxClient` is Linux-only and provides no OS isolation. Its
workspace is a local directory. Native Docker or hosted clients require
separate integration and persistence verification.

Focused tests use deterministic native SDK streams, real migrations, history
and a scripted sandbox on Windows. Linux runs can additionally exercise native
`exec_command`. Real PostgreSQL, sandbox isolation, external model providers
and bot connections require separate integration verification.
