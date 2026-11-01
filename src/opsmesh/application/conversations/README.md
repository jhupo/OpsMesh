# Reviewed conversations

`ConversationApplication` composes the platform's authorized bot delivery, durable Runs,
sessions, knowledge management and Driver lifecycle. It does not provision another Agent,
load application plugins, or select a bot/model vendor.

An application declares a `ConversationGateway` and a `ConversationPolicy` implementing
`review` through the public application's `add_conversation`. At startup the gateway
resolves the platform BotRoute and published Agent into `ConversationOptions`; it does
not persist another copy of the Agent, workspace or route. Lower-level composition can
still supply explicit `ConversationBinding` objects. The policy can call the same
custom tool registered for the Agent.
Use a bot route with `dispatch_mode="managed"` so an absent handler cannot fall back to an
unreviewed automatic Agent reply.

The pipeline persists every accepted event before acknowledgement, reviews messages on
an independent lane, claims one immutable batch per channel, and submits every approved
text batch to the bound Agent. The Agent returns ordinary text, with an empty answer
leaving the channel silent; no JSON output schema or reply flag is required. Moderation
does not classify reply intent. New arrivals wait for another batch. Output must pass review
before the platform sends it. Runs never receive an automatic `reply_target`; durable
delivery keys prevent confirmed sends from repeating after local recovery. An uncertain
provider delivery or message removal is not blindly repeated. Adapter implementations
provide `OpsMeshBot.remove_message`; unsupported adapters report an explicit error.

Chat claims the entire currently approved backlog that fits the estimated context budget;
there is no fixed message-count cap. Each model call handles one combined transcript.
After a batch finishes, the same lane immediately claims new arrivals without another
poll/debounce delay. Only the transition from idle waits briefly to collect nearby messages.
Excess context remains queued for the next combined batch; messages are never silently
discarded. Budget estimation includes event envelopes and bounded attachment descriptions.
Individual oversize messages retain the existing review state. Input moderation still
checks each source message independently so removal decisions identify the exact message.
`activity_history_limit` limits proactive history only; it does not limit chat batches.

Approval pauses retain the active batch and its original Run. Poll deadlines survive
restarts and back off to 30 seconds; approval resumes output review and delivery without
another model submission. A confirmed send awaiting local finalization also stays active
and recovers through the same durable delivery key. Other explicitly retryable errors have
at most eight attempts with delays capped at five minutes. Unknown delivery outcomes and
terminal Run states do not trigger automatic replay. Pending approval continues to block
project reconfiguration through the existing active-batch check.

Proactive participation is opt-in per binding. It shares the channel's chat lock, requires
recent approved human activity, respects configured quiet hours, and consumes a persisted
daily quota/cooldown before external calls. Restart establishes a fresh cooldown.
Chat and proactive Runs share the same group Session for a published Agent binding.
Both paths use the same transcript serializer, preserving timestamps and a stable
participant alias across batches without exposing provider user identifiers. Aliases
include the bot and channel scope, so another group gets unrelated participant labels.
Different groups, principals and Agent revisions remain isolated; each Run retains its
own idempotency key. Publishing a different revision starts a new conversation generation.

Optional image analysis accepts only configured HTTPS hosts and a bounded image count.
Attachment metadata is persisted before enqueueing. A separate lane runs the same bound
Agent in an operation-specific session with tools disabled, then reviews its untrusted
description before admitting it to chat. The image Run never replies directly. Completed
analysis is reused on replay; unsupported attachments or terminal analysis failures require
review. Transport support alone does not imply audio or video understanding. Retention
removes attachment metadata and analysis only after the source inbox event is purged.

If `knowledge_collection` is configured, only a reviewed reply with confirmed delivery is
eligible for capture. The public knowledge API stores the original question, delivered
answer, and source event IDs under a stable ID derived from the Run. It excludes sender
IDs and marks the record as raw, non-authoritative conversation. If the published Agent
binds a memory namespace, the same confirmed transcript is submitted as an idempotent
Memory source and generation request. Namespace policy selects metered extraction and
consolidation models; generation remains a platform background task. Automatic capture
does not enable direct model writes, and unreviewed or undelivered outputs are excluded.
Knowledge records and consolidated Memory retain distinct storage and permissions.
There is no application-owned FAQ Agent or workspace knowledge mirror. Capture failures
do not block chat: transient errors back off for up to one hour, permanent failures move
to `conversation-capture-failed:*`, and source batches remain available for inspection.
Capture pages advance fairly even when an earlier page is deferred.

Inbox persistence uses the shared platform SQLAlchemy database, supporting SQLite and
PostgreSQL. Each gateway has a stable inbox scope and one local consumer protected by
an OS process lock. Cross-host consumer leadership is not implemented; PostgreSQL
storage alone does not enable several hosts to own the same gateway.
Active batches and pending captures retain their binding identity across restarts. Changing
their Agent revision, principal, workspace, collection, or removing the binding is rejected
until that work is finished or explicitly reconciled. Older standalone example queue
databases require explicit offline migration; no legacy fallback or automatic adoption
is provided.

`tests/application/test_conversation_*` covers review/removal failures, immutable batch
boundaries, private sender redaction, output rejection, durable Run/delivery reuse, startup
and shutdown, cooldowns, capture retry/dead-letter behavior and configuration changes.
