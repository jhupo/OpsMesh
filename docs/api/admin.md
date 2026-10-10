# admin

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/admin/announcements` | List Admin Announcements |
| POST | `/api/v1/admin/announcements` | Publish Admin Announcement |
| GET | `/api/v1/admin/announcements/{announcement_id}` | Get Admin Announcement |
| POST | `/api/v1/admin/announcements/{announcement_id}/retract` | Retract Admin Announcement |
| GET | `/api/v1/admin/catalog/{kind}` | List Admin Catalog Resources |
| GET | `/api/v1/admin/catalog/{kind}/{resource_id}` | Get Admin Catalog Resource |
| POST | `/api/v1/admin/catalog/{kind}/{resource_id}/block` | Block Capability Resource |
| POST | `/api/v1/admin/catalog/{kind}/{resource_id}/release` | Release Capability Resource |
| GET | `/api/v1/admin/costs/summary` | Cost Summary |
| GET | `/api/v1/admin/marketplace/reviews` | List Reviews |
| GET | `/api/v1/admin/operations/audit-integrity` | Integrity |
| GET | `/api/v1/admin/operations/history` | History |
| GET | `/api/v1/admin/operations/requests` | Operation Requests |
| GET | `/api/v1/admin/operations/requests/{request_id}` | Operation |
| GET | `/api/v1/admin/operations/runs` | Diagnostic Runs |
| GET | `/api/v1/admin/operations/summary` | Admin Operations Summary |
| GET | `/api/v1/admin/overview` | Admin Overview |
| GET | `/api/v1/admin/platform-policies` | List Admin Platform Policies |
| GET | `/api/v1/admin/platform-policies/risky-execution` | Get Admin Risky Execution Policy |
| PATCH | `/api/v1/admin/platform-policies/risky-execution` | Update Admin Risky Execution Policy |
| GET | `/api/v1/admin/platform-policies/worker-control` | Get Admin Worker Control Policy |
| PATCH | `/api/v1/admin/platform-policies/worker-control` | Update Admin Worker Control Policy |
| GET | `/api/v1/admin/platform-policies/{policy_key}/events` | List Admin Platform Policy Events |
| GET | `/api/v1/admin/queues/{queue_name}/dead-letter-jobs` | List Admin Dead Letter Jobs |
| POST | `/api/v1/admin/queues/{queue_name}/dead-letter-jobs/{job_id}/requeue` | Requeue Admin Dead Letter Job |
| GET | `/api/v1/admin/queues/{queue_name}/metrics` | Admin Queue Metrics |
| GET | `/api/v1/admin/runtime-leases` | List Admin Runtime Leases |
| GET | `/api/v1/admin/runtime-spaces` | List Admin Runtime Spaces |
| POST | `/api/v1/admin/runtime-spaces/{runtime_space_id}/quarantine` | Quarantine Admin Runtime Space |
| GET | `/api/v1/admin/runtimes` | List Admin Runtimes |
| POST | `/api/v1/admin/runtimes/{runtime_id}/force-stop` | Force Stop Admin Runtime |
| GET | `/api/v1/admin/security-events` | List Admin Security Events |
| GET | `/api/v1/admin/system/check-updates` | Admin Check Updates |
| GET | `/api/v1/admin/system/configuration` | Admin System Configuration |
| GET | `/api/v1/admin/system/logs` | List Admin System Logs |
| GET | `/api/v1/admin/system/mail` | Get Mail Configuration |
| PUT | `/api/v1/admin/system/mail` | Save Mail Configuration |
| POST | `/api/v1/admin/system/mail/test` | Test Mail Configuration |
| GET | `/api/v1/admin/system/operational-configuration` | Get Operational Configuration |
| PUT | `/api/v1/admin/system/operational-configuration` | Replace Operational Configuration |
| POST | `/api/v1/admin/system/updates/plans` | Create Plan |
| GET | `/api/v1/admin/system/updates/{job_id}` | Read Job |
| POST | `/api/v1/admin/system/updates/{job_id}/apply` | Apply Job |
| POST | `/api/v1/admin/system/updates/{job_id}/cancel` | Cancel Job |
| GET | `/api/v1/admin/system/updates/{job_id}/events` | Read Events |
| GET | `/api/v1/admin/system/version` | Admin System Version |
| POST | `/api/v1/admin/user-invitations` | Invite User |
| GET | `/api/v1/admin/users` | List Admin Users |
| POST | `/api/v1/admin/users` | Create Admin User |
| GET | `/api/v1/admin/users/{user_id}` | Get Admin User |
| PATCH | `/api/v1/admin/users/{user_id}` | Update Admin User |
| POST | `/api/v1/admin/users/{user_id}/invitation/resend` | Resend User Invitation |
| POST | `/api/v1/admin/users/{user_id}/reset-password` | Reset Admin User Password |
| POST | `/api/v1/admin/users/{user_id}/revoke-tokens` | Revoke Admin User Tokens |
| PUT | `/api/v1/admin/users/{user_id}/status` | Update Admin User Status |
| GET | `/api/v1/admin/worker-leases` | List Admin Worker Leases |
| GET | `/api/v1/admin/workers` | List Admin Workers |
| PATCH | `/api/v1/admin/workers/{worker_id}` | Update Admin Worker |
| POST | `/api/v1/admin/workers/{worker_id}/drain` | Drain Admin Worker |
| GET | `/api/v1/admin/workspaces` | List Admin Workspaces |
| GET | `/api/v1/admin/workspaces/{workspace_id}` | Get Admin Workspace |
| POST | `/api/v1/admin/workspaces/{workspace_id}/marketplace/reviews/{approval_id}/decision` | Decide Review |
| GET | `/api/v1/admin/workspaces/{workspace_id}/members` | List Admin Workspace Members |
| POST | `/api/v1/admin/workspaces/{workspace_id}/members` | Add Admin Workspace Member |
| DELETE | `/api/v1/admin/workspaces/{workspace_id}/members/{member_id}` | Remove Admin Workspace Member |
| PATCH | `/api/v1/admin/workspaces/{workspace_id}/members/{member_id}` | Update Admin Workspace Member |
| POST | `/api/v1/admin/workspaces/{workspace_id}/operations/audit-integrity/verify` | Verify |
| GET | `/api/v1/admin/workspaces/{workspace_id}/operations/scheduler` | Scheduler |
| POST | `/api/v1/admin/workspaces/{workspace_id}/operations/stale-runs/recover` | Recover |
| POST | `/api/v1/admin/workspaces/{workspace_id}/plugin-trust-keys/{key_id}/revoke` | Revoke Publisher Key |
| POST | `/api/v1/admin/workspaces/{workspace_id}/plugins/{install_id}/disable` | Disable Plugin |
| POST | `/api/v1/admin/workspaces/{workspace_id}/plugins/{install_id}/release` | Release Plugin |
| GET | `/api/v1/admin/workspaces/{workspace_id}/projects` | List Admin Workspace Projects |
| GET | `/api/v1/admin/workspaces/{workspace_id}/projects/{project_id}` | Get Admin Project |
| GET | `/api/v1/admin/workspaces/{workspace_id}/projects/{project_id}/quotas` | List Admin Project Quotas |
| PUT | `/api/v1/admin/workspaces/{workspace_id}/projects/{project_id}/quotas` | Upsert Admin Project Quotas |
| DELETE | `/api/v1/admin/workspaces/{workspace_id}/projects/{project_id}/quotas/{quota_key}` | Disable Admin Project Quota |
| PATCH | `/api/v1/admin/workspaces/{workspace_id}/projects/{project_id}/status` | Update Admin Project Status |
| GET | `/api/v1/admin/workspaces/{workspace_id}/resources/{kind}/{resource_id}/authorization` | Get Admin Resource Authorization |
| PUT | `/api/v1/admin/workspaces/{workspace_id}/resources/{kind}/{resource_id}/grants` | Replace Admin Resource Grants |
| PUT | `/api/v1/admin/workspaces/{workspace_id}/resources/{kind}/{resource_id}/owner` | Assign Admin Resource Owner |
| PATCH | `/api/v1/admin/workspaces/{workspace_id}/status` | Update Admin Workspace Status |

## GET `/api/v1/admin/announcements`

List Admin Announcements

Operation ID：`list_admin_announcements_api_v1_admin_announcements_get`。

实现：[src/opsmesh/platform/announcements/routes.py](../../src/opsmesh/platform/announcements/routes.py) · `list_admin_announcements`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AdminAnnouncementResponse_](schemas.md#schema-PageResponse_AdminAnnouncementResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/announcements`

