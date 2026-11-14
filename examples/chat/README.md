# Chat plugin

A native SDK `Agent` with owned `SQLAlchemySession` history. API and bot
messages use the same framework command and ordinary-message matchers.
The plugin consumes `execution.stream` inside its history lock and awaits
`channel.call("stream", event=event, source=text_deltas(result))` before
closing native execution resources.

## Loading and configuration

Install channels, execution and database independently. Register `example_chat`
with the absolute `examples/chat` import root and `conversation_support` with
the absolute `examples/common` import root. Frozen hosts also register the
official bundle's `site/` root; see [Linux deployment](../../docs/linux-deployment.md).
The host SQLite database stores plugin records and values. Configure API
authentication and PostgreSQL before enabling the example.

```json
{"model": "your-model-name", "reasoning": {"effort": "medium"}, "max_turns": 8}
```

Save settings with `--plugin-action configure --module example_chat --config PATH`
while the host is stopped, then enable its entry. Optional settings include
`name`, `instructions`, `qq`, `dingtalk`, `mcp_url` and `mcp_token_ref`.
The SDK reads `OPENAI_API_KEY` and optional `OPENAI_BASE_URL`. Tracing follows
execution's configuration. Configuration changes require a resource restart.

Before starting, apply the declared migration with the existing host manifest:

```sh
opsmesh --manifest /etc/opsmesh/host.json \
  --command opsmesh.plugins.database.commands:migrate
```

The migration owns `example_chat_conversations`, `example_chat_sdk_sessions`
and `example_chat_sdk_messages`. Startup checks the revision; the SDK owns
history reads and writes. Each consumer of `conversation_support` has separate
tables, models and migrations.

## Messages and commands

Send authenticated requests to `POST /plugins/example_chat/messages`:

```json
{"conversation_id":"d250759b-8d9a-4c3a-a1af-397292fc835d","message":"/new Research"}
```

The response envelope contains `conversation_id`, `status`, ordered `messages`
and `error`. Each message has readable `content` and optional structured `data`.
`/new` and `/use` return `data.session_id` and `data.session`. The SDK session
ID is distinct from `conversation_id`. Subsequent text reuses only the channel
UUID, with the selection stored by the plugin under `Event.get_session_id()`.
The scope includes the trusted protocol, connection, plugin and caller.

| Message | Result |
|---|---|
| Ordinary addressed text or `/chat <question>` | Run the selected session; create one on first chat |
| `/help` | List commands |
| `/new [name]` | Create and select an empty owned session |
| `/sessions [limit] [offset]` | Return `data.sessions`, `limit`, `offset`, `next_offset` |
| `/use <id>` | Verify ownership and select a session |
| `/session` | Return selected session metadata |
| `/history [limit]` | Return `data.messages` from native SDK history |
| `/clear` | Clear selected history and retain its ID |
| `/delete <id>` | Delete owned history and index; return `deleted_session_id` |

Management commands never invoke a model or implicitly create a session.
Arguments use the framework's `CommandArg()` dependency factory. Unknown and
unauthorized commands receive the same unavailable reply; every configured
command prefix is excluded from ordinary chat. Business validation failures
send readable messages plus `data.error` with `code`, `message` and `status`.
Unexpected execution failures use the channels safe error protocol.

`Accept: text/event-stream` selects SSE. Complete sends are separate `message`
events. Native text increments use `message_start`, ordered `message_delta`
events with one `message_id`, then `message_end`. A unique `done` follows
successful dispatch and cleanup. Errors terminate the request without success
`done`. The JSON transport aggregates each streamed answer into one message.
Bots aggregate increments before sending a complete answer.

## Lifetime and access

Selection is scoped to each API window or verified bot conversation. `/use`
and every history operation check ownership. Private bot messages and group
mentions are accepted; unmentioned group messages are ignored. Optional QQ
and DingTalk connections are declared in this plugin's configuration.

Execution, history reads, clear and delete share a local guard and PostgreSQL
NOWAIT row lock. Expected busy failures carry status 409 in `data.error`.
Channels also rejects overlapping HTTP requests in one channel scope with
HTTP 409. Selection and live execution do not survive a resource restart;
native session history does. Deletion persists intent before cleanup, and
`/delete <id>` can retry interrupted cleanup. SDK approval continuation is
not implemented. Optional MCP resources close after admitted calls drain.

## Terminal client

The standalone client needs only Python's standard library:

```sh
python examples/chat/client.py --base-url http://127.0.0.1:8080 --stream
python examples/chat/client.py --session <id> --message "Continue the conversation"
python examples/chat/client.py --file ./report.pdf --message "Summarize this document"
```

Enter the API token at the hidden prompt or set `OPSMESH_API_TOKEN`.
`OPSMESH_URL` also sets the server address. Every client instance generates
a fresh conversation UUID. All server commands use the unified messages
endpoint; only `/exit` remains local. IDs come from structured data, never
readable text. `--stream` displays SSE increments as they arrive.

`--new` creates a session; `--plugin example_sandbox` selects the sandbox
example. The client saves only the SDK session ID and a server/plugin/token
fingerprint under `~/.opsmesh/chat/`. `--state-file` selects another file.
On launch it uses `/use` to revalidate the saved ID in the new window scope.
It never saves credentials, history or a reusable channel UUID. Requests do
not follow redirects or automatically retry.

Attachments are uploaded before sending ordered text and attachment segments.
The client accepts at most eight files, each up to 8 MiB. Chat supports images,
PDFs and UTF-8 text; sandbox writes attachments to its native workspace.
See [message formats and retention](../../docs/multimodal-messages.md).

Focused tests use real migrations, native SDK sessions and deterministic
`ScriptedModel` streams. They cover selection isolation, commands, ownership,
busy operations, JSON/SSE output, history persistence and the client protocol.
Remote models, production PostgreSQL and real bot connections require separate
integration verification.
