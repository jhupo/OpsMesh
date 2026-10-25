# Gateway design

QQ and DingTalk adapters implement the common OpsMesh `Adapter` contract. QQ
uses Tencent's official `qq-botpy==1.2.1` client for WebSocket discovery,
authentication, heartbeats, reconnects, event parsing and native API requests.
The host normalizes official callback objects and persists them through the
gateway sink; it does not carry a second QQ protocol implementation.

DingTalk uses its locked official Stream client for callbacks and the strict
host-owned OpenAPI boundary for operations. Both adapters keep credentials and
platform routing inside the gateway worker, expose fixed operation catalogues,
and mark ambiguous outbound results as uncertain instead of retrying silently.
