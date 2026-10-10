# runtime-spaces

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/runtime-spaces` | List Runtime Spaces |
| POST | `/api/v1/workspaces/{workspace_id}/runtime-spaces` | Create Runtime Space |
| GET | `/api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}` | Get Runtime Space |
| PATCH | `/api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}` | Update Runtime Space |
| GET | `/api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}/diagnostics` | Get Runtime Space Diagnostics |
| GET | `/api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}/events` | List Runtime Space Events |
| POST | `/api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}/pause` | Pause Runtime Space |
| POST | `/api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}/reservations/force-release` | Force Release Runtime Space Reservations |
| POST | `/api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}/reset` | Reset Runtime Space |
| POST | `/api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}/resume` | Resume Runtime Space |

## GET `/api/v1/workspaces/{workspace_id}/runtime-spaces`

List Runtime Spaces

Operation ID：`list_runtime_spaces_api_v1_workspaces__workspace_id__runtime_spaces_get`。

实现：[src/opsmesh/runtime/spaces/routes.py](../../src/opsmesh/runtime/spaces/routes.py) · `list_runtime_spaces`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `scope` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_RuntimeSpaceResponse_](schemas.md#schema-PageResponse_RuntimeSpaceResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/runtime-spaces`

Create Runtime Space

Operation ID：`create_runtime_space_api_v1_workspaces__workspace_id__runtime_spaces_post`。

实现：[src/opsmesh/runtime/spaces/routes.py](../../src/opsmesh/runtime/spaces/routes.py) · `create_runtime_space`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/RuntimeSpaceCreateRequest"
}
```

模型：[RuntimeSpaceCreateRequest](schemas.md#schema-RuntimeSpaceCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [RuntimeSpaceResponse](schemas.md#schema-RuntimeSpaceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}`

Get Runtime Space

Operation ID：`get_runtime_space_api_v1_workspaces__workspace_id__runtime_spaces__runtime_space_id__get`。

实现：[src/opsmesh/runtime/spaces/routes.py](../../src/opsmesh/runtime/spaces/routes.py) · `get_runtime_space`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `runtime_space_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [RuntimeSpaceResponse](schemas.md#schema-RuntimeSpaceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}`

Update Runtime Space

Operation ID：`update_runtime_space_api_v1_workspaces__workspace_id__runtime_spaces__runtime_space_id__patch`。

实现：[src/opsmesh/runtime/spaces/routes.py](../../src/opsmesh/runtime/spaces/routes.py) · `update_runtime_space`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `runtime_space_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/RuntimeSpaceUpdateRequest"
}
```

模型：[RuntimeSpaceUpdateRequest](schemas.md#schema-RuntimeSpaceUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [RuntimeSpaceResponse](schemas.md#schema-RuntimeSpaceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}/diagnostics`

Get Runtime Space Diagnostics

Operation ID：`get_runtime_space_diagnostics_api_v1_workspaces__workspace_id__runtime_spaces__runtime_space_id__diagnostics_get`。

实现：[src/opsmesh/runtime/spaces/routes.py](../../src/opsmesh/runtime/spaces/routes.py) · `get_runtime_space_diagnostics`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `runtime_space_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [RuntimeSpaceDiagnosticsResponse](schemas.md#schema-RuntimeSpaceDiagnosticsResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}/events`

List Runtime Space Events

Operation ID：`list_runtime_space_events_api_v1_workspaces__workspace_id__runtime_spaces__runtime_space_id__events_get`。

实现：[src/opsmesh/runtime/spaces/routes.py](../../src/opsmesh/runtime/spaces/routes.py) · `list_runtime_space_events`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `runtime_space_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_RuntimeSpaceEventResponse_](schemas.md#schema-PageResponse_RuntimeSpaceEventResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}/pause`

Pause Runtime Space

Operation ID：`pause_runtime_space_api_v1_workspaces__workspace_id__runtime_spaces__runtime_space_id__pause_post`。

实现：[src/opsmesh/runtime/spaces/routes.py](../../src/opsmesh/runtime/spaces/routes.py) · `pause_runtime_space`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `runtime_space_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/RuntimeSpacePauseRequest"
}
```

模型：[RuntimeSpacePauseRequest](schemas.md#schema-RuntimeSpacePauseRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [RuntimeSpaceControlResponse](schemas.md#schema-RuntimeSpaceControlResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}/reservations/force-release`

Force Release Runtime Space Reservations

Operation ID：`force_release_runtime_space_reservations_api_v1_workspaces__workspace_id__runtime_spaces__runtime_space_id__reservations_force_release_post`。

实现：[src/opsmesh/runtime/spaces/routes.py](../../src/opsmesh/runtime/spaces/routes.py) · `force_release_runtime_space_reservations`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `runtime_space_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/RuntimeSpaceForceReleaseRequest"
}
```

模型：[RuntimeSpaceForceReleaseRequest](schemas.md#schema-RuntimeSpaceForceReleaseRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [RuntimeSpaceForceReleaseResponse](schemas.md#schema-RuntimeSpaceForceReleaseResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}/reset`

Reset Runtime Space

Operation ID：`reset_runtime_space_api_v1_workspaces__workspace_id__runtime_spaces__runtime_space_id__reset_post`。

实现：[src/opsmesh/runtime/spaces/routes.py](../../src/opsmesh/runtime/spaces/routes.py) · `reset_runtime_space`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `runtime_space_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [RuntimeSpaceResetResponse](schemas.md#schema-RuntimeSpaceResetResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/runtime-spaces/{runtime_space_id}/resume`

Resume Runtime Space

Operation ID：`resume_runtime_space_api_v1_workspaces__workspace_id__runtime_spaces__runtime_space_id__resume_post`。

实现：[src/opsmesh/runtime/spaces/routes.py](../../src/opsmesh/runtime/spaces/routes.py) · `resume_runtime_space`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `runtime_space_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [RuntimeSpaceControlResponse](schemas.md#schema-RuntimeSpaceControlResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
