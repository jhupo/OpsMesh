# OpsMesh API 使用说明

接口来自 `src/opsmesh/bootstrap/routers.py` 与实际注册路由。完整参数和返回字段见 [逐接口参考](api/README.md)、[数据模型](api/schemas.md) 和 [OpenAPI JSON](openapi.json)，覆盖全部 550 个接口，而非接口族示例。

## 地址与发现

默认前缀 `/api/v1`，可由 `OPSMESH_API_PREFIX` 配置。以下示例使用 `BASE=http://localhost:8000/api/v1`。开发环境的 `/docs`、`/redoc`、`/openapi.json` 位于服务根路径；生产环境关闭在线文档时，使用仓库导出的合同。

内容通常为 `application/json`。上传按接口声明使用 `multipart/form-data`；文件、头像和流式响应使用各自媒体类型，不能统一调用 `response.json()`。

## 认证与权限

| 调用身份 | 凭据与校验 |
| --- | --- |
| 普通用户 | `Authorization: Bearer <user-token>`；登录或创建 Token 后获取，权限为角色、Token scope 和资源授权的交集 |
| 平台管理员 | 管理员用户的未限制 scope 的 Token，或配置型平台管理员 Token；部分写接口要求可归属到用户的管理员凭据 |
| 内部联调 | 内部 Token 加 `X-User-ID`；只用于受信任后端联调，不向客户端公开内部 Token |
| 自托管 Worker | `X-Runtime-Authorization: Bearer <runtime-credential>`；由注册流程签发，普通用户 Token 不能替代 |
| 插件运行时 | `Authorization: Bearer <plugin-credential>`；限定 URL 的 workspace/install，不认证为普通用户 |
| Worker 心跳 | 对应接口同时检查工作空间运维权限和 `X-Worker-Heartbeat-Token` |

注册、登录、邀请接受、Worker 注册、Webhook 等入口有各自配置、签名或令牌要求。参考页列出实际依赖和作用域；没有用户认证依赖不等于可无条件调用。Header 在参数表中标为“非必填”可能仅代表语法层允许省略，认证依赖仍会拒绝缺失凭据。

工作空间是租户边界。资源 ID 不授予跨空间读取权限；URL 中的 `workspace_id` 必须与资源和执行身份一致。管理员身份也不自动绕过普通工作空间接口的成员要求。客户端可调用 `GET /workspaces/{workspace_id}/access/context` 获取工作空间动作上限，资源级授权、审批和当前状态仍在每次操作及执行前重新检查。

登录示例：

```bash
curl -X POST "$BASE/auth/login" -H 'Content-Type: application/json' \
  --data '{"email":"user@example.com","password":"<your-password>"}'
curl "$BASE/auth/me" -H "Authorization: Bearer $TOKEN"
```

使用登录返回的 `token` 字段；不要把真实凭据写入命令历史、代码或文档。账号注册策略与 Token 的期限、限制字段见 [auth](api/auth.md)。

## 分页与模型

通用分页使用 `limit`（默认 50，1–100）和 `offset`（默认 0，≥0），返回 `items`、`total`、`limit`、`offset`。部分接口采用数组、持久事件游标、Redis 游标或专用分页格式，应以该接口 schema 为准。

UUID 使用字符串；日期时间使用 schema 声明的 date-time 格式。字段的必填、nullable、默认值、枚举、字符串长度、数值边界和嵌套模型均在 [模型参考](api/schemas.md) 中给出。`PATCH` 的省略与显式 null 是否等价由该 schema 和实现决定，不统一假定 null 表示删除。

## 错误与追踪

