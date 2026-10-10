# talent-marketplace

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/marketplace` | List Public Marketplace Listings |
| GET | `/api/v1/talent-market` | List Talent Market |
| GET | `/api/v1/talent-market/{listing_id}/metrics` | Get Talent Listing Metrics |
| GET | `/api/v1/talent-market/{listing_id}/reviews` | List Talent Listing Reviews |
| GET | `/api/v1/workspaces/{workspace_id}/marketplace-installs` | List Workspace Marketplace Installs |
| POST | `/api/v1/workspaces/{workspace_id}/marketplace-listings` | Create Workspace Marketplace Listing |
| POST | `/api/v1/workspaces/{workspace_id}/marketplace-listings/{listing_id}/install` | Install Marketplace Listing |
| GET | `/api/v1/workspaces/{workspace_id}/talent-installs` | List Workspace Talent Installs |
| POST | `/api/v1/workspaces/{workspace_id}/talent-installs/{install_id}/pin` | Update Talent Install Pin |
| POST | `/api/v1/workspaces/{workspace_id}/talent-installs/{install_id}/upgrade` | Upgrade Talent Install |
| GET | `/api/v1/workspaces/{workspace_id}/talent-installs/{install_id}/upgrade-status` | Get Talent Install Upgrade Status |
| POST | `/api/v1/workspaces/{workspace_id}/talent-listings` | Publish Agent To Talent Market |
| POST | `/api/v1/workspaces/{workspace_id}/talent-market/recommendations` | Recommend Talent For Team |
| POST | `/api/v1/workspaces/{workspace_id}/talent-market/{listing_id}/hire` | Hire Agent From Talent Market |
| POST | `/api/v1/workspaces/{workspace_id}/talent-market/{listing_id}/reviews` | Review Talent Listing |
| POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/talent-market/hire` | Hire Talent For Task Staffing Gap |
| POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/talent-market/recommendations` | Recommend Talent For Task Staffing |

## GET `/api/v1/marketplace`

List Public Marketplace Listings

Operation ID：`list_public_marketplace_listings_api_v1_marketplace_get`。

实现：[src/opsmesh/capabilities/marketplace/resource_routes.py](../../src/opsmesh/capabilities/marketplace/resource_routes.py) · `list_public_marketplace_listings`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `listing_type` | query | 是 | string | `enum=["agent", "skill", "mcp_server", "plugin"]` |
| `query` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_MarketplaceListingResponse_](schemas.md#schema-PageResponse_MarketplaceListingResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/talent-market`

List Talent Market

Operation ID：`list_talent_market_api_v1_talent_market_get`。

实现：[src/opsmesh/capabilities/marketplace/routes/catalog.py](../../src/opsmesh/capabilities/marketplace/routes/catalog.py) · `list_talent_market`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `query` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `role` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `skill` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_TalentListingResponse_](schemas.md#schema-PageResponse_TalentListingResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/talent-market/{listing_id}/metrics`

Get Talent Listing Metrics

Operation ID：`get_talent_listing_metrics_api_v1_talent_market__listing_id__metrics_get`。

实现：[src/opsmesh/capabilities/marketplace/routes/catalog.py](../../src/opsmesh/capabilities/marketplace/routes/catalog.py) · `get_talent_listing_metrics`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `listing_id` | path | 是 | string (uuid) | `format="uuid"` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TalentListingMetricsResponse](schemas.md#schema-TalentListingMetricsResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/talent-market/{listing_id}/reviews`

List Talent Listing Reviews

Operation ID：`list_talent_listing_reviews_api_v1_talent_market__listing_id__reviews_get`。

实现：[src/opsmesh/capabilities/marketplace/routes/catalog.py](../../src/opsmesh/capabilities/marketplace/routes/catalog.py) · `list_talent_listing_reviews`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `listing_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_TalentListingReviewResponse_](schemas.md#schema-PageResponse_TalentListingReviewResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/marketplace-installs`

List Workspace Marketplace Installs

Operation ID：`list_workspace_marketplace_installs_api_v1_workspaces__workspace_id__marketplace_installs_get`。

实现：[src/opsmesh/capabilities/marketplace/resource_routes.py](../../src/opsmesh/capabilities/marketplace/resource_routes.py) · `list_workspace_marketplace_installs`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `listing_type` | query | 否 | string anyOf null | `anyOf=[{"enum": ["agent", "skill", "mcp_server", "plugin"], "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_WorkspaceMarketplaceInstallResponse_](schemas.md#schema-PageResponse_WorkspaceMarketplaceInstallResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/marketplace-listings`

Create Workspace Marketplace Listing

Operation ID：`create_workspace_marketplace_listing_api_v1_workspaces__workspace_id__marketplace_listings_post`。

实现：[src/opsmesh/capabilities/marketplace/resource_routes.py](../../src/opsmesh/capabilities/marketplace/resource_routes.py) · `create_workspace_marketplace_listing`。

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
  "$ref": "#/components/schemas/MarketplaceListingCreateRequest"
}
```

模型：[MarketplaceListingCreateRequest](schemas.md#schema-MarketplaceListingCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [MarketplaceListingResponse](schemas.md#schema-MarketplaceListingResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/marketplace-listings/{listing_id}/install`

