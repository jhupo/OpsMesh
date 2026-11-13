# Chat plugin

A native SDK Agent with two entrances: an HTTP route and ordinary bot messages or commands.
The plugin defines the Agent directly and passes it to `execution.run`.

## Files

```text
chat/
├── client.py             # Standalone HTTP transport and CLI entry
├── client_console.py     # Terminal commands and saved selection
├── README.md
└── example_chat/
    ├── __init__.py       # Plugin dependencies and declarations
    ├── config.py         # Model and optional connection settings
    ├── agent.py          # Native Agent and SDK execution
    ├── schemas.py        # Conversation request and response contracts
    ├── conversation.py   # Transport-independent conversation turn
    ├── handlers.py       # Event dependencies and channel response adaptation
    ├── sessions.py       # Ownership, index operations and SDK Session handles
    ├── models.py         # Conversation index only, no duplicate history
    ├── migrations/       # Index and native SQLAlchemySession tables
    ├── lifecycle.py      # Shared database binding after schema validation
    ├── http.py           # Message entrance and owned conversation endpoints
    ├── messages.py       # API and bot message matcher
    ├── commands.py       # Framework conversation command matchers
    ├── connections.py    # Optional QQ, DingTalk and MCP connections
    └── callbacks.py      # SDK observation callbacks
```

Read `agent.py`, `conversation.py`, then `http.py` or `commands.py`. Connection and callback
examples are separate from the conversation logic.

## Loading and configuration

Install the independent channels, execution and database packages. Register the existing `example_chat` module with its absolute `examples/chat` import root and `conversation_support` with its absolute `examples/common` import root. For a frozen host, register the official runtime bundle's `site/` import root as described in [Linux deployment](../../docs/linux-deployment.md).

Plugin records and values belong to the host SQLite database, never host.json. Configure database and channel authentication first; enable the registered dependencies, then save this plugin's configuration and enable its entry:

```json
{"model": "your-model-name"}
```

Use `--plugin-action configure --module example_chat --config PATH` to save that JSON, and `--plugin-action enable --module example_chat` while the service is stopped. Runtime management plugins use the same Host operations. Saving configuration never restarts a running Agent; apply it explicitly. Agent and bot connections are created in startup callbacks, so resource restarts read the current configuration.

`conversation_support` shares index operations, ownership checks and locking
with the sandbox example. Each example owns its ORM model, tables and migration.

| Setting | Purpose |
|---|---|
| `model` | Required SDK model name |
| `reasoning` | Optional native SDK Reasoning, e.g. `{"effort": "medium"}` |
| `name` | Agent display name; defaults to `OpsMesh Chat Agent` |
| `instructions` | System instructions |
| `max_turns` | Runner turn limit; defaults to 8 |
| `qq` | Optional QQ connection settings |
| `dingtalk` | Optional DingTalk connection settings |
| `mcp_url` | Optional trusted Streamable HTTP MCP endpoint |
| `mcp_token_ref` | Environment variable containing the optional MCP token |

The SDK default provider reads `OPENAI_API_KEY` and optional `OPENAI_BASE_URL`.
The example inherits execution's tracing configuration, including Langfuse;
it does not override the destination or disable tracing.

## HTTP

Send an authenticated request to `POST /plugins/example_chat/chat`:

```json
{"session_id": "<conversation-id>", "message": "Explain Python context managers."}
```

The response is `{"text": "The answer.", "session_id": "<conversation-id>"}`.
`ChatRequest` extends the channel's `MessageRequest`. The entrance binds the same
command and message matchers used for bots. `Context()` provides the authenticated
caller, and the normalized message can contain text and attachments.

Session REST routes also enter the framework: each route declaration creates
a Matcher whose handler receives an HTTPEvent. They use the same conversation
store as `/session`, `/history`, and `/clear`; only parameter parsing and response
formatting differ. For example, GET `/sessions/{id}` returns the same metadata
as sending `/session` to `/chat` with that session ID. A REST lookup is an event,
not a text message, and does not need a synthetic command string.

