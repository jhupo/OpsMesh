# channels

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| POST | `/api/v1/plugin-services/{workspace_id}/{install_id}/channels/{connection_id}/attachments` | Upload Attachment |
| POST | `/api/v1/plugin-services/{workspace_id}/{install_id}/channels/{connection_id}/deliveries/{delivery_id}/receipt` | Receipt |
| POST | `/api/v1/plugin-services/{workspace_id}/{install_id}/channels/{connection_id}/events` | Receive Event |
| GET | `/api/v1/workspaces/{workspace_id}/channels` | Connections |
| POST | `/api/v1/workspaces/{workspace_id}/channels` | Create Connection |
| PUT | `/api/v1/workspaces/{workspace_id}/channels/{connection_id}` | Update Connection |

## POST `/api/v1/plugin-services/{workspace_id}/{install_id}/channels/{connection_id}/attachments`

Upload Attachment

Operation ID：`upload_attachment_api_v1_plugin_services__workspace_id___install_id__channels__connection_id__attachments_post`。

实现：[src/opsmesh/messaging/channels/routes.py](../../src/opsmesh/messaging/channels/routes.py) · `upload_attachment`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `connection_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string | `default=""` |

### 请求体

必填：是。

Content-Type：`multipart/form-data`。

```json
{
  "$ref": "#/components/schemas/Body_upload_attachment_api_v1_plugin_services__workspace_id___install_id__channels__connection_id__attachments_post"
}
```

模型：[Body_upload_attachment_api_v1_plugin_services__workspace_id___install_id__channels__connection_id__attachments_post](schemas.md#schema-Body_upload_attachment_api_v1_plugin_services__workspace_id___install_id__channels__connection_id__attachments_post)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [MessageAttachment](schemas.md#schema-MessageAttachment) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/plugin-services/{workspace_id}/{install_id}/channels/{connection_id}/deliveries/{delivery_id}/receipt`

Receipt

Operation ID：`receipt_api_v1_plugin_services__workspace_id___install_id__channels__connection_id__deliveries__delivery_id__receipt_post`。

实现：[src/opsmesh/messaging/channels/routes.py](../../src/opsmesh/messaging/channels/routes.py) · `receipt`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `connection_id` | path | 是 | string (uuid) | `format="uuid"` |
| `delivery_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string | `default=""` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ReplyReceiptRequest"
}
```

模型：[ReplyReceiptRequest](schemas.md#schema-ReplyReceiptRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ReplyReceipt](schemas.md#schema-ReplyReceipt) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/plugin-services/{workspace_id}/{install_id}/channels/{connection_id}/events`

Receive Event

Operation ID：`receive_event_api_v1_plugin_services__workspace_id___install_id__channels__connection_id__events_post`。

实现：[src/opsmesh/messaging/channels/routes.py](../../src/opsmesh/messaging/channels/routes.py) · `receive_event`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `connection_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string | `default=""` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ChannelEvent"
}
```

模型：[ChannelEvent](schemas.md#schema-ChannelEvent)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 202 | Successful Response | application/json | [AcceptedEvent](schemas.md#schema-AcceptedEvent) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/channels`

Connections

Operation ID：`connections_api_v1_workspaces__workspace_id__channels_get`。

实现：[src/opsmesh/messaging/channels/management_routes.py](../../src/opsmesh/messaging/channels/management_routes.py) · `connections`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `limit` | query | 否 | integer | `default=100`; `maximum=500`; `minimum=1` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[ChannelConnectionResponse](schemas.md#schema-ChannelConnectionResponse)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/channels`

Create Connection

Operation ID：`create_connection_api_v1_workspaces__workspace_id__channels_post`。

实现：[src/opsmesh/messaging/channels/management_routes.py](../../src/opsmesh/messaging/channels/management_routes.py) · `create_connection`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/ChannelConnectionCreateRequest"
}
```

模型：[ChannelConnectionCreateRequest](schemas.md#schema-ChannelConnectionCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [ChannelConnectionResponse](schemas.md#schema-ChannelConnectionResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PUT `/api/v1/workspaces/{workspace_id}/channels/{connection_id}`

Update Connection

Operation ID：`update_connection_api_v1_workspaces__workspace_id__channels__connection_id__put`。

实现：[src/opsmesh/messaging/channels/management_routes.py](../../src/opsmesh/messaging/channels/management_routes.py) · `update_connection`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `connection_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ChannelConnectionUpdateRequest"
}
```

模型：[ChannelConnectionUpdateRequest](schemas.md#schema-ChannelConnectionUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ChannelConnectionResponse](schemas.md#schema-ChannelConnectionResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
