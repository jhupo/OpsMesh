# auth

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| POST | `/api/v1/auth/invitations/accept` | Accept User Invitation |
| POST | `/api/v1/auth/login` | Login User |
| POST | `/api/v1/auth/logout` | Logout User |
| GET | `/api/v1/auth/me` | Get Current User Profile |
| PATCH | `/api/v1/auth/me` | Update Current User Profile |
| GET | `/api/v1/auth/me/avatar` | Get Current User Avatar |
| PUT | `/api/v1/auth/password` | Change Current User Password |
| POST | `/api/v1/auth/register` | Register User |
| DELETE | `/api/v1/auth/tokens` | Revoke Current User Tokens |
| GET | `/api/v1/auth/tokens` | List Current User Tokens |
| POST | `/api/v1/auth/tokens` | Create Current User Token |
| DELETE | `/api/v1/auth/tokens/{token_id}` | Revoke Current User Token |
| POST | `/api/v1/auth/tokens/{token_id}/rotate` | Rotate Current User Token |

## POST `/api/v1/auth/invitations/accept`

Accept User Invitation

Operation ID：`accept_user_invitation_api_v1_auth_invitations_accept_post`。

实现：[src/opsmesh/identity/invitations/routes.py](../../src/opsmesh/identity/invitations/routes.py) · `accept_user_invitation`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

参数：无显式路径、查询或 Header 参数。

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AcceptUserInvitationRequest"
}
```

模型：[AcceptUserInvitationRequest](schemas.md#schema-AcceptUserInvitationRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [CurrentUserResponse](schemas.md#schema-CurrentUserResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/auth/login`

Login User

Operation ID：`login_user_api_v1_auth_login_post`。

实现：[src/opsmesh/identity/auth/routes.py](../../src/opsmesh/identity/auth/routes.py) · `login_user`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

参数：无显式路径、查询或 Header 参数。

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/UserLoginRequest"
}
```

模型：[UserLoginRequest](schemas.md#schema-UserLoginRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [UserAPITokenCreateResponse](schemas.md#schema-UserAPITokenCreateResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/auth/logout`

Logout User

Operation ID：`logout_user_api_v1_auth_logout_post`。

实现：[src/opsmesh/identity/auth/routes.py](../../src/opsmesh/identity/auth/routes.py) · `logout_user`。

权限依赖：`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 204 | Successful Response | — | 无响应体 |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/auth/me`

Get Current User Profile

Operation ID：`get_current_user_profile_api_v1_auth_me_get`。

实现：[src/opsmesh/identity/auth/routes.py](../../src/opsmesh/identity/auth/routes.py) · `get_current_user_profile`。

权限依赖：`opsmesh.identity.auth.dependencies.account_action_dependency.<locals>.require_account_action`：`action=profile:read`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [CurrentUserResponse](schemas.md#schema-CurrentUserResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/auth/me`

Update Current User Profile

Operation ID：`update_current_user_profile_api_v1_auth_me_patch`。

实现：[src/opsmesh/identity/auth/routes.py](../../src/opsmesh/identity/auth/routes.py) · `update_current_user_profile`。

权限依赖：`opsmesh.identity.auth.dependencies.account_action_dependency.<locals>.require_account_action`：`action=profile:write`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/CurrentUserUpdateRequest"
}
```

模型：[CurrentUserUpdateRequest](schemas.md#schema-CurrentUserUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [CurrentUserResponse](schemas.md#schema-CurrentUserResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/auth/me/avatar`

Get Current User Avatar

Operation ID：`get_current_user_avatar_api_v1_auth_me_avatar_get`。

实现：[src/opsmesh/identity/auth/routes.py](../../src/opsmesh/identity/auth/routes.py) · `get_current_user_avatar`。

权限依赖：`opsmesh.identity.auth.dependencies.account_action_dependency.<locals>.require_account_action`：`action=profile:read`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | image/webp | string (binary) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PUT `/api/v1/auth/password`

Change Current User Password

