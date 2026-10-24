# TypeSafe Moderation Plugin

This optional adapter submits each original message to `jev-latest` through the
public System One HTTP endpoint. It only checks configured moderation categories.
It does not decide relevance, reply triggers, Agent selection or tool permissions.
The core framework has no TypeSafe dependency or embedded credentials.

The package ID remains `typesafe-decision`; version 0.2.0 contributes
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
  "proxy_timeout_action": "allow",
  "threshold": 0.85,
  "category_thresholds": {},
  "rejection_instructions": "Generate a short, clear refusal in a warm, lively voice. Name the concern without quoting the input. Do not claim that removal succeeded. Do not ridicule the sender.",
  "recall_blocked": true
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
  "rejection_agent_id": "<dedicated-text-only-agent-instance-uuid>",
  "rejection_timeout_seconds": 20,
  "filter_max_attempts": 3,
  "filter_retry_seconds": 1,
  "debounce_seconds": 0.4,
  "batch_max_wait_seconds": 2,
  "batch_max_messages": 20
}
```

For a selected group, configure `management.actions: ["recall"]` and a grant for
`typesafe-decision/message_filter/moderate` with `actions: ["recall"], automatic: true`.
The channel adapter must implement recall and the platform must grant permission.
The group and message ID come from the original receipt; the model cannot select
another message. Failed moderation never becomes an automatic recall.

The adapter has one HTTP attempt per invocation. `proxy_timeout_action` defaults to
`"block"`. Setting it to `"allow"` explicitly lets a proxy-mode HTTP request timeout
continue to the chat Agent after the first timed-out attempt. The plugin logs a
warning and returns `action: "allow", reason: "moderation_proxy_timeout"` without
scores, recall or refusal; the receipt records this as skipped moderation, not a
successful classification. Normal valid responses still use the configured threshold.
Direct/environment-mode timeouts, media preparation failures, credential errors,
HTTP errors and malformed evaluations still fail admission. The host retries these
within the configured attempt count, then marks the receipt blocked for administrative
recovery. Credential resolution and HTTP/media work have bounded timeouts.
A category score at or above the threshold returns drop; recall_blocked optionally
adds a typed RecallMessage operation for a group or guild message.
`category_thresholds` overrides the global threshold for named categories. Values
in this example illustrate configuration and are not calibrated accuracy claims.
Omitting `categories` enables all ten defaults: advertising, violence, sexual content,
self-harm, jailbreak, political sensitivity, non-violent illegal activity, personal
information misuse, unethical conduct and copyright infringement. This includes all
nine categories shown in the Qwen3Guard screenshot, regardless of its checkbox state.
Neutral discussion, prevention, authorized use and help-seeking are distinguished
from harmful requests in the category descriptions. The default threshold is **0.85**
for every category; a score equal to 0.85 triggers the action. Explicitly replacing
`categories` changes the coverage, so omit it to retain all defaults.

The plugin supplies bounded `MessageFilterResult.rejection_input` containing trusted
instructions and the qualifying category names. It does not forward the rejected
input to the refusal model. Configure a dedicated text-only Agent instance owned by
the channel target's platform account: no tools, MCP, sandbox, handoffs, callbacks, guardrails,
structured output or remote prompts. Give it trusted refusal instructions and a model
provider. `rejection_agent_id` refers to that existing instance, not an Agent definition.
The gateway starts an ordinary bounded execution Run without a Session. The refusal
is generated independently of the group's chat history; it never enters the chat Agent.
Wording, category definitions and thresholds belong to the deployment.

Use a host and plugin SDK supporting typed conversation management before installing this version.
The recall is durably queued when the denial is recorded, before refusal generation.
The generated notice is an independent message delivery; a failed or uncertain recall
cannot block it. Generation failure leaves the input blocked; an existing failed
generation Run requires execution recovery rather than replaying the original input.
Neither model wording nor a queued delivery proves successful removal. This is a source
change, not proof of live QQ acceptance.
Text and image attachments are inspected. Unsupported audio/video/files fail closed;
use another filter adapter if the deployment must admit those formats.

## Reply and Identity Policy

Bind the channel's single `target` to an existing Agent or Workflow instance and
an explicitly limited platform `user_id`. All private conversations and allowed
groups use that target. List allowed external group IDs in `policy.groups`;
member account bindings are optional and do not select the execution principal.
Private users have separate Sessions; each group shares one Session.
To let the Agent decide whether to join a conversation, configure that group
with `visibility: "all", activation: "always"`.
The platform must actually provide all-message events. Configure the Agent's own
instructions and structured output schema to return a typed gateway reply or ignore,
then select target `output_mode: "message"`. The schema is GatewayReply in the host
contract; ordinary text targets do not interpret JSON as control instructions.

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

## Group Conversation Guidance

Reply timing and persona belong to the Agent instructions or an optional business
plugin. They do not belong in this moderation adapter. A single model request can
return the existing typed `reply` or `ignore` result; adding another serial planner
request is optional and increases the number of network calls.

Instructions should identify the addressee from mentions, references, sender changes
and the current topic. A short acknowledgement such as "wait a moment" does not by
itself invite another response. Allow an occasional concise interjection when the
assistant can answer an open question or provide specific relevant help, while
avoiding interruptions of clearly directed human conversations. A request to stop
participating should be respected until the assistant is invited again. Do not make
the model claim access to tools or group management actions it has not been granted.

References consulted for these design choices:

- [MaiBot chat and reply behavior](https://docs.mai-mai.org/en/faq/chat-and-reply)
- [AstrBot Social Context plugin](https://github.com/kazamisama/astrbot_plugin_social_context)
- [Qwen3Guard model card](https://huggingface.co/Qwen/Qwen3Guard-Gen-8B)

These are sources of design ideas, not dependencies or evidence that OpsMesh
implements their planners, statistics, or full feature sets.

For a deployment-owned lively group persona see [Eleven](../group-activity/eleven.md).
For quiet-time conversation starters see [Group Activity](../group-activity/README.md).
