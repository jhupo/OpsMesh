# workspace-resources

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{workspace_id}/agents` | List Agents |
| POST | `/api/v1/workspaces/{workspace_id}/agents` | Create Agent |
| DELETE | `/api/v1/workspaces/{workspace_id}/agents/{agent_id}` | Delete Agent |
| GET | `/api/v1/workspaces/{workspace_id}/agents/{agent_id}` | Get Agent |
| PATCH | `/api/v1/workspaces/{workspace_id}/agents/{agent_id}` | Update Agent |
| POST | `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/activate` | Activate Agent |
| POST | `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/archive` | Archive Agent |
| POST | `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/clone` | Clone Agent |
| GET | `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/sessions` | List Agent Sessions |
| GET | `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/sessions/{session_id}` | Get Agent Session |
| POST | `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/sessions/{session_id}/activate` | Activate Agent Session |
| POST | `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/sessions/{session_id}/archive` | Archive Agent Session |
| POST | `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/sessions/{session_id}/freeze` | Freeze Agent Session |
| DELETE | `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/sessions/{session_id}/items` | Clear Agent Session Items |
| GET | `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/versions` | List Agent Versions |
| POST | `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/versions/{version}/rollback` | Rollback Agent Version |
| GET | `/api/v1/workspaces/{workspace_id}/audit-events` | List Audit Events |
| GET | `/api/v1/workspaces/{workspace_id}/runs` | List Runs |
| POST | `/api/v1/workspaces/{workspace_id}/runs/{agent_run_id}/cancel` | Cancel Run |
| GET | `/api/v1/workspaces/{workspace_id}/runs/{agent_run_id}/events` | List Run Events |
| GET | `/api/v1/workspaces/{workspace_id}/runs/{agent_run_id}/project-io` | Get Run Project Io State |
| GET | `/api/v1/workspaces/{workspace_id}/runs/{agent_run_id}/project-snapshot` | Get Run Project Snapshot |
| POST | `/api/v1/workspaces/{workspace_id}/runs/{agent_run_id}/retry` | Retry Run |
| GET | `/api/v1/workspaces/{workspace_id}/tasks` | List Tasks |
| POST | `/api/v1/workspaces/{workspace_id}/tasks` | Create Task |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/handoff-queue` | List Task Handoff Queue |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/manager-queue` | List Task Manager Queue |
| POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/cancel` | Cancel Task |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/collaboration-recovery` | Get Task Collaboration Recovery Plan |
| POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/collaboration-recovery` | Apply Task Collaboration Recovery Plan |
| POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/control` | Apply Task Control Action |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/control-diagnostics` | Get Task Control Diagnostics |
| POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/corrections` | Create Task Correction |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/corrections/diagnostics` | Get Task Correction Diagnostics |
| POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/delivery-decision` | Apply Task Delivery Decision |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/delivery-review` | Get Task Delivery Review |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/events/stream` | Stream Task Events |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/execution-diagnostics` | Get Task Execution Diagnostics |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/execution-status` | Get Task Execution Status |
| POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/feedback` | Record Task Feedback |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/interaction-transcript` | Get Task Interaction Transcript |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/live-status` | Get Task Live Status |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/manager-diagnostics` | Get Task Manager Diagnostics |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/messages` | List Task Messages |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/observation` | Get Task Observation |
| POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/operator-actions` | Apply Task Operator Action |
| POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/plan/apply-orchestration` | Apply Orchestration To Task |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/plan/diagnostics` | Get Task Plan Diagnostics |
| POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/plan/mutate` | Mutate Task Plan |
| POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/plan/regenerate` | Regenerate Task Plan |
| POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/plan/retry` | Retry Task Plan |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/planning-attempts` | List Task Planning Attempts |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/timeline` | Get Task Timeline |
| GET | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/transfers` | List Task Transfers |
| POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/transfers` | Request Task Transfer |
| POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/transfers/{transfer_id}/accept` | Accept Task Transfer |
| POST | `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/transfers/{transfer_id}/reject` | Reject Task Transfer |
| GET | `/api/v1/workspaces/{workspace_id}/teams` | List Teams |
| POST | `/api/v1/workspaces/{workspace_id}/teams` | Create Team |
| GET | `/api/v1/workspaces/{workspace_id}/teams/command-center` | Get Workspace Team Command Center |
| PATCH | `/api/v1/workspaces/{workspace_id}/teams/{team_id}` | Update Team |
| GET | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/command-center` | Get Team Command Center |
| POST | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/command-center/actions/apply` | Apply Team Command Center Actions |
| POST | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/execution-loop/enqueue` | Enqueue Team Execution Loop |
| POST | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/execution-loop/finalize` | Finalize Team Execution Loop Tasks |
| POST | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/execution-loop/run` | Run Team Execution Loop Iteration |
| GET | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/execution-overview` | Get Team Execution Overview |
| GET | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/members` | List Team Members |
| POST | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/members` | Create Team Member |
| PATCH | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/members/{member_id}` | Update Team Member |
| POST | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/members/{member_id}/model-provider` | Update Team Member Model Provider |
| GET | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/operations-console` | Get Team Operations Console |
| POST | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/operator-actions` | Apply Team Operator Action |
| GET | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/org-chart` | Get Team Org Chart |
| GET | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/project-dashboard` | Get Team Project Dashboard |
| GET | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/project-space` | Get Team Project Space |
| GET | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/runtime` | Get Team Runtime |
| POST | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/runtime/bind` | Bind Team Runtime |
| POST | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/runtime/continue` | Continue Team Runtime |
| POST | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/runtime/ensure` | Ensure Team Runtime |
| POST | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/runtime/pause` | Pause Team Runtime |
| POST | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/runtime/resume` | Resume Team Runtime |
| POST | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/runtime/start` | Start Team Runtime |
| POST | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/runtime/stop` | Stop Team Runtime |
| GET | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/sessions` | List Team Sessions |
| GET | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/sessions/{session_id}` | Get Team Session |
| POST | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/sessions/{session_id}/activate` | Activate Team Session |
| POST | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/sessions/{session_id}/archive` | Archive Team Session |
| POST | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/sessions/{session_id}/freeze` | Freeze Team Session |
| DELETE | `/api/v1/workspaces/{workspace_id}/teams/{team_id}/sessions/{session_id}/items` | Clear Team Session Items |

## GET `/api/v1/workspaces/{workspace_id}/agents`

List Agents

Operation ID：`list_agents_api_v1_workspaces__workspace_id__agents_get`。

