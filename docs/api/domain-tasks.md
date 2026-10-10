# domain-tasks

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/domain-items` | List Domain Items |
| POST | `/api/v1/workspaces/{workspace_id}/domain-items` | Create Domain Item |
| GET | `/api/v1/workspaces/{workspace_id}/domain-projects` | List Domain Projects |
| POST | `/api/v1/workspaces/{workspace_id}/domain-projects` | Create Domain Project |
| POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/review-comments` | Create Review Comment |
| POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/revision-requests` | Create Revision Request |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/view` | Get Task View |

## GET `/api/v1/workspaces/{workspace_id}/domain-items`

List Domain Items

Operation ID：`list_domain_items_api_v1_workspaces__workspace_id__domain_items_get`。

实现：[src/opsmesh/workspaces/domain_items/routes.py](../../src/opsmesh/workspaces/domain_items/routes.py) · `list_domain_items`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `task_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `project_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_DomainItemResponse_](schemas.md#schema-PageResponse_DomainItemResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/domain-items`

Create Domain Item

Operation ID：`create_domain_item_api_v1_workspaces__workspace_id__domain_items_post`。

实现：[src/opsmesh/workspaces/domain_items/routes.py](../../src/opsmesh/workspaces/domain_items/routes.py) · `create_domain_item`。

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
  "$ref": "#/components/schemas/DomainItemCreateRequest"
}
```

模型：[DomainItemCreateRequest](schemas.md#schema-DomainItemCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [DomainItemResponse](schemas.md#schema-DomainItemResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/domain-projects`

List Domain Projects

Operation ID：`list_domain_projects_api_v1_workspaces__workspace_id__domain_projects_get`。

实现：[src/opsmesh/workspaces/domain_items/routes.py](../../src/opsmesh/workspaces/domain_items/routes.py) · `list_domain_projects`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `domain_type` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_DomainProjectResponse_](schemas.md#schema-PageResponse_DomainProjectResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/domain-projects`

Create Domain Project

Operation ID：`create_domain_project_api_v1_workspaces__workspace_id__domain_projects_post`。

实现：[src/opsmesh/workspaces/domain_items/routes.py](../../src/opsmesh/workspaces/domain_items/routes.py) · `create_domain_project`。

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
  "$ref": "#/components/schemas/DomainProjectCreateRequest"
}
```

模型：[DomainProjectCreateRequest](schemas.md#schema-DomainProjectCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [DomainProjectResponse](schemas.md#schema-DomainProjectResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/review-comments`

Create Review Comment

Operation ID：`create_review_comment_api_v1_workspaces__workspace_id__tasks__task_id__review_comments_post`。

实现：[src/opsmesh/workspaces/domain_items/routes.py](../../src/opsmesh/workspaces/domain_items/routes.py) · `create_review_comment`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ReviewCommentCreateRequest"
}
```

模型：[ReviewCommentCreateRequest](schemas.md#schema-ReviewCommentCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [ReviewCommentResponse](schemas.md#schema-ReviewCommentResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/revision-requests`

Create Revision Request

Operation ID：`create_revision_request_api_v1_workspaces__workspace_id__tasks__task_id__revision_requests_post`。

实现：[src/opsmesh/workspaces/domain_items/routes.py](../../src/opsmesh/workspaces/domain_items/routes.py) · `create_revision_request`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/RevisionRequestCreateRequest"
}
```

模型：[RevisionRequestCreateRequest](schemas.md#schema-RevisionRequestCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [RevisionRequestResponse](schemas.md#schema-RevisionRequestResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/view`

Get Task View

Operation ID：`get_task_view_api_v1_workspaces__workspace_id__tasks__task_id__view_get`。

实现：[src/opsmesh/workspaces/domain_items/routes.py](../../src/opsmesh/workspaces/domain_items/routes.py) · `get_task_view`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskViewResponse](schemas.md#schema-TaskViewResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
