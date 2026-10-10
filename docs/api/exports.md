# exports

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| POST | `/api/v1/workspaces/{workspace_id}/exports/archive` | Export Workspace Archive |
| POST | `/api/v1/workspaces/{workspace_id}/exports/archive/import` | Import Workspace Archive |
| POST | `/api/v1/workspaces/{workspace_id}/exports/archive/import/preview` | Preview Workspace Archive Import |
| POST | `/api/v1/workspaces/{workspace_id}/exports/archive/jobs` | Create Workspace Archive Export Job |
| GET | `/api/v1/workspaces/{workspace_id}/exports/archive/jobs/{job_id}` | Get Workspace Archive Export Job |
| GET | `/api/v1/workspaces/{workspace_id}/exports/archive/jobs/{job_id}/download` | Download Workspace Archive Export Job |
| POST | `/api/v1/workspaces/{workspace_id}/exports/archive/jobs/{job_id}/restore-drill` | Run Workspace Archive Restore Drill |
| POST | `/api/v1/workspaces/{workspace_id}/exports/archive/jobs/{job_id}/verify` | Verify Workspace Archive Export Job |
| GET | `/api/v1/workspaces/{workspace_id}/exports/lifecycle-diagnostics` | Get Workspace Data Lifecycle Diagnostics |
| POST | `/api/v1/workspaces/{workspace_id}/exports/metadata` | Export Workspace Metadata |
| POST | `/api/v1/workspaces/{workspace_id}/exports/metadata/import` | Import Workspace Metadata |
| POST | `/api/v1/workspaces/{workspace_id}/exports/metadata/import/preview` | Preview Workspace Metadata Import |
| GET | `/api/v1/workspaces/{workspace_id}/exports/recovery-readiness` | Get Workspace Recovery Readiness |
| POST | `/api/v1/workspaces/{workspace_id}/exports/recovery-readiness/actions/apply` | Apply Workspace Recovery Readiness Actions |
| POST | `/api/v1/workspaces/{workspace_id}/exports/retention/apply` | Apply Workspace Retention |
| POST | `/api/v1/workspaces/{workspace_id}/exports/retention/preview` | Preview Workspace Retention |

## POST `/api/v1/workspaces/{workspace_id}/exports/archive`

Export Workspace Archive

Operation ID：`export_workspace_archive_api_v1_workspaces__workspace_id__exports_archive_post`。

实现：[src/opsmesh/resources/transfers/routes/archive.py](../../src/opsmesh/resources/transfers/routes/archive.py) · `export_workspace_archive`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/WorkspaceArchiveExportRequest"
}
```

模型：[WorkspaceArchiveExportRequest](schemas.md#schema-WorkspaceArchiveExportRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | any |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/exports/archive/import`

Import Workspace Archive

Operation ID：`import_workspace_archive_api_v1_workspaces__workspace_id__exports_archive_import_post`。

实现：[src/opsmesh/resources/transfers/routes/archive_import.py](../../src/opsmesh/resources/transfers/routes/archive_import.py) · `import_workspace_archive`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`multipart/form-data`。

```json
{
  "$ref": "#/components/schemas/Body_import_workspace_archive_api_v1_workspaces__workspace_id__exports_archive_import_post"
}
```

