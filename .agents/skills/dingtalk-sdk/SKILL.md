---
name: dingtalk-sdk
description: Implement or review DingTalk Stream gateway events, robot messages, cards, media, OpenAPI calls, credentials and connection lifecycle against the locked dingtalk-stream SDK.
---

# DingTalk integration

Read `src/opsmesh/gateway/adapters/dingtalk/` and the installed `dingtalk_stream` source. Resolve its exact version from `uv.lock`, including prerelease status. Official upstream: https://github.com/open-dingtalk/dingtalk-stream-sdk-python and https://opensource.dingtalk.com/developerpedia/docs/learn/stream/overview.

- Delegate Stream authentication, subscriptions, frame parsing, ACKs, heartbeats, reconnect and shutdown to the native client. Register robot, event, card and graph callbacks deliberately; a callback ACK is not an Agent task result.
- Use native ChatbotMessage parsing for text/image/richText and retain file/audio/video or future fields as structured payloads. Preserve sender/conversation identifiers; never treat `isAdmin` as a platform role.
- Session webhook URLs and expiry are secrets. Validate HTTPS, exact host/path, no redirects and expiration before sending. Handle both HTTP errors and platform error responses.
- Native helper methods sometimes swallow errors, use blocking requests or return `None` on both success and failure. Inspect each method before using it for durable delivery. Use strict HTTP responses for OpenAPI operations where the helper cannot establish success; document this reason.
- Use the SDK's access-token cache. Keep credentials in the connection worker, inject authorization for fixed official API hosts, and reject caller-supplied auth headers, arbitrary URLs and filesystem paths.
- Cover session replies, proactive messages, cards and updates, media upload/download metadata, event callbacks and graph callbacks. Match each API's required application scopes; do not claim access merely because a method exists.
- Expose only the declared operation catalogue with strict parameter/result schemas; never expose api.request or caller-selected HTTP routing.
- Bound payload sizes/timeouts, persist operation receipts, and mark transport-loss side effects uncertain rather than silently retrying.

Follow repository authorization for tests. Real platform verification requires a DingTalk app, Stream mode and granted scopes. Record exactly which paths were exercised.

- DingTalkAdapter directly inherits the shared OpsMesh Adapter. Its official SDK handlers publish the gateway Event through the same durable sink. Keep events.py and messages.py for platform mapping; do not add native/Adapter, a protocol wrapper or an unused ASGI driver.
