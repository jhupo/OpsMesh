# Agent 配置与组装

## 所有者与入口

`src/opsmesh/agents/manager.py` 的 AgentManager 是可信宿主的内存配置注册表；`agents/catalog` 管理加密数据库草稿、不可变发布和权限。`config/` 保存可序列化值；`assembly.py` 和 `resolvers/` 组装原生 Agent/SandboxAgent。`bootstrap/execution.py` 注入模型、密钥和插件视图。

## 不变量

- 配置不保存活的 Agent、MCP session、容器句柄或 SDK Client。持久实例归 execution，编排实例归 workflows。
- 使用原生 SDK 参数及类型；配置中省略字段与显式 null 的语义不能随序列化合并。原生对象由运行时组装，不从任意导入字符串执行用户代码。
- provider 注册表和凭据解析器是宿主边界；不恢复已退役的顶级 providers 表/API。
- 种子 JSON 只导入不存在的记录，不覆盖在线管理。发布和回滚均增加版本；创建实例冻结配置图，Run 准入冻结模型参数和价格，凭据值仍按引用动态解析。
- 模型重试使用原生 OpenAI Client 的 max_retries；单操作总时限覆盖重试与退避。Temporal 不叠加模型 Activity 重试，失败不能推定服务商未计费。
- 工具、MCP、Hook、Skill 的 ID 由注册资源解析。只选择当前配置明确绑定的资源，不扫描并自动启用所有插件能力。
- SDK 支持某功能不等于 OpsMesh 已持久化或通过 API 暴露该功能。先核对锁定版本和本仓库实际入口。

## 改动检查点

核对配置序列化、注册 ID 解析、原生 SDK 签名、密钥注入及异常路径资源关闭；实例持久化改动另读 [execution](execution-lifecycle.md)。

相关源码：`agents/config/agent.py`、`config/sandbox.py`、`config/manifest.py`、`providers/factory.py`、`resolvers/mcp.py`。
