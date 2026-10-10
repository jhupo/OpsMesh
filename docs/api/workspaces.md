# workspaces

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/workspaces` | List Workspaces |
| POST | `/api/v1/workspaces` | Create Workspace |
| POST | `/api/v1/workspaces/invites/accept` | Accept Workspace Invite |
| GET | `/api/v1/workspaces/{workspace_id}` | Get Workspace |
| PATCH | `/api/v1/workspaces/{workspace_id}` | Update Workspace |
| GET | `/api/v1/workspaces/{workspace_id}/health` | Get Workspace Health |
| GET | `/api/v1/workspaces/{workspace_id}/health/snapshots` | List Workspace Health Snapshots |
| POST | `/api/v1/workspaces/{workspace_id}/health/snapshots` | Create Workspace Health Snapshot |
| GET | `/api/v1/workspaces/{workspace_id}/health/trends` | Get Workspace Health Trends |
| GET | `/api/v1/workspaces/{workspace_id}/invites` | List Workspace Invites |
| POST | `/api/v1/workspaces/{workspace_id}/invites` | Create Workspace Invite |
| DELETE | `/api/v1/workspaces/{workspace_id}/invites/{invite_id}` | Revoke Workspace Invite |
| GET | `/api/v1/workspaces/{workspace_id}/members` | List Workspace Members |
| POST | `/api/v1/workspaces/{workspace_id}/members` | Create Workspace Member |
| DELETE | `/api/v1/workspaces/{workspace_id}/members/{member_id}` | Disable Workspace Member |
| PATCH | `/api/v1/workspaces/{workspace_id}/members/{member_id}` | Update Workspace Member |
| GET | `/api/v1/workspaces/{workspace_id}/quotas` | List Workspace Quotas |
| PUT | `/api/v1/workspaces/{workspace_id}/quotas` | Upsert Workspace Quotas |
| GET | `/api/v1/workspaces/{workspace_id}/quotas/execution-summary` | Get Workspace Execution Slot Summary |
| DELETE | `/api/v1/workspaces/{workspace_id}/quotas/{quota_key}` | Disable Workspace Quota |

## GET `/api/v1/workspaces`

List Workspaces

Operation ID：`list_workspaces_api_v1_workspaces_get`。

实现：[src/opsmesh/workspaces/management/dependencies.py](../../src/opsmesh/workspaces/management/dependencies.py) · `list_workspaces`。

权限依赖：`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_WorkspaceResponse_](schemas.md#schema-PageResponse_WorkspaceResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces`

Create Workspace

Operation ID：`create_workspace_api_v1_workspaces_post`。

实现：[src/opsmesh/workspaces/management/dependencies.py](../../src/opsmesh/workspaces/management/dependencies.py) · `create_workspace`。

权限依赖：`opsmesh.identity.auth.dependencies.account_action_dependency.<locals>.require_account_action`：`action=workspaces:create`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `Idempotency-Key` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/WorkspaceCreateRequest"
}
```

模型：[WorkspaceCreateRequest](schemas.md#schema-WorkspaceCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [WorkspaceResponse](schemas.md#schema-WorkspaceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/invites/accept`

Accept Workspace Invite

Operation ID：`accept_workspace_invite_api_v1_workspaces_invites_accept_post`。

实现：[src/opsmesh/workspaces/members/invitation_routes.py](../../src/opsmesh/workspaces/members/invitation_routes.py) · `accept_workspace_invite`。

权限依赖：`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/WorkspaceInviteAcceptRequest"
}
```

