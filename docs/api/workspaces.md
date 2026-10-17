# workspaces

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/workspaces` | List Workspaces |
| POST | `/api/v1/workspaces` | Create Workspace |
| GET | `/api/v1/workspaces/invitations/received` | List Received Invitations |
| POST | `/api/v1/workspaces/invitations/{invitation_id}/accept` | Accept Invitation |
| GET | `/api/v1/workspaces/projects/invitations/received` | List Received Invitations |
| POST | `/api/v1/workspaces/projects/invitations/{invitation_id}/accept` | Accept Invitation |
| DELETE | `/api/v1/workspaces/{workspace_id}` | Delete Workspace |
| GET | `/api/v1/workspaces/{workspace_id}` | Read Workspace |
| PATCH | `/api/v1/workspaces/{workspace_id}` | Update Workspace |
| POST | `/api/v1/workspaces/{workspace_id}/archive` | Archive Workspace |
| GET | `/api/v1/workspaces/{workspace_id}/capacity` | Read Capacity |
| PUT | `/api/v1/workspaces/{workspace_id}/capacity/{kind}` | Update Capacity Limit |
| GET | `/api/v1/workspaces/{workspace_id}/credentials` | List Credentials |
| POST | `/api/v1/workspaces/{workspace_id}/credentials` | Create Credential |
| GET | `/api/v1/workspaces/{workspace_id}/credentials/{credential_id}` | Read Credential |
| POST | `/api/v1/workspaces/{workspace_id}/credentials/{credential_id}/revoke` | Revoke Credential |
| POST | `/api/v1/workspaces/{workspace_id}/credentials/{credential_id}/rotate` | Rotate Credential |
| GET | `/api/v1/workspaces/{workspace_id}/members` | List Members |
| GET | `/api/v1/workspaces/{workspace_id}/members/invitations` | List Invitations |
| POST | `/api/v1/workspaces/{workspace_id}/members/invitations` | Create Invitation |
| DELETE | `/api/v1/workspaces/{workspace_id}/members/invitations/{invitation_id}` | Revoke Invitation |
| DELETE | `/api/v1/workspaces/{workspace_id}/members/{user_id}` | Remove Member |
| PATCH | `/api/v1/workspaces/{workspace_id}/members/{user_id}` | Update Member Role |
| GET | `/api/v1/workspaces/{workspace_id}/model-providers` | List Connections |
| POST | `/api/v1/workspaces/{workspace_id}/model-providers` | Create Connection |
| GET | `/api/v1/workspaces/{workspace_id}/model-providers/default` | Read Default Model |
| DELETE | `/api/v1/workspaces/{workspace_id}/model-providers/{connection_id}` | Disable Connection |
| GET | `/api/v1/workspaces/{workspace_id}/model-providers/{connection_id}` | Read Connection |
| PATCH | `/api/v1/workspaces/{workspace_id}/model-providers/{connection_id}` | Update Connection |
| POST | `/api/v1/workspaces/{workspace_id}/model-providers/{connection_id}/default` | Set Default Connection |
| GET | `/api/v1/workspaces/{workspace_id}/model-providers/{connection_id}/probe` | Read Probe |
| POST | `/api/v1/workspaces/{workspace_id}/model-providers/{connection_id}/probe` | Start Probe |
| GET | `/api/v1/workspaces/{workspace_id}/notifications` | List Notifications |
| GET | `/api/v1/workspaces/{workspace_id}/notifications/preferences` | Read Preferences |
| PUT | `/api/v1/workspaces/{workspace_id}/notifications/preferences` | Update Preferences |
| POST | `/api/v1/workspaces/{workspace_id}/notifications/read` | Mark Notifications Read |
| POST | `/api/v1/workspaces/{workspace_id}/notifications/{notification_id}/archive` | Archive Notification |
| GET | `/api/v1/workspaces/{workspace_id}/projects` | List Projects |
| POST | `/api/v1/workspaces/{workspace_id}/projects` | Create Project |
| DELETE | `/api/v1/workspaces/{workspace_id}/projects/{project_id}` | Delete Project |
| GET | `/api/v1/workspaces/{workspace_id}/projects/{project_id}` | Read Project |
| PATCH | `/api/v1/workspaces/{workspace_id}/projects/{project_id}` | Update Project |
| POST | `/api/v1/workspaces/{workspace_id}/projects/{project_id}/archive` | Archive Project |
| GET | `/api/v1/workspaces/{workspace_id}/projects/{project_id}/members` | List Members |
| GET | `/api/v1/workspaces/{workspace_id}/projects/{project_id}/members/invitations` | List Invitations |
| POST | `/api/v1/workspaces/{workspace_id}/projects/{project_id}/members/invitations` | Create Invitation |
| DELETE | `/api/v1/workspaces/{workspace_id}/projects/{project_id}/members/invitations/{invitation_id}` | Revoke Invitation |
| DELETE | `/api/v1/workspaces/{workspace_id}/projects/{project_id}/members/{user_id}` | Remove Member |
| PATCH | `/api/v1/workspaces/{workspace_id}/projects/{project_id}/members/{user_id}` | Update Member Role |
| POST | `/api/v1/workspaces/{workspace_id}/transfer-ownership` | Transfer Workspace Ownership |

## GET `/api/v1/workspaces`

List Workspaces

Operation ID：`workspaces.list`。

实现：[src/opsmesh/workspaces/routes.py](../../src/opsmesh/workspaces/routes.py) · `list_workspaces`。

List the workspaces the current user is a member of.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspacePage](schemas.md#schema-WorkspacePage) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/workspaces`

