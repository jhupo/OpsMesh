# OpsMesh Plugin SDK

Status: remote connector contracts and signed manifests, 2026-09-18.

This independent Python package does not import the OpsMesh backend. It provides:

- `contracts`: message ingress, remote plugin manifests and capability declarations.
- `client.AutomationClient`: authenticated message submission and event/task state queries.
- `client.AsyncAutomationClient`: asynchronous submission, state queries and live subscriptions.
- `webhooks.verify_delivery`: verification of OpsMesh reply signatures.
- `webhooks.parse_automation_delivery`: authenticated, typed replies scoped to the configured
  workspace and automation; includes progress, pending approvals and monotonic sequence numbers.
- `packages`: Ed25519 signed plugin manifests (install the `signing` extra).

Build a wheel from this repository with `uv build --package opsmesh-plugin-sdk`.
The local build is not a PyPI publication. External connector repositories can install the wheel
with its `signing` extra, without copying the backend source tree.

```python
import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opsmesh_plugin_sdk.contracts import CapabilityDeclaration, PluginManifest
from opsmesh_plugin_sdk.packages import sign_package

# Provision once and keep the private bytes in the publisher's secret manager.
key = Ed25519PrivateKey.generate()
manifest = PluginManifest(
    key="example.order-assistant",
    version="1.0.0",
    name="Order assistant connector",
    capabilities=[
        CapabilityDeclaration(
            key="logs", kind="mcp_server", title="Order log queries",
            required_permissions=["mcp.call"],
        ),
        CapabilityDeclaration(
            key="reply", kind="reply_channel", title="Reply to the sender",
            required_permissions=["messages.send"],
        ),
    ],
)
package = sign_package(manifest, "publisher-2026", key.private_bytes_raw())
public_key = base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii")
# Register this public key through a trusted administrator, and distribute package JSON.
package_json = package.model_dump_json()
```

The platform administrator approves the publisher key for one workspace and exact plugin key,
then binds every capability to a configured resource in that workspace. Declared permissions
never grant agents access automatically. Raw credentials and executable Python entrypoints are
not accepted in the manifest. Remote service deployment remains the connector operator's job;
OpsMesh enable/disable controls platform calls, not that external process.

The complete lifecycle API and version semantics are documented in the repository's
`docs/automation-and-extension-contracts.md`.

## Message collaboration

`AutomationClient.submit(IncomingMessage(...))` returns an accepted inbox event, not a completed
task. Poll `client.state(event.id)` for its task state, pending approval identifiers and bounded
output. Use a stable event_id when retrying the same message.

Construct it with an `httpx.Client` whose base_url includes `/api/v1/`, authentication carries
the automation principal's workspace token, and timeout is explicitly configured. The caller
owns and closes the HTTP client. HTTP failures use HTTPX's standard exceptions; retry submission
with the same message, including its original occurred_at, rather than generating another event.

Messages use an explicit action: start, follow_up, add_instruction, pause, resume or cancel.
Every action except start requires reply_to_event_id from a prior accepted message in the same
automation, conversation and sender scope. Administrators must enable those actions on the
automation. A follow_up on active work adds instructions; on terminal work it creates a new task
with the previous result as context. It does not silently resurrect a terminated task.

For replies, verify raw bytes with `parse_automation_delivery(body, headers, secret=...,
workspace_id=..., automation_id=...)` before reading its typed data. Persist envelope.id for
deduplication and the greatest sequence per data.event_id to reject stale delivery. Persist this
state in the connector's own database; the SDK does not implement the external channel or its
storage. Keep channel authentication and sender identity validation in the external plugin.

Approval notifications are informational. The SDK never converts a channel sender into a
platform approver or automatically approves a request. Do not distribute platform credentials
to end users, and do not import plugin code into the OpsMesh API/Worker host.

## Structured data and live output (2026-09-18)

Configure the automation's input_schema/output_schema and contract_version before submitting.
Only data fields listed in model_input_fields reach the model. Sender/user level claims are not
platform permissions. Select public agent node IDs in stream_output_nodes and opt into
stream_tool_events; both are disabled by default. output_binding can select a completed node's
structured result using the existing workflow binding contract.

```python
from collections.abc import AsyncIterator
from uuid import UUID

import httpx
from opsmesh_plugin_sdk.client import AsyncAutomationClient
from opsmesh_plugin_sdk.contracts import AutomationStreamEvent, IncomingMessage

async def process_message(
    http: httpx.AsyncClient,
    workspace_id: UUID,
    automation_id: UUID,
    message: IncomingMessage,
) -> AsyncIterator[AutomationStreamEvent]:
    # The caller configures base_url=https://platform.example/api/v1/, authentication,
    # and a streaming read timeout, and validates the external sender's identity.
    sdk = AsyncAutomationClient(http, workspace_id, automation_id)
    accepted = await sdk.submit(message)
    async for event in sdk.events(accepted.id):
        # output.text replaces the preview for attempt_id, it is NOT a text delta.
        # output.completed carries the validated business object in data["output"].
        # tool.* carries call_id/name/status, never raw tool arguments or results.
        # Process/deduplicate the event before durably saving event.cursor.
        yield event
```

For reconnection call `events(accepted_event_id, cursor=saved_cursor)`. Normal 55-second server
rollover is automatic; HTTP/network errors propagate to the caller's retry policy. `once=True`
reads one bounded batch. Cursors are opaque and scoped to the accepted event. A missing retained
cursor produces stream.reset: clear previews, apply its state, and consume the retained suffix.
Redis keeps approximately 10,000 task events, not permanent model transcripts. Durable final
results remain available through state queries and configured signed webhooks.

Final output must match the automation's output_schema after redaction. Rejection returns
output.rejected and output_contract_rejected with no business object. Preview text is provisional,
bounded, and disabled for runs with blocking output guardrails. The external plugin owns channel
formatting, throttling and message updates; unsupported channels can display only progress/final
results. No external plugin implementation is included in this package.
