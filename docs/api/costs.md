# costs

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/costs/budget` | Get Cost Budget |
| PUT | `/api/v1/workspaces/{workspace_id}/costs/budget` | Put Cost Budget |
| GET | `/api/v1/workspaces/{workspace_id}/costs/pricing-rules` | List Pricing Rules |
| POST | `/api/v1/workspaces/{workspace_id}/costs/pricing-rules` | Create Pricing Rule |
| POST | `/api/v1/workspaces/{workspace_id}/costs/pricing-rules/{pricing_rule_id}/disable` | Disable Pricing Rule |
| GET | `/api/v1/workspaces/{workspace_id}/costs/summary` | Cost Summary |
| GET | `/api/v1/workspaces/{workspace_id}/costs/usage` | List Model Usage |

## GET `/api/v1/workspaces/{workspace_id}/costs/budget`

Get Cost Budget

Operation ID：`get_cost_budget_api_v1_workspaces__workspace_id__costs_budget_get`。

实现：[src/opsmesh/governance/costs/routes.py](../../src/opsmesh/governance/costs/routes.py) · `get_cost_budget`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `currency` | query | 否 | string | `default="USD"`; `pattern="^[A-Za-z]{3}$"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceCostBudgetResponse](schemas.md#schema-WorkspaceCostBudgetResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PUT `/api/v1/workspaces/{workspace_id}/costs/budget`

Put Cost Budget

Operation ID：`put_cost_budget_api_v1_workspaces__workspace_id__costs_budget_put`。

实现：[src/opsmesh/governance/costs/routes.py](../../src/opsmesh/governance/costs/routes.py) · `put_cost_budget`。

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
  "$ref": "#/components/schemas/WorkspaceCostBudgetRequest"
}
```

模型：[WorkspaceCostBudgetRequest](schemas.md#schema-WorkspaceCostBudgetRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceCostBudgetResponse](schemas.md#schema-WorkspaceCostBudgetResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/costs/pricing-rules`

List Pricing Rules

Operation ID：`list_pricing_rules_api_v1_workspaces__workspace_id__costs_pricing_rules_get`。

实现：[src/opsmesh/governance/costs/routes.py](../../src/opsmesh/governance/costs/routes.py) · `list_pricing_rules`。

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
| 200 | Successful Response | application/json | array&lt;[ModelPricingRuleResponse](schemas.md#schema-ModelPricingRuleResponse)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/costs/pricing-rules`

Create Pricing Rule

Operation ID：`create_pricing_rule_api_v1_workspaces__workspace_id__costs_pricing_rules_post`。

实现：[src/opsmesh/governance/costs/routes.py](../../src/opsmesh/governance/costs/routes.py) · `create_pricing_rule`。

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
  "$ref": "#/components/schemas/ModelPricingRuleCreateRequest"
}
```

模型：[ModelPricingRuleCreateRequest](schemas.md#schema-ModelPricingRuleCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [ModelPricingRuleResponse](schemas.md#schema-ModelPricingRuleResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/costs/pricing-rules/{pricing_rule_id}/disable`

Disable Pricing Rule

Operation ID：`disable_pricing_rule_api_v1_workspaces__workspace_id__costs_pricing_rules__pricing_rule_id__disable_post`。

实现：[src/opsmesh/governance/costs/routes.py](../../src/opsmesh/governance/costs/routes.py) · `disable_pricing_rule`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `pricing_rule_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ModelPricingRuleResponse](schemas.md#schema-ModelPricingRuleResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/costs/summary`

Cost Summary

Operation ID：`cost_summary_api_v1_workspaces__workspace_id__costs_summary_get`。

实现：[src/opsmesh/governance/costs/routes.py](../../src/opsmesh/governance/costs/routes.py) · `cost_summary`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `start_at` | query | 否 | string (date-time) anyOf null | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `end_at` | query | 否 | string (date-time) anyOf null | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `currency` | query | 否 | string | `default="USD"`; `pattern="^[A-Za-z]{3}$"` |
| `group_by` | query | 否 | string | `default="model"`; `enum=["provider", "model", "agent", "run", "day"]` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [CostSummaryResponse](schemas.md#schema-CostSummaryResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/costs/usage`

List Model Usage

Operation ID：`list_model_usage_api_v1_workspaces__workspace_id__costs_usage_get`。

实现：[src/opsmesh/governance/costs/routes.py](../../src/opsmesh/governance/costs/routes.py) · `list_model_usage`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `start_at` | query | 否 | string (date-time) anyOf null | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `end_at` | query | 否 | string (date-time) anyOf null | `anyOf=[{"format": "date-time", "type": "string"}, {"type": "null"}]` |
| `provider` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `model` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `trace_id` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 32, "minLength": 32, "type": "string"}, {"type": "null"}]` |
| `request_id` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 80, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_ModelUsageRecordResponse_](schemas.md#schema-PageResponse_ModelUsageRecordResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