Create Workspace

Operation ID：`workspaces.create`。

实现：[src/opsmesh/workspaces/routes.py](../../src/opsmesh/workspaces/routes.py) · `create_workspace`。

Create one workspace; the creator becomes its owner.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

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
| 201 | Successful Response | application/json | [WorkspaceView](schemas.md#schema-WorkspaceView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/workspaces/invitations/received`

List Received Invitations

Operation ID：`workspaces.invitations.received`。

实现：[src/opsmesh/workspaces/members/routes.py](../../src/opsmesh/workspaces/members/routes.py) · `list_received_invitations`。

List the workspace invitations addressed to the current user.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SpaceInvitationPage](schemas.md#schema-SpaceInvitationPage) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/workspaces/invitations/{invitation_id}/accept`

Accept Invitation

Operation ID：`workspaces.invitations.accept`。

实现：[src/opsmesh/workspaces/members/routes.py](../../src/opsmesh/workspaces/members/routes.py) · `accept_invitation`。

Accept one invitation addressed to the current user and join.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `invitation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [SpaceMemberView](schemas.md#schema-SpaceMemberView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/workspaces/projects/invitations/received`

List Received Invitations

Operation ID：`workspaces.projects.invitations.received`。

实现：[src/opsmesh/workspaces/projects/members/routes.py](../../src/opsmesh/workspaces/projects/members/routes.py) · `list_received_invitations`。

List the project invitations addressed to the current user.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ProjectInvitationPage](schemas.md#schema-ProjectInvitationPage) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/workspaces/projects/invitations/{invitation_id}/accept`

Accept Invitation

Operation ID：`workspaces.projects.invitations.accept`。

实现：[src/opsmesh/workspaces/projects/members/routes.py](../../src/opsmesh/workspaces/projects/members/routes.py) · `accept_invitation`。

Accept one project invitation addressed to the current user and join.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `invitation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [ProjectMemberView](schemas.md#schema-ProjectMemberView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## DELETE `/api/v1/workspaces/{workspace_id}`

Delete Workspace

Operation ID：`workspaces.delete`。

实现：[src/opsmesh/workspaces/routes.py](../../src/opsmesh/workspaces/routes.py) · `delete_workspace`。

Delete one workspace; its history stays and every access is refused.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 204 | Successful Response | — | 无响应体 |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/workspaces/{workspace_id}`

Read Workspace

Operation ID：`workspaces.read`。

实现：[src/opsmesh/workspaces/routes.py](../../src/opsmesh/workspaces/routes.py) · `read_workspace`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceView](schemas.md#schema-WorkspaceView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## PATCH `/api/v1/workspaces/{workspace_id}`

Update Workspace

Operation ID：`workspaces.update`。

实现：[src/opsmesh/workspaces/routes.py](../../src/opsmesh/workspaces/routes.py) · `update_workspace`。

Change the workspace metadata; an archived workspace refuses writes.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

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
| 200 | Successful Response | application/json | [WorkspaceView](schemas.md#schema-WorkspaceView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/workspaces/{workspace_id}/archive`

Archive Workspace

Operation ID：`workspaces.archive`。

实现：[src/opsmesh/workspaces/routes.py](../../src/opsmesh/workspaces/routes.py) · `archive_workspace`。

Archive one workspace; reads stay open, writes are refused.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceView](schemas.md#schema-WorkspaceView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/workspaces/{workspace_id}/capacity`

Read Capacity

Operation ID：`workspaces.capacity.read`。

实现：[src/opsmesh/workspaces/capacity/routes.py](../../src/opsmesh/workspaces/capacity/routes.py) · `read_capacity`。

Read every capacity kind's limit and held reservations.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [CapacitySnapshotView](schemas.md#schema-CapacitySnapshotView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## PUT `/api/v1/workspaces/{workspace_id}/capacity/{kind}`

Update Capacity Limit

Operation ID：`workspaces.capacity.update_limit`。

实现：[src/opsmesh/workspaces/capacity/routes.py](../../src/opsmesh/workspaces/capacity/routes.py) · `update_capacity_limit`。

Replace one capacity kind's limit; below the held amount is refused.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `kind` | path | 是 | string | `maxLength=32` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/CapacityLimitUpdateRequest"
}
```

模型：[CapacityLimitUpdateRequest](schemas.md#schema-CapacityLimitUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [CapacitySnapshotView](schemas.md#schema-CapacitySnapshotView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/workspaces/{workspace_id}/credentials`

List Credentials

Operation ID：`workspaces.credentials.list`。

实现：[src/opsmesh/workspaces/credentials/routes.py](../../src/opsmesh/workspaces/credentials/routes.py) · `list_credentials`。

List the workspace's credential metadata.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [CredentialPage](schemas.md#schema-CredentialPage) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/workspaces/{workspace_id}/credentials`

Create Credential

Operation ID：`workspaces.credentials.create`。

实现：[src/opsmesh/workspaces/credentials/routes.py](../../src/opsmesh/workspaces/credentials/routes.py) · `create_credential`。

Store one encrypted workspace secret.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/CredentialCreateRequest"
}
```

模型：[CredentialCreateRequest](schemas.md#schema-CredentialCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [CredentialView](schemas.md#schema-CredentialView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/workspaces/{workspace_id}/credentials/{credential_id}`

