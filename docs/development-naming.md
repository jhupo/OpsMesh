# 命名与公开接口规范

本项目遵循[PEP 8](https://peps.python.org/pep-0008/#naming-conventions)的CapWords类名、snake_case函数/模块名和Error异常后缀。在此基础上约定以下项目规则；OpsMesh前缀是本项目的公开接口设计选择，并非PEP 8强制要求。

## 框架入口与扩展基类

以OpsMesh开头标识框架所属：OpsMeshApplication、OpsMeshSettings、OpsMeshContract、OpsMeshError、OpsMeshDriver、OpsMeshAdapter、OpsMeshBot、OpsMeshEvent、OpsMeshMessage、OpsMeshMatcher、OpsMeshPlugin、OpsMeshAgentBackend。公开重导出位于所属包，__all__明确声明稳定接口。

继承者用具体实现名称，例如QQAdapter、HTTPXDriver、OpenAIAgentsBackend。SDK原生类型保留上游名称；在适配器中使用必要的import alias区分，不能修改第三方命名。

## 业务对象表达职责

数据类型使用业务名词：AgentSpecification、ConversationSession、UserPrincipal、ModelProfile、WorkspaceArtifact、WorkspaceSnapshot、MemoryExtraction、PermissionGrant。避免脱离上下文不明确的Record、Data、Info、Item等单独类名。

接口及实现按行为选后缀：Catalog表示版本化定义目录，Registry表示进程内代码/扩展登记，Repository/Store表示存储，Provider表示环境/后端提供能力，Service表示用例组合。类型名称应说明其拥有的职责；不机械叠加ManagerServiceImpl等后缀。

例：AgentDefinitionCatalog、ModelProfileCatalog、CodeToolRegistry、PersistentAgentRuntime、LocalWorkspaceService、PersistentMemoryService、VersionedRecordStore、EnvironmentSecretResolver。异步数据库明确名为AsyncDatabase；带版本数据行名为StoredRecord。

## 方法、字段和文件

方法采用动词或动词短语，例如publish_revision、request_generation、resolve_tool、authorize、subscribe。bool字段采用is_/has_或明确状态词；时间单位写入字段名，如timeout_seconds。标识使用_id、不可变版本引用使用_ref，持久版本与执行attempt分别命名。

文件名保持简洁的snake_case，由包路径提供业务语境，如modules/agents/internal/catalog.py；类名需要更完整的语义。不为满足字数而扩写业内明确概念，如HTTP、MCP、DTO、ID。词法一致性高于同义替换：同一角色不要在不同模块交替使用Catalog/Registry。

## 变更与评审

当前尚未发布稳定API，命名调整同步修改定义、导入、注解、测试、示例及文档；不以大量旧名alias替代实际修改。稳定版后，公开重命名需兼容策略和变更说明。评审同时检查职责是否正确，不能只检查名字长度。

## 函数与文件的职责拆分

跨越独立阶段时拆分，例如配置校验、SDK对象构建、资源打开、执行、结果持久化和清理。编排函数保留清楚的执行顺序；业务规则与协议映射放进职责明确的组件。同一资源的获取和释放应能一起审查，不能为了减少行数把生命周期拆散。

当前OpenAI后端的`backend.py`只编排构建、恢复、Runner执行、结果转换与释放；`agent_builder.py`、`tools.py`、`streaming.py`、`state.py`、`sandbox.py`、`tracing.py`分别维护SDK映射。应用层由`AgentPublicationValidator`负责发布准备和校验，`AgentExecutionBridge`负责上下文与资源，`RunResultFinalizer`负责结果收尾；`AgentContextBindings`、`RunResourceScope`和`RunUsageAccounting`提供各自明确的行为。组合根注入所需依赖，应用流程不接收整个服务容器。

不为每个小函数创建一个文件，不把简单局部步骤强制包装为类，也不以固定行数替代职责判断。函数变长时首先检查输入/输出、状态所有权、错误处理和释放责任是否仍然集中。
