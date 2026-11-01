# 资源生命周期与受保护管理服务

2026-10-31。本文描述当前实现及一致性边界。

## 不可变定义与可变可用性

模型版本、Tool、Skill、MCP、Script 的已发布定义不被撤销操作改写。
每种资源使用独立 availability 表，记录 `enabled`、操作人、原因和 CAS revision。
首次修改使用 `expected_revision=0`，之后使用读取到的 availability revision；它与模型定义 revision 无关。

禁用版本后，内部执行目录的默认 `get` 拒绝解析；管理服务仍可读取原定义、列举版本、查看引用影响及重新启用。
重复发布相同定义不会重新启用已撤销版本。新模型版本不会改写旧版本的可用性。
Tool 网关每次调用、Script 每次执行、Skill 每次加载和 MCP 每次发现/调用重新检查状态。
已开始的外部副作用不会因撤销自动回滚。

公共管理入口为 `ModelManagement`、`CapabilityManagement`、`SkillManagement`、`ScriptManagement`、`MCPManagement`。
管理方法要求显式 `actor_id`；执行方法保留 `principal_id`。它们通过平台授权器检查活跃账号和资源权限。
写操作先记录审计 intent，再执行，再记录成功或失败；这不是跨业务表和审计表的单个原子事务。
`Depends(get_models)`、`get_capabilities`、`get_skills`、`get_script_tools`、`get_mcp_catalog` 返回这些受保护门面。
运行时和组装层持有内部目录，以便进行平台内部装配；Python 插件仍是受信任代码，不构成进程安全沙箱。

HTTP 主要入口：

| 操作 | 路径 |
| --- | --- |
| 模型历史 | `GET /api/v1/models/{id}/versions` |
| 模型可用性 | `GET/PUT /api/v1/models/{id@revision}/availability` |
| 模型影响查询 | `GET /api/v1/models/{reference}/references` |
| 能力列举 | `GET /api/v1/capability-versions/{tool|skill|mcp|script}` |
| 能力历史定义 | `GET /api/v1/capability-versions/{kind}/{reference}` |
| 能力可用性 | `GET/PUT /api/v1/capability-versions/{kind}/{reference}/availability` |
| 能力影响查询 | `GET /api/v1/capability-versions/{kind}/{reference}/references` |

引用查询由组装层读取 Agent 草稿/历史和活跃 Run，并按调用者对引用对象的权限过滤。
资源不会因存在引用而禁止撤销；撤销是主动阻止后续执行的管理操作。

## 知识集合、文档与导入

`KnowledgeManagement` 提供集合创建、授权列举、读取、CAS 更新、删除，文档读取、CAS 更新/删除、文本导入、向量重建和任务查询。
现有文档没有集合元数据时，首次授权管理访问会补建默认集合记录。
集合删除写入 tombstone 后清理文档，后续读取和追加拒绝该集合；集合标识不被复用。

`POST /api/v1/knowledge/collections/{id}/imports` 接受多条 text/markdown source、幂等键、chunk_size 和 overlap。
分块保留文本，并优先按换行截断；当前不做 OCR、PDF 抽取或 Markdown AST 语义分块。
来源 id 在批次内必须唯一。任务记录总数、完成数、文档 id、终态及安全错误码。
重复提交相同请求可以恢复失败/中断任务；幂等键对应不同请求时拒绝。
持久任务 claim 和续租阻止两个进程同时执行同一任务。
当前导入/重建由请求协程执行，状态持久化供另一请求查询；没有独立的知识后台调度队列。

文档内容和向量缓存通过 `provider id + dimensions + 完整文档内容` 计算不可变 key。
更新先生成并写入新向量，再以文档 revision CAS 发布新内容；读取总是按当前文档内容查向量。
因此更新失败、并发 CAS 冲突或切换 provider 不会把旧内容的向量用于新文档。
重建操作重新生成集合内文档的向量，记录独立维护任务。不可达旧向量目前保留在缓存表中。

`GET /api/v1/knowledge/documents/{id}` 一次读取返回 `{document, revision}`，避免分别读取内容和 revision 的竞态。
`PUT/DELETE` 要求 `expected_revision`。集合和文档权限在服务内检查。
插件通过 `get_knowledge` 获得受保护数据服务；部署期 embedding 扩展通过 `get_embedding_registry().register_embedding_provider(...)` 注册，启动冻结后不可变更。

## 工作区与快照

`WorkspaceManagement` 提供授权创建/恢复、按权限过滤列表、文件树、stat、读取、写入、删除文件、重命名、materialize、快照列表/删除。
文件删除不递归删除目录；重命名不覆盖目标。路径解析统一拒绝越界、盘符、备用分隔符和 symlink。
每个文件操作进入跨进程工作区 mutation lease，文件和包含只读文件的目录不能被重命名/删除来绕过只读标志。

快照删除先查询持久引用；被 Agent、Run、检查点或 sandbox 引用的快照返回 `RESOURCE_IN_USE`。
允许删除的快照仅写 tombstone，保留 manifest 及 blob 引用以保护内容；普通 get/list/restore 不再返回它。
该策略不会因为跨域发布/删除竞态物理清除内容；它不提供所有域共同遵守的全局分布式事务锁。
竞争中的调用可能因快照已撤销而失败，调用方应重新解析并重试或改用新快照。

## 记忆权限与策略

`MemoryManagement` 检查活跃调用者，namespace 服务继续独立执行 owner/readers/contributors ACL。
`GET /api/v1/memory/namespaces` 只返回可访问的 namespace。
`PUT /api/v1/memory/namespaces/{id}/settings` 由 owner 修改 readers、contributors 和 policy，并使用 namespace revision CAS。
正文更新只接受 `MemoryCandidate`，无法修改 owner、权限或 policy；已撤销的读取者后续 read 立即被拒绝。
policy 变更也会推进 namespace revision，使旧生成任务的发布 CAS 不能覆盖新的策略。

## 验证

资源治理测试覆盖历史保留/撤销、缓存 MCP 定义重新检查、Skill/Script 拒绝执行、文档向量一致性、失败导入恢复、多 worker claim、工作区只读/路径边界、快照引用及软删除、记忆 ACL 撤销。
HTTP 测试验证管理入口和直接 DI 调用遵循相同授权，全部使用本地数据库、文件和受控 provider，不调用真实模型。
