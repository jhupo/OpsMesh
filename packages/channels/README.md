# opsmesh-channels

Independent OpsMesh plugin for API, QQ and DingTalk. Install the matching core
and plugin distributions from the same release. The installed module is
`opsmesh.plugins.channels`; its entry point is `channels`.

```python
from opsmesh.plugins import require

channels = require("channels")

@channels.api.get("/status")
async def status() -> dict[str, str]:
    return {"status": "ready"}
```

The host allowlist uses `opsmesh.plugins.channels`. The source lives directly in
`packages/channels/src/`; setuptools maps it to the installed namespace.

API owns its FastAPI application, token authentication and listener, configured
under `config.api`. FastAPI Depends authenticates requests; handlers run through
framework Events, Messages, UserContext, Matchers and Dependent. Native Starlette
responses are used, with managed cleanup for streamed dependency resources.

QQ owns a separate FastAPI Webhook listener under `config.qq`. Consumer plugins
declare accounts; the QQ module opens connections and validates signatures.
DingTalk owns its official Stream SDK connection and needs no local listener.
The API and QQ listeners share the Uvicorn lifecycle implementation.

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

## Message routes

Ordinary route bodies remain validated models in `HTTPEvent.payload`. Bind a
message entrance directly to existing framework matchers:

```python
from opsmesh import on_message
from opsmesh.adapters import Channel, Event

chat = on_message(priority=10, block=True)

@chat.handle()
async def answer(channel: Channel, event: Event):
    await channel.send(event, "Received: " + event.get_plaintext())

channels.api.message("/chat", handlers=(chat,))
```

The request is `{"message": "hello"}` or an ordered list of typed text and
attachment references. Plugins may extend `MessageRequest` with their own fields.
The API accepts only the bound plugin's persistent, blocking matchers with
distinct priorities. A handler explicitly sends one response; no reply returns
204. `UserContext` comes from authentication. API streams retain dependencies
until completion or disconnect.

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
| QQ guild ID | `get_delete_member` | DELETE `/guilds/{guild_id}/members/{user_id}` |
| DingTalk openConversationId | `query_open_group_base_info` | POST `/v1.0/im/groups/baseInfos/query` |
| DingTalk interconnection group | `query_group_member` | GET `/v1.0/im/interconnections/conversations/members` |
| DingTalk interconnection group | `update_group_name` | PUT `/v1.0/im/interconnections/groups/names` |
| DingTalk interconnection group | `add_group_member`, `remove_group_member` | POST `/v1.0/im/interconnections/groups/members`, `/members/remove` |

QQ mappings follow [Tencent's botpy API](https://github.com/tencent-connect/botpy/blob/master/botpy/api.py).
Member listing requires private-domain access; mute and removal require guild
administration permissions. These endpoints accept guild IDs, not ordinary QQ
group openids. DingTalk mappings follow the official `im_1_0` client in
[alibabacloud-dingtalk](https://pypi.org/project/alibabacloud-dingtalk/2.2.67/).
The application must have the corresponding OpenAPI grant and group access;
interconnection-group operations do not apply to every DingTalk chat type.

Known QQ guild/member and robot-group notifications become `QQNoticeEvent`.
Subscribed DingTalk Stream `EVENT` frames become `DingTalkNoticeEvent` using the
[official Stream SDK](https://github.com/open-dingtalk/dingtalk-stream-sdk-python).
Subscribe to the required native events in the platform console; handlers use
`on_notice` or `on_type`. Events preserve native event IDs, types and payloads;
unknown actors stay absent. Ordinary QQ-group administration and application
approval operations are not exposed. Their availability has not been verified.

Group results retain native JSON. Local parameter failures raise `ValueError`;
unknown operations raise `UnsupportedOperation`. HTTP 401/403 and 429 raise
`OperationPermissionDenied` and `OperationRateLimited`. Other vendor failures
raise `OperationError`, preserving safe `status`, `code` and `request_id` fields
without response bodies. HTTP 200 vendor error codes remain inspectable through
`OperationError.code`. No automatic approval, retry or membership policy runs.
