# capabilities-publication-reviews

[返回接口索引](README.md)

| 方法 | 路径 | 操作 |
| --- | --- | --- |
| POST | `/api/v1/capabilities/definitions/{definition_id}/versions/{version_number}/publication-review` | Submit Publication Review |
| GET | `/api/v1/capabilities/publication-reviews/admin` | List Publication Reviews For Admin |
| POST | `/api/v1/capabilities/publication-reviews/admin/{review_id}/decision` | Decide Publication Review |
| GET | `/api/v1/capabilities/publication-reviews/mine` | List My Publication Reviews |
| GET | `/api/v1/capabilities/publication-reviews/{review_id}` | Read Publication Review |
| POST | `/api/v1/capabilities/publication-reviews/{review_id}/withdraw` | Withdraw Publication Review |

## POST `/api/v1/capabilities/definitions/{definition_id}/versions/{version_number}/publication-review`

Submit Publication Review

Operation ID：`capabilities.publication_reviews.submit`。

实现：[src/opsmesh/capabilities/publication_routes.py](../../src/opsmesh/capabilities/publication_routes.py) · `submit_publication_review`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `definition_id` | path | 是 | string (uuid) | `format="uuid"` |
| `version_number` | path | 是 | integer | — |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/PublicationReviewSubmission"
}
```

模型：[PublicationReviewSubmission](schemas.md#schema-PublicationReviewSubmission)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 201 | Successful Response | application/json | [PublicationReviewView](schemas.md#schema-PublicationReviewView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/capabilities/publication-reviews/admin`

List Publication Reviews For Admin

Operation ID：`capabilities.publication_reviews.admin_list`。

实现：[src/opsmesh/capabilities/publication_routes.py](../../src/opsmesh/capabilities/publication_routes.py) · `list_publication_reviews_for_admin`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `status` | query | 否 | string | `default="pending"`; `pattern="^(pending&#124;approved&#124;rejected&#124;cancelled)$"` |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[PublicationReviewView](schemas.md#schema-PublicationReviewView)&gt; |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/capabilities/publication-reviews/admin/{review_id}/decision`

Decide Publication Review

Operation ID：`capabilities.publication_reviews.decide`。

实现：[src/opsmesh/capabilities/publication_routes.py](../../src/opsmesh/capabilities/publication_routes.py) · `decide_publication_review`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `review_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 请求体

必填：是。

Content-Type：`application/json`。

```json
{
  "$ref": "#/components/schemas/PublicationReviewDecision"
}
```

模型：[PublicationReviewDecision](schemas.md#schema-PublicationReviewDecision)

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PublicationReviewView](schemas.md#schema-PublicationReviewView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/capabilities/publication-reviews/mine`

List My Publication Reviews

Operation ID：`capabilities.publication_reviews.mine`。

实现：[src/opsmesh/capabilities/publication_routes.py](../../src/opsmesh/capabilities/publication_routes.py) · `list_my_publication_reviews`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `limit` | query | 否 | integer | `default=50`; `maximum=100`; `minimum=1` |
| `offset` | query | 否 | integer | `default=0`; `minimum=0` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | array&lt;[PublicationReviewView](schemas.md#schema-PublicationReviewView)&gt; |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## GET `/api/v1/capabilities/publication-reviews/{review_id}`

Read Publication Review

Operation ID：`capabilities.publication_reviews.read`。

实现：[src/opsmesh/capabilities/publication_routes.py](../../src/opsmesh/capabilities/publication_routes.py) · `read_publication_review`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `review_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PublicationReviewView](schemas.md#schema-PublicationReviewView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。

## POST `/api/v1/capabilities/publication-reviews/{review_id}/withdraw`

Withdraw Publication Review

Operation ID：`capabilities.publication_reviews.withdraw`。

实现：[src/opsmesh/capabilities/publication_routes.py](../../src/opsmesh/capabilities/publication_routes.py) · `withdraw_publication_review`。

权限依赖：未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。

### 参数

| 名称 | 位置 | 必填 | 类型 | 说明与约束 |
| --- | --- | --- | --- | --- |
| `review_id` | path | 是 | string (uuid) | `format="uuid"` |
| `X-Session-ID` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Installation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Instance-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Invocation-Id` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |
| `X-OpsMesh-Namespace` | header | 否 | string anyOf null | `anyOf=[{"type": "string"}, {"type": "null"}]` |

### 响应

| HTTP | 说明 | Content-Type | 返回类型 |
| --- | --- | --- | --- |
| 200 | Successful Response | application/json | [PublicationReviewView](schemas.md#schema-PublicationReviewView) |
| 400 | Bad Request | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 401 | Unauthorized | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 403 | Forbidden | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 404 | Not Found | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 409 | Conflict | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |
| 422 | Unprocessable Content | application/json | [ErrorResponse](schemas.md#schema-ErrorResponse) |

所有错误响应使用统一封装 `{"error": {"code", "message", "request_id", "details"}}`；客户端可传入 `X-Request-ID` 并获得回显，追踪标识见响应头 `X-Trace-ID`、`X-Span-ID` 与 `traceparent`。