Read Credential

Operation ID：`workspaces.credentials.read`。

实现：[src/opsmesh/workspaces/credentials/routes.py](../../src/opsmesh/workspaces/credentials/routes.py) · `read_credential`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `credential_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [CredentialView](schemas.md#schema-CredentialView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/workspaces/{workspace_id}/credentials/{credential_id}/revoke`

Revoke Credential

Operation ID：`workspaces.credentials.revoke`。

实现：[src/opsmesh/workspaces/credentials/routes.py](../../src/opsmesh/workspaces/credentials/routes.py) · `revoke_credential`。

Revoke one credential; every future resolution is refused.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `credential_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [CredentialView](schemas.md#schema-CredentialView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/workspaces/{workspace_id}/credentials/{credential_id}/rotate`

Rotate Credential

Operation ID：`workspaces.credentials.rotate`。

实现：[src/opsmesh/workspaces/credentials/routes.py](../../src/opsmesh/workspaces/credentials/routes.py) · `rotate_credential`。

Replace the stored secret and advance its revision.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `credential_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/CredentialRotateRequest"
}
```

模型：[CredentialRotateRequest](schemas.md#schema-CredentialRotateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [CredentialView](schemas.md#schema-CredentialView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/workspaces/{workspace_id}/members`

List Members

Operation ID：`workspaces.members.list`。

实现：[src/opsmesh/workspaces/members/routes.py](../../src/opsmesh/workspaces/members/routes.py) · `list_members`。

List the members of one workspace.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SpaceMemberPage](schemas.md#schema-SpaceMemberPage) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/workspaces/{workspace_id}/members/invitations`

List Invitations

Operation ID：`workspaces.invitations.list`。

实现：[src/opsmesh/workspaces/members/routes.py](../../src/opsmesh/workspaces/members/routes.py) · `list_invitations`。

List the invitations of one workspace.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SpaceInvitationPage](schemas.md#schema-SpaceInvitationPage) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/workspaces/{workspace_id}/members/invitations`

Create Invitation

Operation ID：`workspaces.invitations.create`。

实现：[src/opsmesh/workspaces/members/routes.py](../../src/opsmesh/workspaces/members/routes.py) · `create_invitation`。

Invite one active user to the workspace under a stated role.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/InvitationCreateRequest"
}
```

模型：[InvitationCreateRequest](schemas.md#schema-InvitationCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [SpaceInvitationView](schemas.md#schema-SpaceInvitationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## DELETE `/api/v1/workspaces/{workspace_id}/members/invitations/{invitation_id}`

Revoke Invitation

Operation ID：`workspaces.invitations.revoke`。

实现：[src/opsmesh/workspaces/members/routes.py](../../src/opsmesh/workspaces/members/routes.py) · `revoke_invitation`。

Revoke one pending invitation; a decided one is never rewritten.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `invitation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SpaceInvitationView](schemas.md#schema-SpaceInvitationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## DELETE `/api/v1/workspaces/{workspace_id}/members/{user_id}`

Remove Member

Operation ID：`workspaces.members.remove`。

实现：[src/opsmesh/workspaces/members/routes.py](../../src/opsmesh/workspaces/members/routes.py) · `remove_member`。

Remove one member, or leave the workspace as that member.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `user_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 204 | Successful Response | — | 无响应体 |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## PATCH `/api/v1/workspaces/{workspace_id}/members/{user_id}`

Update Member Role

Operation ID：`workspaces.members.update_role`。

实现：[src/opsmesh/workspaces/members/routes.py](../../src/opsmesh/workspaces/members/routes.py) · `update_member_role`。

Change one member's space role; ownership moves only by transfer.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `user_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/MemberRoleUpdateRequest"
}
```

模型：[MemberRoleUpdateRequest](schemas.md#schema-MemberRoleUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SpaceMemberView](schemas.md#schema-SpaceMemberView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/workspaces/{workspace_id}/model-providers`

