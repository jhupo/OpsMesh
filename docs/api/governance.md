# governance

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| GET | `/api/v1/governance/approvals` | List Approvals |
| GET | `/api/v1/governance/approvals/{approval_id}` | Read Approval |
| POST | `/api/v1/governance/approvals/{approval_id}/approve` | Approve Approval |
| POST | `/api/v1/governance/approvals/{approval_id}/reject` | Reject Approval |
| GET | `/api/v1/governance/audit/events` | List Audit Events |
| POST | `/api/v1/governance/audit/events` | Record Event |
| GET | `/api/v1/governance/audit/events/{event_id}` | Get Audit Event |
| POST | `/api/v1/governance/audit/verifications` | Create Verification |
| GET | `/api/v1/governance/audit/verifications/{verification_id}` | Get Audit Verification |
| GET | `/api/v1/governance/security-events` | List Security Events |
| GET | `/api/v1/governance/security-events/{event_id}` | Read Security Event |

## GET `/api/v1/governance/approvals`

List Approvals

Operation ID：`governance.approvals.list`。

实现：[src/opsmesh/governance/approvals/routes.py](../../src/opsmesh/governance/approvals/routes.py) · `list_approvals`。

The requests assigned to the current subject.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `status` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 20, "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ApprovalPage](schemas.md#schema-ApprovalPage) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/governance/approvals/{approval_id}`

Read Approval

Operation ID：`governance.approvals.read`。

实现：[src/opsmesh/governance/approvals/routes.py](../../src/opsmesh/governance/approvals/routes.py) · `read_approval`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `approval_id` | path | 是 | string (uuid) | `format="uuid"` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ApprovalRequestView](schemas.md#schema-ApprovalRequestView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/governance/approvals/{approval_id}/approve`

Approve Approval

Operation ID：`governance.approvals.approve`。

实现：[src/opsmesh/governance/approvals/routes.py](../../src/opsmesh/governance/approvals/routes.py) · `approve_approval`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `approval_id` | path | 是 | string (uuid) | `format="uuid"` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ApprovalDecisionRequest"
}
```

模型：[ApprovalDecisionRequest](schemas.md#schema-ApprovalDecisionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ApprovalRequestView](schemas.md#schema-ApprovalRequestView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/governance/approvals/{approval_id}/reject`

Reject Approval

Operation ID：`governance.approvals.reject`。

实现：[src/opsmesh/governance/approvals/routes.py](../../src/opsmesh/governance/approvals/routes.py) · `reject_approval`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `approval_id` | path | 是 | string (uuid) | `format="uuid"` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ApprovalDecisionRequest"
}
```

模型：[ApprovalDecisionRequest](schemas.md#schema-ApprovalDecisionRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [ApprovalRequestView](schemas.md#schema-ApprovalRequestView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/governance/audit/events`

List Audit Events

Operation ID：`governance.audit.events.list`。

实现：[src/opsmesh/governance/audit/routes.py](../../src/opsmesh/governance/audit/routes.py) · `list_audit_events`。

One cursor page ordered by chain sequence; filters are ANDed.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `after_sequence` | query | 否 | integer anyOf null | `anyOf=[{"minimum": 0, "type": "integer"}, {"type": "null"}]` |
| `actor_type` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 20, "type": "string"}, {"type": "null"}]` |
| `action` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 200, "type": "string"}, {"type": "null"}]` |
| `target_type` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 200, "type": "string"}, {"type": "null"}]` |
| `target_id` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 200, "type": "string"}, {"type": "null"}]` |
| `workspace_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `project_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |
| `limit` | query | 否 | integer | `default=50`; `maximum=200`; `minimum=1` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AuditEventPage](schemas.md#schema-AuditEventPage) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/governance/audit/events`

Record Event

Operation ID：`governance.audit.events.record`。

实现：[src/opsmesh/governance/audit/routes.py](../../src/opsmesh/governance/audit/routes.py) · `record_event`。

Append one extension-reported fact under the verified invocation scope.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

参数：无显式路径、查询或 Header 参数。

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/ExtensionAuditAppend"
}
```

模型：[ExtensionAuditAppend](schemas.md#schema-ExtensionAuditAppend)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [AuditAppendView](schemas.md#schema-AuditAppendView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/governance/audit/events/{event_id}`

Get Audit Event

Operation ID：`governance.audit.events.read`。

实现：[src/opsmesh/governance/audit/routes.py](../../src/opsmesh/governance/audit/routes.py) · `get_audit_event`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `event_id` | path | 是 | string (uuid) | `format="uuid"` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AuditEventView](schemas.md#schema-AuditEventView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/governance/audit/verifications`

Create Verification

Operation ID：`governance.audit.verifications.create`。

实现：[src/opsmesh/governance/audit/routes.py](../../src/opsmesh/governance/audit/routes.py) · `create_verification`。

Register one verification run and stage its durable execution.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

参数：无显式路径、查询或 Header 参数。

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/AuditVerificationRequest"
}
```

模型：[AuditVerificationRequest](schemas.md#schema-AuditVerificationRequest)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [AuditVerificationView](schemas.md#schema-AuditVerificationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/governance/audit/verifications/{verification_id}`

Get Audit Verification

Operation ID：`governance.audit.verifications.read`。

实现：[src/opsmesh/governance/audit/routes.py](../../src/opsmesh/governance/audit/routes.py) · `get_audit_verification`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `verification_id` | path | 是 | string (uuid) | `format="uuid"` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [AuditVerificationView](schemas.md#schema-AuditVerificationView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/governance/security-events`

List Security Events

Operation ID：`governance.security_events.list`。

实现：[src/opsmesh/governance/security_events/routes.py](../../src/opsmesh/governance/security_events/routes.py) · `list_security_events`。

One offset page of security facts; filters are ANDed server-side.

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `kind` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 100, "type": "string"}, {"type": "null"}]` |
| `severity` | query | 否 | string anyOf null | `anyOf=[{"maxLength": 20, "type": "string"}, {"type": "null"}]` |
| `user_id` | query | 否 | string (uuid) anyOf null | `anyOf=[{"format": "uuid", "type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SecurityEventPage](schemas.md#schema-SecurityEventPage) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/governance/security-events/{event_id}`

Read Security Event

Operation ID：`governance.security_events.read`。

实现：[src/opsmesh/governance/security_events/routes.py](../../src/opsmesh/governance/security_events/routes.py) · `read_security_event`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `event_id` | path | 是 | string (uuid) | `format="uuid"` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [SecurityEventView](schemas.md#schema-SecurityEventView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。
