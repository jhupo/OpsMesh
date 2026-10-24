---
name: adapter-qq
description: Implement or review QQ gateway behavior using nonebot-adapter-qq, including native drivers, intents, events, rich messages, webhook verification, APIs and reconnect lifecycle.
---

# QQ gateway integration

Read `src/opsmesh/gateway/adapters/qq/` and the installed `nonebot.adapters.qq` implementation. Resolve versions in `uv.lock`; upstream sources are https://github.com/nonebot/adapter-qq and https://github.com/nonebot/nonebot2.

- Use the native adapter for access tokens, WebSocket identify/resume/heartbeat/sharding, event parsing, message audit handling and signed webhook validation. Do not implement the protocol or retain botpy shims.
- Drivers need HTTP client and WebSocket client support. Webhook mode also needs ASGI support. Run their lifecycle inside the independent Gateway Worker, never the business API process.
- Keep configuration per channel. Embed an explicit native driver and adapter configuration instead of calling process-global NoneBot initialization. Do not leak one channel's credentials into another.
- Normalize C2C, group, guild channel and guild DM separately. Guild DM is a subtype of guild message: check it first. Prefix conversation IDs to prevent collisions and keep reply routing secrets encrypted.
- Use native Message/MessageSegment serialization for text, attachments, markdown, keyboard, ark, embed, media and references. Preserve original event payloads; unsupported message bodies must not become silently empty successful tasks.
- Discover callable QQ APIs from the SDK's `API` descriptors, validate parameters against native signatures, and execute only in the connection-owning worker. Availability in the SDK is not a grant of QQ platform permission.
- Persist event deduplication and outbound operation state. SDK background dispatch acknowledges independently of the database; document that boundary. Uncertain external sends require an explicit retry.
- External QQ group roles are informational, not OpsMesh administrator roles. Never log tokens, raw messages, signatures or request payloads.

For checks, follow AGENTS.md and current user authorization. Live acceptance needs real QQ application credentials and approved scopes; mocks do not count as a platform test.
