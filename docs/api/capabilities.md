# capabilities

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities` | List Capabilities |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities` | Create Capability |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities/agents/{agent_profile_id}/effective-catalog` | Get Effective Agent Capability Catalog |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities/agents/{agent_profile_id}/tool-policy-diagnostics` | Get Agent Tool Policy Diagnostics |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities/catalog` | Get Workspace Capability Catalog |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities/governance` | Get Workspace Capability Governance |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/governance/actions/apply` | Apply Workspace Capability Governance Actions |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/managed-mcp` | Create Managed Mcp |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-catalog` | List Mcp Catalog |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-credentials` | List Mcp Credential References |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-credentials` | Create Mcp Credential Reference |
| PATCH | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-credentials/{credential_id}` | Update Mcp Credential Reference |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-credentials/{credential_id}/disable` | Disable Mcp Credential Reference |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-credentials/{credential_id}/rotate` | Rotate Mcp Credential Reference |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers` | List Mcp Servers |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers` | Create Mcp Server |
| PATCH | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{mcp_server_id}` | Update Mcp Server |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{mcp_server_id}/disable` | Disable Mcp Server |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{mcp_server_id}/discover` | Discover Mcp Server Tools |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{mcp_server_id}/discovered-tools` | List Discovered Mcp Tools |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{mcp_server_id}/health-check` | Record Mcp Server Health Check |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{mcp_server_id}/tools` | Allow Mcp Tool |
| PATCH | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{mcp_server_id}/tools/{allowlist_id}` | Update Mcp Tool |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{mcp_server_id}/tools/{allowlist_id}/disable` | Disable Mcp Tool |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{mcp_server_id}/tools/{allowlist_id}/enable` | Enable Discovered Mcp Tool |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{server_id}/deployment` | Get Managed Mcp |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{server_id}/deployment` | Control Managed Mcp |
| PUT | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{server_id}/deployment/host` | Bind Managed Mcp Host |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-tool-call-logs` | List Mcp Tool Call Logs |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-tool-call-logs` | Log Mcp Tool Call |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities/mcp-tools` | List Mapped Mcp Tools |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities/resources` | List Capability Resources |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/resources` | Create Capability Resource |
| PATCH | `/api/v1/workspaces/{workspace_id}/capabilities/resources/{resource_id}` | Update Capability Resource |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/resources/{resource_id}/disable` | Disable Capability Resource |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities/skills` | List Skills |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/skills` | Create Skill |
| PATCH | `/api/v1/workspaces/{workspace_id}/capabilities/skills/{skill_id}` | Update Skill |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/skills/{skill_id}/install` | Install Skill By Id |
| PUT | `/api/v1/workspaces/{workspace_id}/capabilities/teams/{team_id}/policy` | Update Team Capability Policy |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities/tool-groups` | List Tool Groups |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/tool-groups` | Create Tool Group |
| PATCH | `/api/v1/workspaces/{workspace_id}/capabilities/tool-groups/{group_id}` | Update Tool Group |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities/tool-policy-matrix` | Get Workspace Tool Policy Matrix |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities/workspace-skills` | List Workspace Skills |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/workspace-skills` | Install Workspace Skill |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities/workspace-skills/{install_id}/availability` | Get Workspace Skill Availability |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/workspace-skills/{install_id}/disable` | Disable Workspace Skill |
| GET | `/api/v1/workspaces/{workspace_id}/capabilities/workspace-skills/{install_id}/impact` | Get Workspace Skill Impact |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/workspace-skills/{install_id}/rollback` | Rollback Workspace Skill |
| POST | `/api/v1/workspaces/{workspace_id}/capabilities/workspace-skills/{install_id}/upgrade` | Upgrade Workspace Skill |
| PATCH | `/api/v1/workspaces/{workspace_id}/capabilities/{capability_id}` | Update Capability |

## GET `/api/v1/workspaces/{workspace_id}/capabilities`

List Capabilities

Operation ID：`list_capabilities_api_v1_workspaces__workspace_id__capabilities_get`。

实现：[src/opsmesh/capabilities/catalog/dependencies.py](../../src/opsmesh/capabilities/catalog/dependencies.py) · `list_capabilities`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `category` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_CapabilityResponse_](schemas.md#schema-PageResponse_CapabilityResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities`

