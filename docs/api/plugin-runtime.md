# plugin-runtime

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| POST | `/api/v1/plugin-runtime/{workspace_id}/{install_id}/automations/{automation_id}/attachments` | Upload Attachment |
| POST | `/api/v1/plugin-runtime/{workspace_id}/{install_id}/automations/{automation_id}/events` | Receive |
| GET | `/api/v1/plugin-runtime/{workspace_id}/{install_id}/automations/{automation_id}/events/{event_id}` | State |
| POST | `/api/v1/plugin-runtime/{workspace_id}/{install_id}/automations/{automation_id}/events/{event_id}/approvals/{approval_id}/decision` | Decide Approval |
| GET | `/api/v1/plugin-runtime/{workspace_id}/{install_id}/automations/{automation_id}/events/{event_id}/stream` | Stream |
| GET | `/api/v1/plugin-runtime/{workspace_id}/{install_id}/configuration` | Configuration |
| GET | `/api/v1/plugin-runtime/{workspace_id}/{install_id}/context` | Installation Context |
| POST | `/api/v1/plugin-runtime/{workspace_id}/{install_id}/identity` | User Identity |
| POST | `/api/v1/plugin-runtime/{workspace_id}/{install_id}/knowledge/search` | Search Knowledge |
| POST | `/api/v1/plugin-runtime/{workspace_id}/{install_id}/logs` | Log Event |
| POST | `/api/v1/plugin-runtime/{workspace_id}/{install_id}/memory` | Remember |
| POST | `/api/v1/plugin-runtime/{workspace_id}/{install_id}/permissions` | Permissions |
| POST | `/api/v1/plugin-runtime/{workspace_id}/{install_id}/resources/query` | Query Resources |
| GET | `/api/v1/plugin-runtime/{workspace_id}/{install_id}/storage` | Values |
| DELETE | `/api/v1/plugin-runtime/{workspace_id}/{install_id}/storage/{key}` | Delete Value |
| GET | `/api/v1/plugin-runtime/{workspace_id}/{install_id}/storage/{key}` | Read Value |
| PUT | `/api/v1/plugin-runtime/{workspace_id}/{install_id}/storage/{key}` | Write Value |

## POST `/api/v1/plugin-runtime/{workspace_id}/{install_id}/automations/{automation_id}/attachments`

Upload Attachment

Operation ID：`upload_attachment_api_v1_plugin_runtime__workspace_id___install_id__automations__automation_id__attachments_post`。

实现：[src/opsmesh/capabilities/plugins/runtime_routes.py](../../src/opsmesh/capabilities/plugins/runtime_routes.py) · `upload_attachment`。

权限依赖：`opsmesh.capabilities.plugins.runtime_routes.plugin_principal`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `automation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string | `default=""` |

### 请求体

必填：是。

Content-Type：`multipart/form-data`。

```json
{
  "$ref": "#/components/schemas/Body_upload_attachment_api_v1_plugin_runtime__workspace_id___install_id__automations__automation_id__attachments_post"
}
```

模型：[Body_upload_attachment_api_v1_plugin_runtime__workspace_id___install_id__automations__automation_id__attachments_post](schemas.md#schema-Body_upload_attachment_api_v1_plugin_runtime__workspace_id___install_id__automations__automation_id__attachments_post)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [MessageAttachment](schemas.md#schema-MessageAttachment) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/plugin-runtime/{workspace_id}/{install_id}/automations/{automation_id}/events`

Receive

Operation ID：`receive_api_v1_plugin_runtime__workspace_id___install_id__automations__automation_id__events_post`。

实现：[src/opsmesh/capabilities/plugins/runtime_routes.py](../../src/opsmesh/capabilities/plugins/runtime_routes.py) · `receive`。

权限依赖：`opsmesh.capabilities.plugins.runtime_routes.plugin_principal`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `automation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string | `default=""` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AutomationMessage"
}
```