Install Marketplace Listing

Operation ID：`install_marketplace_listing_api_v1_workspaces__workspace_id__marketplace_listings__listing_id__install_post`。

实现：[src/opsmesh/capabilities/marketplace/resource_routes.py](../../src/opsmesh/capabilities/marketplace/resource_routes.py) · `install_marketplace_listing`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `listing_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/MarketplaceInstallRequest"
}
```

模型：[MarketplaceInstallRequest](schemas.md#schema-MarketplaceInstallRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [WorkspaceMarketplaceInstallResponse](schemas.md#schema-WorkspaceMarketplaceInstallResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/talent-installs`

List Workspace Talent Installs

Operation ID：`list_workspace_talent_installs_api_v1_workspaces__workspace_id__talent_installs_get`。

实现：[src/opsmesh/capabilities/marketplace/routes/install.py](../../src/opsmesh/capabilities/marketplace/routes/install.py) · `list_workspace_talent_installs`。

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
| 200 | Successful Response | application/json | [PageResponse_WorkspaceAgentInstallResponse_](schemas.md#schema-PageResponse_WorkspaceAgentInstallResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/talent-installs/{install_id}/pin`

Update Talent Install Pin

Operation ID：`update_talent_install_pin_api_v1_workspaces__workspace_id__talent_installs__install_id__pin_post`。

实现：[src/opsmesh/capabilities/marketplace/routes/install.py](../../src/opsmesh/capabilities/marketplace/routes/install.py) · `update_talent_install_pin`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/TalentInstallPinRequest"
}
```

模型：[TalentInstallPinRequest](schemas.md#schema-TalentInstallPinRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceAgentInstallResponse](schemas.md#schema-WorkspaceAgentInstallResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/talent-installs/{install_id}/upgrade`

Upgrade Talent Install

Operation ID：`upgrade_talent_install_api_v1_workspaces__workspace_id__talent_installs__install_id__upgrade_post`。

实现：[src/opsmesh/capabilities/marketplace/routes/install.py](../../src/opsmesh/capabilities/marketplace/routes/install.py) · `upgrade_talent_install`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/TalentInstallUpgradeRequest"
}
```

模型：[TalentInstallUpgradeRequest](schemas.md#schema-TalentInstallUpgradeRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceAgentInstallResponse](schemas.md#schema-WorkspaceAgentInstallResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/talent-installs/{install_id}/upgrade-status`

Get Talent Install Upgrade Status

Operation ID：`get_talent_install_upgrade_status_api_v1_workspaces__workspace_id__talent_installs__install_id__upgrade_status_get`。