Create Capability

Operation ID：`create_capability_api_v1_workspaces__workspace_id__capabilities_post`。

实现：[src/opsmesh/capabilities/catalog/dependencies.py](../../src/opsmesh/capabilities/catalog/dependencies.py) · `create_capability`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/CapabilityCreateRequest"
}
```

模型：[CapabilityCreateRequest](schemas.md#schema-CapabilityCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [CapabilityResponse](schemas.md#schema-CapabilityResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/capabilities/agents/{agent_profile_id}/effective-catalog`

Get Effective Agent Capability Catalog

Operation ID：`get_effective_agent_capability_catalog_api_v1_workspaces__workspace_id__capabilities_agents__agent_profile_id__effective_catalog_get`。

实现：[src/opsmesh/capabilities/catalog/routes.py](../../src/opsmesh/capabilities/catalog/routes.py) · `get_effective_agent_capability_catalog`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_profile_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `team_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [EffectiveCapabilityCatalogResponse](schemas.md#schema-EffectiveCapabilityCatalogResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/capabilities/agents/{agent_profile_id}/tool-policy-diagnostics`

Get Agent Tool Policy Diagnostics

Operation ID：`get_agent_tool_policy_diagnostics_api_v1_workspaces__workspace_id__capabilities_agents__agent_profile_id__tool_policy_diagnostics_get`。

实现：[src/opsmesh/capabilities/mcp/observability_routes.py](../../src/opsmesh/capabilities/mcp/observability_routes.py) · `get_agent_tool_policy_diagnostics`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_profile_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentToolPolicyDiagnosticsResponse](schemas.md#schema-AgentToolPolicyDiagnosticsResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/capabilities/catalog`

Get Workspace Capability Catalog

Operation ID：`get_workspace_capability_catalog_api_v1_workspaces__workspace_id__capabilities_catalog_get`。

实现：[src/opsmesh/capabilities/catalog/routes.py](../../src/opsmesh/capabilities/catalog/routes.py) · `get_workspace_capability_catalog`。

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
| 200 | Successful Response | application/json | [WorkspaceCapabilityCatalogResponse](schemas.md#schema-WorkspaceCapabilityCatalogResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/capabilities/governance`

Get Workspace Capability Governance

Operation ID：`get_workspace_capability_governance_api_v1_workspaces__workspace_id__capabilities_governance_get`。

实现：[src/opsmesh/capabilities/governance/routes.py](../../src/opsmesh/capabilities/governance/routes.py) · `get_workspace_capability_governance`。

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
| 200 | Successful Response | application/json | [WorkspaceCapabilityGovernanceResponse](schemas.md#schema-WorkspaceCapabilityGovernanceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/governance/actions/apply`

Apply Workspace Capability Governance Actions

Operation ID：`apply_workspace_capability_governance_actions_api_v1_workspaces__workspace_id__capabilities_governance_actions_apply_post`。

实现：[src/opsmesh/capabilities/governance/routes.py](../../src/opsmesh/capabilities/governance/routes.py) · `apply_workspace_capability_governance_actions`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/WorkspaceCapabilityGovernanceApplyRequest"
}
```

模型：[WorkspaceCapabilityGovernanceApplyRequest](schemas.md#schema-WorkspaceCapabilityGovernanceApplyRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceCapabilityGovernanceApplyResponse](schemas.md#schema-WorkspaceCapabilityGovernanceApplyResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/managed-mcp`

Create Managed Mcp

Operation ID：`create_managed_mcp_api_v1_workspaces__workspace_id__capabilities_managed_mcp_post`。

实现：[src/opsmesh/capabilities/mcp/managed_routes.py](../../src/opsmesh/capabilities/mcp/managed_routes.py) · `create_managed_mcp`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/ManagedMcpCreateRequest"
}
```

模型：[ManagedMcpCreateRequest](schemas.md#schema-ManagedMcpCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 202 | Successful Response | application/json | array&lt;[ManagedMcpResponse](schemas.md#schema-ManagedMcpResponse)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/capabilities/mcp-catalog`

List Mcp Catalog

Operation ID：`list_mcp_catalog_api_v1_workspaces__workspace_id__capabilities_mcp_catalog_get`。

实现：[src/opsmesh/capabilities/mcp/server_routes.py](../../src/opsmesh/capabilities/mcp/server_routes.py) · `list_mcp_catalog`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `agent_profile_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_McpCatalogServerResponse_](schemas.md#schema-PageResponse_McpCatalogServerResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/capabilities/mcp-credentials`

List Mcp Credential References

Operation ID：`list_mcp_credential_references_api_v1_workspaces__workspace_id__capabilities_mcp_credentials_get`。

实现：[src/opsmesh/capabilities/mcp/credential_routes.py](../../src/opsmesh/capabilities/mcp/credential_routes.py) · `list_mcp_credential_references`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `mcp_server_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `include_disabled` | query | 否 | boolean | `default=false` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_McpCredentialReferenceResponse_](schemas.md#schema-PageResponse_McpCredentialReferenceResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/mcp-credentials`

Create Mcp Credential Reference

Operation ID：`create_mcp_credential_reference_api_v1_workspaces__workspace_id__capabilities_mcp_credentials_post`。

实现：[src/opsmesh/capabilities/mcp/credential_routes.py](../../src/opsmesh/capabilities/mcp/credential_routes.py) · `create_mcp_credential_reference`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/McpCredentialReferenceCreateRequest"
}
```

模型：[McpCredentialReferenceCreateRequest](schemas.md#schema-McpCredentialReferenceCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [McpCredentialReferenceResponse](schemas.md#schema-McpCredentialReferenceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/workspaces/{workspace_id}/capabilities/mcp-credentials/{credential_id}`

