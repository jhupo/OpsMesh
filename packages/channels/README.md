# opsmesh-channels

Independent OpsMesh plugin for API, QQ and DingTalk. Install the matching core
and plugin distributions from the same release. The installed module is
`opsmesh.plugins.channels`; its entry point is `channels`.

```python
from opsmesh.plugins import require

channels = require("channels")

from opsmesh import on_message
from opsmesh.matchers import Matcher

chat = on_message(priority=10, block=True)

@chat.handle()
async def answer(matcher: Matcher) -> None:
    await matcher.finish("Ready.")

channels.api.message(handlers=(chat,))
```

The host allowlist uses `opsmesh.plugins.channels`. The source lives directly in
`packages/channels/src/`; setuptools maps it to the installed namespace.

API owns its FastAPI application, token authentication and listener, configured
under `config.api`. FastAPI Depends authenticates requests; handlers run through
framework Events, Messages, UserContext, Matchers and Dependent. Native Starlette
responses are used, with managed cleanup for streamed dependency resources.

QQ uses Tencent's qqbot-agent-sdk==1.2.2 under config.qq.
Consumer plugins declare accounts; the native SDK manages tokens, Gateway
handshakes, heartbeats, reconnects and message/media requests. OpsMesh adapts
events and plugin lifecycle, retaining notices and configured intents.
Set QQ_API_BASE=https://api.bot.qq.com and
QQ_TOKEN_URL=https://api.bot.qq.com/app/getAppAccessToken before starting
the host to select the current endpoints; otherwise SDK defaults apply.
The removed protocol fields api_base, sandbox_api_base, token_url,
reconnect_delay and account field sandbox are not accepted.
DingTalk owns its official Stream SDK connection and needs no local listener.
The API listener owns Uvicorn. QQ does not open a local HTTP listener.

Inputs open after consumer readiness and drain before consumers shut down.
DingTalk's console must use Stream mode. The channel adds cancellable lifecycle
ownership around the SDK's native parsing and acknowledgement behavior.
See the repository's `docs/channels-and-callbacks.md` for configuration details.

MIT; see LICENSE.

## Message attachments

API, QQ and DingTalk share framework message segments for text and attachments.
Attachment bytes are fetched lazily under transfer limits; user context contains
no file handles or download credentials. API uploads and downloads are scoped to
the authenticated user and consuming plugin. API, QQ and DingTalk support media replies with transport-specific formats.
QQ uses inline media uploads; DingTalk uses official media IDs and robot templates. See the [attachment guide](../../docs/multimodal-messages.md)
for formats, authenticated endpoints, temporary cache limits and example usage.

## Message protocol

Bind the standard message entrance to existing framework matchers:

```python
from opsmesh import on_message
from opsmesh.adapters import Channel, Event

chat = on_message(priority=10, block=True)

@chat.handle()
async def answer(channel: Channel, event: Event):
    await channel.send(event, "Received: " + event.get_plaintext())

channels.api.message(handlers=(chat,))
```

The only business message endpoint is `POST /plugins/{plugin_id}/messages`.
`MessageRequest` accepts `message` and an optional canonical UUID
`conversation_id`. Message content is text or an ordered list of text and
validated attachment references. Unknown fields and custom body models are
rejected. Keep the returned conversation ID to continue the same conversation;
each user, plugin, connection and conversation has a separate framework scope.
Concurrent requests in that scope receive 409.

The API binds persistent, blocking matchers with distinct priorities and their
scoped framework continuations. Handlers may send multiple complete messages.
JSON replies contain `conversation_id`, `status` (`completed`, `waiting`, or
`failed`), `messages`, and a safe `error`. Empty results return 200 with no
messages. Add one `MessageSegment.structured({...})` alongside readable content
to populate an API message's `data`. Bot plugins select readable content
without a data segment before sending; unsupported segments are rejected.

`Accept: text/event-stream` selects SSE. Complete messages produce separate
`message` events. For incremental output, call `channel.call("stream",
event=event, source=source())` within the source's execution scope. The source
contains complete `Message` values and explicitly bounded text blocks:

```python
from opsmesh.plugins.channels import (
    Message, MessageSegment, StreamMessageStart, StreamTextDelta,
    StreamMessageEnd,
)

async def source():
    yield StreamMessageStart("answer", format="markdown", title="Answer")
    yield StreamTextDelta("answer", 1, "**Summary**")
    yield StreamMessageEnd("answer")
    yield Message(MessageSegment.table(["Item", "Count"], [["Reports", 2]]))

await channel.call("stream", event=event, source=source())
```

Text blocks must close before another block or complete message. IDs are unique
within the source, delta sequences start at 1 and increase without gaps. There
is no legacy string-iterator or per-call `format` argument. JSON buffers bounded text blocks for its response envelope. Bot connections
without native streaming reject incremental blocks; the calling plugin buffers
its model output and sends complete messages. SSE sends `message_start`,
`message_delta`, and `message_end`; `done` follows successful dispatch and
handler cleanup. Failure emits `error` without `done`. Disconnect cancels the
producer. HTTP transport ownership remains live until actual writes and receipt
callbacks finish, independently of handler dependencies.

