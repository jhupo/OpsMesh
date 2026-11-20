# Chat plugin

> **Migration note:** This README describes the pre-restoration implementation. Its commands and examples do not target the restored NoneBot2 core. See the [repository overview](../../README.md) for current interfaces and migration status.

A native SDK `Agent` with owned `SQLAlchemySession` history. API and bot
messages use the same framework command and ordinary-message matchers.
The plugin consumes `execution.stream` inside its history lock and awaits
`channel.call("stream", event=event, source=ConversationOutput(...).stream(result))` before
closing native execution resources.

## Loading and configuration

This example requires channels, execution, database, and the shared
`examples/common` code. Configure PostgreSQL before running the example.

```json
{
  "config": {
    "model": "your-model-name",
    "reasoning": {"effort": "medium"},
    "max_turns": 8
  },
  "dependencies": {
    "channels": {"api": {"main": {"host": "127.0.0.1", "port": 8080}}}
  }
}
```

Optional settings include `name`, `instructions`, `mcp_url` and `mcp_token_ref`.
Channels converts the request's `user` profile into `UserContext`; it does not
authenticate it. This example treats the profile as an identity claim and leaves
HTTP authentication to the plugin: install your own middleware or an external
gateway when requests must be verified. Omit the `api` binding for a bot-only
setup.

For robot accounts, add `qq` or `dingtalk` under `dependencies.channels` and
put the account and secret reference under the connection name. This example
runs in-process and uses channel streaming and structured output.
See [group assistants](../groups/README.md) for code-defined
group rules and multiple agents on one robot.
The SDK reads `OPENAI_API_KEY` and optional `OPENAI_BASE_URL`. Tracing follows
execution's configuration. Configuration changes require a resource restart.

The database migration must be applied before startup. It owns
`example_chat_conversations`, `example_chat_sdk_sessions`
and `example_chat_sdk_messages`. Startup checks the revision; the SDK owns
history reads and writes. Each consumer of `conversation_support` has separate
tables, models and migrations.

## Messages and commands

Send requests to `POST /plugins/example_chat/messages`:

```json
{
  "user": {"id": "operator", "name": "Operator"},
  "conversation_id": "d250759b-8d9a-4c3a-a1af-397292fc835d",
  "message": "/new Research"
}
```

The response envelope contains `conversation_id`, `status`, ordered `messages`
and `error`. Each message has readable `content` and optional structured `data`.
`/new` and `/use` return `data.session_id` and `data.session`. The SDK session
ID is distinct from `conversation_id`. Subsequent text reuses only the channel
UUID, with the selection stored by the plugin under `Event.get_session_id()`.
The scope includes the connection, conversation type, conversation and the
scoped user ID; the client-supplied profile is not proof of identity.

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
`OPSMESH_URL` also sets the server address. Every request carries `user` in
the body; `--user` or `OPSMESH_USER_ID` supplies its ID and defaults to
`chat-client`. This profile is input data, not a credential. The client sends
the token as `Authorization: Bearer`, and the consuming plugin decides whether
to verify it. Every client instance generates a fresh conversation UUID. All
server commands use the unified messages endpoint; only `/exit` remains local.
IDs come from structured data, never readable text. `--stream` displays SSE
increments as they arrive.

`--new` creates a session; `--plugin example_sandbox` selects the sandbox
example. The client saves only the SDK session ID and a server/plugin/user/token
fingerprint under `~/.opsmesh/chat/`. `--state-file` selects another file.
On launch it uses `/use` to revalidate the saved ID in the new window scope.
It never saves credentials, history or a reusable channel UUID. Requests do
not follow redirects or automatically retry.

Attachments are uploaded before sending ordered text and attachment segments.
The client accepts at most eight files, each up to 8 MiB. Chat supports images,
PDFs and UTF-8 text; sandbox writes attachments to its native workspace.
This example uses the removed Channels attachment interface. Current attachment support is documented by the [QQ](../../packages/qq/README.md) and [DingTalk](../../packages/dingtalk/README.md) adapters.

Focused tests use real migrations, native SDK sessions and deterministic
`ScriptedModel` streams. They cover selection isolation, commands, ownership,
busy operations, JSON/SSE output, history persistence and the client protocol.
Remote models, production PostgreSQL and real bot connections require separate
integration verification.

## Output policy

`output_format` explicitly selects `markdown` (default) or `text`; content is
never guessed from punctuation. `publication` defaults to `buffered`, sending
the final response after SDK guardrails succeed. Explicit `progressive` mode
uses typed text blocks and may show text that a later guardrail rejects; it
cannot retract already published text. Approval interruptions are errors in
this example, not successful completions or automatic continuation.

The shared example output reader consumes the native SDK stream once. Custom
structured output needs a formatter returning `channels.Message`. Optional file
selection can publish completed native PNG/JPEG image-generation results;
ordinary tool-result images are not automatically public. This chat example
does not enable image generation or file tools by default.

The client displays tables and file links alongside text. `--download-dir ./out`
saves returned attachments with authenticated, bounded requests to the same
server. Downloads do not follow redirects or overwrite files. Use `--stream`
with `publication: progressive` for live text increments; buffered output stays
a complete SSE message. A plain terminal displays Markdown source.


The example chooses presentation using the current channel's `send_types` and
`stream_formats`. QQ receives plain text, and connections without native
incremental output use buffered publication. Table-to-text conversion and
HTTP-only metadata selection are explicit example policies. Channels rejects
unsupported formats instead of making these decisions for the agent.