实现：[src/opsmesh/agents/profiles/routes.py](../../src/opsmesh/agents/profiles/routes.py) · `list_agents`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AgentProfileResponse_](schemas.md#schema-PageResponse_AgentProfileResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/agents`

Create Agent

Operation ID：`create_agent_api_v1_workspaces__workspace_id__agents_post`。

实现：[src/opsmesh/agents/profiles/routes.py](../../src/opsmesh/agents/profiles/routes.py) · `create_agent`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `Idempotency-Key` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AgentProfileCreateRequest"
}
```

模型：[AgentProfileCreateRequest](schemas.md#schema-AgentProfileCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [AgentProfileResponse](schemas.md#schema-AgentProfileResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## DELETE `/api/v1/workspaces/{workspace_id}/agents/{agent_id}`

Delete Agent

Operation ID：`delete_agent_api_v1_workspaces__workspace_id__agents__agent_id__delete`。

实现：[src/opsmesh/agents/profiles/routes.py](../../src/opsmesh/agents/profiles/routes.py) · `delete_agent`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 204 | Successful Response | — | 无响应体 |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/agents/{agent_id}`

Get Agent

Operation ID：`get_agent_api_v1_workspaces__workspace_id__agents__agent_id__get`。

实现：[src/opsmesh/agents/profiles/routes.py](../../src/opsmesh/agents/profiles/routes.py) · `get_agent`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentProfileResponse](schemas.md#schema-AgentProfileResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/workspaces/{workspace_id}/agents/{agent_id}`

Update Agent

Operation ID：`update_agent_api_v1_workspaces__workspace_id__agents__agent_id__patch`。

实现：[src/opsmesh/agents/profiles/routes.py](../../src/opsmesh/agents/profiles/routes.py) · `update_agent`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AgentProfileUpdateRequest"
}
```

模型：[AgentProfileUpdateRequest](schemas.md#schema-AgentProfileUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentProfileResponse](schemas.md#schema-AgentProfileResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/activate`

Activate Agent

Operation ID：`activate_agent_api_v1_workspaces__workspace_id__agents__agent_id__activate_post`。

