# capabilities

工具、Skill、MCP、知识库目录，ToolManifest和调用治理。

已实现代码工具及网关、版本化能力目录、参数校验、调用授权、MCP连接与工具桥接、eager/lazy Skill装配和以下扩展：

- `SkillCatalog.publish_git` 从允许的HTTPS主机或部署明确允许的本地目录导入固定40位提交，直接读取Git对象，不运行checkout过滤器、仓库脚本、hook或子模块。验证路径、软链接、文件/字节上限，保留来源提交；HTTP不接受本地目录。Git服务端仍须允许获取该提交，私有仓库凭证和仓库下载磁盘配额未实现。
- `ScriptTools.publish/get/execute` 将Python入口和wheel依赖存为摘要校验的不可变制品。发布同时建立`script:<ref>`工具绑定；网关负责授权与幂等。每次执行创建独立工作区/venv，仅使用制品中的wheel、`--no-index --no-deps`安装并执行`pip check`。参数通过JSON文件传入，stdout必须为JSON；超时、取消、资源限制归ExecutionProvider。Docker是平台默认，local必须显式授信；venv本身不是安全沙箱。当前不自动解析或从网络下载依赖。
- `KnowledgeIndex.search(mode="lexical"|"vector"|"hybrid")` 提供BM25、持久向量cosine和RRF混合排序，按授权集合检索。默认`LocalHashEmbedding`是确定性词/字符特征向量，不是预训练语义模型。代码插件通过`get_knowledge().register_embedding_provider(provider, default=True)`提供版本化Provider；注册支持归属/回滚/禁用，启动后冻结。Provider版本变更使用新的向量缓存空间。
- MCP支持`stdio`、`streamable_http`和旧版`sse`。远端调用解析部署secret引用，先做主体授权和工具allowlist检查；stdio仅显式授信执行。

真实本地验证包括临时Git仓库、离线wheel安装/执行、Embedding Provider替换和持久缓存、临时MCP SSE服务器发现/调用。HTTP回归覆盖工作区发布脚本→Agent调用和权限拒绝。未把本地协议测试当作远端供应商或生产环境验收。

公开调用合同位于`api.py`，领域事件位于`events.py`；实现、仓储与表归`internal/`。跨模块只依赖公开合同，`bootstrap/`负责具体实现的组装。当前支持状态与测试证据见[SDK覆盖表](../../../../docs/design/sdk-coverage.md)，职责设计见[架构设计](../../../../docs/design/architecture.md)。
