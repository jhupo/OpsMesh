# TypeSafe Moderation Plugin

This optional adapter submits each original message to `jev-latest` through the
public System One HTTP endpoint. It only checks configured moderation categories.
It does not decide relevance, reply triggers, Agent selection or tool permissions.
The core framework has no TypeSafe dependency or embedded credentials.

The package ID remains `typesafe-decision`; version 0.1.2 contributes
`typesafe-decision/message_filter/moderate`. Replace the old decision Workflow
binding with a channel filter binding. No Workflow is required for a single Agent.

## Install and Configure

```bash
opsmesh-plugin build sdk/python/examples/typesafe-decision --output ./moderation.zip
opsmesh-plugin check ./moderation.zip
opsmesh plugin install ./moderation.zip
opsmesh plugin enable typesafe-decision
```

Configure the installed plugin with credential references:

```json
{
  "api_key_ref": "env:TYPESAFE_API_KEY",
  "model": "jev-latest",
  "network_mode": "proxy",
  "proxy_ref": "env:TYPESAFE_PROXY_URL",
  "timeout_seconds": 4,
  "threshold": 0.95,
  "blocked_action": "recall",
  "categories": {
    "advertising": "Unsolicited advertising, scams, or commercial spam.",
    "violence": "Threats or incitement to violent wrongdoing.",
    "sexual": "Prohibited sexually explicit content or sexual exploitation."
  }
}
```

Set the referenced environment variables in each Gateway process, or use managed
`secret:<id>` references. Proxy URLs require an HTTP(S) scheme and explicit port.
For direct access use `network_mode: "direct", proxy_ref: null`; environment mode
also uses a null proxy_ref. This client's routing is independent of QQ and Agent
model providers. The adapter uses host-provided httpx; setup does not connect.

## Bind the Channel

Add these fields to the existing channel policy, preserving other settings:

```json
{
  "message_filters": ["typesafe-decision/message_filter/moderate"],
  "filter_max_attempts": 3,
  "filter_retry_seconds": 1,
  "debounce_seconds": 0.4,
  "batch_max_wait_seconds": 2,
  "batch_max_messages": 20
}
```

For a selected group, `allow_recall: true` is required for recall. The channel
adapter must support recall and the platform must grant the bot permission.
The group and message ID come from the original receipt; the model cannot select
another message. Failed moderation never becomes an automatic recall or allow.

The adapter has one HTTP attempt per invocation. The host retries transient or
malformed evaluations within the configured attempt count, then marks the receipt
blocked for administrative recovery. Credential resolution and HTTP/media work have
bounded timeouts. A category score at or above the threshold selects blocked_action.
Text and image attachments are inspected. Unsupported audio/video/files fail closed;
use another filter adapter if the deployment must admit those formats.

## Reply and Identity Policy

Configure an explicit shared group route with the observed conversation key, an
authorized restricted platform account and an Agent instance. Members keep their
own external IDs and roles without each registering a platform account.

The default group activation only admits mentions to the Agent. To let an Agent
decide whether names or follow-ups need a reply, explicitly configure that group
with `visibility: "all", activation: "always"` and `session_scope: "group"`.
The platform must actually provide all-message events. Configure the Agent's own
instructions and structured output schema to return a typed gateway reply or ignore,
then select route `output_mode: "message"`. The schema is GatewayReply in the host
contract; ordinary text routes do not interpret JSON as control instructions.

Messages are moderated individually, queued, and batched without merging sender
identities. New receipts during an active Agent Run form its next batch. Native
Agents SDK Session storage owns the transcript. The gateway does not reconstruct
past exchanges or give platform-supplied history an unaudited path to the model.

For multiple participants the invocation source contains every sender and role;
its top-level external identity/role is empty. A plugin must not infer that all
batched messages have one administrator's authority. Group roles never grant
platform permissions. Classification scores are not authorization or OpenAI billing
receipts. This example does not imply a real API, proxy or QQ acceptance result.

Protocol reference: [TypeSafe Python SDK](https://docs.typesafe.ai/sdk/python/api/clients/async).
The adapter follows the System One request/response contract in SDK 0.7.5, with
strict numeric validation and bounded responses; the vendor package is not bundled.
