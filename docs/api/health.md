# health

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/health` | Health Check |
| GET | `/api/v1/health/live` | Liveness Check |
| GET | `/api/v1/health/ready` | Readiness Check |
| GET | `/api/v1/health/startup` | Startup Check |

## GET `/api/v1/health`

Health Check

Operation ID：`health_check_api_v1_health_get`。

实现：[src/opsmesh/platform/health/routes.py](../../src/opsmesh/platform/health/routes.py) · `health_check`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

参数：无显式路径、查询或 Header 参数。

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [HealthResponse](schemas.md#schema-HealthResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/health/live`

Liveness Check

Operation ID：`liveness_check_api_v1_health_live_get`。

实现：[src/opsmesh/platform/health/routes.py](../../src/opsmesh/platform/health/routes.py) · `liveness_check`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

参数：无显式路径、查询或 Header 参数。

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [HealthResponse](schemas.md#schema-HealthResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/health/ready`

Readiness Check

Operation ID：`readiness_check_api_v1_health_ready_get`。

实现：[src/opsmesh/platform/health/routes.py](../../src/opsmesh/platform/health/routes.py) · `readiness_check`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

参数：无显式路径、查询或 Header 参数。

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ReadinessResponse](schemas.md#schema-ReadinessResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/health/startup`

Startup Check

Operation ID：`startup_check_api_v1_health_startup_get`。

实现：[src/opsmesh/platform/health/routes.py](../../src/opsmesh/platform/health/routes.py) · `startup_check`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

参数：无显式路径、查询或 Header 参数。

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ReadinessResponse](schemas.md#schema-ReadinessResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