实现：[src/opsmesh/agents/profiles/routes.py](../../src/opsmesh/agents/profiles/routes.py) · `activate_agent`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentProfileResponse](schemas.md#schema-AgentProfileResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/archive`

Archive Agent

Operation ID：`archive_agent_api_v1_workspaces__workspace_id__agents__agent_id__archive_post`。

实现：[src/opsmesh/agents/profiles/routes.py](../../src/opsmesh/agents/profiles/routes.py) · `archive_agent`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentProfileResponse](schemas.md#schema-AgentProfileResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/clone`

Clone Agent

Operation ID：`clone_agent_api_v1_workspaces__workspace_id__agents__agent_id__clone_post`。

实现：[src/opsmesh/agents/profiles/routes.py](../../src/opsmesh/agents/profiles/routes.py) · `clone_agent`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `Idempotency-Key` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：否。

Content-Type：`application/json`。

```json
{
  "anyOf": [
    {
      "$ref": "#/components/schemas/AgentProfileCloneRequest"
    },
    {
      "type": "null"
    }
  ],
  "title": "Request"
}
```

模型：[AgentProfileCloneRequest](schemas.md#schema-AgentProfileCloneRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [AgentProfileResponse](schemas.md#schema-AgentProfileResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/sessions`

List Agent Sessions

Operation ID：`list_agent_sessions_api_v1_workspaces__workspace_id__agents__agent_id__sessions_get`。

实现：[src/opsmesh/agents/sessions/routes.py](../../src/opsmesh/agents/sessions/routes.py) · `list_agent_sessions`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `team_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `task_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AgentSessionSummaryResponse_](schemas.md#schema-PageResponse_AgentSessionSummaryResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/sessions/{session_id}`

Get Agent Session

Operation ID：`get_agent_session_api_v1_workspaces__workspace_id__agents__agent_id__sessions__session_id__get`。

实现：[src/opsmesh/agents/sessions/routes.py](../../src/opsmesh/agents/sessions/routes.py) · `get_agent_session`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_id` | path | 是 | string (uuid) | `format="uuid"` |
| `session_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `item_limit` | query | 否 | integer | `default=50`; `maximum=500`; `minimum=1` |
| `item_offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentSessionDetailResponse](schemas.md#schema-AgentSessionDetailResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/sessions/{session_id}/activate`

Activate Agent Session

Operation ID：`activate_agent_session_api_v1_workspaces__workspace_id__agents__agent_id__sessions__session_id__activate_post`。

实现：[src/opsmesh/agents/sessions/routes.py](../../src/opsmesh/agents/sessions/routes.py) · `activate_agent_session`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_id` | path | 是 | string (uuid) | `format="uuid"` |
| `session_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentSessionSummaryResponse](schemas.md#schema-AgentSessionSummaryResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/sessions/{session_id}/archive`

Archive Agent Session

Operation ID：`archive_agent_session_api_v1_workspaces__workspace_id__agents__agent_id__sessions__session_id__archive_post`。

实现：[src/opsmesh/agents/sessions/routes.py](../../src/opsmesh/agents/sessions/routes.py) · `archive_agent_session`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_id` | path | 是 | string (uuid) | `format="uuid"` |
| `session_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentSessionSummaryResponse](schemas.md#schema-AgentSessionSummaryResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/sessions/{session_id}/freeze`

Freeze Agent Session

Operation ID：`freeze_agent_session_api_v1_workspaces__workspace_id__agents__agent_id__sessions__session_id__freeze_post`。

实现：[src/opsmesh/agents/sessions/routes.py](../../src/opsmesh/agents/sessions/routes.py) · `freeze_agent_session`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_id` | path | 是 | string (uuid) | `format="uuid"` |
| `session_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentSessionSummaryResponse](schemas.md#schema-AgentSessionSummaryResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## DELETE `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/sessions/{session_id}/items`

Clear Agent Session Items

Operation ID：`clear_agent_session_items_api_v1_workspaces__workspace_id__agents__agent_id__sessions__session_id__items_delete`。

实现：[src/opsmesh/agents/sessions/routes.py](../../src/opsmesh/agents/sessions/routes.py) · `clear_agent_session_items`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_id` | path | 是 | string (uuid) | `format="uuid"` |
| `session_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentSessionClearResponse](schemas.md#schema-AgentSessionClearResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/versions`

List Agent Versions

Operation ID：`list_agent_versions_api_v1_workspaces__workspace_id__agents__agent_id__versions_get`。

实现：[src/opsmesh/agents/profiles/routes.py](../../src/opsmesh/agents/profiles/routes.py) · `list_agent_versions`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AgentProfileVersionResponse_](schemas.md#schema-PageResponse_AgentProfileVersionResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/agents/{agent_id}/versions/{version}/rollback`

Rollback Agent Version

Operation ID：`rollback_agent_version_api_v1_workspaces__workspace_id__agents__agent_id__versions__version__rollback_post`。

实现：[src/opsmesh/agents/profiles/routes.py](../../src/opsmesh/agents/profiles/routes.py) · `rollback_agent_version`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_id` | path | 是 | string (uuid) | `format="uuid"` |
| `version` | path | 是 | integer | `minimum=1` |
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
      "$ref": "#/components/schemas/AgentProfileRollbackRequest"
    },
    {
      "type": "null"
    }
  ],
  "title": "Request"
}
```

模型：[AgentProfileRollbackRequest](schemas.md#schema-AgentProfileRollbackRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentProfileResponse](schemas.md#schema-AgentProfileResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/audit-events`

List Audit Events

Operation ID：`list_audit_events_api_v1_workspaces__workspace_id__audit_events_get`。

实现：[src/opsmesh/orchestration/runs/routes.py](../../src/opsmesh/orchestration/runs/routes.py) · `list_audit_events`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=admin, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
| 200 | Successful Response | application/json | [PageResponse_AuditEventResponse_](schemas.md#schema-PageResponse_AuditEventResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/runs`

List Runs

Operation ID：`list_runs_api_v1_workspaces__workspace_id__runs_get`。

实现：[src/opsmesh/orchestration/runs/routes.py](../../src/opsmesh/orchestration/runs/routes.py) · `list_runs`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
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

## POST `/api/v1/workspaces/{workspace_id}/runs/{agent_run_id}/cancel`

Cancel Run

Operation ID：`cancel_run_api_v1_workspaces__workspace_id__runs__agent_run_id__cancel_post`。

实现：[src/opsmesh/orchestration/runs/routes.py](../../src/opsmesh/orchestration/runs/routes.py) · `cancel_run`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=control`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_run_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentRunResponse](schemas.md#schema-AgentRunResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/runs/{agent_run_id}/events`

List Run Events

Operation ID：`list_run_events_api_v1_workspaces__workspace_id__runs__agent_run_id__events_get`。

实现：[src/opsmesh/orchestration/runs/routes.py](../../src/opsmesh/orchestration/runs/routes.py) · `list_run_events`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_run_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
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

## GET `/api/v1/workspaces/{workspace_id}/runs/{agent_run_id}/project-io`

Get Run Project Io State

Operation ID：`get_run_project_io_state_api_v1_workspaces__workspace_id__runs__agent_run_id__project_io_get`。

实现：[src/opsmesh/orchestration/runs/routes.py](../../src/opsmesh/orchestration/runs/routes.py) · `get_run_project_io_state`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_run_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentRunProjectIOStateResponse](schemas.md#schema-AgentRunProjectIOStateResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/runs/{agent_run_id}/project-snapshot`

Get Run Project Snapshot

Operation ID：`get_run_project_snapshot_api_v1_workspaces__workspace_id__runs__agent_run_id__project_snapshot_get`。

实现：[src/opsmesh/orchestration/runs/routes.py](../../src/opsmesh/orchestration/runs/routes.py) · `get_run_project_snapshot`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_run_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentRunProjectSnapshotResponse](schemas.md#schema-AgentRunProjectSnapshotResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/runs/{agent_run_id}/retry`

Retry Run

Operation ID：`retry_run_api_v1_workspaces__workspace_id__runs__agent_run_id__retry_post`。

实现：[src/opsmesh/orchestration/runs/routes.py](../../src/opsmesh/orchestration/runs/routes.py) · `retry_run`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=invoke`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `agent_run_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [AgentRunResponse](schemas.md#schema-AgentRunResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks`

List Tasks

Operation ID：`list_tasks_api_v1_workspaces__workspace_id__tasks_get`。

实现：[src/opsmesh/orchestration/tasks/routes/base.py](../../src/opsmesh/orchestration/tasks/routes/base.py) · `list_tasks`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_TaskResponse_](schemas.md#schema-PageResponse_TaskResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks`

Create Task

Operation ID：`create_task_api_v1_workspaces__workspace_id__tasks_post`。

实现：[src/opsmesh/orchestration/tasks/routes/base.py](../../src/opsmesh/orchestration/tasks/routes/base.py) · `create_task`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `Idempotency-Key` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/TaskCreateRequest"
}
```

模型：[TaskCreateRequest](schemas.md#schema-TaskCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [TaskResponse](schemas.md#schema-TaskResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/handoff-queue`

List Task Handoff Queue

Operation ID：`list_task_handoff_queue_api_v1_workspaces__workspace_id__tasks_handoff_queue_get`。

实现：[src/opsmesh/orchestration/tasks/routes/base.py](../../src/opsmesh/orchestration/tasks/routes/base.py) · `list_task_handoff_queue`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `team_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `handoff_status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `include_terminal` | query | 否 | boolean | `default=false` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskHandoffQueueResponse](schemas.md#schema-TaskHandoffQueueResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/manager-queue`

List Task Manager Queue

Operation ID：`list_task_manager_queue_api_v1_workspaces__workspace_id__tasks_manager_queue_get`。

实现：[src/opsmesh/orchestration/tasks/routes/base.py](../../src/opsmesh/orchestration/tasks/routes/base.py) · `list_task_manager_queue`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `team_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `include_healthy` | query | 否 | boolean | `default=false` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskManagerQueueResponse](schemas.md#schema-TaskManagerQueueResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/cancel`

Cancel Task

Operation ID：`cancel_task_api_v1_workspaces__workspace_id__tasks__task_id__cancel_post`。

实现：[src/opsmesh/orchestration/tasks/routes/base.py](../../src/opsmesh/orchestration/tasks/routes/base.py) · `cancel_task`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=control`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskResponse](schemas.md#schema-TaskResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/collaboration-recovery`

Get Task Collaboration Recovery Plan

Operation ID：`get_task_collaboration_recovery_plan_api_v1_workspaces__workspace_id__tasks__task_id__collaboration_recovery_get`。

实现：[src/opsmesh/orchestration/tasks/routes/operations.py](../../src/opsmesh/orchestration/tasks/routes/operations.py) · `get_task_collaboration_recovery_plan`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `max_actions` | query | 否 | integer | `default=10`; `maximum=50`; `minimum=1` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskCollaborationRecoveryPlanResponse](schemas.md#schema-TaskCollaborationRecoveryPlanResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/collaboration-recovery`

Apply Task Collaboration Recovery Plan

Operation ID：`apply_task_collaboration_recovery_plan_api_v1_workspaces__workspace_id__tasks__task_id__collaboration_recovery_post`。

实现：[src/opsmesh/orchestration/tasks/routes/operations.py](../../src/opsmesh/orchestration/tasks/routes/operations.py) · `apply_task_collaboration_recovery_plan`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=approve, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/TaskCollaborationRecoveryApplyRequest"
}
```

模型：[TaskCollaborationRecoveryApplyRequest](schemas.md#schema-TaskCollaborationRecoveryApplyRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskCollaborationRecoveryApplyResponse](schemas.md#schema-TaskCollaborationRecoveryApplyResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/control`

Apply Task Control Action

Operation ID：`apply_task_control_action_api_v1_workspaces__workspace_id__tasks__task_id__control_post`。

实现：[src/opsmesh/orchestration/tasks/routes/operations.py](../../src/opsmesh/orchestration/tasks/routes/operations.py) · `apply_task_control_action`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=control`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/TaskControlActionRequest"
}
```

模型：[TaskControlActionRequest](schemas.md#schema-TaskControlActionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskControlActionResponse](schemas.md#schema-TaskControlActionResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/control-diagnostics`

Get Task Control Diagnostics

Operation ID：`get_task_control_diagnostics_api_v1_workspaces__workspace_id__tasks__task_id__control_diagnostics_get`。

实现：[src/opsmesh/orchestration/tasks/routes/operations.py](../../src/opsmesh/orchestration/tasks/routes/operations.py) · `get_task_control_diagnostics`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskControlDiagnosticsResponse](schemas.md#schema-TaskControlDiagnosticsResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/corrections`

Create Task Correction

Operation ID：`create_task_correction_api_v1_workspaces__workspace_id__tasks__task_id__corrections_post`。

实现：[src/opsmesh/orchestration/tasks/routes/operations.py](../../src/opsmesh/orchestration/tasks/routes/operations.py) · `create_task_correction`。

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
  "$ref": "#/components/schemas/TaskCorrectionRequest"
}
```

模型：[TaskCorrectionRequest](schemas.md#schema-TaskCorrectionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [TaskCorrectionResponse](schemas.md#schema-TaskCorrectionResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/corrections/diagnostics`

Get Task Correction Diagnostics

Operation ID：`get_task_correction_diagnostics_api_v1_workspaces__workspace_id__tasks__task_id__corrections_diagnostics_get`。

实现：[src/opsmesh/orchestration/tasks/routes/operations.py](../../src/opsmesh/orchestration/tasks/routes/operations.py) · `get_task_correction_diagnostics`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskCorrectionDiagnosticsResponse](schemas.md#schema-TaskCorrectionDiagnosticsResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/delivery-decision`

Apply Task Delivery Decision

Operation ID：`apply_task_delivery_decision_api_v1_workspaces__workspace_id__tasks__task_id__delivery_decision_post`。

实现：[src/opsmesh/orchestration/tasks/routes/operations.py](../../src/opsmesh/orchestration/tasks/routes/operations.py) · `apply_task_delivery_decision`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=approve, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/TaskDeliveryDecisionRequest"
}
```

模型：[TaskDeliveryDecisionRequest](schemas.md#schema-TaskDeliveryDecisionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskDeliveryDecisionResponse](schemas.md#schema-TaskDeliveryDecisionResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/delivery-review`

Get Task Delivery Review

Operation ID：`get_task_delivery_review_api_v1_workspaces__workspace_id__tasks__task_id__delivery_review_get`。

实现：[src/opsmesh/orchestration/tasks/routes/operations.py](../../src/opsmesh/orchestration/tasks/routes/operations.py) · `get_task_delivery_review`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskDeliveryReviewResponse](schemas.md#schema-TaskDeliveryReviewResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/events/stream`

Stream Task Events

Operation ID：`stream_task_events_api_v1_workspaces__workspace_id__tasks__task_id__events_stream_get`。

实现：[src/opsmesh/orchestration/tasks/routes/events.py](../../src/opsmesh/orchestration/tasks/routes/events.py) · `stream_task_events`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `after_sequence` | query | 否 | integer | `default=0`; `minimum=0` |
| `event_cursor` | query | 否 | string | `default="$"`; `maxLength=64`; `minLength=1` |
| `message_limit` | query | 否 | integer | `default=50`; `maximum=200`; `minimum=1` |
| `poll_seconds` | query | 否 | number | `default=1.0`; `maximum=10.0`; `minimum=0.25` |
| `heartbeat_seconds` | query | 否 | number | `default=15.0`; `maximum=60.0`; `minimum=1.0` |
| `once` | query | 否 | boolean | `default=false` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | text/event-stream | string |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/execution-diagnostics`

Get Task Execution Diagnostics

Operation ID：`get_task_execution_diagnostics_api_v1_workspaces__workspace_id__tasks__task_id__execution_diagnostics_get`。

实现：[src/opsmesh/orchestration/tasks/routes/operations.py](../../src/opsmesh/orchestration/tasks/routes/operations.py) · `get_task_execution_diagnostics`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskExecutionDiagnosticsResponse](schemas.md#schema-TaskExecutionDiagnosticsResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/execution-status`

Get Task Execution Status

Operation ID：`get_task_execution_status_api_v1_workspaces__workspace_id__tasks__task_id__execution_status_get`。

实现：[src/opsmesh/orchestration/tasks/routes/operations.py](../../src/opsmesh/orchestration/tasks/routes/operations.py) · `get_task_execution_status`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `message_limit` | query | 否 | integer | `default=20`; `maximum=100`; `minimum=1` |
| `event_limit` | query | 否 | integer | `default=30`; `maximum=200`; `minimum=1` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskExecutionStatusResponse](schemas.md#schema-TaskExecutionStatusResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/feedback`

Record Task Feedback

Operation ID：`record_task_feedback_api_v1_workspaces__workspace_id__tasks__task_id__feedback_post`。

实现：[src/opsmesh/orchestration/tasks/routes/events.py](../../src/opsmesh/orchestration/tasks/routes/events.py) · `record_task_feedback`。

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
  "$ref": "#/components/schemas/TaskFeedbackRequest"
}
```

模型：[TaskFeedbackRequest](schemas.md#schema-TaskFeedbackRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [TaskMessageResponse](schemas.md#schema-TaskMessageResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/interaction-transcript`

Get Task Interaction Transcript

Operation ID：`get_task_interaction_transcript_api_v1_workspaces__workspace_id__tasks__task_id__interaction_transcript_get`。

实现：[src/opsmesh/orchestration/tasks/routes/operations.py](../../src/opsmesh/orchestration/tasks/routes/operations.py) · `get_task_interaction_transcript`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=200`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `message_type` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 120, "minLength": 1, "type": "string"}, {"type": "null"}]` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskInteractionTranscriptResponse](schemas.md#schema-TaskInteractionTranscriptResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/live-status`

Get Task Live Status

Operation ID：`get_task_live_status_api_v1_workspaces__workspace_id__tasks__task_id__live_status_get`。

实现：[src/opsmesh/orchestration/tasks/routes/events.py](../../src/opsmesh/orchestration/tasks/routes/events.py) · `get_task_live_status`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `after_sequence` | query | 否 | integer | `default=0`; `minimum=0` |
| `message_limit` | query | 否 | integer | `default=50`; `maximum=200`; `minimum=1` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskLiveStatusResponse](schemas.md#schema-TaskLiveStatusResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/manager-diagnostics`

Get Task Manager Diagnostics

Operation ID：`get_task_manager_diagnostics_api_v1_workspaces__workspace_id__tasks__task_id__manager_diagnostics_get`。

实现：[src/opsmesh/orchestration/tasks/routes/operations.py](../../src/opsmesh/orchestration/tasks/routes/operations.py) · `get_task_manager_diagnostics`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskManagerDiagnosticsResponse](schemas.md#schema-TaskManagerDiagnosticsResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/messages`

List Task Messages

Operation ID：`list_task_messages_api_v1_workspaces__workspace_id__tasks__task_id__messages_get`。

实现：[src/opsmesh/orchestration/tasks/routes/events.py](../../src/opsmesh/orchestration/tasks/routes/events.py) · `list_task_messages`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `message_type` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_TaskMessageResponse_](schemas.md#schema-PageResponse_TaskMessageResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/observation`

Get Task Observation

Operation ID：`get_task_observation_api_v1_workspaces__workspace_id__tasks__task_id__observation_get`。

实现：[src/opsmesh/orchestration/tasks/routes/operations.py](../../src/opsmesh/orchestration/tasks/routes/operations.py) · `get_task_observation`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `view_type` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]`; `default="auto"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskObservationResponse](schemas.md#schema-TaskObservationResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/operator-actions`

Apply Task Operator Action

Operation ID：`apply_task_operator_action_api_v1_workspaces__workspace_id__tasks__task_id__operator_actions_post`。

实现：[src/opsmesh/orchestration/tasks/routes/operations.py](../../src/opsmesh/orchestration/tasks/routes/operations.py) · `apply_task_operator_action`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=control`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/TaskOperatorActionRequest"
}
```

模型：[TaskOperatorActionRequest](schemas.md#schema-TaskOperatorActionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskOperatorActionResponse](schemas.md#schema-TaskOperatorActionResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/plan/apply-orchestration`

Apply Orchestration To Task

Operation ID：`apply_orchestration_to_task_api_v1_workspaces__workspace_id__tasks__task_id__plan_apply_orchestration_post`。

实现：[src/opsmesh/orchestration/planning/routes.py](../../src/opsmesh/orchestration/planning/routes.py) · `apply_orchestration_to_task`。

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
  "$ref": "#/components/schemas/OrchestrationApplyRequest"
}
```

模型：[OrchestrationApplyRequest](schemas.md#schema-OrchestrationApplyRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskResponse](schemas.md#schema-TaskResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/plan/diagnostics`

Get Task Plan Diagnostics

Operation ID：`get_task_plan_diagnostics_api_v1_workspaces__workspace_id__tasks__task_id__plan_diagnostics_get`。

实现：[src/opsmesh/orchestration/planning/routes.py](../../src/opsmesh/orchestration/planning/routes.py) · `get_task_plan_diagnostics`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskPlanDiagnosticsResponse](schemas.md#schema-TaskPlanDiagnosticsResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/plan/mutate`

Mutate Task Plan

Operation ID：`mutate_task_plan_api_v1_workspaces__workspace_id__tasks__task_id__plan_mutate_post`。

实现：[src/opsmesh/orchestration/planning/routes.py](../../src/opsmesh/orchestration/planning/routes.py) · `mutate_task_plan`。

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
  "$ref": "#/components/schemas/TaskPlanMutationRequest"
}
```

模型：[TaskPlanMutationRequest](schemas.md#schema-TaskPlanMutationRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskResponse](schemas.md#schema-TaskResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/plan/regenerate`