模型：[AutomationMessage](schemas.md#schema-AutomationMessage)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 202 | Successful Response | application/json | [AcceptedEvent](schemas.md#schema-AcceptedEvent) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/plugin-runtime/{workspace_id}/{install_id}/automations/{automation_id}/events/{event_id}`

State

Operation ID：`state_api_v1_plugin_runtime__workspace_id___install_id__automations__automation_id__events__event_id__get`。

实现：[src/opsmesh/capabilities/plugins/runtime_routes.py](../../src/opsmesh/capabilities/plugins/runtime_routes.py) · `state`。

权限依赖：`opsmesh.capabilities.plugins.runtime_routes.plugin_principal`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `automation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `event_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string | `default=""` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [EventState](schemas.md#schema-EventState) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/plugin-runtime/{workspace_id}/{install_id}/automations/{automation_id}/events/{event_id}/approvals/{approval_id}/decision`

Decide Approval

Operation ID：`decide_approval_api_v1_plugin_runtime__workspace_id___install_id__automations__automation_id__events__event_id__approvals__approval_id__decision_post`。

实现：[src/opsmesh/capabilities/plugins/runtime_routes.py](../../src/opsmesh/capabilities/plugins/runtime_routes.py) · `decide_approval`。

权限依赖：`opsmesh.capabilities.plugins.runtime_routes.plugin_principal`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `automation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `event_id` | path | 是 | string (uuid) | `format="uuid"` |
| `approval_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string | `default=""` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ApprovalDecision"
}
```

模型：[ApprovalDecision](schemas.md#schema-ApprovalDecision)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ApprovalReceipt](schemas.md#schema-ApprovalReceipt) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/plugin-runtime/{workspace_id}/{install_id}/automations/{automation_id}/events/{event_id}/stream`

Stream

Operation ID：`stream_api_v1_plugin_runtime__workspace_id___install_id__automations__automation_id__events__event_id__stream_get`。

实现：[src/opsmesh/capabilities/plugins/runtime_routes.py](../../src/opsmesh/capabilities/plugins/runtime_routes.py) · `stream`。

权限依赖：`opsmesh.capabilities.plugins.runtime_routes.plugin_principal`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `automation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `event_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `cursor` | query | 否 | string | `default="0-0"`; `pattern="^\\d{1,20}-\\d{1,20}$"` |
| `once` | query | 否 | boolean | `default=false` |
| `authorization` | header | 否 | string | `default=""` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/x-ndjson | string |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/plugin-runtime/{workspace_id}/{install_id}/configuration`

Configuration

Operation ID：`configuration_api_v1_plugin_runtime__workspace_id___install_id__configuration_get`。

实现：[src/opsmesh/capabilities/plugins/runtime_routes.py](../../src/opsmesh/capabilities/plugins/runtime_routes.py) · `configuration`。

权限依赖：`opsmesh.capabilities.plugins.runtime_routes.plugin_principal`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string | `default=""` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | object |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/plugin-runtime/{workspace_id}/{install_id}/context`

Installation Context

Operation ID：`installation_context_api_v1_plugin_runtime__workspace_id___install_id__context_get`。

实现：[src/opsmesh/capabilities/plugins/runtime_routes.py](../../src/opsmesh/capabilities/plugins/runtime_routes.py) · `installation_context`。

权限依赖：`opsmesh.capabilities.plugins.runtime_routes.plugin_principal`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string | `default=""` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PluginContext](schemas.md#schema-PluginContext) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/plugin-runtime/{workspace_id}/{install_id}/identity`

User Identity

Operation ID：`user_identity_api_v1_plugin_runtime__workspace_id___install_id__identity_post`。

实现：[src/opsmesh/capabilities/plugins/runtime_routes.py](../../src/opsmesh/capabilities/plugins/runtime_routes.py) · `user_identity`。

权限依赖：`opsmesh.capabilities.plugins.runtime_routes.plugin_principal`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string | `default=""` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/UserContext"
}
```

模型：[UserContext](schemas.md#schema-UserContext)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [UserIdentity](schemas.md#schema-UserIdentity) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/plugin-runtime/{workspace_id}/{install_id}/knowledge/search`

Search Knowledge

Operation ID：`search_knowledge_api_v1_plugin_runtime__workspace_id___install_id__knowledge_search_post`。

实现：[src/opsmesh/capabilities/plugins/runtime_routes.py](../../src/opsmesh/capabilities/plugins/runtime_routes.py) · `search_knowledge`。

权限依赖：`opsmesh.capabilities.plugins.runtime_routes.plugin_principal`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string | `default=""` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/KnowledgeQuery"
}
```

模型：[KnowledgeQuery](schemas.md#schema-KnowledgeQuery)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[KnowledgeHit](schemas.md#schema-KnowledgeHit)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/plugin-runtime/{workspace_id}/{install_id}/logs`

Log Event

Operation ID：`log_event_api_v1_plugin_runtime__workspace_id___install_id__logs_post`。

实现：[src/opsmesh/capabilities/plugins/runtime_routes.py](../../src/opsmesh/capabilities/plugins/runtime_routes.py) · `log_event`。

权限依赖：`opsmesh.capabilities.plugins.runtime_routes.plugin_principal`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string | `default=""` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/PluginLog"
}
```

模型：[PluginLog](schemas.md#schema-PluginLog)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 204 | Successful Response | — | 无响应体 |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/plugin-runtime/{workspace_id}/{install_id}/memory`