List Connections

Operation ID：`workspaces.model_providers.list`。

实现：[src/opsmesh/workspaces/model_providers/routes.py](../../src/opsmesh/workspaces/model_providers/routes.py) · `list_connections`。

List the workspace's model provider connections.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ModelProviderPage](schemas.md#schema-ModelProviderPage) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/workspaces/{workspace_id}/model-providers`

Create Connection

Operation ID：`workspaces.model_providers.create`。

实现：[src/opsmesh/workspaces/model_providers/routes.py](../../src/opsmesh/workspaces/model_providers/routes.py) · `create_connection`。

Register one connection over an existing active provider credential.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ModelProviderCreateRequest"
}
```

模型：[ModelProviderCreateRequest](schemas.md#schema-ModelProviderCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [ModelProviderView](schemas.md#schema-ModelProviderView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/workspaces/{workspace_id}/model-providers/default`

Read Default Model

Operation ID：`workspaces.model_providers.read_default`。

实现：[src/opsmesh/workspaces/model_providers/routes.py](../../src/opsmesh/workspaces/model_providers/routes.py) · `read_default_model`。

Resolve the workspace's default model; a missing default is a 404.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [DefaultModelView](schemas.md#schema-DefaultModelView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## DELETE `/api/v1/workspaces/{workspace_id}/model-providers/{connection_id}`