Regenerate Task Plan

Operation ID：`regenerate_task_plan_api_v1_workspaces__workspace_id__tasks__task_id__plan_regenerate_post`。

实现：[src/opsmesh/orchestration/planning/routes.py](../../src/opsmesh/orchestration/planning/routes.py) · `regenerate_task_plan`。

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
  "$ref": "#/components/schemas/TaskPlanRegenerateRequest"
}
```

模型：[TaskPlanRegenerateRequest](schemas.md#schema-TaskPlanRegenerateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskResponse](schemas.md#schema-TaskResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/plan/retry`

Retry Task Plan

Operation ID：`retry_task_plan_api_v1_workspaces__workspace_id__tasks__task_id__plan_retry_post`。

实现：[src/opsmesh/orchestration/planning/routes.py](../../src/opsmesh/orchestration/planning/routes.py) · `retry_task_plan`。

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
  "$ref": "#/components/schemas/TaskPlanRetryRequest"
}
```

模型：[TaskPlanRetryRequest](schemas.md#schema-TaskPlanRetryRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskResponse](schemas.md#schema-TaskResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/planning-attempts`

List Task Planning Attempts

Operation ID：`list_task_planning_attempts_api_v1_workspaces__workspace_id__tasks__task_id__planning_attempts_get`。