HTTP/领域错误采用以下结构；参数校验失败的 `details` 是仅保留 type、loc、msg 的脱敏错误数组：

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "request_id": "client-request-id",
    "details": [{"type":"missing","loc":["body","email"],"msg":"Field required"}]
  }
}
```

| HTTP | 常见含义 |
| --- | --- |
| 400 | 无效业务参数 |
| 401 | 缺少或无效认证 |
| 403 | 工作空间、Token scope、资源授权或策略拒绝 |
| 404 | 资源不存在或不可见 |
| 409 | 状态、版本、幂等键或执行前置条件冲突 |
| 413 | 请求内容过大 |
| 422 | 参数校验失败 |
| 429 | 限流或配额限制 |
| 503 | 依赖不可用或维护准入拒绝 |

具体领域 `error.code` 以实际返回为准；不能仅凭 HTTP 状态判断可自动重试。未处理的内部异常不保证使用这个公开错误模型。参考页的响应表来自路由声明，不是所有可能运行错误的穷举。

可发送 `X-Request-ID` 与 W3C `traceparent`，在响应的 `X-Request-ID`、`X-Trace-ID`、`traceparent`、`X-Process-Time-Ms` 中关联请求。`X-Request-ID` 是追踪标识，不能代替业务幂等键。

## Chat 与长任务接入

先配置工作空间、模型凭据、Agent 指令和工具权限，绑定有效的 Runtime。意图路由属于用户配置的 Manager；平台不自动选择知识库、订单工具或专家。

1. `POST /workspaces/{workspace_id}/conversations` 创建对话。`mode=agent` 指定 `agent_profile_id`；`mode=auto` 指定 Manager 或使用工作空间配置；`mode=team` 指定 `agent_team_id`。
2. `POST /workspaces/{workspace_id}/conversations/{conversation_id}/messages`，发送 `{"body":"用户问题"}` 和 `Idempotency-Key`。返回 HTTP 202 与 Turn，不代表模型已完成。
3. 查询 `.../messages`、`.../events?after_id=0&limit=100`、`.../executions` 获取状态、回复、任务和 Run。相同对话按轮次串行，不同请求在 Worker 活动槽与 Runtime 容量内并发。
4. Task 的 `GET /workspaces/{workspace_id}/tasks/{task_id}/events/stream` 提供 SSE；Run 的事件、审批和 Artifact 接口提供持久执行证据。
5. 使用 `.../turns/{turn_id}/cancel` 取消，或显式 `.../turns/{turn_id}/retry` 重试符合条件的失败轮次。不要在超时后自动重新发送具有副作用的业务请求。

```bash
curl -X POST "$BASE/workspaces/$WORKSPACE_ID/conversations" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data '{"title":"分析","mode":"agent","agent_profile_id":"<agent-uuid>"}'
curl -X POST "$BASE/workspaces/$WORKSPACE_ID/conversations/$CONVERSATION_ID/messages" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $REQUEST_KEY" --data '{"body":"分析我提供的日志"}'
```

重复提交同一幂等键及相同输入返回原 Turn；同一键使用不同输入返回冲突。取消不能撤销已经发生的外部副作用。审批使用独立 API，自然语言“同意”不自动授权。原生 SDK Session 管理历史，客户端每轮只发新增输入。详见 [对话](conversations.md)、[完整 conversation 合同](api/conversations.md) 和 `scripts/conversation_client.py`。

## 流式与文件

| 接口 | 编码与消费方式 |
| --- | --- |
| `/workspaces/{workspace_id}/tasks/{task_id}/events/stream` | `text/event-stream`；按 event/data 帧读取 snapshot、消息、工具事件、心跳与终止事件；参数支持 after_sequence、event_cursor、once |
| `/workspaces/{workspace_id}/automations/{automation_id}/events/{event_id}/stream` | `application/x-ndjson`；逐行 JSON，cursor 为 Redis stream 游标 |
| `/plugin-runtime/{workspace_id}/{install_id}/automations/{automation_id}/events/{event_id}/stream` | 插件凭据下的同类 NDJSON 流 |
| `/auth/me/avatar` | `image/webp` 二进制 |
| 文件与 Artifact 的 `/download` | 文件原始字节，Content-Type 使用存储元数据，Content-Disposition 提供下载文件名 |
| `/metrics` | Prometheus 文本格式 |

流建立后的失败可能以流事件或连接关闭表现，不能再依赖初始 HTTP 状态。客户端重连应使用对应游标，结合持久事件查询确认状态；对话 events 接口本身是游标轮询，不能当作 SSE。

## 更新文档

```bash
uv run python scripts/export_api_docs.py
uv run python scripts/export_api_docs.py --check
```

导出不启动 API lifespan、不执行任务、不连接业务 MCP 或调用模型。输出固定使用 `/api/v1`；部署采用其他前缀时替换路径前缀。生成内容不读取数据库资源或导出运行密钥。
