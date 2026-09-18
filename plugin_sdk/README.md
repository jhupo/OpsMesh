# OpsMesh Plugin SDK

Status: remote connector contracts and signed manifests, 2026-09-18.

This independent Python package does not import the OpsMesh backend. It provides:

- `contracts`: message ingress, remote plugin manifests and capability declarations.
- `client.AutomationClient`: authenticated submission of connector messages.
- `webhooks.verify_delivery`: verification of OpsMesh reply signatures.
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
