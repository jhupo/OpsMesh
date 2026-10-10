# self-hosted-runtime

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| POST | `/api/v1/self-hosted/artifact-uploads` | Register Artifact Upload |
| POST | `/api/v1/self-hosted/heartbeat` | Heartbeat |
| GET | `/api/v1/self-hosted/jobs/next` | Poll Job |
| POST | `/api/v1/self-hosted/jobs/{agent_run_id}/claim` | Claim Job |
| POST | `/api/v1/self-hosted/jobs/{agent_run_id}/complete` | Complete Job |
| GET | `/api/v1/self-hosted/jobs/{agent_run_id}/project/archive` | Download Project Archive |
| PUT | `/api/v1/self-hosted/jobs/{agent_run_id}/project/outputs/{project_output_id}` | Upload Project Output |
| POST | `/api/v1/self-hosted/local-files` | Create Local File Reference |
| GET | `/api/v1/self-hosted/mcp-jobs/next` | Poll Mcp Job |
| GET | `/api/v1/self-hosted/mcp-jobs/{mcp_job_id}/cancellation` | Mcp Job Cancellation |
| POST | `/api/v1/self-hosted/mcp-jobs/{mcp_job_id}/claim` | Claim Mcp Job |
| POST | `/api/v1/self-hosted/mcp-jobs/{mcp_job_id}/complete` | Complete Mcp Job |
| POST | `/api/v1/self-hosted/progress` | Upload Progress |
| POST | `/api/v1/self-hosted/register` | Register Self Hosted Runtime |
| GET | `/api/v1/workspaces/{workspace_id}/self-hosted/connector-manifest` | Self Hosted Connector Manifest |
| POST | `/api/v1/workspaces/{workspace_id}/self-hosted/credentials/{credential_id}/revoke` | Revoke Runtime Credential |
| POST | `/api/v1/workspaces/{workspace_id}/self-hosted/enrollment-tokens` | Create Enrollment Token |
| POST | `/api/v1/workspaces/{workspace_id}/self-hosted/worker-cleanup` | Cleanup Self Hosted Workers |
| GET | `/api/v1/workspaces/{workspace_id}/self-hosted/workers/trust` | List Self Hosted Worker Trust |
| POST | `/api/v1/workspaces/{workspace_id}/self-hosted/workers/{worker_id}/quarantine` | Quarantine Self Hosted Worker |
| POST | `/api/v1/workspaces/{workspace_id}/self-hosted/workers/{worker_id}/resume` | Resume Self Hosted Worker |
| POST | `/api/v1/workspaces/{workspace_id}/self-hosted/workers/{worker_id}/revoke` | Revoke Self Hosted Worker |

## POST `/api/v1/self-hosted/artifact-uploads`

Register Artifact Upload

Operation ID：`register_artifact_upload_api_v1_self_hosted_artifact_uploads_post`。

实现：[src/opsmesh/runtime/self_hosted/routes/artifacts.py](../../src/opsmesh/runtime/self_hosted/routes/artifacts.py) · `register_artifact_upload`。

权限依赖：`opsmesh.runtime.self_hosted.routes.dependencies.get_authenticated_worker`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `X-Runtime-Authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ArtifactUploadRequest"
}
```

