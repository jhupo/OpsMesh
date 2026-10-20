# extensions

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/extensions/deployments/{deployment_id}` | Get Deployment |
| POST | `/api/v1/extensions/deployments/{deployment_id}/stop` | Stop Deployment |
| GET | `/api/v1/extensions/installations` | Installations |
| POST | `/api/v1/extensions/installations` | Install |
| GET | `/api/v1/extensions/installations/{installation_id}` | Installation |
| POST | `/api/v1/extensions/installations/{installation_id}/disable` | Disable |
| POST | `/api/v1/extensions/installations/{installation_id}/enable` | Enable |
| POST | `/api/v1/extensions/installations/{installation_id}/uninstall` | Uninstall |
| POST | `/api/v1/extensions/instances` | Create Instance |
| GET | `/api/v1/extensions/instances/{instance_id}` | Get Instance |
| PUT | `/api/v1/extensions/instances/{instance_id}` | Update Instance |
| POST | `/api/v1/extensions/instances/{instance_id}/deployments` | Start Deployment |
| POST | `/api/v1/extensions/instances/{instance_id}/invocations` | Invoke |
| GET | `/api/v1/extensions/invocations/{invocation_id}` | Get Invocation |
| POST | `/api/v1/extensions/invocations/{invocation_id}/cancel` | Cancel |
| GET | `/api/v1/extensions/invocations/{invocation_id}/host-calls` | Host Calls |
| POST | `/api/v1/extensions/invocations/{invocation_id}/host-calls/{receipt_id}/reconcile` | Reconcile Host Call |
| POST | `/api/v1/extensions/invocations/{invocation_id}/reconcile` | Reconcile |
| POST | `/api/v1/extensions/namespaces/{namespace_id}/cleanup` | Cleanup |
| GET | `/api/v1/extensions/namespaces/{namespace_id}/cleanup-preview` | Preview |
| POST | `/api/v1/extensions/namespaces/{namespace_id}/restore` | Restore State |
| GET | `/api/v1/extensions/namespaces/{namespace_id}/snapshot` | Snapshot State |
| GET | `/api/v1/extensions/namespaces/{namespace_id}/state` | Export State |

## GET `/api/v1/extensions/deployments/{deployment_id}`

Get Deployment

Operation ID：`extensions.deployments.get`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `get_deployment`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `deployment_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [DeploymentView](schemas.md#schema-DeploymentView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/extensions/deployments/{deployment_id}/stop`

Stop Deployment

Operation ID：`extensions.deployments.stop`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `stop_deployment`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `deployment_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/RevisionRequest"
}
```

模型：[RevisionRequest](schemas.md#schema-RevisionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 202 | Successful Response | application/json | [DeploymentView](schemas.md#schema-DeploymentView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/extensions/installations`

Installations

Operation ID：`extensions.installations.list`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `installations`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `limit` | query | 否 | integer | `default=50` |
| `offset` | query | 否 | integer | `default=0` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[InstallationView](schemas.md#schema-InstallationView)&gt; |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/extensions/installations`

Install

Operation ID：`extensions.installations.create`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `install`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/InstallRequest"
}
```

模型：[InstallRequest](schemas.md#schema-InstallRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 202 | Successful Response | application/json | [InstallationView](schemas.md#schema-InstallationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/extensions/installations/{installation_id}`

Installation

Operation ID：`extensions.installations.get`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `installation`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `installation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [InstallationView](schemas.md#schema-InstallationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/extensions/installations/{installation_id}/disable`

Disable

Operation ID：`extensions.installations.disable`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `disable`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `installation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/RevisionRequest"
}
```

模型：[RevisionRequest](schemas.md#schema-RevisionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [InstallationView](schemas.md#schema-InstallationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/extensions/installations/{installation_id}/enable`

Enable

Operation ID：`extensions.installations.enable`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `enable`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `installation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/RevisionRequest"
}
```

模型：[RevisionRequest](schemas.md#schema-RevisionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [InstallationView](schemas.md#schema-InstallationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/extensions/installations/{installation_id}/uninstall`

Uninstall

Operation ID：`extensions.installations.uninstall`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `uninstall`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `installation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/RevisionRequest"
}
```

模型：[RevisionRequest](schemas.md#schema-RevisionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 202 | Successful Response | application/json | [InstallationView](schemas.md#schema-InstallationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/extensions/instances`

Create Instance

Operation ID：`extensions.instances.create`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `create_instance`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/InstanceConfiguration"
}
```

模型：[InstanceConfiguration](schemas.md#schema-InstanceConfiguration)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [InstanceView](schemas.md#schema-InstanceView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/extensions/instances/{instance_id}`

Get Instance

Operation ID：`extensions.instances.get`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `get_instance`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `instance_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [InstanceView](schemas.md#schema-InstanceView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## PUT `/api/v1/extensions/instances/{instance_id}`

Update Instance

Operation ID：`extensions.instances.update`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `update_instance`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `instance_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/InstanceUpdate"
}
```

模型：[InstanceUpdate](schemas.md#schema-InstanceUpdate)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [InstanceView](schemas.md#schema-InstanceView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/extensions/instances/{instance_id}/deployments`

Start Deployment

Operation ID：`extensions.deployments.create`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `start_deployment`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `instance_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/DeploymentRequest"
}
```

模型：[DeploymentRequest](schemas.md#schema-DeploymentRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 202 | Successful Response | application/json | [DeploymentView](schemas.md#schema-DeploymentView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/extensions/instances/{instance_id}/invocations`

Invoke

Operation ID：`extensions.invocations.create`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `invoke`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `instance_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/InvokeRequest"
}
```

模型：[InvokeRequest](schemas.md#schema-InvokeRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 202 | Successful Response | application/json | [InvocationView](schemas.md#schema-InvocationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/extensions/invocations/{invocation_id}`

Get Invocation

Operation ID：`extensions.invocations.get`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `get_invocation`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `invocation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [InvocationView](schemas.md#schema-InvocationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/extensions/invocations/{invocation_id}/cancel`

Cancel

Operation ID：`extensions.invocations.cancel`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `cancel`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `invocation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/RevisionRequest"
}
```

模型：[RevisionRequest](schemas.md#schema-RevisionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [InvocationView](schemas.md#schema-InvocationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/extensions/invocations/{invocation_id}/host-calls`

Host Calls

Operation ID：`extensions.invocations.host_calls.list`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `host_calls`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `invocation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[HostCallView](schemas.md#schema-HostCallView)&gt; |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/extensions/invocations/{invocation_id}/host-calls/{receipt_id}/reconcile`

Reconcile Host Call

Operation ID：`extensions.invocations.host_calls.reconcile`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `reconcile_host_call`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `invocation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `receipt_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ExtensionReconcileRequest"
}
```

模型：[ExtensionReconcileRequest](schemas.md#schema-ExtensionReconcileRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [HostCallView](schemas.md#schema-HostCallView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/extensions/invocations/{invocation_id}/reconcile`

Reconcile

Operation ID：`extensions.invocations.reconcile`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `reconcile`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `invocation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ExtensionReconcileRequest"
}
```

模型：[ExtensionReconcileRequest](schemas.md#schema-ExtensionReconcileRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [InvocationView](schemas.md#schema-InvocationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/extensions/namespaces/{namespace_id}/cleanup`

Cleanup

Operation ID：`extensions.state.cleanup`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `cleanup`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `namespace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/CleanupRequest"
}
```

模型：[CleanupRequest](schemas.md#schema-CleanupRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [CleanupResult](schemas.md#schema-CleanupResult) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/extensions/namespaces/{namespace_id}/cleanup-preview`

Preview

Operation ID：`extensions.state.cleanup_preview`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `preview`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `namespace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [CleanupPreview](schemas.md#schema-CleanupPreview) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/extensions/namespaces/{namespace_id}/restore`

Restore State

Operation ID：`extensions.state.restore`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `restore_state`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `namespace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/StateRestoreRequest"
}
```

模型：[StateRestoreRequest](schemas.md#schema-StateRestoreRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [StateRestoreResult](schemas.md#schema-StateRestoreResult) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/extensions/namespaces/{namespace_id}/snapshot`

Snapshot State

Operation ID：`extensions.state.snapshot`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `snapshot_state`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `namespace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [StateSnapshot](schemas.md#schema-StateSnapshot) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/extensions/namespaces/{namespace_id}/state`

Export State

Operation ID：`extensions.state.export`。

实现：[src/opsmesh/extensions/routes.py](../../src/opsmesh/extensions/routes.py) · `export_state`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `namespace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `cursor` | query | 否 | string | `default=""` |
| `limit` | query | 否 | integer | `default=100` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [StatePage](schemas.md#schema-StatePage) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。