Remember

Operation ID：`remember_api_v1_plugin_runtime__workspace_id___install_id__memory_post`。

实现：[src/opsmesh/capabilities/plugins/runtime_routes.py](../../src/opsmesh/capabilities/plugins/runtime_routes.py) · `remember`。

权限依赖：`opsmesh.capabilities.plugins.runtime_routes.plugin_principal`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string | `default=""` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/MemoryWrite"
}
```

模型：[MemoryWrite](schemas.md#schema-MemoryWrite)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [MemoryReceipt](schemas.md#schema-MemoryReceipt) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/plugin-runtime/{workspace_id}/{install_id}/permissions`

Permissions

Operation ID：`permissions_api_v1_plugin_runtime__workspace_id___install_id__permissions_post`。

实现：[src/opsmesh/capabilities/plugins/runtime_routes.py](../../src/opsmesh/capabilities/plugins/runtime_routes.py) · `permissions`。

权限依赖：`opsmesh.capabilities.plugins.runtime_routes.plugin_principal`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string | `default=""` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/PermissionQuery"
}
```

模型：[PermissionQuery](schemas.md#schema-PermissionQuery)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PermissionResult](schemas.md#schema-PermissionResult) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/plugin-runtime/{workspace_id}/{install_id}/resources/query`

Query Resources

Operation ID：`query_resources_api_v1_plugin_runtime__workspace_id___install_id__resources_query_post`。

实现：[src/opsmesh/capabilities/plugins/runtime_routes.py](../../src/opsmesh/capabilities/plugins/runtime_routes.py) · `query_resources`。

权限依赖：`opsmesh.capabilities.plugins.runtime_routes.plugin_principal`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string | `default=""` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ResourceQuery"
}
```

模型：[ResourceQuery](schemas.md#schema-ResourceQuery)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ResourcePage](schemas.md#schema-ResourcePage) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/plugin-runtime/{workspace_id}/{install_id}/storage`

Values

Operation ID：`values_api_v1_plugin_runtime__workspace_id___install_id__storage_get`。

实现：[src/opsmesh/capabilities/plugins/runtime_routes.py](../../src/opsmesh/capabilities/plugins/runtime_routes.py) · `values`。

权限依赖：`opsmesh.capabilities.plugins.runtime_routes.plugin_principal`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `prefix` | query | 否 | string | `default=""`; `maxLength=120` |
| `offset` | query | 否 | integer | `default=0`; `maximum=512`; `minimum=0` |
| `authorization` | header | 否 | string | `default=""` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[StoredValue](schemas.md#schema-StoredValue)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## DELETE `/api/v1/plugin-runtime/{workspace_id}/{install_id}/storage/{key}`

Delete Value

Operation ID：`delete_value_api_v1_plugin_runtime__workspace_id___install_id__storage__key__delete`。

实现：[src/opsmesh/capabilities/plugins/runtime_routes.py](../../src/opsmesh/capabilities/plugins/runtime_routes.py) · `delete_value`。

权限依赖：`opsmesh.capabilities.plugins.runtime_routes.plugin_principal`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `key` | path | 是 | string | — |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `expected_revision` | query | 是 | integer | `minimum=1` |
| `authorization` | header | 否 | string | `default=""` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 204 | Successful Response | — | 无响应体 |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/plugin-runtime/{workspace_id}/{install_id}/storage/{key}`

Read Value

Operation ID：`read_value_api_v1_plugin_runtime__workspace_id___install_id__storage__key__get`。

实现：[src/opsmesh/capabilities/plugins/runtime_routes.py](../../src/opsmesh/capabilities/plugins/runtime_routes.py) · `read_value`。

权限依赖：`opsmesh.capabilities.plugins.runtime_routes.plugin_principal`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `key` | path | 是 | string | — |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string | `default=""` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [StoredValue](schemas.md#schema-StoredValue) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PUT `/api/v1/plugin-runtime/{workspace_id}/{install_id}/storage/{key}`

Write Value

Operation ID：`write_value_api_v1_plugin_runtime__workspace_id___install_id__storage__key__put`。

实现：[src/opsmesh/capabilities/plugins/runtime_routes.py](../../src/opsmesh/capabilities/plugins/runtime_routes.py) · `write_value`。

权限依赖：`opsmesh.capabilities.plugins.runtime_routes.plugin_principal`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `key` | path | 是 | string | — |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string | `default=""` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/StoreWrite"
}
```

模型：[StoreWrite](schemas.md#schema-StoreWrite)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [StoredValue](schemas.md#schema-StoredValue) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
