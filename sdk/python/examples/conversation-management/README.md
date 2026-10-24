# Conversation Management Plugin

The `conversation-management/tool/manage` resource submits one typed operation per
tool invocation or reads an existing receipt using `operation_id`. It uses the
host-injected management capability; it has no API client or channel credentials.

Build and install this directory with the normal plugin commands. Bind
`conversation-management/tools/manage` to the Agent and grant the resource
`conversation-management/tool/manage` in the current group's management policy.
Agent tool binding IDs use `tools`, while installation resource IDs use `tool`.
Select the allowed actions, permitted external roles and maximum mute duration.
See the [management policy example](../../../../src/opsmesh/gateway/management/README.md).

Supply `request` with an action and a ConversationRef from the trusted invocation
source, or supply `operation_id` to inspect its receipt. The host limits requests
to the source conversation and rejects batches with multiple senders. It refreshes
the requester's platform group role before executing an operation. External group
roles never grant OpsMesh platform administration permissions.

Only a succeeded receipt proves completion. Awaiting approval, pending and uncertain
receipts must be described with their actual status. Reuse the returned operation ID
to inspect progress. A new submission is a new effect; do not resubmit uncertain
mutations to work around recovery. Destructive actions require the configured
platform administrator approval.
