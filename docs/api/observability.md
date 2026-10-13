# observability

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/operations/audit-events` | Filter Audit Events |
| GET | `/api/v1/workspaces/{workspace_id}/operations/correlation` | Operations Correlation |
| GET | `/api/v1/workspaces/{workspace_id}/operations/failed-runs` | Inspect Failed Runs |
| GET | `/api/v1/workspaces/{workspace_id}/operations/outcomes` | Execution Outcomes |
| GET | `/api/v1/workspaces/{workspace_id}/operations/overview` | Operations Overview |
| GET | `/api/v1/workspaces/{workspace_id}/operations/run-events` | List Run Events |
| GET | `/api/v1/workspaces/{workspace_id}/operations/runtime-events` | List Runtime Events |
| GET | `/api/v1/workspaces/{workspace_id}/operations/security-events` | Filter Security Events |

## GET `/api/v1/workspaces/{workspace_id}/operations/audit-events`

Filter Audit Events

Operation ID：`filter_audit_events_api_v1_workspaces__workspace_id__operations_audit_events_get`。

实现：[src/opsmesh/governance/observability/routes.py](../../src/opsmesh/governance/observability/routes.py) · `filter_audit_events`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `action` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `target_type` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `trace_id` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 32, "minLength": 32, "type": "string"}, {"type": "null"}]` |
| `request_id` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 80, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AuditEventResponse_](schemas.md#schema-PageResponse_AuditEventResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/correlation`

Operations Correlation

Operation ID：`operations_correlation_api_v1_workspaces__workspace_id__operations_correlation_get`。

实现：[src/opsmesh/governance/observability/routes.py](../../src/opsmesh/governance/observability/routes.py) · `operations_correlation`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `trace_id` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 32, "minLength": 32, "type": "string"}, {"type": "null"}]` |
| `request_id` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 80, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [EvidenceCorrelationResponse](schemas.md#schema-EvidenceCorrelationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/failed-runs`

Inspect Failed Runs

Operation ID：`inspect_failed_runs_api_v1_workspaces__workspace_id__operations_failed_runs_get`。

实现：[src/opsmesh/governance/observability/routes.py](../../src/opsmesh/governance/observability/routes.py) · `inspect_failed_runs`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
| 200 | Successful Response | application/json | [PageResponse_AgentRunResponse_](schemas.md#schema-PageResponse_AgentRunResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/outcomes`

Execution Outcomes

Operation ID：`execution_outcomes_api_v1_workspaces__workspace_id__operations_outcomes_get`。

实现：[src/opsmesh/governance/observability/routes.py](../../src/opsmesh/governance/observability/routes.py) · `execution_outcomes`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `window_seconds` | query | 否 | integer | `default=86400`; `maximum=2592000`; `minimum=60` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [OperationsOutcomesResponse](schemas.md#schema-OperationsOutcomesResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/overview`

Operations Overview

Operation ID：`operations_overview_api_v1_workspaces__workspace_id__operations_overview_get`。

实现：[src/opsmesh/governance/observability/routes.py](../../src/opsmesh/governance/observability/routes.py) · `operations_overview`。

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
| 200 | Successful Response | application/json | [WorkspaceOperationsOverviewResponse](schemas.md#schema-WorkspaceOperationsOverviewResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/run-events`

List Run Events

Operation ID：`list_run_events_api_v1_workspaces__workspace_id__operations_run_events_get`。

实现：[src/opsmesh/governance/observability/routes.py](../../src/opsmesh/governance/observability/routes.py) · `list_run_events`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `event_type` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `trace_id` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 32, "minLength": 32, "type": "string"}, {"type": "null"}]` |
| `request_id` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 80, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_RunEventResponse_](schemas.md#schema-PageResponse_RunEventResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/runtime-events`

List Runtime Events

Operation ID：`list_runtime_events_api_v1_workspaces__workspace_id__operations_runtime_events_get`。

实现：[src/opsmesh/governance/observability/routes.py](../../src/opsmesh/governance/observability/routes.py) · `list_runtime_events`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `runtime_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `event_type` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `trace_id` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 32, "minLength": 32, "type": "string"}, {"type": "null"}]` |
| `request_id` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 80, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_RuntimeEventEvidenceResponse_](schemas.md#schema-PageResponse_RuntimeEventEvidenceResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/security-events`

Filter Security Events

Operation ID：`filter_security_events_api_v1_workspaces__workspace_id__operations_security_events_get`。

实现：[src/opsmesh/governance/observability/routes.py](../../src/opsmesh/governance/observability/routes.py) · `filter_security_events`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `action` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `severity` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `user_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_SecurityEventResponse_](schemas.md#schema-PageResponse_SecurityEventResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
