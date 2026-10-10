# workspace-memory

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/memories/configuration` | Get Memory Configuration |
| PUT | `/api/v1/workspaces/{workspace_id}/memories/configuration` | Update Memory Configuration |
| GET | `/api/v1/workspaces/{workspace_id}/memories/embedding-events` | List Memory Embedding Events |
| POST | `/api/v1/workspaces/{workspace_id}/memories/embeddings/retry` | Retry Memory Embeddings |
| GET | `/api/v1/workspaces/{workspace_id}/memories/lifecycle-events` | List Memory Lifecycle Events |
| GET | `/api/v1/workspaces/{workspace_id}/memories/retrieval-events` | List Memory Retrieval Events |
| GET | `/api/v1/workspaces/{workspace_id}/memories/semantic` | List Semantic Memory |
| POST | `/api/v1/workspaces/{workspace_id}/memories/semantic` | Upsert Semantic Memory |
| POST | `/api/v1/workspaces/{workspace_id}/memories/semantic/{memory_entry_id}/archive` | Archive Semantic Memory |
| GET | `/api/v1/workspaces/{workspace_id}/memories/semantic/{memory_entry_id}/versions` | List Semantic Memory Versions |

## GET `/api/v1/workspaces/{workspace_id}/memories/configuration`

Get Memory Configuration

Operation ID：`get_memory_configuration_api_v1_workspaces__workspace_id__memories_configuration_get`。

实现：[src/opsmesh/resources/memory/routes.py](../../src/opsmesh/resources/memory/routes.py) · `get_memory_configuration`。

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
| 200 | Successful Response | application/json | [WorkspaceMemoryConfigurationResponse](schemas.md#schema-WorkspaceMemoryConfigurationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PUT `/api/v1/workspaces/{workspace_id}/memories/configuration`

Update Memory Configuration

Operation ID：`update_memory_configuration_api_v1_workspaces__workspace_id__memories_configuration_put`。

实现：[src/opsmesh/resources/memory/routes.py](../../src/opsmesh/resources/memory/routes.py) · `update_memory_configuration`。

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
  "$ref": "#/components/schemas/WorkspaceMemoryConfigurationUpdateRequest"
}
```

模型：[WorkspaceMemoryConfigurationUpdateRequest](schemas.md#schema-WorkspaceMemoryConfigurationUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceMemoryConfigurationResponse](schemas.md#schema-WorkspaceMemoryConfigurationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/memories/embedding-events`

List Memory Embedding Events

Operation ID：`list_memory_embedding_events_api_v1_workspaces__workspace_id__memories_embedding_events_get`。

实现：[src/opsmesh/resources/memory/routes.py](../../src/opsmesh/resources/memory/routes.py) · `list_memory_embedding_events`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=200`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [MemoryEmbeddingEventListResponse](schemas.md#schema-MemoryEmbeddingEventListResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/memories/embeddings/retry`

Retry Memory Embeddings

Operation ID：`retry_memory_embeddings_api_v1_workspaces__workspace_id__memories_embeddings_retry_post`。

实现：[src/opsmesh/resources/memory/routes.py](../../src/opsmesh/resources/memory/routes.py) · `retry_memory_embeddings`。

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
| 200 | Successful Response | application/json | [MemoryEmbeddingRetryResponse](schemas.md#schema-MemoryEmbeddingRetryResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/memories/lifecycle-events`

List Memory Lifecycle Events

Operation ID：`list_memory_lifecycle_events_api_v1_workspaces__workspace_id__memories_lifecycle_events_get`。

实现：[src/opsmesh/resources/memory/routes.py](../../src/opsmesh/resources/memory/routes.py) · `list_memory_lifecycle_events`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=200`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [MemoryLifecycleEventListResponse](schemas.md#schema-MemoryLifecycleEventListResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/memories/retrieval-events`

List Memory Retrieval Events

Operation ID：`list_memory_retrieval_events_api_v1_workspaces__workspace_id__memories_retrieval_events_get`。

实现：[src/opsmesh/resources/memory/routes.py](../../src/opsmesh/resources/memory/routes.py) · `list_memory_retrieval_events`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=200`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [MemoryRetrievalEventListResponse](schemas.md#schema-MemoryRetrievalEventListResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/memories/semantic`

List Semantic Memory

Operation ID：`list_semantic_memory_api_v1_workspaces__workspace_id__memories_semantic_get`。

实现：[src/opsmesh/resources/memory/routes.py](../../src/opsmesh/resources/memory/routes.py) · `list_semantic_memory`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `scope_type` | query | 否 | string anyOf null | `anyOf=[{"enum": ["workspace", "team", "agent"], "type": "string"}, {"type": "null"}]` |
| `scope_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `include_archived` | query | 否 | boolean | `default=false` |
| `limit` | query | 否 | integer | `default=50`; `maximum=200`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SemanticMemoryListResponse](schemas.md#schema-SemanticMemoryListResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/memories/semantic`

Upsert Semantic Memory

Operation ID：`upsert_semantic_memory_api_v1_workspaces__workspace_id__memories_semantic_post`。

实现：[src/opsmesh/resources/memory/routes.py](../../src/opsmesh/resources/memory/routes.py) · `upsert_semantic_memory`。

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
  "$ref": "#/components/schemas/SemanticMemoryUpsertRequest"
}
```

模型：[SemanticMemoryUpsertRequest](schemas.md#schema-SemanticMemoryUpsertRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SemanticMemoryResponse](schemas.md#schema-SemanticMemoryResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/memories/semantic/{memory_entry_id}/archive`

Archive Semantic Memory

Operation ID：`archive_semantic_memory_api_v1_workspaces__workspace_id__memories_semantic__memory_entry_id__archive_post`。

实现：[src/opsmesh/resources/memory/routes.py](../../src/opsmesh/resources/memory/routes.py) · `archive_semantic_memory`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `memory_entry_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/SemanticMemoryArchiveRequest"
}
```

模型：[SemanticMemoryArchiveRequest](schemas.md#schema-SemanticMemoryArchiveRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SemanticMemoryResponse](schemas.md#schema-SemanticMemoryResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/memories/semantic/{memory_entry_id}/versions`

List Semantic Memory Versions

Operation ID：`list_semantic_memory_versions_api_v1_workspaces__workspace_id__memories_semantic__memory_entry_id__versions_get`。

实现：[src/opsmesh/resources/memory/routes.py](../../src/opsmesh/resources/memory/routes.py) · `list_semantic_memory_versions`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `memory_entry_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[SemanticMemoryVersionResponse](schemas.md#schema-SemanticMemoryVersionResponse)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
