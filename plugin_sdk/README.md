# OpsMesh Plugin SDK

Status: remote connector contracts and signed manifests, 2026-09-18.

This independent Python package does not import the OpsMesh backend. It provides:

- `contracts`: message ingress, remote plugin manifests and capability declarations.
- `client.AutomationClient`: authenticated message submission and event/task state queries.
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
