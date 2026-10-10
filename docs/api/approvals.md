# approvals

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/approvals` | List Approvals |
| POST | `/api/v1/workspaces/{workspace_id}/approvals/{approval_id}/approve` | Approve |
| POST | `/api/v1/workspaces/{workspace_id}/approvals/{approval_id}/reject` | Reject |

## GET `/api/v1/workspaces/{workspace_id}/approvals`

List Approvals

Operation ID：`list_approvals_api_v1_workspaces__workspace_id__approvals_get`。

实现：[src/opsmesh/orchestration/approvals/routes.py](../../src/opsmesh/orchestration/approvals/routes.py) · `list_approvals`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=approve, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
| 200 | Successful Response | application/json | [PageResponse_ApprovalResponse_](schemas.md#schema-PageResponse_ApprovalResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/approvals/{approval_id}/approve`

Approve

Operation ID：`approve_api_v1_workspaces__workspace_id__approvals__approval_id__approve_post`。

实现：[src/opsmesh/orchestration/approvals/routes.py](../../src/opsmesh/orchestration/approvals/routes.py) · `approve`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=approve, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `approval_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ApprovalDecisionRequest"
}
```

模型：[ApprovalDecisionRequest](schemas.md#schema-ApprovalDecisionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ApprovalResponse](schemas.md#schema-ApprovalResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/approvals/{approval_id}/reject`

Reject

Operation ID：`reject_api_v1_workspaces__workspace_id__approvals__approval_id__reject_post`。

实现：[src/opsmesh/orchestration/approvals/routes.py](../../src/opsmesh/orchestration/approvals/routes.py) · `reject`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=approve, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `approval_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ApprovalDecisionRequest"
}
```

模型：[ApprovalDecisionRequest](schemas.md#schema-ApprovalDecisionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ApprovalResponse](schemas.md#schema-ApprovalResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
