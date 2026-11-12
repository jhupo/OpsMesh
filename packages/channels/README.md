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
Only the Uvicorn lifecycle implementation is shared. There is no `http` module
or `config.http` compatibility setting.

Inputs open after consumer readiness and drain before consumers shut down.
DingTalk's console must use Stream mode. The channel adds cancellable lifecycle
ownership around the SDK's native parsing and acknowledgement behavior.
See the repository's `docs/channels-and-callbacks.md` for configuration details.

MIT; see LICENSE.