Disable Connection

Operation ID：`workspaces.model_providers.disable`。

实现：[src/opsmesh/workspaces/model_providers/routes.py](../../src/opsmesh/workspaces/model_providers/routes.py) · `disable_connection`。

Disable one connection; a disabled default steps down with it.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `connection_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ModelProviderView](schemas.md#schema-ModelProviderView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/workspaces/{workspace_id}/model-providers/{connection_id}`

Read Connection

Operation ID：`workspaces.model_providers.read`。

实现：[src/opsmesh/workspaces/model_providers/routes.py](../../src/opsmesh/workspaces/model_providers/routes.py) · `read_connection`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `connection_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ModelProviderView](schemas.md#schema-ModelProviderView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## PATCH `/api/v1/workspaces/{workspace_id}/model-providers/{connection_id}`

Update Connection

Operation ID：`workspaces.model_providers.update`。

实现：[src/opsmesh/workspaces/model_providers/routes.py](../../src/opsmesh/workspaces/model_providers/routes.py) · `update_connection`。

Change one connection's catalog, endpoint or default selection.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `connection_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ModelProviderUpdateRequest"
}
```

模型：[ModelProviderUpdateRequest](schemas.md#schema-ModelProviderUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ModelProviderView](schemas.md#schema-ModelProviderView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/workspaces/{workspace_id}/model-providers/{connection_id}/default`

Set Default Connection

Operation ID：`workspaces.model_providers.set_default`。

实现：[src/opsmesh/workspaces/model_providers/routes.py](../../src/opsmesh/workspaces/model_providers/routes.py) · `set_default_connection`。

Make one connection the workspace default.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `connection_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ModelProviderView](schemas.md#schema-ModelProviderView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/workspaces/{workspace_id}/model-providers/{connection_id}/probe`

Read Probe

Operation ID：`workspaces.model_providers.read_probe`。

实现：[src/opsmesh/workspaces/model_providers/routes.py](../../src/opsmesh/workspaces/model_providers/routes.py) · `read_probe`。

Read the latest probe state of one connection.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `connection_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ProbeView](schemas.md#schema-ProbeView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/workspaces/{workspace_id}/model-providers/{connection_id}/probe`

Start Probe

Operation ID：`workspaces.model_providers.probe`。

实现：[src/opsmesh/workspaces/model_providers/routes.py](../../src/opsmesh/workspaces/model_providers/routes.py) · `start_probe`。

Start one durable connection probe; the result is read back later.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `connection_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 202 | Successful Response | application/json | [ProbeView](schemas.md#schema-ProbeView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/workspaces/{workspace_id}/notifications`

List Notifications

Operation ID：`workspaces.notifications.list`。

实现：[src/opsmesh/workspaces/notifications/routes.py](../../src/opsmesh/workspaces/notifications/routes.py) · `list_notifications`。

