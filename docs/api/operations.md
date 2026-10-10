# operations

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/operations/audit-events` | Filter Audit Events |
| GET | `/api/v1/workspaces/{workspace_id}/operations/audit-integrity` | Audit Integrity Status |
| POST | `/api/v1/workspaces/{workspace_id}/operations/audit-integrity/verify` | Queue Audit Integrity Verification |
| GET | `/api/v1/workspaces/{workspace_id}/operations/blocked-steps` | Operations Blocked Steps |
| POST | `/api/v1/workspaces/{workspace_id}/operations/blocked-steps/unblock` | Unblock Blocked Steps |
| GET | `/api/v1/workspaces/{workspace_id}/operations/capacity` | Operations Capacity |
| GET | `/api/v1/workspaces/{workspace_id}/operations/control-plane` | Operations Control Plane |
| GET | `/api/v1/workspaces/{workspace_id}/operations/correlation` | Operations Correlation |
| GET | `/api/v1/workspaces/{workspace_id}/operations/dead-letter-jobs` | List Dead Letter Jobs |
| POST | `/api/v1/workspaces/{workspace_id}/operations/dead-letter-jobs/{job_id}/requeue` | Requeue Dead Letter Job |
| GET | `/api/v1/workspaces/{workspace_id}/operations/failed-runs` | Inspect Failed Runs |
| GET | `/api/v1/workspaces/{workspace_id}/operations/mcp-jobs` | Operations Mcp Jobs |
| GET | `/api/v1/workspaces/{workspace_id}/operations/model-providers` | Model Provider Operations |
| GET | `/api/v1/workspaces/{workspace_id}/operations/outcomes` | Operations Outcomes |
| GET | `/api/v1/workspaces/{workspace_id}/operations/overview` | Operations Overview |
| GET | `/api/v1/workspaces/{workspace_id}/operations/queue-governance` | Queue Governance |
| POST | `/api/v1/workspaces/{workspace_id}/operations/queue-governance/reconcile` | Reconcile Queue Governance |
| GET | `/api/v1/workspaces/{workspace_id}/operations/queue-insights` | Queue Insights |
| GET | `/api/v1/workspaces/{workspace_id}/operations/queue-metrics` | Queue Metrics |
| GET | `/api/v1/workspaces/{workspace_id}/operations/run-activity` | Operations Run Activity |
| GET | `/api/v1/workspaces/{workspace_id}/operations/run-events` | List Run Events |
| GET | `/api/v1/workspaces/{workspace_id}/operations/runtime-capacity` | Operations Runtime Capacity |
| POST | `/api/v1/workspaces/{workspace_id}/operations/runtime-cleanup` | Cleanup Runtimes |
| GET | `/api/v1/workspaces/{workspace_id}/operations/runtime-events` | List Runtime Events |
| GET | `/api/v1/workspaces/{workspace_id}/operations/runtime-leases` | List Runtime Leases |
| GET | `/api/v1/workspaces/{workspace_id}/operations/scheduler` | Operations Scheduler |
| POST | `/api/v1/workspaces/{workspace_id}/operations/scheduler/pause` | Pause Scheduler |
| POST | `/api/v1/workspaces/{workspace_id}/operations/scheduler/resume` | Resume Scheduler |
| GET | `/api/v1/workspaces/{workspace_id}/operations/security-events` | Filter Security Events |
| GET | `/api/v1/workspaces/{workspace_id}/operations/self-hosted-machines` | Operations Self Hosted Machines |
| GET | `/api/v1/workspaces/{workspace_id}/operations/stale-runs` | Stale Runs Diagnostics |
| POST | `/api/v1/workspaces/{workspace_id}/operations/stale-runs/recover` | Recover Stale Runs |
| GET | `/api/v1/workspaces/{workspace_id}/operations/team-runtimes/{team_id}/timeline` | Team Runtime Timeline |
| POST | `/api/v1/workspaces/{workspace_id}/operations/worker-heartbeats` | Record Worker Heartbeat |
| GET | `/api/v1/workspaces/{workspace_id}/operations/worker-leases` | List Worker Leases |
| GET | `/api/v1/workspaces/{workspace_id}/operations/worker-lifecycle` | Operations Worker Lifecycle |
| GET | `/api/v1/workspaces/{workspace_id}/operations/workers` | List Workers |
| POST | `/api/v1/workspaces/{workspace_id}/operations/workers/{worker_id}/drain` | Drain Worker |
| POST | `/api/v1/workspaces/{workspace_id}/operations/workers/{worker_id}/status` | Update Worker Status |

## GET `/api/v1/workspaces/{workspace_id}/operations/audit-events`

Filter Audit Events

Operation ID：`filter_audit_events_api_v1_workspaces__workspace_id__operations_audit_events_get`。

实现：[src/opsmesh/runtime/operations/routes/events.py](../../src/opsmesh/runtime/operations/routes/events.py) · `filter_audit_events`。

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
| 200 | Successful Response | application/json | [AuditEventFilterResponse](schemas.md#schema-AuditEventFilterResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/audit-integrity`

