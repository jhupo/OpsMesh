# 完整接口参考

由 `opsmesh.bootstrap.api` 实际注册的路由和 OpenAPI 自动生成。

共 **225 个接口**、**184 个路径**、**218 个模型**。默认前缀 `/api/v1`。

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

[OpenAPI JSON](../openapi.json) · [全部数据模型](schemas.md)

| 接口分组 | 接口数 |
| --- | --- |
| [capabilities](capabilities.md) | 12 |
| [capabilities-publication-reviews](capabilities-publication-reviews.md) | 6 |
| [channels](channels.md) | 18 |
| [extensions](extensions.md) | 23 |
| [identity](identity.md) | 26 |
| [orchestration](orchestration.md) | 92 |
| [platform](platform.md) | 27 |
| [resources](resources.md) | 21 |
