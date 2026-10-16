# identity

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| POST | `/api/v1/auth/login` | Login |
| POST | `/api/v1/auth/logout` | Logout |
| POST | `/api/v1/auth/password` | Change Password |
| POST | `/api/v1/auth/register` | Register |
| GET | `/api/v1/auth/sessions` | List Sessions |
| DELETE | `/api/v1/auth/sessions/{session_id}` | Revoke Session |
| GET | `/api/v1/invitations` | List Invitations |
| POST | `/api/v1/invitations` | Create Invitation |
| POST | `/api/v1/invitations/accept` | Accept Invitation |
| GET | `/api/v1/invitations/{invitation_id}` | Read Invitation |
| POST | `/api/v1/invitations/{invitation_id}/resend` | Resend Invitation |
| POST | `/api/v1/invitations/{invitation_id}/revoke` | Revoke Invitation |
| GET | `/api/v1/users` | List Users |
| GET | `/api/v1/users/me` | Read Current Profile |
| PATCH | `/api/v1/users/me` | Update Current Profile |
| DELETE | `/api/v1/users/me/avatar` | Delete Current Avatar |
| GET | `/api/v1/users/me/avatar` | Read Current Avatar |
| PUT | `/api/v1/users/me/avatar` | Upload Current Avatar |
| GET | `/api/v1/users/me/quotas` | Read Current Quota |
| GET | `/api/v1/users/{user_id}` | Read User |
| PATCH | `/api/v1/users/{user_id}` | Admin Update User |
| GET | `/api/v1/users/{user_id}/quotas` | Read User Quota |
| PUT | `/api/v1/users/{user_id}/quotas` | Update User Quota |

## POST `/api/v1/auth/login`

Login

Operation ID：`identity.auth.login`。

实现：[src/opsmesh/identity/auth/routes.py](../../src/opsmesh/identity/auth/routes.py) · `login`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

参数：无显式路径、查询或 Header 参数。

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/LoginRequest"
}
```

模型：[LoginRequest](schemas.md#schema-LoginRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SessionGrant](schemas.md#schema-SessionGrant) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/auth/logout`

Logout

Operation ID：`identity.auth.logout`。

实现：[src/opsmesh/identity/auth/routes.py](../../src/opsmesh/identity/auth/routes.py) · `logout`。

Revoke the presenting session; the identity channel stays available.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
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

## POST `/api/v1/auth/password`

Change Password

Operation ID：`identity.auth.change_password`。

实现：[src/opsmesh/identity/auth/routes.py](../../src/opsmesh/identity/auth/routes.py) · `change_password`。

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
  "$ref": "#/components/schemas/ChangePasswordRequest"
}
```

模型：[ChangePasswordRequest](schemas.md#schema-ChangePasswordRequest)

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

## POST `/api/v1/auth/register`

Register

Operation ID：`identity.auth.register`。

实现：[src/opsmesh/identity/auth/routes.py](../../src/opsmesh/identity/auth/routes.py) · `register`。

Open an account and return its first session.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

参数：无显式路径、查询或 Header 参数。

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/RegisterRequest"
}
```

模型：[RegisterRequest](schemas.md#schema-RegisterRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [SessionGrant](schemas.md#schema-SessionGrant) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/auth/sessions`

List Sessions

Operation ID：`identity.auth.sessions.list`。

实现：[src/opsmesh/identity/auth/routes.py](../../src/opsmesh/identity/auth/routes.py) · `list_sessions`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[SessionInfo](schemas.md#schema-SessionInfo)&gt; |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## DELETE `/api/v1/auth/sessions/{session_id}`

Revoke Session

Operation ID：`identity.auth.sessions.revoke`。

实现：[src/opsmesh/identity/auth/routes.py](../../src/opsmesh/identity/auth/routes.py) · `revoke_session`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `session_id` | path | 是 | string (uuid) | `format="uuid"` |
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

## GET `/api/v1/invitations`

List Invitations

Operation ID：`identity.invitations.list`。

实现：[src/opsmesh/identity/invitations/routes.py](../../src/opsmesh/identity/invitations/routes.py) · `list_invitations`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 20, "type": "string"}, {"type": "null"}]` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [InvitationPage](schemas.md#schema-InvitationPage) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/invitations`

Create Invitation

Operation ID：`identity.invitations.create`。

实现：[src/opsmesh/identity/invitations/routes.py](../../src/opsmesh/identity/invitations/routes.py) · `create_invitation`。

Create one pending invitation and register its mail in the same transaction.

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
  "$ref": "#/components/schemas/CreateInvitationRequest"
}
```

模型：[CreateInvitationRequest](schemas.md#schema-CreateInvitationRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [InvitationView](schemas.md#schema-InvitationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/invitations/accept`

Accept Invitation

Operation ID：`identity.invitations.accept`。

实现：[src/opsmesh/identity/invitations/routes.py](../../src/opsmesh/identity/invitations/routes.py) · `accept_invitation`。

Accept one invitation with its token; no session exists before acceptance.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

