# MCP 日志分析员工流程示例

状态：当前实现示例（2026-09-18）。本示例复用后端已有的 MCP、Agent Profile、Team、Workflow、Approval、Memory 和 Capability Catalog 合同，不引入专用日志业务模块。

## 配置顺序

1. 创建远程 MCP Server：`server_type` 为 `streamable_http` 或 `sse`，`connection` 包含 URL、传输类型和鉴权方式。密钥不得放入连接配置，应创建绑定当前 Workspace/Server 的加密 MCP Credential Reference。
2. 调用 `POST /workspaces/{workspace_id}/capabilities/mcp-servers/{server_id}/discover`。如果存在多个可用凭据，请在请求体中指定 `credential_id`；发现成功后只绑定这一个凭据供执行使用。OpsMesh 使用官方 MCP Python SDK 的分页 `ClientSession.list_tools()` 获取工具名称、标题、描述和输入/输出 JSON Schema，保存发现校验和。
3. 调用 `GET .../{server_id}/discovered-tools` 检查差异。新发现或 Schema 变化的工具不能直接执行；逐个调用 `POST .../{server_id}/tools/{allowlist_id}/enable`，必要时完成资源审核。发现的工具默认是中风险、每次执行须审批；管理员可单独修改其权限策略，工具名称及 Schema 只能由远端重新发现更新。
4. 创建员工和日志分析专家两个 Agent Profile，各自填写 `role` 与 `instructions`；加入 Team 并配置 `team_role`、`department`、`position_title` 和 `responsibilities`。执行时职责作为上下文提供给 Agent。
5. 配置 Workflow：MCP `fetch_logs` 节点取数；专家 Agent 分析；员工 Agent 汇总；产品工具 `search_workspace_memory` 检索知识；经过确认后由 `upsert_semantic_memory` 写入稳定结论。节点间按工作流输入绑定传递结构化输出，可增加条件分支和人工审批节点。

每个 Run 的授权快照固定工具、Schema、Server/Tool 配置版本和凭据指纹。重新发现后变更的工具需要重新启用，旧 Run 不会静默获得新能力。直接工具节点的输出保留结构化对象供条件和下游节点使用；工具审批后 Worker 续接同一 Run。远程 MCP 服务可在独立仓库维护，OpsMesh 仅依赖协议与授权后的工具合同。