实现：[src/opsmesh/capabilities/marketplace/routes/install.py](../../src/opsmesh/capabilities/marketplace/routes/install.py) · `get_talent_install_upgrade_status`。

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
| 200 | Successful Response | application/json | [TalentUpgradeStatusResponse](schemas.md#schema-TalentUpgradeStatusResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/talent-listings`

Publish Agent To Talent Market

Operation ID：`publish_agent_to_talent_market_api_v1_workspaces__workspace_id__talent_listings_post`。

实现：[src/opsmesh/capabilities/marketplace/routes/publish.py](../../src/opsmesh/capabilities/marketplace/routes/publish.py) · `publish_agent_to_talent_market`。

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
  "$ref": "#/components/schemas/TalentListingCreateRequest"
}
```

模型：[TalentListingCreateRequest](schemas.md#schema-TalentListingCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [TalentListingResponse](schemas.md#schema-TalentListingResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/talent-market/recommendations`

Recommend Talent For Team

Operation ID：`recommend_talent_for_team_api_v1_workspaces__workspace_id__talent_market_recommendations_post`。

实现：[src/opsmesh/capabilities/marketplace/routes/recommendation.py](../../src/opsmesh/capabilities/marketplace/routes/recommendation.py) · `recommend_talent_for_team`。

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
  "$ref": "#/components/schemas/TalentRecommendationRequest"
}
```

模型：[TalentRecommendationRequest](schemas.md#schema-TalentRecommendationRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TalentRecommendationResponse](schemas.md#schema-TalentRecommendationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/talent-market/{listing_id}/hire`

Hire Agent From Talent Market

Operation ID：`hire_agent_from_talent_market_api_v1_workspaces__workspace_id__talent_market__listing_id__hire_post`。

实现：[src/opsmesh/capabilities/marketplace/routes/hiring.py](../../src/opsmesh/capabilities/marketplace/routes/hiring.py) · `hire_agent_from_talent_market`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `listing_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/HireTalentRequest"
}
```

模型：[HireTalentRequest](schemas.md#schema-HireTalentRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [WorkspaceAgentInstallResponse](schemas.md#schema-WorkspaceAgentInstallResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/talent-market/{listing_id}/reviews`

Review Talent Listing

Operation ID：`review_talent_listing_api_v1_workspaces__workspace_id__talent_market__listing_id__reviews_post`。

实现：[src/opsmesh/capabilities/marketplace/routes/review.py](../../src/opsmesh/capabilities/marketplace/routes/review.py) · `review_talent_listing`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `listing_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/TalentListingReviewCreateRequest"
}
```

模型：[TalentListingReviewCreateRequest](schemas.md#schema-TalentListingReviewCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [TalentListingReviewResponse](schemas.md#schema-TalentListingReviewResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/talent-market/hire`

Hire Talent For Task Staffing Gap

Operation ID：`hire_talent_for_task_staffing_gap_api_v1_workspaces__workspace_id__tasks__task_id__talent_market_hire_post`。

实现：[src/opsmesh/capabilities/marketplace/routes/hiring.py](../../src/opsmesh/capabilities/marketplace/routes/hiring.py) · `hire_talent_for_task_staffing_gap`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/HireTaskTalentRequest"
}
```

模型：[HireTaskTalentRequest](schemas.md#schema-HireTaskTalentRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [WorkspaceAgentInstallResponse](schemas.md#schema-WorkspaceAgentInstallResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/talent-market/recommendations`

Recommend Talent For Task Staffing

Operation ID：`recommend_talent_for_task_staffing_api_v1_workspaces__workspace_id__tasks__task_id__talent_market_recommendations_post`。

实现：[src/opsmesh/capabilities/marketplace/routes/recommendation.py](../../src/opsmesh/capabilities/marketplace/routes/recommendation.py) · `recommend_talent_for_task_staffing`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `max_candidates_per_role` | query | 否 | integer | `default=3`; `maximum=10`; `minimum=1` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskTalentRecommendationResponse](schemas.md#schema-TaskTalentRecommendationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