Publish Admin Announcement

Operation ID：`publish_admin_announcement_api_v1_admin_announcements_post`。

实现：[src/opsmesh/platform/announcements/routes.py](../../src/opsmesh/platform/announcements/routes.py) · `publish_admin_announcement`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminAnnouncementCreateRequest"
}
```

模型：[AdminAnnouncementCreateRequest](schemas.md#schema-AdminAnnouncementCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [AdminAnnouncementResponse](schemas.md#schema-AdminAnnouncementResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/announcements/{announcement_id}`

Get Admin Announcement

Operation ID：`get_admin_announcement_api_v1_admin_announcements__announcement_id__get`。

实现：[src/opsmesh/platform/announcements/routes.py](../../src/opsmesh/platform/announcements/routes.py) · `get_admin_announcement`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `announcement_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminAnnouncementResponse](schemas.md#schema-AdminAnnouncementResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/announcements/{announcement_id}/retract`

Retract Admin Announcement

Operation ID：`retract_admin_announcement_api_v1_admin_announcements__announcement_id__retract_post`。

实现：[src/opsmesh/platform/announcements/routes.py](../../src/opsmesh/platform/announcements/routes.py) · `retract_admin_announcement`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `announcement_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminAnnouncementResponse](schemas.md#schema-AdminAnnouncementResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/catalog/{kind}`

List Admin Catalog Resources

Operation ID：`list_admin_catalog_resources_api_v1_admin_catalog__kind__get`。