Shared turns raise `ConversationError`. The entrance handlers translate it to
an HTTP error or a bot reply; model execution does not depend on HTTP types.

The request cannot supply caller identity. Empty questions or questions over
8000 characters are rejected. The plugin validates conversation ownership on
every operation; knowing an ID does not grant access. Omitting session_id
creates a new conversation, so clients should send it on subsequent turns. An SDK approval interruption returns HTTP 409;
it is never automatically approved.

## Bot commands

Declare a connection in this plugin's configuration, for example:

```json
{
  "model": "your-model-name",
  "qq": {"app_id": "12345", "secret_ref": "QQ_APP_SECRET"}
}
```

DingTalk uses `dingtalk.client_id` and `dingtalk.client_secret_ref` instead.
Only configured connections start. Commands operate on connections owned by
this plugin, using the framework's default `/` command prefix:

| Message | Behavior |
|---|---|
| `/chat Explain Python` | Pass `Explain Python` to the Agent and send its answer |
| `/chat` | Show usage without calling a model |
| `/help` | List commands without calling a model |
| Ordinary addressed text | Run a conversation turn |
| Unknown commands | Reply with command help without invoking the model |

`on_command` registers the Matcher and Rule. `CommandArg()` injects the parsed
Message arguments; no manual prefix splitting is needed. `Channel`, `Event`
and `Context()` come from framework dependency injection. An approval
interruption produces an explanatory bot response without continuing the run.

## Lifecycle and limits

Optional MCP connections use the native `MCPServerManager` and framework
`on_startup` / `on_shutdown` declarations. Connections are ready before the
plugin accepts requests. HTTP requests and bot queues drain before plugin
shutdown closes MCP connections. This example launches no detached background
calls and does not implement a second active-call counter.

History uses native `SQLAlchemySession` over the shared PostgreSQL engine.
The plugin owns only the conversation index and exclusive turn scope. Empty
conversations persist immediately. Clearing uses SDK `clear_session()` and
keeps the index. Deletion first persists a deleting marker, then clears history
and removes the index; retry DELETE to finish interrupted cleanup. A marked
conversation cannot run again.

Execution, history reads, clear and delete share a process-local guard plus
PostgreSQL row locking with NOWAIT. Busy operations return HTTP 409; locks
release on completion, failure or cancellation. Different conversations can
run concurrently. No database engine or message-history implementation is
created inside execution. SDK approval continuation and streaming remain
outside this example's supported operations.

For bots, the default persistent conversation is scoped by plugin, channel,
connection, channel conversation and verified caller. Group members do not
share history automatically. Bot commands include /chat, /help, /session,
/history and /clear. Conversation selection commands (/new, /use, /sessions,
/delete) are terminal commands calling the HTTP management endpoints; this
example does not implement bot-side selection switching.

## Session API and migration

All paths are under `/plugins/example_chat`. Every operation requires the
configured API identity token.

| Method | Path | Behavior |
|---|---|---|
| POST | `/sessions` | Create with optional name; return metadata and ID |
| GET | `/sessions?limit=50&offset=0` | List owned indexes; limit 1-200 |
| GET | `/sessions/{id}` | Read metadata |
| GET | `/sessions/{id}/messages` | Read the latest 100 native history items |
| DELETE | `/sessions/{id}/messages` | Clear history, keep index; return 204 |
| DELETE | `/sessions/{id}` | Delete owned history and index; return 204 |
| POST | `/chat` | Execute text or a declared command in the selected session |

Deploy the updated host and plugin together. Stop the host before applying the
registered migration with its normal manifest and environment, then restart:

```sh
opsmesh --manifest /etc/opsmesh/host.json \
  --command opsmesh.plugins.database.commands:migrate
```

The manifest must load example_chat so its migration is discovered. Startup
checks the revision; it does not silently create tables. The migration creates
`example_chat_conversations`, `example_chat_sdk_sessions`, and
`example_chat_sdk_messages`. The latter two match the installed SDK's native
SQLAlchemySession schema and are used directly by the SDK.