实现：[src/opsmesh/orchestration/tasks/routes/events.py](../../src/opsmesh/orchestration/tasks/routes/events.py) · `list_task_planning_attempts`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_TaskPlanningAttemptResponse_](schemas.md#schema-PageResponse_TaskPlanningAttemptResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/timeline`

Get Task Timeline

Operation ID：`get_task_timeline_api_v1_workspaces__workspace_id__tasks__task_id__timeline_get`。

实现：[src/opsmesh/orchestration/tasks/routes/operations.py](../../src/opsmesh/orchestration/tasks/routes/operations.py) · `get_task_timeline`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=200`; `maximum=500`; `minimum=1` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskTimelineResponse](schemas.md#schema-TaskTimelineResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/transfers`

List Task Transfers

Operation ID：`list_task_transfers_api_v1_workspaces__workspace_id__tasks__task_id__transfers_get`。

实现：[src/opsmesh/orchestration/tasks/routes/transfers.py](../../src/opsmesh/orchestration/tasks/routes/transfers.py) · `list_task_transfers`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[TaskTransferResponse](schemas.md#schema-TaskTransferResponse)&gt; |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/transfers`

Request Task Transfer

Operation ID：`request_task_transfer_api_v1_workspaces__workspace_id__tasks__task_id__transfers_post`。

实现：[src/opsmesh/orchestration/tasks/routes/transfers.py](../../src/opsmesh/orchestration/tasks/routes/transfers.py) · `request_task_transfer`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `Idempotency-Key` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/TaskTransferCreateRequest"
}
```

模型：[TaskTransferCreateRequest](schemas.md#schema-TaskTransferCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [TaskTransferResponse](schemas.md#schema-TaskTransferResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/transfers/{transfer_id}/accept`

Accept Task Transfer

Operation ID：`accept_task_transfer_api_v1_workspaces__workspace_id__tasks__task_id__transfers__transfer_id__accept_post`。

实现：[src/opsmesh/orchestration/tasks/routes/transfers.py](../../src/opsmesh/orchestration/tasks/routes/transfers.py) · `accept_task_transfer`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `transfer_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/TaskTransferDecisionRequest"
}
```

模型：[TaskTransferDecisionRequest](schemas.md#schema-TaskTransferDecisionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskTransferResponse](schemas.md#schema-TaskTransferResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/tasks/{task_id}/transfers/{transfer_id}/reject`

Reject Task Transfer

Operation ID：`reject_task_transfer_api_v1_workspaces__workspace_id__tasks__task_id__transfers__transfer_id__reject_post`。

实现：[src/opsmesh/orchestration/tasks/routes/transfers.py](../../src/opsmesh/orchestration/tasks/routes/transfers.py) · `reject_task_transfer`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `task_id` | path | 是 | string (uuid) | `format="uuid"` |
| `transfer_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/TaskTransferDecisionRequest"
}
```

模型：[TaskTransferDecisionRequest](schemas.md#schema-TaskTransferDecisionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [TaskTransferResponse](schemas.md#schema-TaskTransferResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/teams`

List Teams

Operation ID：`list_teams_api_v1_workspaces__workspace_id__teams_get`。

实现：[src/opsmesh/teams/management/routes.py](../../src/opsmesh/teams/management/routes.py) · `list_teams`。

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
| 200 | Successful Response | application/json | [PageResponse_AgentTeamResponse_](schemas.md#schema-PageResponse_AgentTeamResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/teams`

Create Team

Operation ID：`create_team_api_v1_workspaces__workspace_id__teams_post`。

实现：[src/opsmesh/teams/management/routes.py](../../src/opsmesh/teams/management/routes.py) · `create_team`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `Idempotency-Key` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AgentTeamCreateRequest"
}
```

模型：[AgentTeamCreateRequest](schemas.md#schema-AgentTeamCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [AgentTeamResponse](schemas.md#schema-AgentTeamResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/teams/command-center`

Get Workspace Team Command Center

Operation ID：`get_workspace_team_command_center_api_v1_workspaces__workspace_id__teams_command_center_get`。

实现：[src/opsmesh/teams/management/routes.py](../../src/opsmesh/teams/management/routes.py) · `get_workspace_team_command_center`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `include_completed` | query | 否 | boolean | `default=false` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [WorkspaceTeamCommandCenterResponse](schemas.md#schema-WorkspaceTeamCommandCenterResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/workspaces/{workspace_id}/teams/{team_id}`

Update Team

Operation ID：`update_team_api_v1_workspaces__workspace_id__teams__team_id__patch`。

实现：[src/opsmesh/teams/management/routes.py](../../src/opsmesh/teams/management/routes.py) · `update_team`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/AgentTeamUpdateRequest"
}
```

模型：[AgentTeamUpdateRequest](schemas.md#schema-AgentTeamUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamResponse](schemas.md#schema-AgentTeamResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/teams/{team_id}/command-center`

Get Team Command Center

Operation ID：`get_team_command_center_api_v1_workspaces__workspace_id__teams__team_id__command_center_get`。

实现：[src/opsmesh/teams/management/routes.py](../../src/opsmesh/teams/management/routes.py) · `get_team_command_center`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `include_completed` | query | 否 | boolean | `default=false` |
| `queue_limit` | query | 否 | integer | `default=50`; `maximum=200`; `minimum=1` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamCommandCenterResponse](schemas.md#schema-AgentTeamCommandCenterResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/teams/{team_id}/command-center/actions/apply`

Apply Team Command Center Actions

Operation ID：`apply_team_command_center_actions_api_v1_workspaces__workspace_id__teams__team_id__command_center_actions_apply_post`。

实现：[src/opsmesh/teams/execution/routes.py](../../src/opsmesh/teams/execution/routes.py) · `apply_team_command_center_actions`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, path_resource_action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/AgentTeamCommandCenterApplyRequest"
}
```

模型：[AgentTeamCommandCenterApplyRequest](schemas.md#schema-AgentTeamCommandCenterApplyRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamCommandCenterApplyResponse](schemas.md#schema-AgentTeamCommandCenterApplyResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/teams/{team_id}/execution-loop/enqueue`

Enqueue Team Execution Loop

Operation ID：`enqueue_team_execution_loop_api_v1_workspaces__workspace_id__teams__team_id__execution_loop_enqueue_post`。

实现：[src/opsmesh/teams/execution/routes.py](../../src/opsmesh/teams/execution/routes.py) · `enqueue_team_execution_loop`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/AgentTeamExecutionLoopEnqueueRequest"
}
```

模型：[AgentTeamExecutionLoopEnqueueRequest](schemas.md#schema-AgentTeamExecutionLoopEnqueueRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamExecutionLoopEnqueueResponse](schemas.md#schema-AgentTeamExecutionLoopEnqueueResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/teams/{team_id}/execution-loop/finalize`

Finalize Team Execution Loop Tasks

Operation ID：`finalize_team_execution_loop_tasks_api_v1_workspaces__workspace_id__teams__team_id__execution_loop_finalize_post`。

实现：[src/opsmesh/teams/execution/routes.py](../../src/opsmesh/teams/execution/routes.py) · `finalize_team_execution_loop_tasks`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/AgentTeamExecutionLoopFinalizeRequest"
}
```

模型：[AgentTeamExecutionLoopFinalizeRequest](schemas.md#schema-AgentTeamExecutionLoopFinalizeRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamExecutionLoopFinalizeResponse](schemas.md#schema-AgentTeamExecutionLoopFinalizeResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/teams/{team_id}/execution-loop/run`