Update Mcp Credential Reference

Operation ID：`update_mcp_credential_reference_api_v1_workspaces__workspace_id__capabilities_mcp_credentials__credential_id__patch`。

实现：[src/opsmesh/capabilities/mcp/credential_routes.py](../../src/opsmesh/capabilities/mcp/credential_routes.py) · `update_mcp_credential_reference`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/McpCredentialReferenceUpdateRequest"
}
```

模型：[McpCredentialReferenceUpdateRequest](schemas.md#schema-McpCredentialReferenceUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [McpCredentialReferenceResponse](schemas.md#schema-McpCredentialReferenceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/mcp-credentials/{credential_id}/disable`

Disable Mcp Credential Reference

Operation ID：`disable_mcp_credential_reference_api_v1_workspaces__workspace_id__capabilities_mcp_credentials__credential_id__disable_post`。

实现：[src/opsmesh/capabilities/mcp/credential_routes.py](../../src/opsmesh/capabilities/mcp/credential_routes.py) · `disable_mcp_credential_reference`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
| 200 | Successful Response | application/json | [McpCredentialReferenceResponse](schemas.md#schema-McpCredentialReferenceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/mcp-credentials/{credential_id}/rotate`

Rotate Mcp Credential Reference

Operation ID：`rotate_mcp_credential_reference_api_v1_workspaces__workspace_id__capabilities_mcp_credentials__credential_id__rotate_post`。

实现：[src/opsmesh/capabilities/mcp/credential_routes.py](../../src/opsmesh/capabilities/mcp/credential_routes.py) · `rotate_mcp_credential_reference`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/McpCredentialReferenceRotateRequest"
}
```

模型：[McpCredentialReferenceRotateRequest](schemas.md#schema-McpCredentialReferenceRotateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [McpCredentialReferenceResponse](schemas.md#schema-McpCredentialReferenceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers`

List Mcp Servers

Operation ID：`list_mcp_servers_api_v1_workspaces__workspace_id__capabilities_mcp_servers_get`。

实现：[src/opsmesh/capabilities/mcp/server_routes.py](../../src/opsmesh/capabilities/mcp/server_routes.py) · `list_mcp_servers`。

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
| 200 | Successful Response | application/json | [PageResponse_McpServerResponse_](schemas.md#schema-PageResponse_McpServerResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers`

Create Mcp Server

Operation ID：`create_mcp_server_api_v1_workspaces__workspace_id__capabilities_mcp_servers_post`。

实现：[src/opsmesh/capabilities/mcp/server_routes.py](../../src/opsmesh/capabilities/mcp/server_routes.py) · `create_mcp_server`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/McpServerCreateRequest"
}
```

模型：[McpServerCreateRequest](schemas.md#schema-McpServerCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [McpServerResponse](schemas.md#schema-McpServerResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{mcp_server_id}`