Operation ID：`change_current_user_password_api_v1_auth_password_put`。

实现：[src/opsmesh/identity/auth/routes.py](../../src/opsmesh/identity/auth/routes.py) · `change_current_user_password`。

权限依赖：`opsmesh.identity.auth.dependencies.account_action_dependency.<locals>.require_account_action`：`action=password:change`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/PasswordChangeRequest"
}
```

模型：[PasswordChangeRequest](schemas.md#schema-PasswordChangeRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [CurrentUserResponse](schemas.md#schema-CurrentUserResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/auth/register`

Register User

Operation ID：`register_user_api_v1_auth_register_post`。

实现：[src/opsmesh/identity/auth/routes.py](../../src/opsmesh/identity/auth/routes.py) · `register_user`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

参数：无显式路径、查询或 Header 参数。

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/UserRegisterRequest"
}
```

模型：[UserRegisterRequest](schemas.md#schema-UserRegisterRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [CurrentUserResponse](schemas.md#schema-CurrentUserResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## DELETE `/api/v1/auth/tokens`

Revoke Current User Tokens

Operation ID：`revoke_current_user_tokens_api_v1_auth_tokens_delete`。

实现：[src/opsmesh/identity/auth/routes.py](../../src/opsmesh/identity/auth/routes.py) · `revoke_current_user_tokens`。

权限依赖：`opsmesh.identity.auth.dependencies.account_action_dependency.<locals>.require_account_action`：`action=tokens:manage`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [UserAPITokenRevokeAllResponse](schemas.md#schema-UserAPITokenRevokeAllResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/auth/tokens`

List Current User Tokens

Operation ID：`list_current_user_tokens_api_v1_auth_tokens_get`。

实现：[src/opsmesh/identity/auth/routes.py](../../src/opsmesh/identity/auth/routes.py) · `list_current_user_tokens`。

权限依赖：`opsmesh.identity.auth.dependencies.account_action_dependency.<locals>.require_account_action`：`action=tokens:read`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[UserAPITokenResponse](schemas.md#schema-UserAPITokenResponse)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/auth/tokens`

Create Current User Token

Operation ID：`create_current_user_token_api_v1_auth_tokens_post`。

实现：[src/opsmesh/identity/auth/routes.py](../../src/opsmesh/identity/auth/routes.py) · `create_current_user_token`。

权限依赖：`opsmesh.identity.auth.dependencies.account_action_dependency.<locals>.require_account_action`：`action=tokens:manage`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/UserAPITokenCreateRequest"
}
```

模型：[UserAPITokenCreateRequest](schemas.md#schema-UserAPITokenCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [UserAPITokenCreateResponse](schemas.md#schema-UserAPITokenCreateResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## DELETE `/api/v1/auth/tokens/{token_id}`

Revoke Current User Token

Operation ID：`revoke_current_user_token_api_v1_auth_tokens__token_id__delete`。

实现：[src/opsmesh/identity/auth/routes.py](../../src/opsmesh/identity/auth/routes.py) · `revoke_current_user_token`。

权限依赖：`opsmesh.identity.auth.dependencies.account_action_dependency.<locals>.require_account_action`：`action=tokens:manage`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `token_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [UserAPITokenResponse](schemas.md#schema-UserAPITokenResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/auth/tokens/{token_id}/rotate`

Rotate Current User Token

Operation ID：`rotate_current_user_token_api_v1_auth_tokens__token_id__rotate_post`。

实现：[src/opsmesh/identity/auth/routes.py](../../src/opsmesh/identity/auth/routes.py) · `rotate_current_user_token`。

权限依赖：`opsmesh.identity.auth.dependencies.account_action_dependency.<locals>.require_account_action`：`action=tokens:manage`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `token_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/UserAPITokenRotateRequest"
}
```

模型：[UserAPITokenRotateRequest](schemas.md#schema-UserAPITokenRotateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [UserAPITokenCreateResponse](schemas.md#schema-UserAPITokenCreateResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
