# QQ SDK provenance

OpsMesh uses Tencent's official `qq-botpy` package, version 1.2.1. The package
is imported as `botpy` and owns QQ gateway WebSocket handling, event objects and
the native `BotAPI`. OpsMesh only adapts those public SDK callbacks to the host
gateway contract; it does not copy or reimplement the QQ protocol.

Upstream: https://github.com/tencent-connect/botpy