模型：[Body_import_workspace_archive_api_v1_workspaces__workspace_id__exports_archive_import_post](schemas.md#schema-Body_import_workspace_archive_api_v1_workspaces__workspace_id__exports_archive_import_post)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceImportResponse](schemas.md#schema-WorkspaceImportResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/exports/archive/import/preview`

Preview Workspace Archive Import

Operation ID：`preview_workspace_archive_import_api_v1_workspaces__workspace_id__exports_archive_import_preview_post`。

实现：[src/opsmesh/resources/transfers/routes/archive_import.py](../../src/opsmesh/resources/transfers/routes/archive_import.py) · `preview_workspace_archive_import`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`multipart/form-data`。

```json
{
  "$ref": "#/components/schemas/Body_preview_workspace_archive_import_api_v1_workspaces__workspace_id__exports_archive_import_preview_post"
}
```

模型：[Body_preview_workspace_archive_import_api_v1_workspaces__workspace_id__exports_archive_import_preview_post](schemas.md#schema-Body_preview_workspace_archive_import_api_v1_workspaces__workspace_id__exports_archive_import_preview_post)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceImportResponse](schemas.md#schema-WorkspaceImportResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/exports/archive/jobs`

Create Workspace Archive Export Job

Operation ID：`create_workspace_archive_export_job_api_v1_workspaces__workspace_id__exports_archive_jobs_post`。

实现：[src/opsmesh/resources/transfers/routes/archive_jobs.py](../../src/opsmesh/resources/transfers/routes/archive_jobs.py) · `create_workspace_archive_export_job`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/WorkspaceArchiveExportRequest"
}
```

模型：[WorkspaceArchiveExportRequest](schemas.md#schema-WorkspaceArchiveExportRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 202 | Successful Response | application/json | [WorkspaceExportJobResponse](schemas.md#schema-WorkspaceExportJobResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/exports/archive/jobs/{job_id}`

Get Workspace Archive Export Job

Operation ID：`get_workspace_archive_export_job_api_v1_workspaces__workspace_id__exports_archive_jobs__job_id__get`。

实现：[src/opsmesh/resources/transfers/routes/archive_jobs.py](../../src/opsmesh/resources/transfers/routes/archive_jobs.py) · `get_workspace_archive_export_job`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `job_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceExportJobResponse](schemas.md#schema-WorkspaceExportJobResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/exports/archive/jobs/{job_id}/download`

Download Workspace Archive Export Job

Operation ID：`download_workspace_archive_export_job_api_v1_workspaces__workspace_id__exports_archive_jobs__job_id__download_get`。

实现：[src/opsmesh/resources/transfers/routes/archive_jobs.py](../../src/opsmesh/resources/transfers/routes/archive_jobs.py) · `download_workspace_archive_export_job`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `job_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | any |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/exports/archive/jobs/{job_id}/restore-drill`

Run Workspace Archive Restore Drill

Operation ID：`run_workspace_archive_restore_drill_api_v1_workspaces__workspace_id__exports_archive_jobs__job_id__restore_drill_post`。

实现：[src/opsmesh/resources/transfers/routes/archive_jobs.py](../../src/opsmesh/resources/transfers/routes/archive_jobs.py) · `run_workspace_archive_restore_drill`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `job_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：否。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/WorkspaceArchiveRestoreDrillRequest"
}
```

模型：[WorkspaceArchiveRestoreDrillRequest](schemas.md#schema-WorkspaceArchiveRestoreDrillRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceRestoreDrillResponse](schemas.md#schema-WorkspaceRestoreDrillResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/exports/archive/jobs/{job_id}/verify`

Verify Workspace Archive Export Job

Operation ID：`verify_workspace_archive_export_job_api_v1_workspaces__workspace_id__exports_archive_jobs__job_id__verify_post`。

实现：[src/opsmesh/resources/transfers/routes/archive_jobs.py](../../src/opsmesh/resources/transfers/routes/archive_jobs.py) · `verify_workspace_archive_export_job`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `job_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceArchiveIntegrityResponse](schemas.md#schema-WorkspaceArchiveIntegrityResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/exports/lifecycle-diagnostics`

Get Workspace Data Lifecycle Diagnostics

Operation ID：`get_workspace_data_lifecycle_diagnostics_api_v1_workspaces__workspace_id__exports_lifecycle_diagnostics_get`。

实现：[src/opsmesh/resources/lifecycle/routes.py](../../src/opsmesh/resources/lifecycle/routes.py) · `get_workspace_data_lifecycle_diagnostics`。

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
| 200 | Successful Response | application/json | [WorkspaceDataLifecycleResponse](schemas.md#schema-WorkspaceDataLifecycleResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/exports/metadata`

Export Workspace Metadata

Operation ID：`export_workspace_metadata_api_v1_workspaces__workspace_id__exports_metadata_post`。

实现：[src/opsmesh/resources/transfers/routes/metadata.py](../../src/opsmesh/resources/transfers/routes/metadata.py) · `export_workspace_metadata`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/WorkspaceExportRequest"
}
```

模型：[WorkspaceExportRequest](schemas.md#schema-WorkspaceExportRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | any |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/exports/metadata/import`

Import Workspace Metadata

Operation ID：`import_workspace_metadata_api_v1_workspaces__workspace_id__exports_metadata_import_post`。

实现：[src/opsmesh/resources/transfers/routes/metadata.py](../../src/opsmesh/resources/transfers/routes/metadata.py) · `import_workspace_metadata`。

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
  "$ref": "#/components/schemas/WorkspaceImportRequest"
}
```

模型：[WorkspaceImportRequest](schemas.md#schema-WorkspaceImportRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceImportResponse](schemas.md#schema-WorkspaceImportResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/exports/metadata/import/preview`

Preview Workspace Metadata Import

Operation ID：`preview_workspace_metadata_import_api_v1_workspaces__workspace_id__exports_metadata_import_preview_post`。

实现：[src/opsmesh/resources/transfers/routes/metadata.py](../../src/opsmesh/resources/transfers/routes/metadata.py) · `preview_workspace_metadata_import`。

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
  "$ref": "#/components/schemas/WorkspaceImportRequest"
}
```

模型：[WorkspaceImportRequest](schemas.md#schema-WorkspaceImportRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceImportResponse](schemas.md#schema-WorkspaceImportResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/exports/recovery-readiness`

