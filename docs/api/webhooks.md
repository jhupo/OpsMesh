# webhooks

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/webhook-subscriptions` | List Webhook Subscriptions |
| POST | `/api/v1/workspaces/{workspace_id}/webhook-subscriptions` | Create Webhook Subscription |
| PATCH | `/api/v1/workspaces/{workspace_id}/webhook-subscriptions/{subscription_id}` | Update Webhook Subscription |
| GET | `/api/v1/workspaces/{workspace_id}/webhook-subscriptions/{subscription_id}/delivery-attempts` | List Webhook Delivery Attempts |
| POST | `/api/v1/workspaces/{workspace_id}/webhook-subscriptions/{subscription_id}/delivery-attempts/{delivery_attempt_id}/replay` | Replay Webhook Delivery Attempt |
| POST | `/api/v1/workspaces/{workspace_id}/webhook-subscriptions/{subscription_id}/disable` | Disable Webhook Subscription |
| POST | `/api/v1/workspaces/{workspace_id}/webhook-subscriptions/{subscription_id}/rotate-signing-secret` | Rotate Webhook Signing Secret |

## GET `/api/v1/workspaces/{workspace_id}/webhook-subscriptions`

List Webhook Subscriptions

Operation ID：`list_webhook_subscriptions_api_v1_workspaces__workspace_id__webhook_subscriptions_get`。

实现：[src/opsmesh/orchestration/webhooks/routes.py](../../src/opsmesh/orchestration/webhooks/routes.py) · `list_webhook_subscriptions`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_WebhookSubscriptionResponse_](schemas.md#schema-PageResponse_WebhookSubscriptionResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/webhook-subscriptions`

Create Webhook Subscription

Operation ID：`create_webhook_subscription_api_v1_workspaces__workspace_id__webhook_subscriptions_post`。

实现：[src/opsmesh/orchestration/webhooks/routes.py](../../src/opsmesh/orchestration/webhooks/routes.py) · `create_webhook_subscription`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/WebhookSubscriptionCreateRequest"
}
```

模型：[WebhookSubscriptionCreateRequest](schemas.md#schema-WebhookSubscriptionCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [WebhookSubscriptionResponse](schemas.md#schema-WebhookSubscriptionResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/workspaces/{workspace_id}/webhook-subscriptions/{subscription_id}`

Update Webhook Subscription

Operation ID：`update_webhook_subscription_api_v1_workspaces__workspace_id__webhook_subscriptions__subscription_id__patch`。

实现：[src/opsmesh/orchestration/webhooks/routes.py](../../src/opsmesh/orchestration/webhooks/routes.py) · `update_webhook_subscription`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `subscription_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/WebhookSubscriptionUpdateRequest"
}
```

模型：[WebhookSubscriptionUpdateRequest](schemas.md#schema-WebhookSubscriptionUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WebhookSubscriptionResponse](schemas.md#schema-WebhookSubscriptionResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/webhook-subscriptions/{subscription_id}/delivery-attempts`

List Webhook Delivery Attempts

Operation ID：`list_webhook_delivery_attempts_api_v1_workspaces__workspace_id__webhook_subscriptions__subscription_id__delivery_attempts_get`。

实现：[src/opsmesh/orchestration/webhooks/routes.py](../../src/opsmesh/orchestration/webhooks/routes.py) · `list_webhook_delivery_attempts`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `subscription_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_WebhookDeliveryAttemptResponse_](schemas.md#schema-PageResponse_WebhookDeliveryAttemptResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/webhook-subscriptions/{subscription_id}/delivery-attempts/{delivery_attempt_id}/replay`

Replay Webhook Delivery Attempt

Operation ID：`replay_webhook_delivery_attempt_api_v1_workspaces__workspace_id__webhook_subscriptions__subscription_id__delivery_attempts__delivery_attempt_id__replay_post`。

实现：[src/opsmesh/orchestration/webhooks/routes.py](../../src/opsmesh/orchestration/webhooks/routes.py) · `replay_webhook_delivery_attempt`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `subscription_id` | path | 是 | string (uuid) | `format="uuid"` |
| `delivery_attempt_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WebhookDeliveryAttemptResponse](schemas.md#schema-WebhookDeliveryAttemptResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/webhook-subscriptions/{subscription_id}/disable`

Disable Webhook Subscription

Operation ID：`disable_webhook_subscription_api_v1_workspaces__workspace_id__webhook_subscriptions__subscription_id__disable_post`。

实现：[src/opsmesh/orchestration/webhooks/routes.py](../../src/opsmesh/orchestration/webhooks/routes.py) · `disable_webhook_subscription`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `subscription_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WebhookSubscriptionResponse](schemas.md#schema-WebhookSubscriptionResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/webhook-subscriptions/{subscription_id}/rotate-signing-secret`

Rotate Webhook Signing Secret

Operation ID：`rotate_webhook_signing_secret_api_v1_workspaces__workspace_id__webhook_subscriptions__subscription_id__rotate_signing_secret_post`。

实现：[src/opsmesh/orchestration/webhooks/routes.py](../../src/opsmesh/orchestration/webhooks/routes.py) · `rotate_webhook_signing_secret`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `subscription_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/WebhookSigningSecretRotateRequest"
}
```

模型：[WebhookSigningSecretRotateRequest](schemas.md#schema-WebhookSigningSecretRotateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WebhookSubscriptionResponse](schemas.md#schema-WebhookSubscriptionResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
