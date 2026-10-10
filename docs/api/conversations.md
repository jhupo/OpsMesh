# conversations

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/conversations` | List Conversations |
| POST | `/api/v1/workspaces/{workspace_id}/conversations` | Create Conversation |
| GET | `/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}` | Get Conversation |
| GET | `/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/events` | List Events |
| GET | `/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/executions` | List Executions |
| GET | `/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/messages` | List Messages |
| POST | `/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/messages` | Send Message |
| POST | `/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/turns/{turn_id}/cancel` | Cancel Turn |
| POST | `/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/turns/{turn_id}/retry` | Retry Turn |

## GET `/api/v1/workspaces/{workspace_id}/conversations`

List Conversations

Operation ID：`list_conversations_api_v1_workspaces__workspace_id__conversations_get`。

实现：[src/opsmesh/orchestration/conversations/routes.py](../../src/opsmesh/orchestration/conversations/routes.py) · `list_conversations`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_ConversationResponse_](schemas.md#schema-PageResponse_ConversationResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/conversations`

Create Conversation

Operation ID：`create_conversation_api_v1_workspaces__workspace_id__conversations_post`。

实现：[src/opsmesh/orchestration/conversations/routes.py](../../src/opsmesh/orchestration/conversations/routes.py) · `create_conversation`。

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
  "$ref": "#/components/schemas/ConversationCreate"
}
```

模型：[ConversationCreate](schemas.md#schema-ConversationCreate)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [ConversationResponse](schemas.md#schema-ConversationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}`

Get Conversation

Operation ID：`get_conversation_api_v1_workspaces__workspace_id__conversations__conversation_id__get`。

实现：[src/opsmesh/orchestration/conversations/routes.py](../../src/opsmesh/orchestration/conversations/routes.py) · `get_conversation`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `conversation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ConversationResponse](schemas.md#schema-ConversationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/events`

List Events

Operation ID：`list_events_api_v1_workspaces__workspace_id__conversations__conversation_id__events_get`。

实现：[src/opsmesh/orchestration/conversations/routes.py](../../src/opsmesh/orchestration/conversations/routes.py) · `list_events`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `conversation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `after_id` | query | 否 | integer | `default=0`; `minimum=0` |
| `limit` | query | 否 | integer | `default=100`; `maximum=200`; `minimum=1` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[opsmesh__orchestration__conversations__schemas__EventResponse](schemas.md#schema-opsmesh__orchestration__conversations__schemas__EventResponse)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/executions`

List Executions

Operation ID：`list_executions_api_v1_workspaces__workspace_id__conversations__conversation_id__executions_get`。

实现：[src/opsmesh/orchestration/conversations/routes.py](../../src/opsmesh/orchestration/conversations/routes.py) · `list_executions`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `conversation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `turn_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_ExecutionResponse_](schemas.md#schema-PageResponse_ExecutionResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/messages`

List Messages

Operation ID：`list_messages_api_v1_workspaces__workspace_id__conversations__conversation_id__messages_get`。

实现：[src/opsmesh/orchestration/conversations/routes.py](../../src/opsmesh/orchestration/conversations/routes.py) · `list_messages`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `conversation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_TurnResponse_](schemas.md#schema-PageResponse_TurnResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/messages`

Send Message

Operation ID：`send_message_api_v1_workspaces__workspace_id__conversations__conversation_id__messages_post`。

实现：[src/opsmesh/orchestration/conversations/routes.py](../../src/opsmesh/orchestration/conversations/routes.py) · `send_message`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `conversation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `Idempotency-Key` | header | 是 | string | `maxLength=128`; `minLength=1` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/MessageCreate"
}
```

模型：[MessageCreate](schemas.md#schema-MessageCreate)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 202 | Successful Response | application/json | [TurnResponse](schemas.md#schema-TurnResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/turns/{turn_id}/cancel`

Cancel Turn

Operation ID：`cancel_turn_api_v1_workspaces__workspace_id__conversations__conversation_id__turns__turn_id__cancel_post`。

实现：[src/opsmesh/orchestration/conversations/routes.py](../../src/opsmesh/orchestration/conversations/routes.py) · `cancel_turn`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `conversation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `turn_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TurnResponse](schemas.md#schema-TurnResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/turns/{turn_id}/retry`

Retry Turn

Operation ID：`retry_turn_api_v1_workspaces__workspace_id__conversations__conversation_id__turns__turn_id__retry_post`。

实现：[src/opsmesh/orchestration/conversations/routes.py](../../src/opsmesh/orchestration/conversations/routes.py) · `retry_turn`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `conversation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `turn_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TurnResponse](schemas.md#schema-TurnResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