实现：[src/opsmesh/platform/overview/catalog_routes.py](../../src/opsmesh/platform/overview/catalog_routes.py) · `list_admin_catalog_resources`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `kind` | path | 是 | [AdminCatalogKind](schemas.md#schema-AdminCatalogKind) | — |
| `workspace_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AdminCatalogResourceResponse_](schemas.md#schema-PageResponse_AdminCatalogResourceResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/catalog/{kind}/{resource_id}`

Get Admin Catalog Resource

Operation ID：`get_admin_catalog_resource_api_v1_admin_catalog__kind___resource_id__get`。

实现：[src/opsmesh/platform/overview/catalog_routes.py](../../src/opsmesh/platform/overview/catalog_routes.py) · `get_admin_catalog_resource`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `kind` | path | 是 | [AdminCatalogKind](schemas.md#schema-AdminCatalogKind) | — |
| `resource_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminCatalogResourceResponse](schemas.md#schema-AdminCatalogResourceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/catalog/{kind}/{resource_id}/block`

Block Capability Resource

Operation ID：`block_capability_resource_api_v1_admin_catalog__kind___resource_id__block_post`。

实现：[src/opsmesh/capabilities/governance/admin_routes.py](../../src/opsmesh/capabilities/governance/admin_routes.py) · `block_capability_resource`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `kind` | path | 是 | [AdminCapabilityKind](schemas.md#schema-AdminCapabilityKind) | — |
| `resource_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminCapabilityGovernanceRequest"
}
```

模型：[AdminCapabilityGovernanceRequest](schemas.md#schema-AdminCapabilityGovernanceRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminCapabilityGovernanceResponse](schemas.md#schema-AdminCapabilityGovernanceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/catalog/{kind}/{resource_id}/release`

Release Capability Resource

Operation ID：`release_capability_resource_api_v1_admin_catalog__kind___resource_id__release_post`。

实现：[src/opsmesh/capabilities/governance/admin_routes.py](../../src/opsmesh/capabilities/governance/admin_routes.py) · `release_capability_resource`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `kind` | path | 是 | [AdminCapabilityKind](schemas.md#schema-AdminCapabilityKind) | — |
| `resource_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminCapabilityGovernanceRequest"
}
```

模型：[AdminCapabilityGovernanceRequest](schemas.md#schema-AdminCapabilityGovernanceRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminCapabilityGovernanceResponse](schemas.md#schema-AdminCapabilityGovernanceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/costs/summary`

Cost Summary

Operation ID：`cost_summary_api_v1_admin_costs_summary_get`。

实现：[src/opsmesh/governance/costs/admin_routes.py](../../src/opsmesh/governance/costs/admin_routes.py) · `cost_summary`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `start_at` | query | 否 | string (date-time) anyOf null | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `end_at` | query | 否 | string (date-time) anyOf null | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `workspace_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `currency` | query | 否 | string | `default="USD"`; `pattern="^[A-Za-z]{3}$"` |
| `group_by` | query | 否 | string | `default="workspace"`; `enum=["workspace", "provider", "model", "day"]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=200`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminCostSummaryResponse](schemas.md#schema-AdminCostSummaryResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/marketplace/reviews`

List Reviews

Operation ID：`list_reviews_api_v1_admin_marketplace_reviews_get`。

实现：[src/opsmesh/capabilities/marketplace/admin_routes.py](../../src/opsmesh/capabilities/marketplace/admin_routes.py) · `list_reviews`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"enum": ["pending", "approved", "rejected"], "type": "string"}, {"type": "null"}]`; `default="pending"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_ApprovalResponse_](schemas.md#schema-PageResponse_ApprovalResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/operations/audit-integrity`

Integrity

Operation ID：`integrity_api_v1_admin_operations_audit_integrity_get`。

实现：[src/opsmesh/runtime/operations/admin_dashboard_routes.py](../../src/opsmesh/runtime/operations/admin_dashboard_routes.py) · `integrity`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_IntegrityResponse_](schemas.md#schema-PageResponse_IntegrityResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/operations/history`

History

Operation ID：`history_api_v1_admin_operations_history_get`。

实现：[src/opsmesh/runtime/operations/admin_dashboard_routes.py](../../src/opsmesh/runtime/operations/admin_dashboard_routes.py) · `history`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `start_at` | query | 否 | string (date-time) anyOf null | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `end_at` | query | 否 | string (date-time) anyOf null | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_HistoryResponse_](schemas.md#schema-PageResponse_HistoryResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/operations/requests`

Operation Requests

Operation ID：`operation_requests_api_v1_admin_operations_requests_get`。

实现：[src/opsmesh/runtime/operations/admin_dashboard_routes.py](../../src/opsmesh/runtime/operations/admin_dashboard_routes.py) · `operation_requests`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"enum": ["pending", "running", "completed", "failed"], "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AdminOperationResponse_](schemas.md#schema-PageResponse_AdminOperationResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/operations/requests/{request_id}`

Operation

Operation ID：`operation_api_v1_admin_operations_requests__request_id__get`。

实现：[src/opsmesh/runtime/operations/admin_dashboard_routes.py](../../src/opsmesh/runtime/operations/admin_dashboard_routes.py) · `operation`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `request_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminOperationResponse](schemas.md#schema-AdminOperationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/operations/runs`

Diagnostic Runs

Operation ID：`diagnostic_runs_api_v1_admin_operations_runs_get`。

实现：[src/opsmesh/runtime/operations/admin_dashboard_routes.py](../../src/opsmesh/runtime/operations/admin_dashboard_routes.py) · `diagnostic_runs`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `kind` | query | 否 | string | `default="failed"`; `enum=["failed", "stale"]` |
| `workspace_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `stale_after_seconds` | query | 否 | integer | `default=900`; `maximum=86400`; `minimum=60` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AgentRunResponse_](schemas.md#schema-PageResponse_AgentRunResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/operations/summary`

Admin Operations Summary

Operation ID：`admin_operations_summary_api_v1_admin_operations_summary_get`。

实现：[src/opsmesh/runtime/queues/admin_routes.py](../../src/opsmesh/runtime/queues/admin_routes.py) · `admin_operations_summary`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `queue_name` | query | 否 | string | `default="agent_runs"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminOperationsSummaryResponse](schemas.md#schema-AdminOperationsSummaryResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/overview`

Admin Overview

Operation ID：`admin_overview_api_v1_admin_overview_get`。

实现：[src/opsmesh/platform/overview/routes.py](../../src/opsmesh/platform/overview/routes.py) · `admin_overview`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminOverviewResponse](schemas.md#schema-AdminOverviewResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/platform-policies`

List Admin Platform Policies

Operation ID：`list_admin_platform_policies_api_v1_admin_platform_policies_get`。

实现：[src/opsmesh/governance/policies/routes.py](../../src/opsmesh/governance/policies/routes.py) · `list_admin_platform_policies`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AdminPlatformPolicyResponse_](schemas.md#schema-PageResponse_AdminPlatformPolicyResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/platform-policies/risky-execution`

Get Admin Risky Execution Policy

Operation ID：`get_admin_risky_execution_policy_api_v1_admin_platform_policies_risky_execution_get`。

实现：[src/opsmesh/governance/policies/routes.py](../../src/opsmesh/governance/policies/routes.py) · `get_admin_risky_execution_policy`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminPlatformPolicyResponse](schemas.md#schema-AdminPlatformPolicyResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/admin/platform-policies/risky-execution`

Update Admin Risky Execution Policy

Operation ID：`update_admin_risky_execution_policy_api_v1_admin_platform_policies_risky_execution_patch`。

实现：[src/opsmesh/governance/policies/routes.py](../../src/opsmesh/governance/policies/routes.py) · `update_admin_risky_execution_policy`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminRiskyExecutionPolicyUpdateRequest"
}
```

模型：[AdminRiskyExecutionPolicyUpdateRequest](schemas.md#schema-AdminRiskyExecutionPolicyUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminPlatformPolicyResponse](schemas.md#schema-AdminPlatformPolicyResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/platform-policies/worker-control`

Get Admin Worker Control Policy

Operation ID：`get_admin_worker_control_policy_api_v1_admin_platform_policies_worker_control_get`。

实现：[src/opsmesh/governance/policies/routes.py](../../src/opsmesh/governance/policies/routes.py) · `get_admin_worker_control_policy`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminPlatformPolicyResponse](schemas.md#schema-AdminPlatformPolicyResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/admin/platform-policies/worker-control`

Update Admin Worker Control Policy

Operation ID：`update_admin_worker_control_policy_api_v1_admin_platform_policies_worker_control_patch`。

实现：[src/opsmesh/governance/policies/routes.py](../../src/opsmesh/governance/policies/routes.py) · `update_admin_worker_control_policy`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminWorkerControlPolicyUpdateRequest"
}
```

模型：[AdminWorkerControlPolicyUpdateRequest](schemas.md#schema-AdminWorkerControlPolicyUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminPlatformPolicyResponse](schemas.md#schema-AdminPlatformPolicyResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/platform-policies/{policy_key}/events`

List Admin Platform Policy Events

Operation ID：`list_admin_platform_policy_events_api_v1_admin_platform_policies__policy_key__events_get`。

实现：[src/opsmesh/governance/policies/routes.py](../../src/opsmesh/governance/policies/routes.py) · `list_admin_platform_policy_events`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `policy_key` | path | 是 | string | — |
| `event_type` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AdminPlatformPolicyEventResponse_](schemas.md#schema-PageResponse_AdminPlatformPolicyEventResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/queues/{queue_name}/dead-letter-jobs`

List Admin Dead Letter Jobs

Operation ID：`list_admin_dead_letter_jobs_api_v1_admin_queues__queue_name__dead_letter_jobs_get`。

实现：[src/opsmesh/runtime/queues/admin_routes.py](../../src/opsmesh/runtime/queues/admin_routes.py) · `list_admin_dead_letter_jobs`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `queue_name` | path | 是 | string | — |
| `limit` | query | 否 | integer | `default=50`; `maximum=200`; `minimum=1` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminDeadLetterJobsResponse](schemas.md#schema-AdminDeadLetterJobsResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/queues/{queue_name}/dead-letter-jobs/{job_id}/requeue`

Requeue Admin Dead Letter Job

Operation ID：`requeue_admin_dead_letter_job_api_v1_admin_queues__queue_name__dead_letter_jobs__job_id__requeue_post`。

实现：[src/opsmesh/runtime/queues/admin_routes.py](../../src/opsmesh/runtime/queues/admin_routes.py) · `requeue_admin_dead_letter_job`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `queue_name` | path | 是 | string | — |
| `job_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminRequeueDeadLetterResponse](schemas.md#schema-AdminRequeueDeadLetterResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/queues/{queue_name}/metrics`

Admin Queue Metrics

Operation ID：`admin_queue_metrics_api_v1_admin_queues__queue_name__metrics_get`。

实现：[src/opsmesh/runtime/queues/admin_routes.py](../../src/opsmesh/runtime/queues/admin_routes.py) · `admin_queue_metrics`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `queue_name` | path | 是 | string | — |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [QueueMetricsResponse](schemas.md#schema-QueueMetricsResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/runtime-leases`

List Admin Runtime Leases

Operation ID：`list_admin_runtime_leases_api_v1_admin_runtime_leases_get`。

实现：[src/opsmesh/runtime/instances/lease_admin_routes.py](../../src/opsmesh/runtime/instances/lease_admin_routes.py) · `list_admin_runtime_leases`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `workspace_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `runtime_space_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `workspace_runtime_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AdminRuntimeLeaseResponse_](schemas.md#schema-PageResponse_AdminRuntimeLeaseResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/runtime-spaces`

List Admin Runtime Spaces

Operation ID：`list_admin_runtime_spaces_api_v1_admin_runtime_spaces_get`。

实现：[src/opsmesh/runtime/spaces/admin_routes.py](../../src/opsmesh/runtime/spaces/admin_routes.py) · `list_admin_runtime_spaces`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `workspace_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_RuntimeSpaceResponse_](schemas.md#schema-PageResponse_RuntimeSpaceResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/runtime-spaces/{runtime_space_id}/quarantine`

Quarantine Admin Runtime Space

Operation ID：`quarantine_admin_runtime_space_api_v1_admin_runtime_spaces__runtime_space_id__quarantine_post`。

实现：[src/opsmesh/runtime/spaces/admin_routes.py](../../src/opsmesh/runtime/spaces/admin_routes.py) · `quarantine_admin_runtime_space`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `runtime_space_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminQuarantineRuntimeSpaceRequest"
}
```

模型：[AdminQuarantineRuntimeSpaceRequest](schemas.md#schema-AdminQuarantineRuntimeSpaceRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminQuarantineRuntimeSpaceResponse](schemas.md#schema-AdminQuarantineRuntimeSpaceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/runtimes`

List Admin Runtimes

Operation ID：`list_admin_runtimes_api_v1_admin_runtimes_get`。

实现：[src/opsmesh/runtime/instances/admin_routes.py](../../src/opsmesh/runtime/instances/admin_routes.py) · `list_admin_runtimes`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `runtime_space_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `connection_status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AdminWorkspaceRuntimeResponse_](schemas.md#schema-PageResponse_AdminWorkspaceRuntimeResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/runtimes/{runtime_id}/force-stop`

Force Stop Admin Runtime

Operation ID：`force_stop_admin_runtime_api_v1_admin_runtimes__runtime_id__force_stop_post`。

实现：[src/opsmesh/runtime/instances/admin_routes.py](../../src/opsmesh/runtime/instances/admin_routes.py) · `force_stop_admin_runtime`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `runtime_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminForceStopRuntimeRequest"
}
```

模型：[AdminForceStopRuntimeRequest](schemas.md#schema-AdminForceStopRuntimeRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminWorkspaceRuntimeResponse](schemas.md#schema-AdminWorkspaceRuntimeResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/security-events`

List Admin Security Events

Operation ID：`list_admin_security_events_api_v1_admin_security_events_get`。

实现：[src/opsmesh/governance/security_events/routes.py](../../src/opsmesh/governance/security_events/routes.py) · `list_admin_security_events`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `severity` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `workspace_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AdminSecurityEventResponse_](schemas.md#schema-PageResponse_AdminSecurityEventResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/system/check-updates`

Admin Check Updates

Operation ID：`admin_check_updates_api_v1_admin_system_check_updates_get`。

实现：[src/opsmesh/platform/releases/routes.py](../../src/opsmesh/platform/releases/routes.py) · `admin_check_updates`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `force` | query | 否 | boolean | `default=false` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminReleaseUpdateCheckResponse](schemas.md#schema-AdminReleaseUpdateCheckResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/system/configuration`

Admin System Configuration

Operation ID：`admin_system_configuration_api_v1_admin_system_configuration_get`。

实现：[src/opsmesh/platform/settings/routes.py](../../src/opsmesh/platform/settings/routes.py) · `admin_system_configuration`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminSystemConfigurationResponse](schemas.md#schema-AdminSystemConfigurationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/system/logs`

List Admin System Logs

Operation ID：`list_admin_system_logs_api_v1_admin_system_logs_get`。

实现：[src/opsmesh/governance/audit/admin_routes.py](../../src/opsmesh/governance/audit/admin_routes.py) · `list_admin_system_logs`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `user_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `action` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 120, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `actor_type` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 32, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `target_type` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 120, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `created_after` | query | 否 | string (date-time) anyOf null | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `created_before` | query | 否 | string (date-time) anyOf null | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AdminSystemLogResponse_](schemas.md#schema-PageResponse_AdminSystemLogResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/system/mail`

Get Mail Configuration

Operation ID：`get_mail_configuration_api_v1_admin_system_mail_get`。

实现：[src/opsmesh/messaging/email/routes.py](../../src/opsmesh/messaging/email/routes.py) · `get_mail_configuration`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [MailConfigurationResponse](schemas.md#schema-MailConfigurationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PUT `/api/v1/admin/system/mail`

Save Mail Configuration

Operation ID：`save_mail_configuration_api_v1_admin_system_mail_put`。

实现：[src/opsmesh/messaging/email/routes.py](../../src/opsmesh/messaging/email/routes.py) · `save_mail_configuration`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/MailConfigurationUpdate"
}
```

模型：[MailConfigurationUpdate](schemas.md#schema-MailConfigurationUpdate)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [MailConfigurationResponse](schemas.md#schema-MailConfigurationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/system/mail/test`

Test Mail Configuration

Operation ID：`test_mail_configuration_api_v1_admin_system_mail_test_post`。

实现：[src/opsmesh/messaging/email/routes.py](../../src/opsmesh/messaging/email/routes.py) · `test_mail_configuration`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/EmailRecipient"
}
```

模型：[EmailRecipient](schemas.md#schema-EmailRecipient)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | object |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/system/operational-configuration`

Get Operational Configuration

Operation ID：`get_operational_configuration_api_v1_admin_system_operational_configuration_get`。

实现：[src/opsmesh/platform/settings/routes.py](../../src/opsmesh/platform/settings/routes.py) · `get_operational_configuration`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [OperationalConfiguration](schemas.md#schema-OperationalConfiguration) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PUT `/api/v1/admin/system/operational-configuration`

Replace Operational Configuration

Operation ID：`replace_operational_configuration_api_v1_admin_system_operational_configuration_put`。

实现：[src/opsmesh/platform/settings/routes.py](../../src/opsmesh/platform/settings/routes.py) · `replace_operational_configuration`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/OperationalConfiguration"
}
```

模型：[OperationalConfiguration](schemas.md#schema-OperationalConfiguration)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [OperationalConfiguration](schemas.md#schema-OperationalConfiguration) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/system/updates/plans`

Create Plan

Operation ID：`create_plan_api_v1_admin_system_updates_plans_post`。

实现：[src/opsmesh/platform/updates/routes.py](../../src/opsmesh/platform/updates/routes.py) · `create_plan`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/PlanRequest"
}
```

模型：[PlanRequest](schemas.md#schema-PlanRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 202 | Successful Response | application/json | [JobResponse](schemas.md#schema-JobResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/system/updates/{job_id}`

Read Job

Operation ID：`read_job_api_v1_admin_system_updates__job_id__get`。

实现：[src/opsmesh/platform/updates/routes.py](../../src/opsmesh/platform/updates/routes.py) · `read_job`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `job_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [JobResponse](schemas.md#schema-JobResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/system/updates/{job_id}/apply`

Apply Job

Operation ID：`apply_job_api_v1_admin_system_updates__job_id__apply_post`。

实现：[src/opsmesh/platform/updates/routes.py](../../src/opsmesh/platform/updates/routes.py) · `apply_job`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `job_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ApplyRequest"
}
```

模型：[ApplyRequest](schemas.md#schema-ApplyRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 202 | Successful Response | application/json | [JobResponse](schemas.md#schema-JobResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/system/updates/{job_id}/cancel`

Cancel Job

Operation ID：`cancel_job_api_v1_admin_system_updates__job_id__cancel_post`。

实现：[src/opsmesh/platform/updates/routes.py](../../src/opsmesh/platform/updates/routes.py) · `cancel_job`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `job_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [JobResponse](schemas.md#schema-JobResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/system/updates/{job_id}/events`

Read Events

Operation ID：`read_events_api_v1_admin_system_updates__job_id__events_get`。

实现：[src/opsmesh/platform/updates/routes.py](../../src/opsmesh/platform/updates/routes.py) · `read_events`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `job_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[opsmesh__platform__updates__routes__EventResponse](schemas.md#schema-opsmesh__platform__updates__routes__EventResponse)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/system/version`

Admin System Version

Operation ID：`admin_system_version_api_v1_admin_system_version_get`。

实现：[src/opsmesh/platform/releases/routes.py](../../src/opsmesh/platform/releases/routes.py) · `admin_system_version`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminReleaseVersionResponse](schemas.md#schema-AdminReleaseVersionResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/user-invitations`

Invite User

Operation ID：`invite_user_api_v1_admin_user_invitations_post`。

实现：[src/opsmesh/identity/invitations/admin_routes.py](../../src/opsmesh/identity/invitations/admin_routes.py) · `invite_user`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/UserInvitationRequest"
}
```

模型：[UserInvitationRequest](schemas.md#schema-UserInvitationRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [UserInvitationResponse](schemas.md#schema-UserInvitationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/users`

List Admin Users

Operation ID：`list_admin_users_api_v1_admin_users_get`。

实现：[src/opsmesh/identity/users/admin_routes.py](../../src/opsmesh/identity/users/admin_routes.py) · `list_admin_users`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `status` | query | 否 | string anyOf null | `anyOf=[{"enum": ["active", "disabled", "invited"], "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AdminUserListResponse_](schemas.md#schema-PageResponse_AdminUserListResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/users`

Create Admin User

Operation ID：`create_admin_user_api_v1_admin_users_post`。

实现：[src/opsmesh/identity/users/admin_routes.py](../../src/opsmesh/identity/users/admin_routes.py) · `create_admin_user`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminUserCreateRequest"
}
```

模型：[AdminUserCreateRequest](schemas.md#schema-AdminUserCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [AdminUserCreateResponse](schemas.md#schema-AdminUserCreateResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/users/{user_id}`

Get Admin User

Operation ID：`get_admin_user_api_v1_admin_users__user_id__get`。

实现：[src/opsmesh/identity/users/admin_routes.py](../../src/opsmesh/identity/users/admin_routes.py) · `get_admin_user`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `user_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminUserDetailResponse](schemas.md#schema-AdminUserDetailResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/admin/users/{user_id}`

Update Admin User

Operation ID：`update_admin_user_api_v1_admin_users__user_id__patch`。

实现：[src/opsmesh/identity/users/admin_routes.py](../../src/opsmesh/identity/users/admin_routes.py) · `update_admin_user`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `user_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminUserUpdateRequest"
}
```

模型：[AdminUserUpdateRequest](schemas.md#schema-AdminUserUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminUserResponse](schemas.md#schema-AdminUserResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/users/{user_id}/invitation/resend`

Resend User Invitation

Operation ID：`resend_user_invitation_api_v1_admin_users__user_id__invitation_resend_post`。

实现：[src/opsmesh/identity/invitations/admin_routes.py](../../src/opsmesh/identity/invitations/admin_routes.py) · `resend_user_invitation`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `user_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [UserInvitationResponse](schemas.md#schema-UserInvitationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/users/{user_id}/reset-password`

Reset Admin User Password

Operation ID：`reset_admin_user_password_api_v1_admin_users__user_id__reset_password_post`。

实现：[src/opsmesh/identity/users/admin_routes.py](../../src/opsmesh/identity/users/admin_routes.py) · `reset_admin_user_password`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `user_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminUserPasswordResetResponse](schemas.md#schema-AdminUserPasswordResetResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/users/{user_id}/revoke-tokens`

Revoke Admin User Tokens

Operation ID：`revoke_admin_user_tokens_api_v1_admin_users__user_id__revoke_tokens_post`。

实现：[src/opsmesh/identity/users/admin_routes.py](../../src/opsmesh/identity/users/admin_routes.py) · `revoke_admin_user_tokens`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `user_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminUserTokenRevokeResponse](schemas.md#schema-AdminUserTokenRevokeResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PUT `/api/v1/admin/users/{user_id}/status`

Update Admin User Status

Operation ID：`update_admin_user_status_api_v1_admin_users__user_id__status_put`。

实现：[src/opsmesh/identity/users/admin_routes.py](../../src/opsmesh/identity/users/admin_routes.py) · `update_admin_user_status`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `user_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminUserStatusUpdateRequest"
}
```

模型：[AdminUserStatusUpdateRequest](schemas.md#schema-AdminUserStatusUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminUserResponse](schemas.md#schema-AdminUserResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/worker-leases`

List Admin Worker Leases

Operation ID：`list_admin_worker_leases_api_v1_admin_worker_leases_get`。

实现：[src/opsmesh/runtime/instances/lease_admin_routes.py](../../src/opsmesh/runtime/instances/lease_admin_routes.py) · `list_admin_worker_leases`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `workspace_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `worker_id` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_WorkerLeaseResponse_](schemas.md#schema-PageResponse_WorkerLeaseResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/workers`

List Admin Workers

Operation ID：`list_admin_workers_api_v1_admin_workers_get`。

实现：[src/opsmesh/runtime/workers/admin_routes.py](../../src/opsmesh/runtime/workers/admin_routes.py) · `list_admin_workers`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `worker_type` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_WorkerNodeResponse_](schemas.md#schema-PageResponse_WorkerNodeResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/admin/workers/{worker_id}`

Update Admin Worker

Operation ID：`update_admin_worker_api_v1_admin_workers__worker_id__patch`。

实现：[src/opsmesh/runtime/workers/admin_routes.py](../../src/opsmesh/runtime/workers/admin_routes.py) · `update_admin_worker`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `worker_id` | path | 是 | string | — |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminWorkerUpdateRequest"
}
```

模型：[AdminWorkerUpdateRequest](schemas.md#schema-AdminWorkerUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkerNodeResponse](schemas.md#schema-WorkerNodeResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/workers/{worker_id}/drain`

Drain Admin Worker

Operation ID：`drain_admin_worker_api_v1_admin_workers__worker_id__drain_post`。

实现：[src/opsmesh/runtime/workers/admin_routes.py](../../src/opsmesh/runtime/workers/admin_routes.py) · `drain_admin_worker`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `worker_id` | path | 是 | string | — |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkerNodeResponse](schemas.md#schema-WorkerNodeResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/workspaces`

List Admin Workspaces

Operation ID：`list_admin_workspaces_api_v1_admin_workspaces_get`。

实现：[src/opsmesh/platform/overview/routes.py](../../src/opsmesh/platform/overview/routes.py) · `list_admin_workspaces`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AdminWorkspaceResponse_](schemas.md#schema-PageResponse_AdminWorkspaceResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/workspaces/{workspace_id}`

Get Admin Workspace

Operation ID：`get_admin_workspace_api_v1_admin_workspaces__workspace_id__get`。

实现：[src/opsmesh/workspaces/management/admin_routes.py](../../src/opsmesh/workspaces/management/admin_routes.py) · `get_admin_workspace`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminWorkspaceResponse](schemas.md#schema-AdminWorkspaceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/workspaces/{workspace_id}/marketplace/reviews/{approval_id}/decision`

Decide Review

Operation ID：`decide_review_api_v1_admin_workspaces__workspace_id__marketplace_reviews__approval_id__decision_post`。

实现：[src/opsmesh/capabilities/marketplace/admin_routes.py](../../src/opsmesh/capabilities/marketplace/admin_routes.py) · `decide_review`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `approval_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/PublicationDecisionRequest"
}
```

模型：[PublicationDecisionRequest](schemas.md#schema-PublicationDecisionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ApprovalResponse](schemas.md#schema-ApprovalResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/workspaces/{workspace_id}/members`

List Admin Workspace Members

Operation ID：`list_admin_workspace_members_api_v1_admin_workspaces__workspace_id__members_get`。

实现：[src/opsmesh/workspaces/members/admin_routes.py](../../src/opsmesh/workspaces/members/admin_routes.py) · `list_admin_workspace_members`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AdminWorkspaceMemberResponse_](schemas.md#schema-PageResponse_AdminWorkspaceMemberResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/workspaces/{workspace_id}/members`

Add Admin Workspace Member

Operation ID：`add_admin_workspace_member_api_v1_admin_workspaces__workspace_id__members_post`。

实现：[src/opsmesh/workspaces/members/admin_routes.py](../../src/opsmesh/workspaces/members/admin_routes.py) · `add_admin_workspace_member`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminWorkspaceMemberCreateRequest"
}
```

模型：[AdminWorkspaceMemberCreateRequest](schemas.md#schema-AdminWorkspaceMemberCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [AdminWorkspaceMemberResponse](schemas.md#schema-AdminWorkspaceMemberResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## DELETE `/api/v1/admin/workspaces/{workspace_id}/members/{member_id}`

Remove Admin Workspace Member

Operation ID：`remove_admin_workspace_member_api_v1_admin_workspaces__workspace_id__members__member_id__delete`。

实现：[src/opsmesh/workspaces/members/admin_routes.py](../../src/opsmesh/workspaces/members/admin_routes.py) · `remove_admin_workspace_member`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `member_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminWorkspaceMemberResponse](schemas.md#schema-AdminWorkspaceMemberResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/admin/workspaces/{workspace_id}/members/{member_id}`

Update Admin Workspace Member

Operation ID：`update_admin_workspace_member_api_v1_admin_workspaces__workspace_id__members__member_id__patch`。

实现：[src/opsmesh/workspaces/members/admin_routes.py](../../src/opsmesh/workspaces/members/admin_routes.py) · `update_admin_workspace_member`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `member_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminWorkspaceMemberUpdateRequest"
}
```

模型：[AdminWorkspaceMemberUpdateRequest](schemas.md#schema-AdminWorkspaceMemberUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminWorkspaceMemberResponse](schemas.md#schema-AdminWorkspaceMemberResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/workspaces/{workspace_id}/operations/audit-integrity/verify`

Verify

Operation ID：`verify_api_v1_admin_workspaces__workspace_id__operations_audit_integrity_verify_post`。

实现：[src/opsmesh/runtime/operations/admin_dashboard_routes.py](../../src/opsmesh/runtime/operations/admin_dashboard_routes.py) · `verify`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/OperationRequest"
}
```

模型：[OperationRequest](schemas.md#schema-OperationRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 202 | Successful Response | application/json | [AdminOperationResponse](schemas.md#schema-AdminOperationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/workspaces/{workspace_id}/operations/scheduler`

Scheduler

Operation ID：`scheduler_api_v1_admin_workspaces__workspace_id__operations_scheduler_get`。

实现：[src/opsmesh/runtime/operations/admin_dashboard_routes.py](../../src/opsmesh/runtime/operations/admin_dashboard_routes.py) · `scheduler`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [OperationsSchedulerResponse](schemas.md#schema-OperationsSchedulerResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/workspaces/{workspace_id}/operations/stale-runs/recover`

Recover

Operation ID：`recover_api_v1_admin_workspaces__workspace_id__operations_stale_runs_recover_post`。

实现：[src/opsmesh/runtime/operations/admin_dashboard_routes.py](../../src/opsmesh/runtime/operations/admin_dashboard_routes.py) · `recover`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/RecoveryRequest"
}
```

模型：[RecoveryRequest](schemas.md#schema-RecoveryRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 202 | Successful Response | application/json | [AdminOperationResponse](schemas.md#schema-AdminOperationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/workspaces/{workspace_id}/plugin-trust-keys/{key_id}/revoke`

Revoke Publisher Key

Operation ID：`revoke_publisher_key_api_v1_admin_workspaces__workspace_id__plugin_trust_keys__key_id__revoke_post`。

实现：[src/opsmesh/capabilities/plugins/admin_routes.py](../../src/opsmesh/capabilities/plugins/admin_routes.py) · `revoke_publisher_key`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `key_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminPluginGovernanceRequest"
}
```

模型：[AdminPluginGovernanceRequest](schemas.md#schema-AdminPluginGovernanceRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminPluginPublisherTrustResponse](schemas.md#schema-AdminPluginPublisherTrustResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/workspaces/{workspace_id}/plugins/{install_id}/disable`

Disable Plugin

Operation ID：`disable_plugin_api_v1_admin_workspaces__workspace_id__plugins__install_id__disable_post`。

实现：[src/opsmesh/capabilities/plugins/admin_routes.py](../../src/opsmesh/capabilities/plugins/admin_routes.py) · `disable_plugin`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminPluginGovernanceRequest"
}
```

模型：[AdminPluginGovernanceRequest](schemas.md#schema-AdminPluginGovernanceRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminPluginGovernanceResponse](schemas.md#schema-AdminPluginGovernanceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/admin/workspaces/{workspace_id}/plugins/{install_id}/release`

Release Plugin

Operation ID：`release_plugin_api_v1_admin_workspaces__workspace_id__plugins__install_id__release_post`。

实现：[src/opsmesh/capabilities/plugins/admin_routes.py](../../src/opsmesh/capabilities/plugins/admin_routes.py) · `release_plugin`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `install_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminPluginGovernanceRequest"
}
```

模型：[AdminPluginGovernanceRequest](schemas.md#schema-AdminPluginGovernanceRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminPluginGovernanceResponse](schemas.md#schema-AdminPluginGovernanceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/workspaces/{workspace_id}/projects`

List Admin Workspace Projects

Operation ID：`list_admin_workspace_projects_api_v1_admin_workspaces__workspace_id__projects_get`。

实现：[src/opsmesh/workspaces/projects/admin_routes.py](../../src/opsmesh/workspaces/projects/admin_routes.py) · `list_admin_workspace_projects`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AdminProjectResponse_](schemas.md#schema-PageResponse_AdminProjectResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/workspaces/{workspace_id}/projects/{project_id}`

Get Admin Project

Operation ID：`get_admin_project_api_v1_admin_workspaces__workspace_id__projects__project_id__get`。

实现：[src/opsmesh/workspaces/projects/admin_routes.py](../../src/opsmesh/workspaces/projects/admin_routes.py) · `get_admin_project`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `project_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminProjectResponse](schemas.md#schema-AdminProjectResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/workspaces/{workspace_id}/projects/{project_id}/quotas`

List Admin Project Quotas

Operation ID：`list_admin_project_quotas_api_v1_admin_workspaces__workspace_id__projects__project_id__quotas_get`。

实现：[src/opsmesh/workspaces/projects/admin_routes.py](../../src/opsmesh/workspaces/projects/admin_routes.py) · `list_admin_project_quotas`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `project_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[AdminProjectQuotaResponse](schemas.md#schema-AdminProjectQuotaResponse)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PUT `/api/v1/admin/workspaces/{workspace_id}/projects/{project_id}/quotas`

Upsert Admin Project Quotas

Operation ID：`upsert_admin_project_quotas_api_v1_admin_workspaces__workspace_id__projects__project_id__quotas_put`。

实现：[src/opsmesh/workspaces/projects/admin_routes.py](../../src/opsmesh/workspaces/projects/admin_routes.py) · `upsert_admin_project_quotas`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `project_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminProjectQuotaUpsertRequest"
}
```

模型：[AdminProjectQuotaUpsertRequest](schemas.md#schema-AdminProjectQuotaUpsertRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[AdminProjectQuotaResponse](schemas.md#schema-AdminProjectQuotaResponse)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## DELETE `/api/v1/admin/workspaces/{workspace_id}/projects/{project_id}/quotas/{quota_key}`

Disable Admin Project Quota

Operation ID：`disable_admin_project_quota_api_v1_admin_workspaces__workspace_id__projects__project_id__quotas__quota_key__delete`。

实现：[src/opsmesh/workspaces/projects/admin_routes.py](../../src/opsmesh/workspaces/projects/admin_routes.py) · `disable_admin_project_quota`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `project_id` | path | 是 | string (uuid) | `format="uuid"` |
| `quota_key` | path | 是 | string | — |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminProjectQuotaResponse](schemas.md#schema-AdminProjectQuotaResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/admin/workspaces/{workspace_id}/projects/{project_id}/status`

Update Admin Project Status

Operation ID：`update_admin_project_status_api_v1_admin_workspaces__workspace_id__projects__project_id__status_patch`。

实现：[src/opsmesh/workspaces/projects/admin_routes.py](../../src/opsmesh/workspaces/projects/admin_routes.py) · `update_admin_project_status`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `project_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminProjectStatusUpdateRequest"
}
```

模型：[AdminProjectStatusUpdateRequest](schemas.md#schema-AdminProjectStatusUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminProjectResponse](schemas.md#schema-AdminProjectResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/admin/workspaces/{workspace_id}/resources/{kind}/{resource_id}/authorization`

Get Admin Resource Authorization

Operation ID：`get_admin_resource_authorization_api_v1_admin_workspaces__workspace_id__resources__kind___resource_id__authorization_get`。

实现：[src/opsmesh/identity/authorization/admin_routes.py](../../src/opsmesh/identity/authorization/admin_routes.py) · `get_admin_resource_authorization`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `kind` | path | 是 | [ResourceKind](schemas.md#schema-ResourceKind) | — |
| `resource_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminResourceAuthorizationResponse](schemas.md#schema-AdminResourceAuthorizationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PUT `/api/v1/admin/workspaces/{workspace_id}/resources/{kind}/{resource_id}/grants`

Replace Admin Resource Grants

Operation ID：`replace_admin_resource_grants_api_v1_admin_workspaces__workspace_id__resources__kind___resource_id__grants_put`。

实现：[src/opsmesh/identity/authorization/admin_routes.py](../../src/opsmesh/identity/authorization/admin_routes.py) · `replace_admin_resource_grants`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `kind` | path | 是 | [ResourceKind](schemas.md#schema-ResourceKind) | — |
| `resource_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminResourceGrantUpdateRequest"
}
```

模型：[AdminResourceGrantUpdateRequest](schemas.md#schema-AdminResourceGrantUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminResourceAuthorizationResponse](schemas.md#schema-AdminResourceAuthorizationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PUT `/api/v1/admin/workspaces/{workspace_id}/resources/{kind}/{resource_id}/owner`

Assign Admin Resource Owner

Operation ID：`assign_admin_resource_owner_api_v1_admin_workspaces__workspace_id__resources__kind___resource_id__owner_put`。

实现：[src/opsmesh/identity/authorization/admin_routes.py](../../src/opsmesh/identity/authorization/admin_routes.py) · `assign_admin_resource_owner`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `kind` | path | 是 | [ResourceKind](schemas.md#schema-ResourceKind) | — |
| `resource_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminResourceOwnerUpdateRequest"
}
```

模型：[AdminResourceOwnerUpdateRequest](schemas.md#schema-AdminResourceOwnerUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminResourceAuthorizationResponse](schemas.md#schema-AdminResourceAuthorizationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/admin/workspaces/{workspace_id}/status`

Update Admin Workspace Status

Operation ID：`update_admin_workspace_status_api_v1_admin_workspaces__workspace_id__status_patch`。

实现：[src/opsmesh/workspaces/management/admin_routes.py](../../src/opsmesh/workspaces/management/admin_routes.py) · `update_admin_workspace_status`。

权限依赖：`opsmesh.identity.authorization.admin_dependencies.require_platform_admin`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AdminWorkspaceStatusUpdateRequest"
}
```

模型：[AdminWorkspaceStatusUpdateRequest](schemas.md#schema-AdminWorkspaceStatusUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AdminWorkspaceResponse](schemas.md#schema-AdminWorkspaceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
