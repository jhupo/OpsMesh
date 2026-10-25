---
name: adapter-qq
description: Maintain OpsMesh's QQ adapter against Tencent's official qq-botpy SDK.
---

# QQ gateway integration

Read `src/opsmesh/gateway/adapters/qq/SDK.md`, `SDK.json` and the installed
`qq-botpy==1.2.1` source. The SDK is imported as `botpy` from
https://github.com/tencent-connect/botpy.

- `QQAdapter` directly inherits the OpsMesh Adapter in `adapters/base.py`.
- Let botpy own WebSocket discovery, authentication, heartbeats, reconnects,
  event parsing and native API requests. Do not copy a protocol implementation
  or add a webhook transport that the official SDK does not provide.
- Convert only official botpy callback objects at the host boundary. Preserve
  the JSON-safe native object fields in normalized event `data`.
- Expose a fixed operation catalogue backed by official `botpy.api.BotAPI`
  methods. Reject unknown operations and worker filesystem paths.
- Keep QQ credentials in `QQConfig`; never include secrets in capabilities,
  events or logs.
- Do not add another platform framework, compatibility aliases or local QQ
  protocol model trees.
- Tests and real platform calls require explicit user authorization. Report
  unverified scopes honestly.
