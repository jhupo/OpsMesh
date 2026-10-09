# 平台托管 MCP 进程

平台接受标准 `mcpServers` 中的 stdio 项目配置，由 Worker 在每个 MCP 专属的
`persistent` Docker Runtime 内启动项目，并使用官方 MCP Python SDK 维护长期会话。
同一进程处理后续工具调用；对话结束不会停止 MCP。项目代码不在 API 或 Worker 主进程执行。
stdio 子进程通信遵循 [MCP transport 规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。

## 创建

`POST /api/v1/workspaces/{workspace_id}/capabilities/managed-mcp`

```json
{
  "template_id": "<已批准的 Runtime 模板 UUID>",
  "network_disabled": true,
  "mcpServers": {
    "project-mcp": {
      "command": "python",
      "args": ["/opt/project/server.py"],
      "cwd": "/opt/project",
      "env": {
        "PROJECT_TIMEOUT_SECONDS": "10",
        "PROJECT_API_TOKEN": "<凭据>"
      }
    }
  }
}
```

返回 HTTP 202 和部署记录数组，包含 `mcp_server_id`、`runtime_id`、`status`、
`generation`、`checked_at`、`last_error`。创建者同时需要工作空间的
`manage_capability` 和 `manage_runtime` 权限。MCP 配置、凭据和工具沿用原有审批策略。
存在待审批资源时返回 `pending_approval`；批准后使用 `start` 启动。

整个导入在一个 PostgreSQL 事务中创建 MCP 配置、加密凭据和部署意图。`env` 全部加密存入
MCP Credential，不会出现在普通连接配置、部署响应、Redis Job 或命令参数中。
Worker 启动时重新检查身份、资源权限、平台策略、审批状态、镜像白名单和配额。

## 项目与依赖

支持标准命令形式，包括 `python /opt/project/server.py`、`node /opt/project/index.js`、
`npx -y <package>@<version>`、`uvx <package>==<version>`。无需修改 MCP 项目的协议代码。
Runtime 模板镜像必须包含当前版本的 `opsmesh_runtime.mcp_process`。仓库的 Runtime
Dockerfile 提供 Python、Node/npm 和 uv/uvx。

平台内的路径是 Linux Runtime 路径。桌面 `C:/...` 路径不能直接使用，接口会拒绝。
私有项目需将代码和依赖放入已批准的自定义 Runtime 镜像，例如 `/opt/project`；
本接口不上传本机目录、不挂载宿主机目录，也不从 Git 仓库自动构建镜像。

`npx`/`uvx` 首次下载依赖、项目访问外部业务 API 时，需要设置 `network_disabled: false`，
且 Runtime 模板及平台出网策略必须允许。默认关闭网络。包缓存位于专属 `/workspace` 卷；
工作目录需要可由 Runtime 固定非 root 用户 UID/GID `65532:65532` 访问。
创建配置后会执行 MCP initialize 和 tools/list，发现的工具保持 `discovered`，
经现有工具启用/审批和 Agent 能力授权流程后才可调用。

## 调用审批

发现的工具默认 `risk_level: "medium"`、`requires_approval: true`。MCP 自报的
`readOnlyHint` 等 annotations 不作为免审批依据。管理员或具备能力管理权限的成员确认
工具风险后，可通过 `PATCH /api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{id}/tools/{allowlist_id}`
设置 `{"risk_level":"low","requires_approval":false}`。该配置变更仍经过资源审核、审计和版本校验。

已启用且明确配置为低风险免审批的工具，名称、参数和策略未出现敏感信号时，不调用语义
审核模型，直接执行；显式审批要求、敏感参数或高风险操作仍执行原有审核与平台限制。
发现工具定义或凭据发生变化时恢复保守配置，需重新审核，普通健康刷新不清除现有设置。

模型请求审核只检查请求构建器实际组装的输入，不扫描完整 Run 授权快照或内部运行元数据。
默认 `resource_review.model_request_review.semantic_mode` 为 `risk_based`：低风险输入直接通过，
有风险信号的输入进入语义审核，审核不可用仍保守处理。显式配置 `always` 可继续强制逐次
语义审核；不支持关闭全部审核。工具执行权限与审批独立检查，不因模型请求通过而跳过。

## 生命周期

查询：`GET /api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{id}/deployment`

控制：`POST /api/v1/workspaces/{workspace_id}/capabilities/mcp-servers/{id}/deployment`

```json
{"action": "restart"}
```

支持 `start`、`stop`、`restart`、`refresh`。请求异步返回；通过查询确认终态。
`start` 复用现有健康进程；`restart` 停止专属容器中的全部子进程，再创建新会话；
`stop` 停止容器与 MCP 子进程，保留配置和工作卷以供再次启动。
`refresh` 检查进程并重新发现工具。配置或凭据版本改变时，会重启会话，避免沿用旧凭据。
可通过现有 Runtime 删除接口释放已停止的 Runtime 与工作卷；后续 MCP start 会重新创建。

部署状态和操作版本存于 PostgreSQL。重复投递不重复启动；旧版本 Job 不覆盖新意图。
Worker 周期维护修复队列投递缺失、超过五分钟的中断操作，并约每分钟调度运行中部署的
健康刷新。刷新频率受 Worker 维护周期与队列积压影响；查询的 `checked_at` 是最近检查时间，
不是实时进程探测。权限撤销、资源封禁或凭据不可用时，Worker 拒绝执行并停止运行环境。

Runtime 内每五秒进行空闲 ping；传输故障采用有界退避，最多自动重建三次会话。
已经发出的工具调用发生异常时不会自动重放，避免重复副作用。重试耗尽需显式 `restart`。
进程控制通过容器内的私有 Unix Socket，不对外暴露 MCP 端口。项目原始 stderr 不进入
平台日志；部署错误返回固定错误码。单个工具请求最多运行 50 秒，启动等待最多约 55 秒。

## 升级与验证

需要 Alembic 升级到 `0111_managed_mcp`、更新 API/Worker，以及重新构建 Runtime 镜像并
将新摘要登记为允许的模板镜像。仅更新 API/Worker 无法让旧 Runtime 镜像获得进程管理器。

聚焦流程覆盖标准项目多次调用共享 PID 与内存状态、进程崩溃恢复、重复启动、停止与重启、
配置加密、跨工作空间拒绝、Worker 审批重检、队列丢失恢复和凭据轮换。
`backend/tests/test_managed_mcp_linux.py` 还可以作为独立脚本在受限 Linux 容器内执行，
检查后台启动、私有 Socket、环境注入和停止清理。