Run Team Execution Loop Iteration

Operation ID：`run_team_execution_loop_iteration_api_v1_workspaces__workspace_id__teams__team_id__execution_loop_run_post`。

实现：[src/opsmesh/teams/execution/routes.py](../../src/opsmesh/teams/execution/routes.py) · `run_team_execution_loop_iteration`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/AgentTeamExecutionLoopRunRequest"
}
```

模型：[AgentTeamExecutionLoopRunRequest](schemas.md#schema-AgentTeamExecutionLoopRunRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamExecutionLoopRunResponse](schemas.md#schema-AgentTeamExecutionLoopRunResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/teams/{team_id}/execution-overview`

Get Team Execution Overview

Operation ID：`get_team_execution_overview_api_v1_workspaces__workspace_id__teams__team_id__execution_overview_get`。

实现：[src/opsmesh/teams/management/routes.py](../../src/opsmesh/teams/management/routes.py) · `get_team_execution_overview`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `include_completed` | query | 否 | boolean | `default=false` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamExecutionOverviewResponse](schemas.md#schema-AgentTeamExecutionOverviewResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/teams/{team_id}/members`

List Team Members

Operation ID：`list_team_members_api_v1_workspaces__workspace_id__teams__team_id__members_get`。

实现：[src/opsmesh/teams/management/member_routes.py](../../src/opsmesh/teams/management/member_routes.py) · `list_team_members`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AgentTeamMemberResponse_](schemas.md#schema-PageResponse_AgentTeamMemberResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/teams/{team_id}/members`

Create Team Member

Operation ID：`create_team_member_api_v1_workspaces__workspace_id__teams__team_id__members_post`。

实现：[src/opsmesh/teams/management/member_routes.py](../../src/opsmesh/teams/management/member_routes.py) · `create_team_member`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `Idempotency-Key` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AgentTeamMemberCreateRequest"
}
```

模型：[AgentTeamMemberCreateRequest](schemas.md#schema-AgentTeamMemberCreateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [AgentTeamMemberResponse](schemas.md#schema-AgentTeamMemberResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## PATCH `/api/v1/workspaces/{workspace_id}/teams/{team_id}/members/{member_id}`

Update Team Member

Operation ID：`update_team_member_api_v1_workspaces__workspace_id__teams__team_id__members__member_id__patch`。

实现：[src/opsmesh/teams/management/member_routes.py](../../src/opsmesh/teams/management/member_routes.py) · `update_team_member`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `member_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AgentTeamMemberUpdateRequest"
}
```

模型：[AgentTeamMemberUpdateRequest](schemas.md#schema-AgentTeamMemberUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamMemberResponse](schemas.md#schema-AgentTeamMemberResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/teams/{team_id}/members/{member_id}/model-provider`