模型：[WorkspaceInviteAcceptRequest](schemas.md#schema-WorkspaceInviteAcceptRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceInviteAcceptResponse](schemas.md#schema-WorkspaceInviteAcceptResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}`

Get Workspace

Operation ID：`get_workspace_api_v1_workspaces__workspace_id__get`。

实现：[src/opsmesh/workspaces/management/dependencies.py](../../src/opsmesh/workspaces/management/dependencies.py) · `get_workspace`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceResponse](schemas.md#schema-WorkspaceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/workspaces/{workspace_id}`

Update Workspace

Operation ID：`update_workspace_api_v1_workspaces__workspace_id__patch`。

实现：[src/opsmesh/workspaces/management/dependencies.py](../../src/opsmesh/workspaces/management/dependencies.py) · `update_workspace`。

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
  "$ref": "#/components/schemas/WorkspaceUpdateRequest"
}
```

模型：[WorkspaceUpdateRequest](schemas.md#schema-WorkspaceUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceResponse](schemas.md#schema-WorkspaceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/health`

Get Workspace Health

Operation ID：`get_workspace_health_api_v1_workspaces__workspace_id__health_get`。

实现：[src/opsmesh/workspaces/management/dependencies.py](../../src/opsmesh/workspaces/management/dependencies.py) · `get_workspace_health`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceHealthResponse](schemas.md#schema-WorkspaceHealthResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/health/snapshots`

List Workspace Health Snapshots

Operation ID：`list_workspace_health_snapshots_api_v1_workspaces__workspace_id__health_snapshots_get`。

实现：[src/opsmesh/workspaces/management/dependencies.py](../../src/opsmesh/workspaces/management/dependencies.py) · `list_workspace_health_snapshots`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=200`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_WorkspaceHealthSnapshotResponse_](schemas.md#schema-PageResponse_WorkspaceHealthSnapshotResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/health/snapshots`

Create Workspace Health Snapshot

Operation ID：`create_workspace_health_snapshot_api_v1_workspaces__workspace_id__health_snapshots_post`。

实现：[src/opsmesh/workspaces/management/dependencies.py](../../src/opsmesh/workspaces/management/dependencies.py) · `create_workspace_health_snapshot`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [WorkspaceHealthSnapshotResponse](schemas.md#schema-WorkspaceHealthSnapshotResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/health/trends`

Get Workspace Health Trends

Operation ID：`get_workspace_health_trends_api_v1_workspaces__workspace_id__health_trends_get`。

实现：[src/opsmesh/workspaces/management/dependencies.py](../../src/opsmesh/workspaces/management/dependencies.py) · `get_workspace_health_trends`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=20`; `maximum=200`; `minimum=2` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceHealthTrendResponse](schemas.md#schema-WorkspaceHealthTrendResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/invites`

List Workspace Invites

Operation ID：`list_workspace_invites_api_v1_workspaces__workspace_id__invites_get`。

实现：[src/opsmesh/workspaces/members/invitation_routes.py](../../src/opsmesh/workspaces/members/invitation_routes.py) · `list_workspace_invites`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_members, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
| 200 | Successful Response | application/json | [PageResponse_WorkspaceInviteResponse_](schemas.md#schema-PageResponse_WorkspaceInviteResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/invites`

Create Workspace Invite

Operation ID：`create_workspace_invite_api_v1_workspaces__workspace_id__invites_post`。

实现：[src/opsmesh/workspaces/members/invitation_routes.py](../../src/opsmesh/workspaces/members/invitation_routes.py) · `create_workspace_invite`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_members, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/WorkspaceInviteCreateRequest"
}
```

模型：[WorkspaceInviteCreateRequest](schemas.md#schema-WorkspaceInviteCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [WorkspaceInviteCreateResponse](schemas.md#schema-WorkspaceInviteCreateResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## DELETE `/api/v1/workspaces/{workspace_id}/invites/{invite_id}`

Revoke Workspace Invite

Operation ID：`revoke_workspace_invite_api_v1_workspaces__workspace_id__invites__invite_id__delete`。

实现：[src/opsmesh/workspaces/members/invitation_routes.py](../../src/opsmesh/workspaces/members/invitation_routes.py) · `revoke_workspace_invite`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_members, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `invite_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceInviteResponse](schemas.md#schema-WorkspaceInviteResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/members`

List Workspace Members

Operation ID：`list_workspace_members_api_v1_workspaces__workspace_id__members_get`。

实现：[src/opsmesh/workspaces/members/routes.py](../../src/opsmesh/workspaces/members/routes.py) · `list_workspace_members`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_members, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
| 200 | Successful Response | application/json | [PageResponse_WorkspaceMemberResponse_](schemas.md#schema-PageResponse_WorkspaceMemberResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/members`

Create Workspace Member

Operation ID：`create_workspace_member_api_v1_workspaces__workspace_id__members_post`。

实现：[src/opsmesh/workspaces/members/routes.py](../../src/opsmesh/workspaces/members/routes.py) · `create_workspace_member`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_members, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/WorkspaceMemberCreateRequest"
}
```

模型：[WorkspaceMemberCreateRequest](schemas.md#schema-WorkspaceMemberCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [WorkspaceMemberResponse](schemas.md#schema-WorkspaceMemberResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## DELETE `/api/v1/workspaces/{workspace_id}/members/{member_id}`

Disable Workspace Member

Operation ID：`disable_workspace_member_api_v1_workspaces__workspace_id__members__member_id__delete`。

实现：[src/opsmesh/workspaces/members/routes.py](../../src/opsmesh/workspaces/members/routes.py) · `disable_workspace_member`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_members, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `member_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceMemberResponse](schemas.md#schema-WorkspaceMemberResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/workspaces/{workspace_id}/members/{member_id}`

Update Workspace Member

Operation ID：`update_workspace_member_api_v1_workspaces__workspace_id__members__member_id__patch`。

实现：[src/opsmesh/workspaces/members/routes.py](../../src/opsmesh/workspaces/members/routes.py) · `update_workspace_member`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_members, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `member_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/WorkspaceMemberUpdateRequest"
}
```

模型：[WorkspaceMemberUpdateRequest](schemas.md#schema-WorkspaceMemberUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceMemberResponse](schemas.md#schema-WorkspaceMemberResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/quotas`

List Workspace Quotas

Operation ID：`list_workspace_quotas_api_v1_workspaces__workspace_id__quotas_get`。

实现：[src/opsmesh/workspaces/quotas/routes.py](../../src/opsmesh/workspaces/quotas/routes.py) · `list_workspace_quotas`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
| 200 | Successful Response | application/json | [PageResponse_WorkspaceQuotaResponse_](schemas.md#schema-PageResponse_WorkspaceQuotaResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PUT `/api/v1/workspaces/{workspace_id}/quotas`

Upsert Workspace Quotas

Operation ID：`upsert_workspace_quotas_api_v1_workspaces__workspace_id__quotas_put`。

实现：[src/opsmesh/workspaces/quotas/routes.py](../../src/opsmesh/workspaces/quotas/routes.py) · `upsert_workspace_quotas`。

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
  "$ref": "#/components/schemas/WorkspaceQuotaUpsertRequest"
}
```

模型：[WorkspaceQuotaUpsertRequest](schemas.md#schema-WorkspaceQuotaUpsertRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[WorkspaceQuotaResponse](schemas.md#schema-WorkspaceQuotaResponse)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/quotas/execution-summary`

Get Workspace Execution Slot Summary

Operation ID：`get_workspace_execution_slot_summary_api_v1_workspaces__workspace_id__quotas_execution_summary_get`。

实现：[src/opsmesh/workspaces/quotas/routes.py](../../src/opsmesh/workspaces/quotas/routes.py) · `get_workspace_execution_slot_summary`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceExecutionSlotSummaryResponse](schemas.md#schema-WorkspaceExecutionSlotSummaryResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## DELETE `/api/v1/workspaces/{workspace_id}/quotas/{quota_key}`

Disable Workspace Quota

Operation ID：`disable_workspace_quota_api_v1_workspaces__workspace_id__quotas__quota_key__delete`。

实现：[src/opsmesh/workspaces/quotas/routes.py](../../src/opsmesh/workspaces/quotas/routes.py) · `disable_workspace_quota`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `quota_key` | path | 是 | string | — |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceQuotaResponse](schemas.md#schema-WorkspaceQuotaResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
