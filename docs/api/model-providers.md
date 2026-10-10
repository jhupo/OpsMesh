# model-providers

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/model-provider-capabilities` | List Workspace Model Provider Capabilities |
| GET | `/api/v1/workspaces/{workspace_id}/model-provider-capabilities/agent-runtimes` | List Agent Runtime Adapter Capabilities |
| GET | `/api/v1/workspaces/{workspace_id}/model-provider-credentials` | List Model Provider Credentials |
| POST | `/api/v1/workspaces/{workspace_id}/model-provider-credentials` | Create Model Provider Credential |
| GET | `/api/v1/workspaces/{workspace_id}/model-provider-credentials/usage-audit` | List Model Provider Usage Audit |
| PATCH | `/api/v1/workspaces/{workspace_id}/model-provider-credentials/{credential_id}` | Update Model Provider Credential |
| POST | `/api/v1/workspaces/{workspace_id}/model-provider-credentials/{credential_id}/disable` | Disable Model Provider Credential |
| POST | `/api/v1/workspaces/{workspace_id}/model-provider-credentials/{credential_id}/health-check` | Check Model Provider Credential Health |
| POST | `/api/v1/workspaces/{workspace_id}/model-provider-credentials/{credential_id}/rotate-key` | Rotate Model Provider Credential Key |
| POST | `/api/v1/workspaces/{workspace_id}/model-provider-credentials/{credential_id}/set-default` | Set Default Model Provider Credential |

## GET `/api/v1/workspaces/{workspace_id}/model-provider-capabilities`

List Workspace Model Provider Capabilities

Operation ID：`list_workspace_model_provider_capabilities_api_v1_workspaces__workspace_id__model_provider_capabilities_get`。

实现：[src/opsmesh/agents/providers/capability_routes.py](../../src/opsmesh/agents/providers/capability_routes.py) · `list_workspace_model_provider_capabilities`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `provider` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `capability` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[ModelCapabilityResponse](schemas.md#schema-ModelCapabilityResponse)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/model-provider-capabilities/agent-runtimes`

List Agent Runtime Adapter Capabilities

Operation ID：`list_agent_runtime_adapter_capabilities_api_v1_workspaces__workspace_id__model_provider_capabilities_agent_runtimes_get`。

实现：[src/opsmesh/agents/providers/capability_routes.py](../../src/opsmesh/agents/providers/capability_routes.py) · `list_agent_runtime_adapter_capabilities`。

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
| 200 | Successful Response | application/json | array&lt;[AgentRuntimeAdapterCapabilityResponse](schemas.md#schema-AgentRuntimeAdapterCapabilityResponse)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/model-provider-credentials`

List Model Provider Credentials

Operation ID：`list_model_provider_credentials_api_v1_workspaces__workspace_id__model_provider_credentials_get`。

实现：[src/opsmesh/agents/providers/routes.py](../../src/opsmesh/agents/providers/routes.py) · `list_model_provider_credentials`。

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
| 200 | Successful Response | application/json | [PageResponse_ModelProviderCredentialResponse_](schemas.md#schema-PageResponse_ModelProviderCredentialResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/model-provider-credentials`

Create Model Provider Credential

Operation ID：`create_model_provider_credential_api_v1_workspaces__workspace_id__model_provider_credentials_post`。

实现：[src/opsmesh/agents/providers/routes.py](../../src/opsmesh/agents/providers/routes.py) · `create_model_provider_credential`。

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
  "$ref": "#/components/schemas/ModelProviderCredentialCreateRequest"
}
```

模型：[ModelProviderCredentialCreateRequest](schemas.md#schema-ModelProviderCredentialCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [ModelProviderCredentialResponse](schemas.md#schema-ModelProviderCredentialResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/model-provider-credentials/usage-audit`

List Model Provider Usage Audit

Operation ID：`list_model_provider_usage_audit_api_v1_workspaces__workspace_id__model_provider_credentials_usage_audit_get`。

实现：[src/opsmesh/agents/providers/routes.py](../../src/opsmesh/agents/providers/routes.py) · `list_model_provider_usage_audit`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `action` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_ModelProviderUsageAuditResponse_](schemas.md#schema-PageResponse_ModelProviderUsageAuditResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/workspaces/{workspace_id}/model-provider-credentials/{credential_id}`

Update Model Provider Credential

Operation ID：`update_model_provider_credential_api_v1_workspaces__workspace_id__model_provider_credentials__credential_id__patch`。

实现：[src/opsmesh/agents/providers/routes.py](../../src/opsmesh/agents/providers/routes.py) · `update_model_provider_credential`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `credential_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ModelProviderCredentialUpdateRequest"
}
```

模型：[ModelProviderCredentialUpdateRequest](schemas.md#schema-ModelProviderCredentialUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ModelProviderCredentialResponse](schemas.md#schema-ModelProviderCredentialResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/model-provider-credentials/{credential_id}/disable`

Disable Model Provider Credential

Operation ID：`disable_model_provider_credential_api_v1_workspaces__workspace_id__model_provider_credentials__credential_id__disable_post`。

实现：[src/opsmesh/agents/providers/routes.py](../../src/opsmesh/agents/providers/routes.py) · `disable_model_provider_credential`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `credential_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ModelProviderCredentialResponse](schemas.md#schema-ModelProviderCredentialResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/model-provider-credentials/{credential_id}/health-check`

Check Model Provider Credential Health

Operation ID：`check_model_provider_credential_health_api_v1_workspaces__workspace_id__model_provider_credentials__credential_id__health_check_post`。

实现：[src/opsmesh/agents/providers/routes.py](../../src/opsmesh/agents/providers/routes.py) · `check_model_provider_credential_health`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `credential_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ModelProviderHealthCheckRequest"
}
```

模型：[ModelProviderHealthCheckRequest](schemas.md#schema-ModelProviderHealthCheckRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ModelProviderHealthCheckResponse](schemas.md#schema-ModelProviderHealthCheckResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/model-provider-credentials/{credential_id}/rotate-key`

Rotate Model Provider Credential Key

Operation ID：`rotate_model_provider_credential_key_api_v1_workspaces__workspace_id__model_provider_credentials__credential_id__rotate_key_post`。

实现：[src/opsmesh/agents/providers/routes.py](../../src/opsmesh/agents/providers/routes.py) · `rotate_model_provider_credential_key`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `credential_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ModelProviderCredentialRotateKeyRequest"
}
```

模型：[ModelProviderCredentialRotateKeyRequest](schemas.md#schema-ModelProviderCredentialRotateKeyRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ModelProviderCredentialResponse](schemas.md#schema-ModelProviderCredentialResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/model-provider-credentials/{credential_id}/set-default`

Set Default Model Provider Credential

Operation ID：`set_default_model_provider_credential_api_v1_workspaces__workspace_id__model_provider_credentials__credential_id__set_default_post`。

实现：[src/opsmesh/agents/providers/routes.py](../../src/opsmesh/agents/providers/routes.py) · `set_default_model_provider_credential`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `credential_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ModelProviderCredentialResponse](schemas.md#schema-ModelProviderCredentialResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