Settings `max_output_messages`, `max_output_bytes`, and `stream_buffer_size`
control API output bounds. The byte limit counts UTF-8 message content and
structured metadata, excluding JSON/SSE envelopes; attachment bytes have their
own limits. JSON and SSE use the same handler execution timeout. Shared streams
additionally bound messages, bytes,
events and idle reads. These bounds do not guarantee bounded upstream SDK memory.

### Explicit Markdown and DingTalk AI cards

Strings remain plain text, even if they contain Markdown punctuation. Complete
formatted replies use `MessageSegment.markdown(text, title="Answer")` with
`Matcher.send` or `Channel.send`. Each streaming block declares its own format.
The API preserves Markdown source; clients sanitize rendered HTML themselves.

`MessageSegment.table(columns, rows, caption="")` accepts rectangular scalar
rows (up to 32 columns, 200 rows and 64 KiB). API output preserves the table.
Bot transports reject native tables; QQ also rejects Markdown segments.
The calling plugin explicitly chooses text, Markdown or a file. The pure
`channels.rendering.table_text()` helper is available for a chosen text
conversion, but sends never invoke it automatically.
DingTalk uses native Markdown replies or `sampleMarkdown` in mixed replies.

For incremental DingTalk replies, add a published AI card template to the
consuming plugin's existing robot configuration:

```json
{
  "dependencies": {
    "channels": {
      "dingtalk": {
        "main": {
          "client_id": "configured-app-id",
          "client_secret_ref": "DINGTALK_APP_SECRET",
          "card": {
            "template_id": "published-template.schema",
            "content_key": "msgContent",
            "update_interval": 0.8
          }
        }
      }
    }
  }
}
```

The template must contain a streaming Markdown component bound to
`content_key` and use the native AI-card `flowStatus` lifecycle field.
Grant the application's `Card.Instance.Write` and `Card.Streaming.Write`
permissions and publish the application as required by DingTalk. A template
ID is not a credential; the connection uses the same robot account.

Only text blocks declaring `format="markdown"` use configured cards. Each
Markdown block creates its own card, preserving mixed-message order. Updates coalesce chunks, send complete snapshots, and respect
the interval (at least 0.5 seconds). Completion flushes the final text; failure
or cancellation attempts to mark the same card failed under a bounded cleanup
scope. Empty streams create no card. Output limits still apply. No detached
task outlives the handler and no uncertain send is automatically replayed.
Without a card configuration, the plugin sends a complete Markdown message.
Configured-card errors propagate rather than silently sending a duplicate reply.

The SDK's card helpers can suppress HTTP failures, so channels reuses SDK
message identities and status values with its existing cancellable, checked
HTTP transport. Actual rendering and account permissions require a robot
acceptance test; offline tests do not establish that a template is available.

### Files and delivery receipts

Use existing `event.attachments` for media and `MessageSegment.attachment()`
for image/file/audio/video replies. File selection, descriptions, checksums and
persistence belong to the calling plugin. The conversation examples implement
that policy in `examples/common/conversation_support/files.py`; channels does
not expose an artifact collection or scan model outputs and sandbox directories.

A caller can pass `delivery=Delivery(observer)` to `send` or `stream`. The async
observer receives `DeliveryReceipt` values after actual transport operations.
HTTP records `written`, not client acknowledgment. Robots record `confirmed`
only after platform acceptance. Explicit platform rejections record `failed`;
preflight failures never start a transport operation. Timeouts and interruptions
retain `unknown`;
earlier successes remain intact. Card creation, updates and completion are
separate operations. Original vendor return values remain the send result.
Observers run under the owner's admission, must open their own short-lived I/O,
and must not borrow closed handler dependencies. Observer errors are logged and
do not retry a send. Without an observer, channels performs no persistence.

Agent plugins own output selection, guardrails, SDK item deduplication and file
history. See the chat/sandbox examples for buffered publication, native image
results and a native sandbox `publish_file` tool. The examples are not a
required runtime dependency.

## Platform group operations

Operations use the owning connection and `Channel.call()`. `supported_operations`
is the implemented set, not a statement of the bot's account permissions. Calls
from another plugin are rejected. Use framework `Permission` or a calling hook
for the command initiator's business authorization.