模型：[ArtifactUploadRequest](schemas.md#schema-ArtifactUploadRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [ArtifactUploadResponse](schemas.md#schema-ArtifactUploadResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/self-hosted/heartbeat`

Heartbeat

Operation ID：`heartbeat_api_v1_self_hosted_heartbeat_post`。

实现：[src/opsmesh/runtime/self_hosted/routes/identity.py](../../src/opsmesh/runtime/self_hosted/routes/identity.py) · `heartbeat`。

权限依赖：`opsmesh.runtime.self_hosted.routes.dependencies.get_authenticated_worker`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `X-Runtime-Authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/opsmesh__runtime__self_hosted__schemas__WorkerHeartbeatRequest"
}
```

模型：[opsmesh__runtime__self_hosted__schemas__WorkerHeartbeatRequest](schemas.md#schema-opsmesh__runtime__self_hosted__schemas__WorkerHeartbeatRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [opsmesh__runtime__self_hosted__schemas__WorkerHeartbeatResponse](schemas.md#schema-opsmesh__runtime__self_hosted__schemas__WorkerHeartbeatResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/self-hosted/jobs/next`

Poll Job

Operation ID：`poll_job_api_v1_self_hosted_jobs_next_get`。

实现：[src/opsmesh/runtime/self_hosted/routes/jobs.py](../../src/opsmesh/runtime/self_hosted/routes/jobs.py) · `poll_job`。

权限依赖：`opsmesh.runtime.self_hosted.routes.dependencies.get_authenticated_worker`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `X-Runtime-Authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SelfHostedJobResponse](schemas.md#schema-SelfHostedJobResponse) anyOf null |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/self-hosted/jobs/{agent_run_id}/claim`

Claim Job

Operation ID：`claim_job_api_v1_self_hosted_jobs__agent_run_id__claim_post`。

实现：[src/opsmesh/runtime/self_hosted/routes/jobs.py](../../src/opsmesh/runtime/self_hosted/routes/jobs.py) · `claim_job`。

权限依赖：`opsmesh.runtime.self_hosted.routes.dependencies.get_authenticated_worker`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_run_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Runtime-Authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [JobClaimResponse](schemas.md#schema-JobClaimResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/self-hosted/jobs/{agent_run_id}/complete`

Complete Job

Operation ID：`complete_job_api_v1_self_hosted_jobs__agent_run_id__complete_post`。

实现：[src/opsmesh/runtime/self_hosted/routes/jobs.py](../../src/opsmesh/runtime/self_hosted/routes/jobs.py) · `complete_job`。

权限依赖：`opsmesh.runtime.self_hosted.routes.dependencies.get_authenticated_worker`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_run_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Runtime-Authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/JobCompleteRequest"
}
```

模型：[JobCompleteRequest](schemas.md#schema-JobCompleteRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [JobCompleteResponse](schemas.md#schema-JobCompleteResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/self-hosted/jobs/{agent_run_id}/project/archive`

Download Project Archive

Operation ID：`download_project_archive_api_v1_self_hosted_jobs__agent_run_id__project_archive_get`。

实现：[src/opsmesh/runtime/self_hosted/routes/jobs.py](../../src/opsmesh/runtime/self_hosted/routes/jobs.py) · `download_project_archive`。

权限依赖：`opsmesh.runtime.self_hosted.routes.dependencies.get_authenticated_worker`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_run_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Runtime-Authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | any |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PUT `/api/v1/self-hosted/jobs/{agent_run_id}/project/outputs/{project_output_id}`

Upload Project Output

Operation ID：`upload_project_output_api_v1_self_hosted_jobs__agent_run_id__project_outputs__project_output_id__put`。

实现：[src/opsmesh/runtime/self_hosted/routes/jobs.py](../../src/opsmesh/runtime/self_hosted/routes/jobs.py) · `upload_project_output`。

权限依赖：`opsmesh.runtime.self_hosted.routes.dependencies.get_authenticated_worker`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_run_id` | path | 是 | string (uuid) | `format="uuid"` |
| `project_output_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Runtime-Authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [ArtifactResponse](schemas.md#schema-ArtifactResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/self-hosted/local-files`

Create Local File Reference

Operation ID：`create_local_file_reference_api_v1_self_hosted_local_files_post`。

实现：[src/opsmesh/runtime/self_hosted/routes/artifacts.py](../../src/opsmesh/runtime/self_hosted/routes/artifacts.py) · `create_local_file_reference`。

权限依赖：`opsmesh.runtime.self_hosted.routes.dependencies.get_authenticated_worker`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `X-Runtime-Authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/LocalFileReferenceRequest"
}
```

模型：[LocalFileReferenceRequest](schemas.md#schema-LocalFileReferenceRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [LocalFileReferenceResponse](schemas.md#schema-LocalFileReferenceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/self-hosted/mcp-jobs/next`

Poll Mcp Job

Operation ID：`poll_mcp_job_api_v1_self_hosted_mcp_jobs_next_get`。

实现：[src/opsmesh/runtime/self_hosted/routes/mcp_jobs.py](../../src/opsmesh/runtime/self_hosted/routes/mcp_jobs.py) · `poll_mcp_job`。

权限依赖：`opsmesh.runtime.self_hosted.routes.dependencies.get_authenticated_worker`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `X-Runtime-Authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SelfHostedMcpJobResponse](schemas.md#schema-SelfHostedMcpJobResponse) anyOf null |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/self-hosted/mcp-jobs/{mcp_job_id}/cancellation`

Mcp Job Cancellation

Operation ID：`mcp_job_cancellation_api_v1_self_hosted_mcp_jobs__mcp_job_id__cancellation_get`。

实现：[src/opsmesh/runtime/self_hosted/routes/mcp_jobs.py](../../src/opsmesh/runtime/self_hosted/routes/mcp_jobs.py) · `mcp_job_cancellation`。

权限依赖：`opsmesh.runtime.self_hosted.routes.dependencies.get_authenticated_worker`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `mcp_job_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Runtime-Authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [McpJobCancellationResponse](schemas.md#schema-McpJobCancellationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/self-hosted/mcp-jobs/{mcp_job_id}/claim`

Claim Mcp Job

Operation ID：`claim_mcp_job_api_v1_self_hosted_mcp_jobs__mcp_job_id__claim_post`。

实现：[src/opsmesh/runtime/self_hosted/routes/mcp_jobs.py](../../src/opsmesh/runtime/self_hosted/routes/mcp_jobs.py) · `claim_mcp_job`。

权限依赖：`opsmesh.runtime.self_hosted.routes.dependencies.get_authenticated_worker`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `mcp_job_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Runtime-Authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [McpJobClaimResponse](schemas.md#schema-McpJobClaimResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/self-hosted/mcp-jobs/{mcp_job_id}/complete`

Complete Mcp Job

Operation ID：`complete_mcp_job_api_v1_self_hosted_mcp_jobs__mcp_job_id__complete_post`。

实现：[src/opsmesh/runtime/self_hosted/routes/mcp_jobs.py](../../src/opsmesh/runtime/self_hosted/routes/mcp_jobs.py) · `complete_mcp_job`。

权限依赖：`opsmesh.runtime.self_hosted.routes.dependencies.get_authenticated_worker`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `mcp_job_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Runtime-Authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/McpJobCompleteRequest"
}
```

模型：[McpJobCompleteRequest](schemas.md#schema-McpJobCompleteRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [McpJobCompleteResponse](schemas.md#schema-McpJobCompleteResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/self-hosted/progress`

Upload Progress

Operation ID：`upload_progress_api_v1_self_hosted_progress_post`。

实现：[src/opsmesh/runtime/self_hosted/routes/jobs.py](../../src/opsmesh/runtime/self_hosted/routes/jobs.py) · `upload_progress`。

权限依赖：`opsmesh.runtime.self_hosted.routes.dependencies.get_authenticated_worker`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `X-Runtime-Authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ProgressEventRequest"
}
```

模型：[ProgressEventRequest](schemas.md#schema-ProgressEventRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [RunEventResponse](schemas.md#schema-RunEventResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/self-hosted/register`

Register Self Hosted Runtime

Operation ID：`register_self_hosted_runtime_api_v1_self_hosted_register_post`。

实现：[src/opsmesh/runtime/self_hosted/routes/identity.py](../../src/opsmesh/runtime/self_hosted/routes/identity.py) · `register_self_hosted_runtime`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

参数：无显式路径、查询或 Header 参数。

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/RuntimeRegistrationRequest"
}
```

模型：[RuntimeRegistrationRequest](schemas.md#schema-RuntimeRegistrationRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [RuntimeRegistrationResponse](schemas.md#schema-RuntimeRegistrationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/self-hosted/connector-manifest`

Self Hosted Connector Manifest

Operation ID：`self_hosted_connector_manifest_api_v1_workspaces__workspace_id__self_hosted_connector_manifest_get`。

实现：[src/opsmesh/runtime/self_hosted/routes/worker_control.py](../../src/opsmesh/runtime/self_hosted/routes/worker_control.py) · `self_hosted_connector_manifest`。

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
| 200 | Successful Response | application/json | [SelfHostedConnectorManifestResponse](schemas.md#schema-SelfHostedConnectorManifestResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/self-hosted/credentials/{credential_id}/revoke`

Revoke Runtime Credential

Operation ID：`revoke_runtime_credential_api_v1_workspaces__workspace_id__self_hosted_credentials__credential_id__revoke_post`。

实现：[src/opsmesh/runtime/self_hosted/routes/worker_control.py](../../src/opsmesh/runtime/self_hosted/routes/worker_control.py) · `revoke_runtime_credential`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `credential_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：否。

Content-Type：`application/json`。

```json
{
  "anyOf": [
    {
      "$ref": "#/components/schemas/RuntimeCredentialRevokeRequest"
    },
    {
      "type": "null"
    }
  ],
  "title": "Request"
}
```

模型：[RuntimeCredentialRevokeRequest](schemas.md#schema-RuntimeCredentialRevokeRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 204 | Successful Response | — | 无响应体 |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/self-hosted/enrollment-tokens`

Create Enrollment Token

Operation ID：`create_enrollment_token_api_v1_workspaces__workspace_id__self_hosted_enrollment_tokens_post`。

实现：[src/opsmesh/runtime/self_hosted/routes/enrollment.py](../../src/opsmesh/runtime/self_hosted/routes/enrollment.py) · `create_enrollment_token`。

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
  "$ref": "#/components/schemas/EnrollmentTokenCreateRequest"
}
```

模型：[EnrollmentTokenCreateRequest](schemas.md#schema-EnrollmentTokenCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [EnrollmentTokenCreateResponse](schemas.md#schema-EnrollmentTokenCreateResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/self-hosted/worker-cleanup`

Cleanup Self Hosted Workers

Operation ID：`cleanup_self_hosted_workers_api_v1_workspaces__workspace_id__self_hosted_worker_cleanup_post`。

实现：[src/opsmesh/runtime/self_hosted/routes/worker_control.py](../../src/opsmesh/runtime/self_hosted/routes/worker_control.py) · `cleanup_self_hosted_workers`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `stale_after_seconds` | query | 否 | integer | `default=600`; `maximum=86400`; `minimum=60` |
| `quarantine_after_seconds` | query | 否 | integer anyOf null | `anyOf=[{"maximum": 604800, "minimum": 60, "type": "integer"}, {"type": "null"}]` |
| `job_claim_stale_after_seconds` | query | 否 | integer | `default=900`; `maximum=86400`; `minimum=60` |
| `mcp_job_stale_after_seconds` | query | 否 | integer | `default=900`; `maximum=86400`; `minimum=60` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SelfHostedWorkerCleanupResponse](schemas.md#schema-SelfHostedWorkerCleanupResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/self-hosted/workers/trust`

List Self Hosted Worker Trust

Operation ID：`list_self_hosted_worker_trust_api_v1_workspaces__workspace_id__self_hosted_workers_trust_get`。

实现：[src/opsmesh/runtime/self_hosted/routes/worker_control.py](../../src/opsmesh/runtime/self_hosted/routes/worker_control.py) · `list_self_hosted_worker_trust`。

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
| 200 | Successful Response | application/json | array&lt;[SelfHostedWorkerTrustResponse](schemas.md#schema-SelfHostedWorkerTrustResponse)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/self-hosted/workers/{worker_id}/quarantine`

Quarantine Self Hosted Worker

Operation ID：`quarantine_self_hosted_worker_api_v1_workspaces__workspace_id__self_hosted_workers__worker_id__quarantine_post`。

实现：[src/opsmesh/runtime/self_hosted/routes/worker_control.py](../../src/opsmesh/runtime/self_hosted/routes/worker_control.py) · `quarantine_self_hosted_worker`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `worker_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：否。

Content-Type：`application/json`。

```json
{
  "anyOf": [
    {
      "$ref": "#/components/schemas/SelfHostedWorkerControlRequest"
    },
    {
      "type": "null"
    }
  ],
  "title": "Request"
}
```

模型：[SelfHostedWorkerControlRequest](schemas.md#schema-SelfHostedWorkerControlRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SelfHostedWorkerControlResponse](schemas.md#schema-SelfHostedWorkerControlResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/self-hosted/workers/{worker_id}/resume`

Resume Self Hosted Worker

Operation ID：`resume_self_hosted_worker_api_v1_workspaces__workspace_id__self_hosted_workers__worker_id__resume_post`。

实现：[src/opsmesh/runtime/self_hosted/routes/worker_control.py](../../src/opsmesh/runtime/self_hosted/routes/worker_control.py) · `resume_self_hosted_worker`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `worker_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：否。

Content-Type：`application/json`。

```json
{
  "anyOf": [
    {
      "$ref": "#/components/schemas/SelfHostedWorkerControlRequest"
    },
    {
      "type": "null"
    }
  ],
  "title": "Request"
}
```

模型：[SelfHostedWorkerControlRequest](schemas.md#schema-SelfHostedWorkerControlRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SelfHostedWorkerControlResponse](schemas.md#schema-SelfHostedWorkerControlResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/self-hosted/workers/{worker_id}/revoke`

Revoke Self Hosted Worker

Operation ID：`revoke_self_hosted_worker_api_v1_workspaces__workspace_id__self_hosted_workers__worker_id__revoke_post`。

实现：[src/opsmesh/runtime/self_hosted/routes/worker_control.py](../../src/opsmesh/runtime/self_hosted/routes/worker_control.py) · `revoke_self_hosted_worker`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `worker_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：否。

Content-Type：`application/json`。

```json
{
  "anyOf": [
    {
      "$ref": "#/components/schemas/SelfHostedWorkerControlRequest"
    },
    {
      "type": "null"
    }
  ],
  "title": "Request"
}
```

模型：[SelfHostedWorkerControlRequest](schemas.md#schema-SelfHostedWorkerControlRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SelfHostedWorkerControlResponse](schemas.md#schema-SelfHostedWorkerControlResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
