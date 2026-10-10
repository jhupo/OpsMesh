# agent-messages

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/agent-message-threads` | List Agent Message Threads |
| POST | `/api/v1/workspaces/{workspace_id}/agent-message-threads` | Create Agent Message Thread |
| GET | `/api/v1/workspaces/{workspace_id}/agent-message-threads/agents/{agent_profile_id}/inbox` | Get Agent Inbox |
| POST | `/api/v1/workspaces/{workspace_id}/agent-message-threads/messages/{message_id}/read` | Mark Agent Message Read |
| GET | `/api/v1/workspaces/{workspace_id}/agent-message-threads/summary` | Get Agent Mailbox Summary |
| GET | `/api/v1/workspaces/{workspace_id}/agent-message-threads/{thread_id}/messages` | List Agent Messages |
| POST | `/api/v1/workspaces/{workspace_id}/agent-message-threads/{thread_id}/messages` | Create Agent Message |
| POST | `/api/v1/workspaces/{workspace_id}/agent-message-threads/{thread_id}/status` | Set Agent Message Thread Status |

## GET `/api/v1/workspaces/{workspace_id}/agent-message-threads`

List Agent Message Threads

Operation ID：`list_agent_message_threads_api_v1_workspaces__workspace_id__agent_message_threads_get`。

实现：[src/opsmesh/agents/messages/routes.py](../../src/opsmesh/agents/messages/routes.py) · `list_agent_message_threads`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `task_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `agent_team_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `agent_profile_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AgentMessageThreadResponse_](schemas.md#schema-PageResponse_AgentMessageThreadResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/agent-message-threads`

Create Agent Message Thread

Operation ID：`create_agent_message_thread_api_v1_workspaces__workspace_id__agent_message_threads_post`。

实现：[src/opsmesh/agents/messages/routes.py](../../src/opsmesh/agents/messages/routes.py) · `create_agent_message_thread`。

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
  "$ref": "#/components/schemas/AgentMessageThreadCreateRequest"
}
```

模型：[AgentMessageThreadCreateRequest](schemas.md#schema-AgentMessageThreadCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [AgentMessageThreadResponse](schemas.md#schema-AgentMessageThreadResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/agent-message-threads/agents/{agent_profile_id}/inbox`

Get Agent Inbox

Operation ID：`get_agent_inbox_api_v1_workspaces__workspace_id__agent_message_threads_agents__agent_profile_id__inbox_get`。

实现：[src/opsmesh/agents/messages/routes.py](../../src/opsmesh/agents/messages/routes.py) · `get_agent_inbox`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_profile_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `latest_limit` | query | 否 | integer | `default=20`; `maximum=100`; `minimum=0` |
| `unread_only` | query | 否 | boolean | `default=false` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentInboxSummaryResponse](schemas.md#schema-AgentInboxSummaryResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/agent-message-threads/messages/{message_id}/read`

Mark Agent Message Read

Operation ID：`mark_agent_message_read_api_v1_workspaces__workspace_id__agent_message_threads_messages__message_id__read_post`。

实现：[src/opsmesh/agents/messages/routes.py](../../src/opsmesh/agents/messages/routes.py) · `mark_agent_message_read`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `message_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：否。

Content-Type：`application/json`。

```json
{
  "anyOf": [
    {
      "$ref": "#/components/schemas/AgentMessageMarkReadRequest"
    },
    {
      "type": "null"
    }
  ],
  "title": "Request"
}
```

模型：[AgentMessageMarkReadRequest](schemas.md#schema-AgentMessageMarkReadRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentMessageResponse](schemas.md#schema-AgentMessageResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/agent-message-threads/summary`

Get Agent Mailbox Summary

Operation ID：`get_agent_mailbox_summary_api_v1_workspaces__workspace_id__agent_message_threads_summary_get`。

实现：[src/opsmesh/agents/messages/routes.py](../../src/opsmesh/agents/messages/routes.py) · `get_agent_mailbox_summary`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `latest_limit` | query | 否 | integer | `default=20`; `maximum=100`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentMailboxSummaryResponse](schemas.md#schema-AgentMailboxSummaryResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/agent-message-threads/{thread_id}/messages`

List Agent Messages

Operation ID：`list_agent_messages_api_v1_workspaces__workspace_id__agent_message_threads__thread_id__messages_get`。

实现：[src/opsmesh/agents/messages/routes.py](../../src/opsmesh/agents/messages/routes.py) · `list_agent_messages`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `thread_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `recipient_agent_profile_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AgentMessageResponse_](schemas.md#schema-PageResponse_AgentMessageResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/agent-message-threads/{thread_id}/messages`

Create Agent Message

Operation ID：`create_agent_message_api_v1_workspaces__workspace_id__agent_message_threads__thread_id__messages_post`。

实现：[src/opsmesh/agents/messages/routes.py](../../src/opsmesh/agents/messages/routes.py) · `create_agent_message`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `thread_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AgentMessageCreateRequest"
}
```

模型：[AgentMessageCreateRequest](schemas.md#schema-AgentMessageCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [AgentMessageResponse](schemas.md#schema-AgentMessageResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/agent-message-threads/{thread_id}/status`

Set Agent Message Thread Status

Operation ID：`set_agent_message_thread_status_api_v1_workspaces__workspace_id__agent_message_threads__thread_id__status_post`。

实现：[src/opsmesh/agents/messages/routes.py](../../src/opsmesh/agents/messages/routes.py) · `set_agent_message_thread_status`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `thread_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AgentMessageThreadStatusRequest"
}
```

模型：[AgentMessageThreadStatusRequest](schemas.md#schema-AgentMessageThreadStatusRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentMessageThreadResponse](schemas.md#schema-AgentMessageThreadResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
