# 完整接口参考

由 `opsmesh.main` 实际注册的路由和 OpenAPI 自动生成。

共 **42 个接口**、**33 个路径**、**37 个模型**。默认前缀 `/api/v1`。

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

[OpenAPI JSON](../openapi.json) · [全部数据模型](schemas.md)

| 接口分组 | 接口数 |
| --- | --- |
| [governance](governance.md) | 11 |
| [identity](identity.md) | 23 |
| [platform](platform.md) | 8 |