Audit Integrity Status

Operation ID：`audit_integrity_status_api_v1_workspaces__workspace_id__operations_audit_integrity_get`。

实现：[src/opsmesh/runtime/operations/routes/events.py](../../src/opsmesh/runtime/operations/routes/events.py) · `audit_integrity_status`。

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
| 200 | Successful Response | application/json | [AuditIntegrityStatusResponse](schemas.md#schema-AuditIntegrityStatusResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/operations/audit-integrity/verify`

Queue Audit Integrity Verification

Operation ID：`queue_audit_integrity_verification_api_v1_workspaces__workspace_id__operations_audit_integrity_verify_post`。

实现：[src/opsmesh/runtime/operations/routes/events.py](../../src/opsmesh/runtime/operations/routes/events.py) · `queue_audit_integrity_verification`。

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
| 202 | Successful Response | application/json | [AuditIntegrityVerificationQueuedResponse](schemas.md#schema-AuditIntegrityVerificationQueuedResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/blocked-steps`

Operations Blocked Steps

Operation ID：`operations_blocked_steps_api_v1_workspaces__workspace_id__operations_blocked_steps_get`。

实现：[src/opsmesh/orchestration/scheduling/operation_routes.py](../../src/opsmesh/orchestration/scheduling/operation_routes.py) · `operations_blocked_steps`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `code` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_BlockedStepExplanationResponse_](schemas.md#schema-PageResponse_BlockedStepExplanationResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/operations/blocked-steps/unblock`

Unblock Blocked Steps

Operation ID：`unblock_blocked_steps_api_v1_workspaces__workspace_id__operations_blocked_steps_unblock_post`。

实现：[src/opsmesh/orchestration/scheduling/operation_routes.py](../../src/opsmesh/orchestration/scheduling/operation_routes.py) · `unblock_blocked_steps`。

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
  "$ref": "#/components/schemas/BlockedStepUnblockRequest"
}
```

模型：[BlockedStepUnblockRequest](schemas.md#schema-BlockedStepUnblockRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [BlockedStepUnblockResponse](schemas.md#schema-BlockedStepUnblockResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/capacity`

Operations Capacity

Operation ID：`operations_capacity_api_v1_workspaces__workspace_id__operations_capacity_get`。

实现：[src/opsmesh/runtime/operations/routes/overview.py](../../src/opsmesh/runtime/operations/routes/overview.py) · `operations_capacity`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `queue_name` | query | 否 | string | `default="agent_runs"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [OperationsCapacityResponse](schemas.md#schema-OperationsCapacityResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/control-plane`

Operations Control Plane

Operation ID：`operations_control_plane_api_v1_workspaces__workspace_id__operations_control_plane_get`。

实现：[src/opsmesh/runtime/operations/routes/overview.py](../../src/opsmesh/runtime/operations/routes/overview.py) · `operations_control_plane`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `queue_name` | query | 否 | string | `default="agent_runs"` |
| `window_seconds` | query | 否 | integer | `default=86400`; `maximum=2592000`; `minimum=60` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [OperationsControlPlaneResponse](schemas.md#schema-OperationsControlPlaneResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/correlation`

Operations Correlation

Operation ID：`operations_correlation_api_v1_workspaces__workspace_id__operations_correlation_get`。

实现：[src/opsmesh/runtime/operations/routes/events.py](../../src/opsmesh/runtime/operations/routes/events.py) · `operations_correlation`。

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
| 200 | Successful Response | application/json | [OperationsCorrelationResponse](schemas.md#schema-OperationsCorrelationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/dead-letter-jobs`

List Dead Letter Jobs

Operation ID：`list_dead_letter_jobs_api_v1_workspaces__workspace_id__operations_dead_letter_jobs_get`。

实现：[src/opsmesh/runtime/queues/routes.py](../../src/opsmesh/runtime/queues/routes.py) · `list_dead_letter_jobs`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `queue_name` | query | 否 | string | `default="agent_runs"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=200`; `minimum=1` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [DeadLetterJobsResponse](schemas.md#schema-DeadLetterJobsResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/operations/dead-letter-jobs/{job_id}/requeue`

Requeue Dead Letter Job

Operation ID：`requeue_dead_letter_job_api_v1_workspaces__workspace_id__operations_dead_letter_jobs__job_id__requeue_post`。

实现：[src/opsmesh/runtime/queues/routes.py](../../src/opsmesh/runtime/queues/routes.py) · `requeue_dead_letter_job`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `job_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `queue_name` | query | 否 | string | `default="agent_runs"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [RequeueDeadLetterResponse](schemas.md#schema-RequeueDeadLetterResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/failed-runs`

Inspect Failed Runs

Operation ID：`inspect_failed_runs_api_v1_workspaces__workspace_id__operations_failed_runs_get`。

实现：[src/opsmesh/runtime/operations/routes/events.py](../../src/opsmesh/runtime/operations/routes/events.py) · `inspect_failed_runs`。

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
| 200 | Successful Response | application/json | [FailedJobInspectionResponse](schemas.md#schema-FailedJobInspectionResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/mcp-jobs`

Operations Mcp Jobs

Operation ID：`operations_mcp_jobs_api_v1_workspaces__workspace_id__operations_mcp_jobs_get`。

实现：[src/opsmesh/runtime/operations/routes/overview.py](../../src/opsmesh/runtime/operations/routes/overview.py) · `operations_mcp_jobs`。

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
| 200 | Successful Response | application/json | [OperationsMcpJobsResponse](schemas.md#schema-OperationsMcpJobsResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/model-providers`

Model Provider Operations

Operation ID：`model_provider_operations_api_v1_workspaces__workspace_id__operations_model_providers_get`。

实现：[src/opsmesh/agents/providers/operation_routes.py](../../src/opsmesh/agents/providers/operation_routes.py) · `model_provider_operations`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `run_limit` | query | 否 | integer | `default=50`; `maximum=200`; `minimum=1` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ModelProviderOperationsResponse](schemas.md#schema-ModelProviderOperationsResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/outcomes`

Operations Outcomes

Operation ID：`operations_outcomes_api_v1_workspaces__workspace_id__operations_outcomes_get`。

实现：[src/opsmesh/runtime/operations/routes/overview.py](../../src/opsmesh/runtime/operations/routes/overview.py) · `operations_outcomes`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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

实现：[src/opsmesh/runtime/operations/routes/overview.py](../../src/opsmesh/runtime/operations/routes/overview.py) · `operations_overview`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `queue_name` | query | 否 | string | `default="agent_runs"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [OperationsOverviewResponse](schemas.md#schema-OperationsOverviewResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/queue-governance`

Queue Governance

Operation ID：`queue_governance_api_v1_workspaces__workspace_id__operations_queue_governance_get`。

实现：[src/opsmesh/runtime/queues/routes.py](../../src/opsmesh/runtime/queues/routes.py) · `queue_governance`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `queue_name` | query | 否 | string | `default="agent_runs"` |
| `scan_limit` | query | 否 | integer | `default=500`; `maximum=5000`; `minimum=1` |
| `stale_after_seconds` | query | 否 | integer | `default=900`; `maximum=86400`; `minimum=60` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [QueueGovernanceDiagnosticsResponse](schemas.md#schema-QueueGovernanceDiagnosticsResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/operations/queue-governance/reconcile`

Reconcile Queue Governance

Operation ID：`reconcile_queue_governance_api_v1_workspaces__workspace_id__operations_queue_governance_reconcile_post`。

实现：[src/opsmesh/runtime/queues/routes.py](../../src/opsmesh/runtime/queues/routes.py) · `reconcile_queue_governance`。

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
  "$ref": "#/components/schemas/QueueGovernanceReconcileRequest"
}
```

模型：[QueueGovernanceReconcileRequest](schemas.md#schema-QueueGovernanceReconcileRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [QueueGovernanceReconcileResponse](schemas.md#schema-QueueGovernanceReconcileResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/queue-insights`

Queue Insights

Operation ID：`queue_insights_api_v1_workspaces__workspace_id__operations_queue_insights_get`。

实现：[src/opsmesh/runtime/queues/routes.py](../../src/opsmesh/runtime/queues/routes.py) · `queue_insights`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `queue_name` | query | 否 | string | `default="agent_runs"` |
| `scan_limit` | query | 否 | integer | `default=500`; `maximum=5000`; `minimum=1` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [OperationsQueueInsightsResponse](schemas.md#schema-OperationsQueueInsightsResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/queue-metrics`

Queue Metrics

Operation ID：`queue_metrics_api_v1_workspaces__workspace_id__operations_queue_metrics_get`。

实现：[src/opsmesh/runtime/queues/routes.py](../../src/opsmesh/runtime/queues/routes.py) · `queue_metrics`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `queue_name` | query | 否 | string | `default="agent_runs"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [QueueMetricsResponse](schemas.md#schema-QueueMetricsResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/run-activity`

Operations Run Activity

Operation ID：`operations_run_activity_api_v1_workspaces__workspace_id__operations_run_activity_get`。

实现：[src/opsmesh/runtime/operations/routes/overview.py](../../src/opsmesh/runtime/operations/routes/overview.py) · `operations_run_activity`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `team_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `scan_limit` | query | 否 | integer | `default=500`; `maximum=1000`; `minimum=1` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [OperationsRunActivityResponse](schemas.md#schema-OperationsRunActivityResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/run-events`

List Run Events

Operation ID：`list_run_events_api_v1_workspaces__workspace_id__operations_run_events_get`。

实现：[src/opsmesh/runtime/operations/routes/events.py](../../src/opsmesh/runtime/operations/routes/events.py) · `list_run_events`。

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
| 200 | Successful Response | application/json | [RunEventFilterResponse](schemas.md#schema-RunEventFilterResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/runtime-capacity`

Operations Runtime Capacity

Operation ID：`operations_runtime_capacity_api_v1_workspaces__workspace_id__operations_runtime_capacity_get`。

实现：[src/opsmesh/runtime/operations/routes/overview.py](../../src/opsmesh/runtime/operations/routes/overview.py) · `operations_runtime_capacity`。

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
| 200 | Successful Response | application/json | [OperationsRuntimeCapacityResponse](schemas.md#schema-OperationsRuntimeCapacityResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/operations/runtime-cleanup`

Cleanup Runtimes

Operation ID：`cleanup_runtimes_api_v1_workspaces__workspace_id__operations_runtime_cleanup_post`。

实现：[src/opsmesh/runtime/workers/routes.py](../../src/opsmesh/runtime/workers/routes.py) · `cleanup_runtimes`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `stale_after_seconds` | query | 否 | integer | `default=600`; `maximum=86400`; `minimum=60` |
| `stale_lease_after_seconds` | query | 否 | integer | `default=900`; `maximum=86400`; `minimum=60` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [RuntimeCleanupResponse](schemas.md#schema-RuntimeCleanupResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/runtime-events`

List Runtime Events

Operation ID：`list_runtime_events_api_v1_workspaces__workspace_id__operations_runtime_events_get`。

实现：[src/opsmesh/runtime/operations/routes/events.py](../../src/opsmesh/runtime/operations/routes/events.py) · `list_runtime_events`。

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
| 200 | Successful Response | application/json | [opsmesh__shared__http__pagination__PageResponse_RuntimeEventResponse___2](schemas.md#schema-opsmesh__shared__http__pagination__PageResponse_RuntimeEventResponse___2) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/runtime-leases`

List Runtime Leases

Operation ID：`list_runtime_leases_api_v1_workspaces__workspace_id__operations_runtime_leases_get`。

实现：[src/opsmesh/runtime/workers/routes.py](../../src/opsmesh/runtime/workers/routes.py) · `list_runtime_leases`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `runtime_space_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_RuntimeLeaseResponse_](schemas.md#schema-PageResponse_RuntimeLeaseResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/scheduler`

Operations Scheduler

Operation ID：`operations_scheduler_api_v1_workspaces__workspace_id__operations_scheduler_get`。

实现：[src/opsmesh/orchestration/scheduling/operation_routes.py](../../src/opsmesh/orchestration/scheduling/operation_routes.py) · `operations_scheduler`。

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
| 200 | Successful Response | application/json | [OperationsSchedulerResponse](schemas.md#schema-OperationsSchedulerResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/operations/scheduler/pause`

Pause Scheduler

Operation ID：`pause_scheduler_api_v1_workspaces__workspace_id__operations_scheduler_pause_post`。

实现：[src/opsmesh/orchestration/scheduling/operation_routes.py](../../src/opsmesh/orchestration/scheduling/operation_routes.py) · `pause_scheduler`。

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
  "$ref": "#/components/schemas/SchedulerPauseRequest"
}
```

模型：[SchedulerPauseRequest](schemas.md#schema-SchedulerPauseRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SchedulerControlResponse](schemas.md#schema-SchedulerControlResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/operations/scheduler/resume`

Resume Scheduler

Operation ID：`resume_scheduler_api_v1_workspaces__workspace_id__operations_scheduler_resume_post`。

实现：[src/opsmesh/orchestration/scheduling/operation_routes.py](../../src/opsmesh/orchestration/scheduling/operation_routes.py) · `resume_scheduler`。

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
| 200 | Successful Response | application/json | [SchedulerControlResponse](schemas.md#schema-SchedulerControlResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/security-events`

Filter Security Events

Operation ID：`filter_security_events_api_v1_workspaces__workspace_id__operations_security_events_get`。

实现：[src/opsmesh/runtime/operations/routes/events.py](../../src/opsmesh/runtime/operations/routes/events.py) · `filter_security_events`。

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
| 200 | Successful Response | application/json | [SecurityEventFilterResponse](schemas.md#schema-SecurityEventFilterResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/self-hosted-machines`

Operations Self Hosted Machines

Operation ID：`operations_self_hosted_machines_api_v1_workspaces__workspace_id__operations_self_hosted_machines_get`。

实现：[src/opsmesh/runtime/operations/routes/overview.py](../../src/opsmesh/runtime/operations/routes/overview.py) · `operations_self_hosted_machines`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `stale_after_seconds` | query | 否 | integer | `default=600`; `maximum=86400`; `minimum=60` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [OperationsSelfHostedMachinesResponse](schemas.md#schema-OperationsSelfHostedMachinesResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/stale-runs`

Stale Runs Diagnostics

Operation ID：`stale_runs_diagnostics_api_v1_workspaces__workspace_id__operations_stale_runs_get`。

实现：[src/opsmesh/runtime/operations/routes/events.py](../../src/opsmesh/runtime/operations/routes/events.py) · `stale_runs_diagnostics`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `stale_after_seconds` | query | 否 | integer | `default=900`; `maximum=86400`; `minimum=60` |
| `statuses` | query | 否 | array&lt;string&gt; anyOf null | `anyOf=[{"items": {"enum": ["queued", "running", "waiting_runtime"], "type": "string"}, "type": "array"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=100`; `maximum=500`; `minimum=1` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [StaleRunsDiagnosticsResponse](schemas.md#schema-StaleRunsDiagnosticsResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/operations/stale-runs/recover`

Recover Stale Runs

Operation ID：`recover_stale_runs_api_v1_workspaces__workspace_id__operations_stale_runs_recover_post`。

实现：[src/opsmesh/runtime/operations/routes/events.py](../../src/opsmesh/runtime/operations/routes/events.py) · `recover_stale_runs`。

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
  "$ref": "#/components/schemas/StaleRunRecoveryRequest"
}
```

模型：[StaleRunRecoveryRequest](schemas.md#schema-StaleRunRecoveryRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [StaleRunRecoveryResponse](schemas.md#schema-StaleRunRecoveryResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/team-runtimes/{team_id}/timeline`

Team Runtime Timeline

Operation ID：`team_runtime_timeline_api_v1_workspaces__workspace_id__operations_team_runtimes__team_id__timeline_get`。

实现：[src/opsmesh/runtime/operations/routes/timeline.py](../../src/opsmesh/runtime/operations/routes/timeline.py) · `team_runtime_timeline`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=200`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `source_type` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `event_type` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `since` | query | 否 | string (date-time) anyOf null | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `until` | query | 否 | string (date-time) anyOf null | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `include_runs` | query | 否 | boolean | `default=false` |
| `include_queue` | query | 否 | boolean | `default=true` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TeamRuntimeTimelineResponse](schemas.md#schema-TeamRuntimeTimelineResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/operations/worker-heartbeats`

Record Worker Heartbeat

Operation ID：`record_worker_heartbeat_api_v1_workspaces__workspace_id__operations_worker_heartbeats_post`。

实现：[src/opsmesh/runtime/workers/routes.py](../../src/opsmesh/runtime/workers/routes.py) · `record_worker_heartbeat`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Worker-Heartbeat-Token` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/opsmesh__runtime__workers__schemas__WorkerHeartbeatRequest"
}
```

模型：[opsmesh__runtime__workers__schemas__WorkerHeartbeatRequest](schemas.md#schema-opsmesh__runtime__workers__schemas__WorkerHeartbeatRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [opsmesh__runtime__workers__schemas__WorkerHeartbeatResponse](schemas.md#schema-opsmesh__runtime__workers__schemas__WorkerHeartbeatResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/worker-leases`

List Worker Leases

Operation ID：`list_worker_leases_api_v1_workspaces__workspace_id__operations_worker_leases_get`。

实现：[src/opsmesh/runtime/workers/routes.py](../../src/opsmesh/runtime/workers/routes.py) · `list_worker_leases`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `worker_id` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_WorkerLeaseResponse_](schemas.md#schema-PageResponse_WorkerLeaseResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/worker-lifecycle`

Operations Worker Lifecycle

Operation ID：`operations_worker_lifecycle_api_v1_workspaces__workspace_id__operations_worker_lifecycle_get`。

实现：[src/opsmesh/runtime/operations/routes/overview.py](../../src/opsmesh/runtime/operations/routes/overview.py) · `operations_worker_lifecycle`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `queue_name` | query | 否 | string | `default="agent_runs"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [OperationsWorkerLifecycleResponse](schemas.md#schema-OperationsWorkerLifecycleResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/operations/workers`

List Workers

Operation ID：`list_workers_api_v1_workspaces__workspace_id__operations_workers_get`。

实现：[src/opsmesh/runtime/workers/routes.py](../../src/opsmesh/runtime/workers/routes.py) · `list_workers`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `worker_type` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_WorkerNodeResponse_](schemas.md#schema-PageResponse_WorkerNodeResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/operations/workers/{worker_id}/drain`

Drain Worker

Operation ID：`drain_worker_api_v1_workspaces__workspace_id__operations_workers__worker_id__drain_post`。

实现：[src/opsmesh/runtime/workers/routes.py](../../src/opsmesh/runtime/workers/routes.py) · `drain_worker`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `worker_id` | path | 是 | string | — |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkerNodeResponse](schemas.md#schema-WorkerNodeResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/operations/workers/{worker_id}/status`

Update Worker Status

Operation ID：`update_worker_status_api_v1_workspaces__workspace_id__operations_workers__worker_id__status_post`。

实现：[src/opsmesh/runtime/workers/routes.py](../../src/opsmesh/runtime/workers/routes.py) · `update_worker_status`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `worker_id` | path | 是 | string | — |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/WorkerStatusUpdateRequest"
}
```

模型：[WorkerStatusUpdateRequest](schemas.md#schema-WorkerStatusUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkerNodeResponse](schemas.md#schema-WorkerNodeResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