| Platform and ID scope | Operations | Method and path |
|---|---|---|
| QQ guild ID | `get_guild`, `get_guild_member`, `get_guild_members`, `get_guild_roles` | GET `/guilds/{guild_id}`, `/members/{user_id}`, `/members`, `/roles` |
| QQ guild ID | `mute_member`, `unmute_member` | PATCH `/guilds/{guild_id}/members/{user_id}/mute` |
| QQ guild ID | `mute_all`, `unmute_all` | PATCH `/guilds/{guild_id}/mute` |
| QQ group openid | `get_group_info`, `get_group_bot_state` | GET `/v2/groups/{group_openid}/info`, `/bot_state` |
| QQ group openid | `get_group_members`, `get_group_member` | GET `/v2/groups/{group_openid}/members`, `/members/{member_openid}` |
| QQ group openid | `get_group_restrict_chat_setting`, `set_group_restrict_chat_setting` | GET/POST `/v2/groups/{group_openid}/restrict_chat_setting` |
| QQ group openid | `get_group_join_request_list`, `approve_group_join_request` | GET `/v2/groups/{group_openid}/join_request_list`, POST `/approval_join_request/{member_openid}` |
| DingTalk openConversationId | `query_open_group_base_info` | POST `/v1.0/im/groups/baseInfos/query` |
| DingTalk interconnection group | `query_group_member` | GET `/v1.0/im/interconnections/conversations/members` |
| DingTalk interconnection group | `update_group_name` | PUT `/v1.0/im/interconnections/groups/names` |
| DingTalk interconnection group | `add_group_member`, `remove_group_member` | POST `/v1.0/im/interconnections/groups/members`, `/members/remove` |

QQ mappings follow the [QQ Bot API v2](https://bot.q.qq.com/wiki/develop/api-v2/)
Gateway and OpenAPI contracts. The Gateway accepts C2C, `GROUP_AT_MESSAGE_CREATE`
and `GROUP_MESSAGE_CREATE` events when the corresponding intent is enabled.
QQ permits four passive direct-message replies and five passive group replies;
the channel enforces those limits per incoming message.
Guild member listing requires private-domain access; guild mute requires guild
administration permissions. Ordinary group member and management endpoints may
require an invited application and the robot must be a group administrator;
the caller's user permission remains the responsibility of the consumer plugin.
Kick, blacklist and automatic approval strategy operations are intentionally not
exposed. DingTalk mappings follow the official `im_1_0` client in
[alibabacloud-dingtalk](https://pypi.org/project/alibabacloud-dingtalk/2.2.67/).
The application must have the corresponding OpenAPI grant and group access;
interconnection-group operations do not apply to every DingTalk chat type.

Known QQ guild/member and robot-group notifications become `QQNoticeEvent`.
Subscribed DingTalk Stream `EVENT` frames become `DingTalkNoticeEvent` using the
[official Stream SDK](https://github.com/open-dingtalk/dingtalk-stream-sdk-python).
Subscribe to the required native events in the platform console; handlers use
`on_notice` or `on_type`. Events preserve native event IDs, types and payloads;
unknown actors stay absent. Ordinary QQ group administration is available
through the operations listed above; the consumer plugin remains responsible
for checking the requesting user's group-admin permission before calling them.
Kick, blacklist and automatic approval strategy operations are intentionally
not exposed.

Group results retain native JSON. Local parameter failures raise `ValueError`;
unknown operations raise `UnsupportedOperation`. HTTP 401/403 and 429 raise
`OperationPermissionDenied` and `OperationRateLimited`. Other vendor failures
raise `OperationError`, preserving safe `status`, `code` and `request_id` fields
without response bodies. HTTP 200 vendor error codes remain inspectable through
`OperationError.code`. No automatic approval, retry or membership policy runs.


## Consumer-owned robots

A consuming plugin calls `require("channels")` and supplies its robot accounts
in the plugin configuration file, without adding channel fields to its Settings:

```json
{
  "config": {},
  "dependencies": {
    "channels": {
      "qq": {
        "main": {"app_id": "123456789", "secret_ref": "QQ_APP_SECRET"}
      }
    }
  }
}
```

Use a numeric application ID for QQ. DingTalk uses `dingtalk.main` with
`client_id` and `client_secret_ref`. The CLI imports this file into the plugin
record. Channels validates it and starts the robot only after its consumer is
ready; stopping the consumer closes and removes its connections. Re-enabling
rebuilds them from current saved settings. Duplicate accounts are rejected.

`channels.in_group("group-id", connection="main")` returns a framework Rule
for `on_message` or `on_command`. It filters verified group messages within
the current plugin. Add `protocol="qq"` or `protocol="dingtalk"` if the name
is used by both protocols. Agent selection stays in handler code; see the
[group assistants example](../../examples/groups/README.md).


### Selecting output in a plugin

`channel.send_types` declares implemented complete-message types.
`channel.stream_formats` declares configured incremental text formats: HTTP
supports text and Markdown, DingTalk supports Markdown when a card template is
configured, and QQ has no incremental format. These describe implemented
protocols, not a guarantee of account permissions or remaining platform quotas.

Plugins choose their presentation before calling `Matcher.send`,
`Matcher.finish`, or `channel.call("stream", ...)`. Channels validates messages
once at the send boundary, checks platform restrictions, and performs transport
I/O. It does not select artifacts, convert model results, silently downgrade
formats, or retry uncertain sends. HTTP route declarations and message entrances
share ownership tracking but have distinct contracts.
