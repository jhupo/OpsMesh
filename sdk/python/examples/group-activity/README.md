# Group Activity Plugin

This optional deployment policy observes a configured group's incoming messages and
offers two restricted tools for scheduled conversation starters. It uses existing
message filters, Workflow schedules and the gateway push API. The framework contains
no hard-coded group number, persona, silence threshold or model prompt.

## Configuration

Build, check, install and enable the package with the standard plugin commands:

```bash
opsmesh-plugin build sdk/python/examples/group-activity --output ./group-activity.zip
opsmesh-plugin check ./group-activity.zip
opsmesh plugin install ./group-activity.zip
opsmesh plugin enable group-activity
```

Configure the installed plugin with these deployment-specific values:

```json
{
  "channel_id": "<gateway-channel-uuid>",
  "conversation_id": "<official-group-openid>",
  "scheduler_user_id": "<dedicated-platform-admin-user-uuid>",
  "session_ref": "env:OPSMESH_ACTIVITY_SESSION",
  "api_url": "http://127.0.0.1:8000/api/v1",
  "idle_seconds": 1800,
  "cooldown_seconds": 7200,
  "daily_limit": 3,
  "timezone": "Asia/Shanghai",
  "quiet_start_hour": 23,
  "quiet_end_hour": 9
}
```

The conversation ID is the channel's verified external ID, not the numeric QQ group
number. Add `group-activity/message_filter/observe` before the moderation filter in
the channel's `message_filters` list so observed traffic resets the idle timer, even
if moderation subsequently blocks it. Filters remain subject to normal routing and
authorization; traffic the gateway never receives cannot reset this timer.

The credential must resolve to a valid management API login Session owned by the
configured scheduler administrator. Provision and renew it through the deployment's
normal authentication process; the plugin does not create or refresh admin Sessions.
HTTPS is required except for loopback HTTP. Environment proxy settings are ignored
for this management client. Keep these tools and their credential out of group-facing
Agents: group administrators are not platform administrators.

## Scheduled Flow

Use an existing Workflow instance owned by that scheduler account:

1. A Tool node invokes `group-activity/tool/idle_candidate`.
2. Only when its output has `ready: true`, an Agent node generates a short, natural
   conversation starter. The [Eleven persona](eleven.md) is an optional starting point.
3. A Tool node invokes `group-activity/tool/send_idle` with the returned `nonce` and
   generated `text` (at most 160 characters). Pass the nonce through the Workflow,
   rather than asking the model to invent or select it.
4. An existing schedule trigger runs the Workflow, for example every five minutes
   with `*/5 * * * *` in `Asia/Shanghai`.

Both tools require the configured platform administrator and an invocation without
a channel source. Bind them only to this internal Workflow. Use a separate Agent
instance for topic generation; it must not share the group's conversation Session.
No model call is needed for a schedule tick that is not eligible. Incoming chat does
not need Workflow orchestration and continues through the bound Agent target.

## Limits and Delivery

Defaults are 30 minutes of silence, at least two hours between attempts, at most
three attempts per local calendar day and silence from 23:00 to 09:00. Equal quiet
start/end hours disable quiet hours. A new observed human message is required after
each attempt, so the bot never keeps talking to itself in an empty conversation.
The final tool checks eligibility again and skips a stale nonce. Incoming messages
are checked again after resolving the verified destination and before queuing a push;
this reduces races but cannot make remote delivery atomic with new inbound traffic.

SQLite state is created lazily in the plugin data directory with group write access.
All processes operating on the same group must share this directory and its restricted
service group. Existing installations must also grant that group write access to
`activity.sqlite` when upgrading from 0.1.0. Atomic claims prevent concurrent ticks
from consuming the same opportunity. Duplicate filter invocations do not extend the
timer. A failed or uncertain send still consumes its attempt to avoid blind retries;
inspect gateway deliveries for recovery. A successful tool result means **queued**,
not delivered. The group must already have a valid authorized receipt and a channel
adapter with proactive push support. The plugin never accepts a model-selected group.

Installing this example alone does not enable a production schedule, create an
administrator or update an existing Agent's frozen configuration snapshot. Configure
and validate those deployment resources explicitly before enabling its schedule.
