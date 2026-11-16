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
to populate a message's `data`; robots send only the readable content.

`Accept: text/event-stream` selects SSE. Ordinary sends become separate
`message` events. To send an incremental answer, call
`await channel.call("stream", event=event, source=text_iterator)` inside the
source's execution scope. SSE uses `message_start`, ordered `message_delta`
events, and `message_end` with a shared `message_id`; JSON and robot channels
aggregate the text under output limits. A unique `done` follows successful
dispatch and resource cleanup. Failures emit a safe `error` without `done`.
Buffers are bounded, and disconnect cancels dispatch and closes its resources.
Settings `max_output_messages`, `max_output_bytes`, and `stream_buffer_size`
control API output bounds.

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
