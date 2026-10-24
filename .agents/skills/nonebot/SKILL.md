---
name: nonebot
description: Review or extend NoneBot transport integration, driver composition, adapter lifecycle, event/message bridges and per-channel configuration isolation in OpsMesh.
---

# NoneBot gateway integration

Read `docs/gateway-architecture.md` and the installed NoneBot version in `uv.lock`. Official source: https://github.com/nonebot/nonebot2.

- Reuse Driver/Mixin for transport and maintained Adapter/Bot implementations for platform protocols. Do not duplicate authentication, heartbeat, reconnect or signature verification.
- NoneBot core is not a bundle of every platform. Verify each adapter's maintenance status, declared dependencies and platform permissions. Keep vendor SDK integrations when an ecosystem adapter is unavailable.
- Bridge native Event/Message/MessageSegment into the existing SDK ChannelAdapter contract. Preserve platform payloads and encrypted reply targets; map each conversation namespace deliberately.
- Native plugins/global initialization can read process-wide configuration. Verify per-channel injection or process isolation before enabling a new adapter. Do not mix credentials across channels or silently fall back to another transport.
- Use the gateway worker for connections and native calls. Preserve durable events, identities, leases, outbox, operation receipts, authorization and task isolation. Native matchers must not bypass these contracts.
- Follow AGENTS.md for checks and explicit test authorization. Platform mocks are not real delivery acceptance.
