# opsmesh-channels

Independent OpsMesh plugin for HTTP API, QQ and DingTalk channels. The import module and host configuration namespace are `opsmesh_channels`.

The plugin translates authenticated external requests into framework events, messages and `UserContext`. Business plugins declare routes, commands and bot connections. Channels does not select agents or manage conversations.

## Installation

In a regular Python environment, install the matching `opsmesh` and `opsmesh-channels` wheels from the same release. For local development:

```bash
python -m pip install -e . -e packages/channels
```

Run this command from the repository root. A frozen Linux host instead uses the separate official plugin runtime archive; the framework installer does not install this plugin.

## Use in a plugin

```python
from opsmesh.framework.plugins import require

channels = require("opsmesh_channels")


@channels.api.get("/status")
async def status() -> dict[str, str]:
    return {"status": "ready"}
```

The host manifest must allow `opsmesh_channels` and the consumer module. API configuration belongs to channels and must provide authenticated identities. QQ and DingTalk connections are declared by the consuming plugin; protocol settings remain in the channels configuration.

The channels runtime ID is `opsmesh_channels`. Old `opsmesh.plugins.channels` imports and configuration namespaces are not aliases.

See the repository's `docs/channels-and-callbacks.md` for message handling, response ownership, streaming cleanup and the in-memory bot queue boundary. API, QQ and DingTalk are protocol modules within this single distribution, not three separately loaded plugins.

## License

MIT; see the package's included license files and third-party notices.