参数：无显式路径、查询或 Header 参数。

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AcceptInvitationRequest"
}
```

模型：[AcceptInvitationRequest](schemas.md#schema-AcceptInvitationRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AcceptInvitationResponse](schemas.md#schema-AcceptInvitationResponse) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/invitations/{invitation_id}`

Read Invitation

Operation ID：`identity.invitations.read`。

实现：[src/opsmesh/identity/invitations/routes.py](../../src/opsmesh/identity/invitations/routes.py) · `read_invitation`。

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
| 200 | Successful Response | application/json | [InvitationView](schemas.md#schema-InvitationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/invitations/{invitation_id}/resend`

Resend Invitation

Operation ID：`identity.invitations.resend`。

实现：[src/opsmesh/identity/invitations/routes.py](../../src/opsmesh/identity/invitations/routes.py) · `resend_invitation`。

Resend with a freshly rotated token; the delivered code stops working.

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
| 200 | Successful Response | application/json | [InvitationView](schemas.md#schema-InvitationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/invitations/{invitation_id}/revoke`

Revoke Invitation

Operation ID：`identity.invitations.revoke`。

实现：[src/opsmesh/identity/invitations/routes.py](../../src/opsmesh/identity/invitations/routes.py) · `revoke_invitation`。

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
| 200 | Successful Response | application/json | [InvitationView](schemas.md#schema-InvitationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/users`

List Users

Operation ID：`identity.users.list`。

实现：[src/opsmesh/identity/users/routes.py](../../src/opsmesh/identity/users/routes.py) · `list_users`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 20, "type": "string"}, {"type": "null"}]` |
| `email_query` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 320, "type": "string"}, {"type": "null"}]` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [UserPage](schemas.md#schema-UserPage) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/users/me`

Read Current Profile

Operation ID：`identity.users.read_current`。

实现：[src/opsmesh/identity/users/routes.py](../../src/opsmesh/identity/users/routes.py) · `read_current_profile`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [UserProfileView](schemas.md#schema-UserProfileView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## PATCH `/api/v1/users/me`

Update Current Profile

Operation ID：`identity.users.update_current`。

实现：[src/opsmesh/identity/users/routes.py](../../src/opsmesh/identity/users/routes.py) · `update_current_profile`。

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
  "$ref": "#/components/schemas/UpdateProfileRequest"
}
```

模型：[UpdateProfileRequest](schemas.md#schema-UpdateProfileRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [UserProfileView](schemas.md#schema-UserProfileView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## DELETE `/api/v1/users/me/avatar`

Delete Current Avatar

Operation ID：`identity.users.avatar.delete`。

实现：[src/opsmesh/identity/users/routes.py](../../src/opsmesh/identity/users/routes.py) · `delete_current_avatar`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
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

## GET `/api/v1/users/me/avatar`

Read Current Avatar

Operation ID：`identity.users.avatar.read`。

实现：[src/opsmesh/identity/users/routes.py](../../src/opsmesh/identity/users/routes.py) · `read_current_avatar`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | any |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## PUT `/api/v1/users/me/avatar`

Upload Current Avatar

Operation ID：`identity.users.avatar.upload`。

实现：[src/opsmesh/identity/users/routes.py](../../src/opsmesh/identity/users/routes.py) · `upload_current_avatar`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `Content-Type` | header | 是 | string | — |
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
  "contentMediaType": "application/octet-stream",
  "title": "Body",
  "type": "string"
}
```

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

## GET `/api/v1/users/me/quotas`

Read Current Quota

Operation ID：`identity.quotas.read_current`。

实现：[src/opsmesh/identity/quotas/routes.py](../../src/opsmesh/identity/quotas/routes.py) · `read_current_quota`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [QuotaView](schemas.md#schema-QuotaView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/users/{user_id}`

Read User

Operation ID：`identity.users.read`。

实现：[src/opsmesh/identity/users/routes.py](../../src/opsmesh/identity/users/routes.py) · `read_user`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `user_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [UserProfileView](schemas.md#schema-UserProfileView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## PATCH `/api/v1/users/{user_id}`

Admin Update User

Operation ID：`identity.users.update`。

实现：[src/opsmesh/identity/users/routes.py](../../src/opsmesh/identity/users/routes.py) · `admin_update_user`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
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
  "$ref": "#/components/schemas/AdminUpdateUserRequest"
}
```

模型：[AdminUpdateUserRequest](schemas.md#schema-AdminUpdateUserRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [UserProfileView](schemas.md#schema-UserProfileView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/users/{user_id}/quotas`

Read User Quota

Operation ID：`identity.quotas.read`。

实现：[src/opsmesh/identity/quotas/routes.py](../../src/opsmesh/identity/quotas/routes.py) · `read_user_quota`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `user_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [QuotaView](schemas.md#schema-QuotaView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## PUT `/api/v1/users/{user_id}/quotas`

Update User Quota

Operation ID：`identity.quotas.update`。

实现：[src/opsmesh/identity/quotas/routes.py](../../src/opsmesh/identity/quotas/routes.py) · `update_user_quota`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
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
  "$ref": "#/components/schemas/QuotaLimitsRequest"
}
```

模型：[QuotaLimitsRequest](schemas.md#schema-QuotaLimitsRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [QuotaView](schemas.md#schema-QuotaView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。