List the current user's inbox in one workspace.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `include_archived` | query | 否 | boolean | `default=false` |
| `unread_only` | query | 否 | boolean | `default=false` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [NotificationPage](schemas.md#schema-NotificationPage) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/workspaces/{workspace_id}/notifications/preferences`

Read Preferences

Operation ID：`workspaces.notifications.read_preferences`。

实现：[src/opsmesh/workspaces/notifications/routes.py](../../src/opsmesh/workspaces/notifications/routes.py) · `read_preferences`。

Read the current user's notification preferences in one workspace.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [NotificationPreferenceView](schemas.md#schema-NotificationPreferenceView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## PUT `/api/v1/workspaces/{workspace_id}/notifications/preferences`

Update Preferences

Operation ID：`workspaces.notifications.update_preferences`。

实现：[src/opsmesh/workspaces/notifications/routes.py](../../src/opsmesh/workspaces/notifications/routes.py) · `update_preferences`。

Change the current user's notification preferences.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/NotificationPreferenceUpdateRequest"
}
```

模型：[NotificationPreferenceUpdateRequest](schemas.md#schema-NotificationPreferenceUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [NotificationPreferenceView](schemas.md#schema-NotificationPreferenceView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/workspaces/{workspace_id}/notifications/read`

Mark Notifications Read

Operation ID：`workspaces.notifications.mark_read`。

实现：[src/opsmesh/workspaces/notifications/routes.py](../../src/opsmesh/workspaces/notifications/routes.py) · `mark_notifications_read`。

Mark named notifications, or the whole inbox, read.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/MarkReadRequest"
}
```

模型：[MarkReadRequest](schemas.md#schema-MarkReadRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [MarkReadView](schemas.md#schema-MarkReadView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/workspaces/{workspace_id}/notifications/{notification_id}/archive`

Archive Notification

Operation ID：`workspaces.notifications.archive`。

实现：[src/opsmesh/workspaces/notifications/routes.py](../../src/opsmesh/workspaces/notifications/routes.py) · `archive_notification`。

Archive one of the current user's own notifications.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `notification_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [NotificationView](schemas.md#schema-NotificationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/workspaces/{workspace_id}/projects`

List Projects

Operation ID：`workspaces.projects.list`。

实现：[src/opsmesh/workspaces/projects/routes.py](../../src/opsmesh/workspaces/projects/routes.py) · `list_projects`。

