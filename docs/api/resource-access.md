# resource-access

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/access/context` | Workspace Access Context |
| GET | `/api/v1/workspaces/{workspace_id}/access/{kind}/{resource_id}/grants` | List Grants |
| PUT | `/api/v1/workspaces/{workspace_id}/access/{kind}/{resource_id}/grants/{user_id}` | Replace Grants |
| PUT | `/api/v1/workspaces/{workspace_id}/access/{kind}/{resource_id}/owner` | Assign Owner |
| GET | `/api/v1/workspaces/{workspace_id}/access/{kind}/{resource_id}/permissions` | Effective Permissions |

## GET `/api/v1/workspaces/{workspace_id}/access/context`

Workspace Access Context

Operation ID：`workspace_access_context_api_v1_workspaces__workspace_id__access_context_get`。

实现：[src/opsmesh/identity/authorization/routes.py](../../src/opsmesh/identity/authorization/routes.py) · `workspace_access_context`。

权限依赖：`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceAccessResponse](schemas.md#schema-WorkspaceAccessResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/access/{kind}/{resource_id}/grants`

List Grants

Operation ID：`list_grants_api_v1_workspaces__workspace_id__access__kind___resource_id__grants_get`。

实现：[src/opsmesh/identity/authorization/routes.py](../../src/opsmesh/identity/authorization/routes.py) · `list_grants`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `kind` | path | 是 | [ResourceKind](schemas.md#schema-ResourceKind) | — |
| `resource_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[GrantResponse](schemas.md#schema-GrantResponse)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PUT `/api/v1/workspaces/{workspace_id}/access/{kind}/{resource_id}/grants/{user_id}`

Replace Grants

Operation ID：`replace_grants_api_v1_workspaces__workspace_id__access__kind___resource_id__grants__user_id__put`。

实现：[src/opsmesh/identity/authorization/routes.py](../../src/opsmesh/identity/authorization/routes.py) · `replace_grants`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `kind` | path | 是 | [ResourceKind](schemas.md#schema-ResourceKind) | — |
| `resource_id` | path | 是 | string (uuid) | `format="uuid"` |
| `user_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/GrantRequest"
}
```

模型：[GrantRequest](schemas.md#schema-GrantRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [GrantResponse](schemas.md#schema-GrantResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PUT `/api/v1/workspaces/{workspace_id}/access/{kind}/{resource_id}/owner`

Assign Owner

Operation ID：`assign_owner_api_v1_workspaces__workspace_id__access__kind___resource_id__owner_put`。

实现：[src/opsmesh/identity/authorization/routes.py](../../src/opsmesh/identity/authorization/routes.py) · `assign_owner`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `kind` | path | 是 | [ResourceKind](schemas.md#schema-ResourceKind) | — |
| `resource_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/OwnerRequest"
}
```

模型：[OwnerRequest](schemas.md#schema-OwnerRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 204 | Successful Response | — | 无响应体 |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/access/{kind}/{resource_id}/permissions`

Effective Permissions

Operation ID：`effective_permissions_api_v1_workspaces__workspace_id__access__kind___resource_id__permissions_get`。

实现：[src/opsmesh/identity/authorization/routes.py](../../src/opsmesh/identity/authorization/routes.py) · `effective_permissions`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `kind` | path | 是 | [ResourceKind](schemas.md#schema-ResourceKind) | — |
| `resource_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PermissionResponse](schemas.md#schema-PermissionResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