Update Team Member Model Provider

Operation ID：`update_team_member_model_provider_api_v1_workspaces__workspace_id__teams__team_id__members__member_id__model_provider_post`。

实现：[src/opsmesh/teams/management/member_routes.py](../../src/opsmesh/teams/management/member_routes.py) · `update_team_member_model_provider`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `member_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AgentTeamMemberModelProviderUpdateRequest"
}
```

模型：[AgentTeamMemberModelProviderUpdateRequest](schemas.md#schema-AgentTeamMemberModelProviderUpdateRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentProfileResponse](schemas.md#schema-AgentProfileResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/teams/{team_id}/operations-console`

Get Team Operations Console

Operation ID：`get_team_operations_console_api_v1_workspaces__workspace_id__teams__team_id__operations_console_get`。

实现：[src/opsmesh/teams/management/routes.py](../../src/opsmesh/teams/management/routes.py) · `get_team_operations_console`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `include_completed` | query | 否 | boolean | `default=false` |
| `queue_limit` | query | 否 | integer | `default=50`; `maximum=200`; `minimum=1` |
| `message_limit` | query | 否 | integer | `default=10`; `maximum=50`; `minimum=0` |
| `session_limit` | query | 否 | integer | `default=100`; `maximum=500`; `minimum=1` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamOperationsConsoleResponse](schemas.md#schema-AgentTeamOperationsConsoleResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/teams/{team_id}/operator-actions`

Apply Team Operator Action

Operation ID：`apply_team_operator_action_api_v1_workspaces__workspace_id__teams__team_id__operator_actions_post`。

实现：[src/opsmesh/teams/execution/routes.py](../../src/opsmesh/teams/execution/routes.py) · `apply_team_operator_action`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=operate, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/AgentTeamOperatorActionRequest"
}
```

模型：[AgentTeamOperatorActionRequest](schemas.md#schema-AgentTeamOperatorActionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamOperatorActionResponse](schemas.md#schema-AgentTeamOperatorActionResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/teams/{team_id}/org-chart`

Get Team Org Chart

Operation ID：`get_team_org_chart_api_v1_workspaces__workspace_id__teams__team_id__org_chart_get`。

实现：[src/opsmesh/teams/management/routes.py](../../src/opsmesh/teams/management/routes.py) · `get_team_org_chart`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamOrgChartResponse](schemas.md#schema-AgentTeamOrgChartResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/teams/{team_id}/project-dashboard`

Get Team Project Dashboard

Operation ID：`get_team_project_dashboard_api_v1_workspaces__workspace_id__teams__team_id__project_dashboard_get`。

实现：[src/opsmesh/teams/management/routes.py](../../src/opsmesh/teams/management/routes.py) · `get_team_project_dashboard`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `include_completed` | query | 否 | boolean | `default=false` |
| `limit` | query | 否 | integer | `default=100`; `maximum=500`; `minimum=1` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamProjectDashboardResponse](schemas.md#schema-AgentTeamProjectDashboardResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/teams/{team_id}/project-space`

Get Team Project Space

Operation ID：`get_team_project_space_api_v1_workspaces__workspace_id__teams__team_id__project_space_get`。

实现：[src/opsmesh/teams/management/routes.py](../../src/opsmesh/teams/management/routes.py) · `get_team_project_space`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `include_completed` | query | 否 | boolean | `default=false` |
| `limit` | query | 否 | integer | `default=200`; `maximum=500`; `minimum=1` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamProjectSpaceResponse](schemas.md#schema-AgentTeamProjectSpaceResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/teams/{team_id}/runtime`

Get Team Runtime

Operation ID：`get_team_runtime_api_v1_workspaces__workspace_id__teams__team_id__runtime_get`。

实现：[src/opsmesh/teams/sessions/runtime_routes.py](../../src/opsmesh/teams/sessions/runtime_routes.py) · `get_team_runtime`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamRuntimeResponse](schemas.md#schema-AgentTeamRuntimeResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/teams/{team_id}/runtime/bind`

Bind Team Runtime

Operation ID：`bind_team_runtime_api_v1_workspaces__workspace_id__teams__team_id__runtime_bind_post`。

实现：[src/opsmesh/teams/sessions/runtime_routes.py](../../src/opsmesh/teams/sessions/runtime_routes.py) · `bind_team_runtime`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/AgentTeamRuntimeBindRequest"
}
```

模型：[AgentTeamRuntimeBindRequest](schemas.md#schema-AgentTeamRuntimeBindRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamRuntimeResponse](schemas.md#schema-AgentTeamRuntimeResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/teams/{team_id}/runtime/continue`

Continue Team Runtime

Operation ID：`continue_team_runtime_api_v1_workspaces__workspace_id__teams__team_id__runtime_continue_post`。

实现：[src/opsmesh/teams/sessions/runtime_routes.py](../../src/opsmesh/teams/sessions/runtime_routes.py) · `continue_team_runtime`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/AgentTeamRuntimeControlRequest"
}
```

模型：[AgentTeamRuntimeControlRequest](schemas.md#schema-AgentTeamRuntimeControlRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamRuntimeResponse](schemas.md#schema-AgentTeamRuntimeResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/teams/{team_id}/runtime/ensure`