Get Workspace Recovery Readiness

Operation ID：`get_workspace_recovery_readiness_api_v1_workspaces__workspace_id__exports_recovery_readiness_get`。

实现：[src/opsmesh/resources/lifecycle/routes.py](../../src/opsmesh/resources/lifecycle/routes.py) · `get_workspace_recovery_readiness`。

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
| 200 | Successful Response | application/json | [WorkspaceRecoveryReadinessResponse](schemas.md#schema-WorkspaceRecoveryReadinessResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/exports/recovery-readiness/actions/apply`

Apply Workspace Recovery Readiness Actions

Operation ID：`apply_workspace_recovery_readiness_actions_api_v1_workspaces__workspace_id__exports_recovery_readiness_actions_apply_post`。

实现：[src/opsmesh/resources/lifecycle/routes.py](../../src/opsmesh/resources/lifecycle/routes.py) · `apply_workspace_recovery_readiness_actions`。

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
  "$ref": "#/components/schemas/WorkspaceRecoveryReadinessActionRequest"
}
```

模型：[WorkspaceRecoveryReadinessActionRequest](schemas.md#schema-WorkspaceRecoveryReadinessActionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceRecoveryReadinessActionResponse](schemas.md#schema-WorkspaceRecoveryReadinessActionResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/exports/retention/apply`

Apply Workspace Retention

Operation ID：`apply_workspace_retention_api_v1_workspaces__workspace_id__exports_retention_apply_post`。

实现：[src/opsmesh/resources/lifecycle/routes.py](../../src/opsmesh/resources/lifecycle/routes.py) · `apply_workspace_retention`。

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
  "$ref": "#/components/schemas/WorkspaceRetentionRequest"
}
```

模型：[WorkspaceRetentionRequest](schemas.md#schema-WorkspaceRetentionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceRetentionResponse](schemas.md#schema-WorkspaceRetentionResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/exports/retention/preview`

Preview Workspace Retention

Operation ID：`preview_workspace_retention_api_v1_workspaces__workspace_id__exports_retention_preview_post`。

实现：[src/opsmesh/resources/lifecycle/routes.py](../../src/opsmesh/resources/lifecycle/routes.py) · `preview_workspace_retention`。

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
  "$ref": "#/components/schemas/WorkspaceRetentionRequest"
}
```

模型：[WorkspaceRetentionRequest](schemas.md#schema-WorkspaceRetentionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceRetentionResponse](schemas.md#schema-WorkspaceRetentionResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