The plugin declares `database.DatabaseSchema` in `PluginMetadata.extra`.
The database plugin consumes that declaration through the framework's
`on_plugin_loaded` hook, registers it with the consumer's ownership, and adds
the startup check. Consumer code does not call `register_models`,
`register_migrations` or `check_migrations`. `database = require(...)` records
the dependency and returns its public module; `get_engine()` borrows its engine.

HTTP /chat explicitly selects this plugin's declared command matchers before
its ordinary-text fallback. Dispatch happens once. Unrelated matchers and
other routes cannot consume this request. Command replies carry the same
session_id and do not call the model. Unknown slash commands return 400.

Tests use a temporary SQLite database, the real migration, native SDK sessions,
ScriptedModel and framework dispatch. They cover multi-turn input, history
isolation, persistence after recreating the store, access rejection, busy
operations, cancellation, deletion recovery, HTTP command routing and terminal
state. Linux integration verification additionally uses real PostgreSQL and
competing connections for row-lock checks. Remote model calls are not covered.

## Local terminal client

`client.py` uses only the Python standard library. Start it with:

```powershell
python examples/chat/client.py
```

The default OpsMesh address is `http://192.168.232.129:8080`. Override it with
`--base-url` or `OPSMESH_URL`. Enter the API token at the hidden prompt, or set
`OPSMESH_API_TOKEN` in the current process environment. The deployment's API
token is stored in `/etc/opsmesh/opsmesh.env`; it is not the model API key.
No credentials are saved by this client.

For one request:

```powershell
python examples/chat/client.py --message "What is 2 + 3?"
```

Ordinary text continues the selected conversation. `/help`, `/session`,
`/history` and `/clear` are sent to the server and handled by framework command
matchers in that same conversation.

| Terminal input | Behavior |
|---|---|
| `/new [name]` | Create and select an empty conversation |
| `/sessions` | List your conversations |
| `/use <id>` | Verify ownership and switch this terminal |
| `/session` | Show current metadata |
| `/history` | Show recent SDK history |
| `/clear` | Clear current history without changing the ID |
| `/delete <id>` | Delete that conversation; clear selection if it was current |
| `/exit` | Exit without deleting server history |

`--new` starts a fresh conversation, and `--session <id>` selects one explicitly.
`--plugin example_sandbox` uses the sandbox example's API with the same client.
Saved selection is separated by server, plugin and credential fingerprint.
By default the client saves only a credential fingerprint and selected ID under
`~/.opsmesh/chat/`; no token or conversation history is written there. Use
`--state-file <path>` for a separate saved terminal selection. On startup the
server revalidates the saved ID. A missing saved conversation clears selection;
a missing conversation during an active terminal request reports an error
instead of silently switching. Running terminals keep independent in-memory
selections; the last saved selection becomes the next launch's default.

The client makes a direct connection, does not follow redirects, and does not
automatically retry failed requests. After a timeout, inspect /history before
resending because the server may have completed the turn.

## Attachments and ordinary bot messages

Private messages and group mentions use `on_message` without requiring `/chat`.
Command matchers run first. Group history remains isolated by connection, group,
and user. Unmentioned group messages are not processed by these examples.

API messages accept ordered text and attachment references. Upload files to the
channels endpoint with the same authenticated identity before sending a message.
The standalone client supports repeated `--file` options (up to 8 MiB each):

```shell
python examples/chat/client.py --plugin example_chat --file ./report.pdf --message "Summarize this document"
```

Replace `example_chat` with this example's plugin name. Use `--session <id>` to select
an existing conversation; the normal saved-selection behavior also applies.
Chat accepts supported images, PDFs and UTF-8 text. Sandbox writes attachments
into its native session workspace; processing depends on installed tools.
QQ and DingTalk can send media through `channel.send`; these conversation
examples still return the agent's text output. Plugins choose when to send generated files.
See [message formats, limits and retention](../../docs/multimodal-messages.md).