List the projects of one workspace the current user may see.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ProjectPage](schemas.md#schema-ProjectPage) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/workspaces/{workspace_id}/projects`

Create Project

Operation ID：`workspaces.projects.create`。

实现：[src/opsmesh/workspaces/projects/routes.py](../../src/opsmesh/workspaces/projects/routes.py) · `create_project`。

Create one project; the creator becomes its administrator.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ProjectCreateRequest"
}
```

模型：[ProjectCreateRequest](schemas.md#schema-ProjectCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [ProjectView](schemas.md#schema-ProjectView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## DELETE `/api/v1/workspaces/{workspace_id}/projects/{project_id}`

Delete Project

Operation ID：`workspaces.projects.delete`。

实现：[src/opsmesh/workspaces/projects/routes.py](../../src/opsmesh/workspaces/projects/routes.py) · `delete_project`。

Delete one project; its history stays and every access is refused.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `project_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 204 | Successful Response | — | 无响应体 |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/workspaces/{workspace_id}/projects/{project_id}`

Read Project

Operation ID：`workspaces.projects.read`。

实现：[src/opsmesh/workspaces/projects/routes.py](../../src/opsmesh/workspaces/projects/routes.py) · `read_project`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `project_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ProjectView](schemas.md#schema-ProjectView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## PATCH `/api/v1/workspaces/{workspace_id}/projects/{project_id}`

Update Project

Operation ID：`workspaces.projects.update`。

实现：[src/opsmesh/workspaces/projects/routes.py](../../src/opsmesh/workspaces/projects/routes.py) · `update_project`。

Change the project metadata; an archived project refuses writes.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `project_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ProjectUpdateRequest"
}
```

模型：[ProjectUpdateRequest](schemas.md#schema-ProjectUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ProjectView](schemas.md#schema-ProjectView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/workspaces/{workspace_id}/projects/{project_id}/archive`

Archive Project

Operation ID：`workspaces.projects.archive`。

实现：[src/opsmesh/workspaces/projects/routes.py](../../src/opsmesh/workspaces/projects/routes.py) · `archive_project`。

Archive one project; reads stay open, writes are refused.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `project_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ProjectView](schemas.md#schema-ProjectView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/workspaces/{workspace_id}/projects/{project_id}/members`

List Members

Operation ID：`workspaces.projects.members.list`。

实现：[src/opsmesh/workspaces/projects/members/routes.py](../../src/opsmesh/workspaces/projects/members/routes.py) · `list_members`。

List the members of one project.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `project_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ProjectMemberPage](schemas.md#schema-ProjectMemberPage) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/workspaces/{workspace_id}/projects/{project_id}/members/invitations`

List Invitations

Operation ID：`workspaces.projects.invitations.list`。

实现：[src/opsmesh/workspaces/projects/members/routes.py](../../src/opsmesh/workspaces/projects/members/routes.py) · `list_invitations`。

List the invitations of one project.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `project_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ProjectInvitationPage](schemas.md#schema-ProjectInvitationPage) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/workspaces/{workspace_id}/projects/{project_id}/members/invitations`

Create Invitation

Operation ID：`workspaces.projects.invitations.create`。

实现：[src/opsmesh/workspaces/projects/members/routes.py](../../src/opsmesh/workspaces/projects/members/routes.py) · `create_invitation`。

Invite one workspace member to the project under a stated role.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `project_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/InvitationCreateRequest"
}
```

模型：[InvitationCreateRequest](schemas.md#schema-InvitationCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [ProjectInvitationView](schemas.md#schema-ProjectInvitationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## DELETE `/api/v1/workspaces/{workspace_id}/projects/{project_id}/members/invitations/{invitation_id}`

Revoke Invitation

Operation ID：`workspaces.projects.invitations.revoke`。

实现：[src/opsmesh/workspaces/projects/members/routes.py](../../src/opsmesh/workspaces/projects/members/routes.py) · `revoke_invitation`。

Revoke one pending project invitation; a decided one is never rewritten.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `project_id` | path | 是 | string (uuid) | `format="uuid"` |
| `invitation_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ProjectInvitationView](schemas.md#schema-ProjectInvitationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## DELETE `/api/v1/workspaces/{workspace_id}/projects/{project_id}/members/{user_id}`

Remove Member

Operation ID：`workspaces.projects.members.remove`。

实现：[src/opsmesh/workspaces/projects/members/routes.py](../../src/opsmesh/workspaces/projects/members/routes.py) · `remove_member`。

Remove one project member, or leave the project as that member.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `project_id` | path | 是 | string (uuid) | `format="uuid"` |
| `user_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 204 | Successful Response | — | 无响应体 |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## PATCH `/api/v1/workspaces/{workspace_id}/projects/{project_id}/members/{user_id}`

Update Member Role

Operation ID：`workspaces.projects.members.update_role`。

实现：[src/opsmesh/workspaces/projects/members/routes.py](../../src/opsmesh/workspaces/projects/members/routes.py) · `update_member_role`。

Change one member's project role.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `project_id` | path | 是 | string (uuid) | `format="uuid"` |
| `user_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/MemberRoleUpdateRequest"
}
```

模型：[MemberRoleUpdateRequest](schemas.md#schema-MemberRoleUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ProjectMemberView](schemas.md#schema-ProjectMemberView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/workspaces/{workspace_id}/transfer-ownership`

Transfer Workspace Ownership

Operation ID：`workspaces.transfer_ownership`。

实现：[src/opsmesh/workspaces/routes.py](../../src/opsmesh/workspaces/routes.py) · `transfer_workspace_ownership`。

Move the owner role to another member; the previous owner becomes admin.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/TransferOwnershipRequest"
}
```

模型：[TransferOwnershipRequest](schemas.md#schema-TransferOwnershipRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceView](schemas.md#schema-WorkspaceView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。
