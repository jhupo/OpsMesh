# teams

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/operations/team-runtimes/{team_id}/timeline` | Team Timeline |

## GET `/api/v1/workspaces/{workspace_id}/operations/team-runtimes/{team_id}/timeline`

Team Timeline

Operation ID：`team_timeline_api_v1_workspaces__workspace_id__operations_team_runtimes__team_id__timeline_get`。

实现：[src/opsmesh/teams/observation/routes.py](../../src/opsmesh/teams/observation/routes.py) · `team_timeline`。

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
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TeamTimelineResponse](schemas.md#schema-TeamTimelineResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