Update Mcp Server

Operation ID：`update_mcp_server_api_v1_workspaces__workspace_id__capabilities_mcp_servers__mcp_server_id__patch`。

实现：[src/opsmesh/capabilities/mcp/server_routes.py](../../src/opsmesh/capabilities/mcp/server_routes.py) · `update_mcp_server`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `mcp_server_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/McpServerUpdateRequest"
}
```

模型：[McpServerUpdateRequest](schemas.md#schema-McpServerUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [McpServerResponse](schemas.md#schema-McpServerResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{mcp_server_id}/disable`

Disable Mcp Server

Operation ID：`disable_mcp_server_api_v1_workspaces__workspace_id__capabilities_mcp_servers__mcp_server_id__disable_post`。

实现：[src/opsmesh/capabilities/mcp/server_routes.py](../../src/opsmesh/capabilities/mcp/server_routes.py) · `disable_mcp_server`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `mcp_server_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [McpServerResponse](schemas.md#schema-McpServerResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{mcp_server_id}/discover`

Discover Mcp Server Tools

Operation ID：`discover_mcp_server_tools_api_v1_workspaces__workspace_id__capabilities_mcp_servers__mcp_server_id__discover_post`。

实现：[src/opsmesh/capabilities/mcp/server_routes.py](../../src/opsmesh/capabilities/mcp/server_routes.py) · `discover_mcp_server_tools`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `mcp_server_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/McpServerDiscoveryRequest"
}
```

模型：[McpServerDiscoveryRequest](schemas.md#schema-McpServerDiscoveryRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [McpServerDiscoveryResponse](schemas.md#schema-McpServerDiscoveryResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{mcp_server_id}/discovered-tools`

List Discovered Mcp Tools

Operation ID：`list_discovered_mcp_tools_api_v1_workspaces__workspace_id__capabilities_mcp_servers__mcp_server_id__discovered_tools_get`。

实现：[src/opsmesh/capabilities/mcp/server_routes.py](../../src/opsmesh/capabilities/mcp/server_routes.py) · `list_discovered_mcp_tools`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `mcp_server_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[McpToolAllowResponse](schemas.md#schema-McpToolAllowResponse)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{mcp_server_id}/health-check`

Record Mcp Server Health Check

Operation ID：`record_mcp_server_health_check_api_v1_workspaces__workspace_id__capabilities_mcp_servers__mcp_server_id__health_check_post`。

实现：[src/opsmesh/capabilities/mcp/server_routes.py](../../src/opsmesh/capabilities/mcp/server_routes.py) · `record_mcp_server_health_check`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `mcp_server_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/McpServerHealthCheckRequest"
}
```

模型：[McpServerHealthCheckRequest](schemas.md#schema-McpServerHealthCheckRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [McpServerResponse](schemas.md#schema-McpServerResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{mcp_server_id}/tools`

Allow Mcp Tool

Operation ID：`allow_mcp_tool_api_v1_workspaces__workspace_id__capabilities_mcp_servers__mcp_server_id__tools_post`。

实现：[src/opsmesh/capabilities/mcp/server_routes.py](../../src/opsmesh/capabilities/mcp/server_routes.py) · `allow_mcp_tool`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `mcp_server_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/McpToolAllowRequest"
}
```

模型：[McpToolAllowRequest](schemas.md#schema-McpToolAllowRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [McpToolAllowResponse](schemas.md#schema-McpToolAllowResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{mcp_server_id}/tools/{allowlist_id}`

Update Mcp Tool

Operation ID：`update_mcp_tool_api_v1_workspaces__workspace_id__capabilities_mcp_servers__mcp_server_id__tools__allowlist_id__patch`。

实现：[src/opsmesh/capabilities/mcp/server_routes.py](../../src/opsmesh/capabilities/mcp/server_routes.py) · `update_mcp_tool`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `mcp_server_id` | path | 是 | string (uuid) | `format="uuid"` |
| `allowlist_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/McpToolAllowUpdateRequest"
}
```

模型：[McpToolAllowUpdateRequest](schemas.md#schema-McpToolAllowUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [McpToolAllowResponse](schemas.md#schema-McpToolAllowResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{mcp_server_id}/tools/{allowlist_id}/disable`

Disable Mcp Tool

Operation ID：`disable_mcp_tool_api_v1_workspaces__workspace_id__capabilities_mcp_servers__mcp_server_id__tools__allowlist_id__disable_post`。

实现：[src/opsmesh/capabilities/mcp/server_routes.py](../../src/opsmesh/capabilities/mcp/server_routes.py) · `disable_mcp_tool`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `mcp_server_id` | path | 是 | string (uuid) | `format="uuid"` |
| `allowlist_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [McpToolAllowResponse](schemas.md#schema-McpToolAllowResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{mcp_server_id}/tools/{allowlist_id}/enable`

Enable Discovered Mcp Tool

Operation ID：`enable_discovered_mcp_tool_api_v1_workspaces__workspace_id__capabilities_mcp_servers__mcp_server_id__tools__allowlist_id__enable_post`。

实现：[src/opsmesh/capabilities/mcp/server_routes.py](../../src/opsmesh/capabilities/mcp/server_routes.py) · `enable_discovered_mcp_tool`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `mcp_server_id` | path | 是 | string (uuid) | `format="uuid"` |
| `allowlist_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [McpToolAllowResponse](schemas.md#schema-McpToolAllowResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{server_id}/deployment`

Get Managed Mcp

Operation ID：`get_managed_mcp_api_v1_workspaces__workspace_id__capabilities_mcp_servers__server_id__deployment_get`。

实现：[src/opsmesh/capabilities/mcp/managed_routes.py](../../src/opsmesh/capabilities/mcp/managed_routes.py) · `get_managed_mcp`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `server_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ManagedMcpResponse](schemas.md#schema-ManagedMcpResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{server_id}/deployment`

Control Managed Mcp

Operation ID：`control_managed_mcp_api_v1_workspaces__workspace_id__capabilities_mcp_servers__server_id__deployment_post`。

实现：[src/opsmesh/capabilities/mcp/managed_routes.py](../../src/opsmesh/capabilities/mcp/managed_routes.py) · `control_managed_mcp`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `server_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ManagedMcpActionRequest"
}
```

模型：[ManagedMcpActionRequest](schemas.md#schema-ManagedMcpActionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 202 | Successful Response | application/json | [ManagedMcpResponse](schemas.md#schema-ManagedMcpResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PUT `/api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{server_id}/deployment/host`

Bind Managed Mcp Host

Operation ID：`bind_managed_mcp_host_api_v1_workspaces__workspace_id__capabilities_mcp_servers__server_id__deployment_host_put`。

实现：[src/opsmesh/capabilities/mcp/managed_routes.py](../../src/opsmesh/capabilities/mcp/managed_routes.py) · `bind_managed_mcp_host`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `server_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ManagedMcpHostRequest"
}
```

模型：[ManagedMcpHostRequest](schemas.md#schema-ManagedMcpHostRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ManagedMcpResponse](schemas.md#schema-ManagedMcpResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/capabilities/mcp-tool-call-logs`

List Mcp Tool Call Logs

Operation ID：`list_mcp_tool_call_logs_api_v1_workspaces__workspace_id__capabilities_mcp_tool_call_logs_get`。

实现：[src/opsmesh/capabilities/mcp/observability_routes.py](../../src/opsmesh/capabilities/mcp/observability_routes.py) · `list_mcp_tool_call_logs`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `mcp_server_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `tool_name` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 160, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 32, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `trace_id` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 32, "minLength": 32, "type": "string"}, {"type": "null"}]` |
| `request_id` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 80, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_McpToolCallLogResponse_](schemas.md#schema-PageResponse_McpToolCallLogResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/mcp-tool-call-logs`

Log Mcp Tool Call

Operation ID：`log_mcp_tool_call_api_v1_workspaces__workspace_id__capabilities_mcp_tool_call_logs_post`。

实现：[src/opsmesh/capabilities/mcp/observability_routes.py](../../src/opsmesh/capabilities/mcp/observability_routes.py) · `log_mcp_tool_call`。

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
  "$ref": "#/components/schemas/McpToolCallLogRequest"
}
```

模型：[McpToolCallLogRequest](schemas.md#schema-McpToolCallLogRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [McpToolCallLogResponse](schemas.md#schema-McpToolCallLogResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/capabilities/mcp-tools`

List Mapped Mcp Tools

Operation ID：`list_mapped_mcp_tools_api_v1_workspaces__workspace_id__capabilities_mcp_tools_get`。

实现：[src/opsmesh/capabilities/mcp/server_routes.py](../../src/opsmesh/capabilities/mcp/server_routes.py) · `list_mapped_mcp_tools`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `agent_profile_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[McpToolDescriptor](schemas.md#schema-McpToolDescriptor)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/capabilities/resources`

List Capability Resources

Operation ID：`list_capability_resources_api_v1_workspaces__workspace_id__capabilities_resources_get`。

实现：[src/opsmesh/capabilities/catalog/routes.py](../../src/opsmesh/capabilities/catalog/routes.py) · `list_capability_resources`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `include_disabled` | query | 否 | boolean | `default=false` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_CapabilityResourceResponse_](schemas.md#schema-PageResponse_CapabilityResourceResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/resources`

Create Capability Resource

Operation ID：`create_capability_resource_api_v1_workspaces__workspace_id__capabilities_resources_post`。

实现：[src/opsmesh/capabilities/catalog/routes.py](../../src/opsmesh/capabilities/catalog/routes.py) · `create_capability_resource`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/CapabilityResourceCreateRequest"
}
```

模型：[CapabilityResourceCreateRequest](schemas.md#schema-CapabilityResourceCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [CapabilityResourceResponse](schemas.md#schema-CapabilityResourceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/workspaces/{workspace_id}/capabilities/resources/{resource_id}`

Update Capability Resource

Operation ID：`update_capability_resource_api_v1_workspaces__workspace_id__capabilities_resources__resource_id__patch`。

实现：[src/opsmesh/capabilities/catalog/routes.py](../../src/opsmesh/capabilities/catalog/routes.py) · `update_capability_resource`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `resource_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/CapabilityResourceUpdateRequest"
}
```

模型：[CapabilityResourceUpdateRequest](schemas.md#schema-CapabilityResourceUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [CapabilityResourceResponse](schemas.md#schema-CapabilityResourceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/resources/{resource_id}/disable`

Disable Capability Resource

Operation ID：`disable_capability_resource_api_v1_workspaces__workspace_id__capabilities_resources__resource_id__disable_post`。

实现：[src/opsmesh/capabilities/catalog/routes.py](../../src/opsmesh/capabilities/catalog/routes.py) · `disable_capability_resource`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `resource_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [CapabilityResourceResponse](schemas.md#schema-CapabilityResourceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/capabilities/skills`

List Skills

Operation ID：`list_skills_api_v1_workspaces__workspace_id__capabilities_skills_get`。

实现：[src/opsmesh/capabilities/catalog/dependencies.py](../../src/opsmesh/capabilities/catalog/dependencies.py) · `list_skills`。

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
| 200 | Successful Response | application/json | [PageResponse_SkillResponse_](schemas.md#schema-PageResponse_SkillResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/skills`

Create Skill

Operation ID：`create_skill_api_v1_workspaces__workspace_id__capabilities_skills_post`。

实现：[src/opsmesh/capabilities/catalog/dependencies.py](../../src/opsmesh/capabilities/catalog/dependencies.py) · `create_skill`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/SkillCreateRequest"
}
```

模型：[SkillCreateRequest](schemas.md#schema-SkillCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [SkillResponse](schemas.md#schema-SkillResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/workspaces/{workspace_id}/capabilities/skills/{skill_id}`

Update Skill

Operation ID：`update_skill_api_v1_workspaces__workspace_id__capabilities_skills__skill_id__patch`。

实现：[src/opsmesh/capabilities/catalog/dependencies.py](../../src/opsmesh/capabilities/catalog/dependencies.py) · `update_skill`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `skill_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/SkillUpdateRequest"
}
```

模型：[SkillUpdateRequest](schemas.md#schema-SkillUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SkillResponse](schemas.md#schema-SkillResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/skills/{skill_id}/install`

Install Skill By Id

Operation ID：`install_skill_by_id_api_v1_workspaces__workspace_id__capabilities_skills__skill_id__install_post`。

实现：[src/opsmesh/capabilities/skills/routes.py](../../src/opsmesh/capabilities/skills/routes.py) · `install_skill_by_id`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `skill_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/WorkspaceSkillInstallConfigRequest"
}
```

模型：[WorkspaceSkillInstallConfigRequest](schemas.md#schema-WorkspaceSkillInstallConfigRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [WorkspaceSkillInstallResponse](schemas.md#schema-WorkspaceSkillInstallResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PUT `/api/v1/workspaces/{workspace_id}/capabilities/teams/{team_id}/policy`

Update Team Capability Policy

Operation ID：`update_team_capability_policy_api_v1_workspaces__workspace_id__capabilities_teams__team_id__policy_put`。

实现：[src/opsmesh/capabilities/catalog/routes.py](../../src/opsmesh/capabilities/catalog/routes.py) · `update_team_capability_policy`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/TeamCapabilityPolicyUpdateRequest"
}
```

模型：[TeamCapabilityPolicyUpdateRequest](schemas.md#schema-TeamCapabilityPolicyUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TeamCapabilityPolicyResponse](schemas.md#schema-TeamCapabilityPolicyResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/capabilities/tool-groups`

List Tool Groups

Operation ID：`list_tool_groups_api_v1_workspaces__workspace_id__capabilities_tool_groups_get`。

实现：[src/opsmesh/capabilities/catalog/dependencies.py](../../src/opsmesh/capabilities/catalog/dependencies.py) · `list_tool_groups`。

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
| 200 | Successful Response | application/json | [PageResponse_ToolGroupResponse_](schemas.md#schema-PageResponse_ToolGroupResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/tool-groups`

Create Tool Group

Operation ID：`create_tool_group_api_v1_workspaces__workspace_id__capabilities_tool_groups_post`。

实现：[src/opsmesh/capabilities/catalog/dependencies.py](../../src/opsmesh/capabilities/catalog/dependencies.py) · `create_tool_group`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/ToolGroupCreateRequest"
}
```

模型：[ToolGroupCreateRequest](schemas.md#schema-ToolGroupCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [ToolGroupResponse](schemas.md#schema-ToolGroupResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/workspaces/{workspace_id}/capabilities/tool-groups/{group_id}`

Update Tool Group

Operation ID：`update_tool_group_api_v1_workspaces__workspace_id__capabilities_tool_groups__group_id__patch`。

实现：[src/opsmesh/capabilities/catalog/dependencies.py](../../src/opsmesh/capabilities/catalog/dependencies.py) · `update_tool_group`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `group_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ToolGroupUpdateRequest"
}
```

模型：[ToolGroupUpdateRequest](schemas.md#schema-ToolGroupUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ToolGroupResponse](schemas.md#schema-ToolGroupResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/capabilities/tool-policy-matrix`

Get Workspace Tool Policy Matrix

Operation ID：`get_workspace_tool_policy_matrix_api_v1_workspaces__workspace_id__capabilities_tool_policy_matrix_get`。

实现：[src/opsmesh/capabilities/mcp/observability_routes.py](../../src/opsmesh/capabilities/mcp/observability_routes.py) · `get_workspace_tool_policy_matrix`。

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
| 200 | Successful Response | application/json | [WorkspaceToolPolicyMatrixResponse](schemas.md#schema-WorkspaceToolPolicyMatrixResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/capabilities/workspace-skills`

List Workspace Skills

Operation ID：`list_workspace_skills_api_v1_workspaces__workspace_id__capabilities_workspace_skills_get`。

实现：[src/opsmesh/capabilities/skills/routes.py](../../src/opsmesh/capabilities/skills/routes.py) · `list_workspace_skills`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `include_disabled` | query | 否 | boolean | `default=false` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_WorkspaceSkillInstallResponse_](schemas.md#schema-PageResponse_WorkspaceSkillInstallResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/workspace-skills`

Install Workspace Skill

Operation ID：`install_workspace_skill_api_v1_workspaces__workspace_id__capabilities_workspace_skills_post`。

实现：[src/opsmesh/capabilities/skills/routes.py](../../src/opsmesh/capabilities/skills/routes.py) · `install_workspace_skill`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/WorkspaceSkillInstallRequest"
}
```

模型：[WorkspaceSkillInstallRequest](schemas.md#schema-WorkspaceSkillInstallRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [WorkspaceSkillInstallResponse](schemas.md#schema-WorkspaceSkillInstallResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/capabilities/workspace-skills/{install_id}/availability`

Get Workspace Skill Availability

Operation ID：`get_workspace_skill_availability_api_v1_workspaces__workspace_id__capabilities_workspace_skills__install_id__availability_get`。

实现：[src/opsmesh/capabilities/skills/routes.py](../../src/opsmesh/capabilities/skills/routes.py) · `get_workspace_skill_availability`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceSkillAvailabilityResponse](schemas.md#schema-WorkspaceSkillAvailabilityResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/workspace-skills/{install_id}/disable`

Disable Workspace Skill

Operation ID：`disable_workspace_skill_api_v1_workspaces__workspace_id__capabilities_workspace_skills__install_id__disable_post`。

实现：[src/opsmesh/capabilities/skills/routes.py](../../src/opsmesh/capabilities/skills/routes.py) · `disable_workspace_skill`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceSkillInstallResponse](schemas.md#schema-WorkspaceSkillInstallResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/capabilities/workspace-skills/{install_id}/impact`

Get Workspace Skill Impact

Operation ID：`get_workspace_skill_impact_api_v1_workspaces__workspace_id__capabilities_workspace_skills__install_id__impact_get`。

实现：[src/opsmesh/capabilities/skills/routes.py](../../src/opsmesh/capabilities/skills/routes.py) · `get_workspace_skill_impact`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `target_skill_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceSkillImpactResponse](schemas.md#schema-WorkspaceSkillImpactResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/workspace-skills/{install_id}/rollback`

Rollback Workspace Skill

Operation ID：`rollback_workspace_skill_api_v1_workspaces__workspace_id__capabilities_workspace_skills__install_id__rollback_post`。

实现：[src/opsmesh/capabilities/skills/routes.py](../../src/opsmesh/capabilities/skills/routes.py) · `rollback_workspace_skill`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/WorkspaceSkillRollbackRequest"
}
```

模型：[WorkspaceSkillRollbackRequest](schemas.md#schema-WorkspaceSkillRollbackRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceSkillInstallResponse](schemas.md#schema-WorkspaceSkillInstallResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/capabilities/workspace-skills/{install_id}/upgrade`

Upgrade Workspace Skill

Operation ID：`upgrade_workspace_skill_api_v1_workspaces__workspace_id__capabilities_workspace_skills__install_id__upgrade_post`。

实现：[src/opsmesh/capabilities/skills/routes.py](../../src/opsmesh/capabilities/skills/routes.py) · `upgrade_workspace_skill`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/WorkspaceSkillUpgradeRequest"
}
```

模型：[WorkspaceSkillUpgradeRequest](schemas.md#schema-WorkspaceSkillUpgradeRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceSkillInstallResponse](schemas.md#schema-WorkspaceSkillInstallResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/workspaces/{workspace_id}/capabilities/{capability_id}`

Update Capability

Operation ID：`update_capability_api_v1_workspaces__workspace_id__capabilities__capability_id__patch`。

实现：[src/opsmesh/capabilities/catalog/dependencies.py](../../src/opsmesh/capabilities/catalog/dependencies.py) · `update_capability`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_capability, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `capability_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/CapabilityUpdateRequest"
}
```

模型：[CapabilityUpdateRequest](schemas.md#schema-CapabilityUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [CapabilityResponse](schemas.md#schema-CapabilityResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