Ensure Team Runtime

Operation ID：`ensure_team_runtime_api_v1_workspaces__workspace_id__teams__team_id__runtime_ensure_post`。

实现：[src/opsmesh/teams/sessions/runtime_routes.py](../../src/opsmesh/teams/sessions/runtime_routes.py) · `ensure_team_runtime`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/AgentTeamRuntimeEnsureRequest"
}
```

模型：[AgentTeamRuntimeEnsureRequest](schemas.md#schema-AgentTeamRuntimeEnsureRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamRuntimeResponse](schemas.md#schema-AgentTeamRuntimeResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/teams/{team_id}/runtime/pause`

Pause Team Runtime

Operation ID：`pause_team_runtime_api_v1_workspaces__workspace_id__teams__team_id__runtime_pause_post`。

实现：[src/opsmesh/teams/sessions/runtime_routes.py](../../src/opsmesh/teams/sessions/runtime_routes.py) · `pause_team_runtime`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/AgentTeamRuntimeControlRequest"
}
```

模型：[AgentTeamRuntimeControlRequest](schemas.md#schema-AgentTeamRuntimeControlRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamRuntimeResponse](schemas.md#schema-AgentTeamRuntimeResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/teams/{team_id}/runtime/resume`

Resume Team Runtime

Operation ID：`resume_team_runtime_api_v1_workspaces__workspace_id__teams__team_id__runtime_resume_post`。

实现：[src/opsmesh/teams/sessions/runtime_routes.py](../../src/opsmesh/teams/sessions/runtime_routes.py) · `resume_team_runtime`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/AgentTeamRuntimeControlRequest"
}
```

模型：[AgentTeamRuntimeControlRequest](schemas.md#schema-AgentTeamRuntimeControlRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamRuntimeResponse](schemas.md#schema-AgentTeamRuntimeResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/teams/{team_id}/runtime/start`

Start Team Runtime

Operation ID：`start_team_runtime_api_v1_workspaces__workspace_id__teams__team_id__runtime_start_post`。

实现：[src/opsmesh/teams/sessions/runtime_routes.py](../../src/opsmesh/teams/sessions/runtime_routes.py) · `start_team_runtime`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/AgentTeamRuntimeControlRequest"
}
```

模型：[AgentTeamRuntimeControlRequest](schemas.md#schema-AgentTeamRuntimeControlRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamRuntimeResponse](schemas.md#schema-AgentTeamRuntimeResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/teams/{team_id}/runtime/stop`

Stop Team Runtime

Operation ID：`stop_team_runtime_api_v1_workspaces__workspace_id__teams__team_id__runtime_stop_post`。

实现：[src/opsmesh/teams/sessions/runtime_routes.py](../../src/opsmesh/teams/sessions/runtime_routes.py) · `stop_team_runtime`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=manage_runtime, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

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
  "$ref": "#/components/schemas/AgentTeamRuntimeControlRequest"
}
```

模型：[AgentTeamRuntimeControlRequest](schemas.md#schema-AgentTeamRuntimeControlRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentTeamRuntimeResponse](schemas.md#schema-AgentTeamRuntimeResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/teams/{team_id}/sessions`

List Team Sessions

Operation ID：`list_team_sessions_api_v1_workspaces__workspace_id__teams__team_id__sessions_get`。

实现：[src/opsmesh/teams/sessions/routes.py](../../src/opsmesh/teams/sessions/routes.py) · `list_team_sessions`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PageResponse_AgentSessionSummaryResponse_](schemas.md#schema-PageResponse_AgentSessionSummaryResponse_) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## GET `/api/v1/workspaces/{workspace_id}/teams/{team_id}/sessions/{session_id}`

Get Team Session

Operation ID：`get_team_session_api_v1_workspaces__workspace_id__teams__team_id__sessions__session_id__get`。

实现：[src/opsmesh/teams/sessions/routes.py](../../src/opsmesh/teams/sessions/routes.py) · `get_team_session`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=read, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `session_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `item_limit` | query | 否 | integer | `default=50`; `maximum=500`; `minimum=1` |
| `item_offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentSessionDetailResponse](schemas.md#schema-AgentSessionDetailResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/teams/{team_id}/sessions/{session_id}/activate`

Activate Team Session

Operation ID：`activate_team_session_api_v1_workspaces__workspace_id__teams__team_id__sessions__session_id__activate_post`。

实现：[src/opsmesh/teams/sessions/routes.py](../../src/opsmesh/teams/sessions/routes.py) · `activate_team_session`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `session_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentSessionSummaryResponse](schemas.md#schema-AgentSessionSummaryResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/teams/{team_id}/sessions/{session_id}/archive`

Archive Team Session

Operation ID：`archive_team_session_api_v1_workspaces__workspace_id__teams__team_id__sessions__session_id__archive_post`。

实现：[src/opsmesh/teams/sessions/routes.py](../../src/opsmesh/teams/sessions/routes.py) · `archive_team_session`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `session_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentSessionSummaryResponse](schemas.md#schema-AgentSessionSummaryResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## POST `/api/v1/workspaces/{workspace_id}/teams/{team_id}/sessions/{session_id}/freeze`

Freeze Team Session

Operation ID：`freeze_team_session_api_v1_workspaces__workspace_id__teams__team_id__sessions__session_id__freeze_post`。

实现：[src/opsmesh/teams/sessions/routes.py](../../src/opsmesh/teams/sessions/routes.py) · `freeze_team_session`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `session_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentSessionSummaryResponse](schemas.md#schema-AgentSessionSummaryResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。

## DELETE `/api/v1/workspaces/{workspace_id}/teams/{team_id}/sessions/{session_id}/items`

Clear Team Session Items

Operation ID：`clear_team_session_items_api_v1_workspaces__workspace_id__teams__team_id__sessions__session_id__items_delete`。

实现：[src/opsmesh/teams/sessions/routes.py](../../src/opsmesh/teams/sessions/routes.py) · `clear_team_session_items`。

权限依赖：`opsmesh.identity.auth.dependencies.workspace_dependency.<locals>.require_workspace_context`：`action=write, resource_action=update`；`opsmesh.identity.auth.dependencies.get_current_user`

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `team_id` | path | 是 | string (uuid) | `format="uuid"` |
| `session_id` | path | 是 | string (uuid) | `format="uuid"` |
| `workspace_id` | path | 是 | string (uuid) | `format="uuid"` |
| `authorization` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-User-ID` | header | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AgentSessionClearResponse](schemas.md#schema-AgentSessionClearResponse) |
| 422 | Request validation failed | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。
