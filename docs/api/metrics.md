# metrics

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/metrics` | Metrics |

## GET `/api/v1/metrics`

Metrics

Operation ID：`metrics_api_v1_metrics_get`。

实现：[src/opsmesh/platform/health/metrics_routes.py](../../src/opsmesh/platform/health/metrics_routes.py) · `metrics`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

参数：无显式路径、查询或 Header 参数。

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | text/plain | string |
| 422 | Request validation failed | text/plain | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
