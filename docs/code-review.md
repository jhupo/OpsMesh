# 模块审查与网关能力对照

本轮依据工作区源码、锁定 SDK 源码及
[Octop 224ed07](https://github.com/TencentCloud/octop-gateway/tree/224ed078ce20042fb5fa59240d6e3c7c34bcd67f)
检查通用网关能力、QQ/钉钉适配、跨模块依赖及重复实现。
内置适配范围仍为 QQ、钉钉。功能矩阵和当前限制见
[Gateway](../src/opsmesh/gateway/README.md#octop-能力对照)。

## 已修正的问题

| 问题 | 影响与处理 |
| --- | --- |
| 同一会话并发处理普通消息 | 收件按身份/会话排序；控制命令可执行取消；debounce 使用持久合批状态和体积上限 |
| 最终回复排在旧占位/typing 后面 | 丢弃从未发送的临时提示，已尝试的请求继续保留对账语义 |
| QQ 流式最终文本改写已发送前缀 | replace 帧以持久成功回执的内容为前缀，分片索引独立于普通消息序号 |
| 群上下文欠缺平台随附消息 | 接入 QQ 近期消息、引用、ASR 和富媒体，按 TTL/数量/发送者过滤；冻结本地历史在提交前重查绑定和路由 |
| 钉钉只发送纯文本 | 短文本使用 Markdown 模板，长文本使用纯文本；Webhook 与 OpenAPI 共用选择规则 |
| 只有显式列表推送 | 增加已知 subjects 和显式 broadcast，目的地去重并重新授权，不枚举厂商全部用户 |
| 模型设置按 provider/model 去重 | 改为按 Agent ID 冻结，避免不同子 Agent 的默认模型设置互相覆盖 |
| 流式输出范围过窄且手动消费完整响应 | 使用官方 Temporal Agents 流式路径；公开 handoff/根沙箱可输出，工具 Agent 及其后代保持内部 |
| 编排附件作为 JSON 文本传入 Agent | 增加显式 messages 模式，使用已有 execution 原生输入校验 |
| 配置图遍历重复 | execution 冻结复用 agents 的图解析器，统一节点/深度/环检测 |
| 直接执行和模型 Activity 重复创建模型 | 共用 execution 模型绑定，统一认证、网络、超时、SDK 重试与计费配置 |
| 用户状态校验重复且放在 execution | 集中至 users.service.current_user；workflow/runtime 使用相同当前身份加载 |
| 跨模块导入 HTTP 路由函数 | 加密器依赖移至 shared/http；通用目标端口与注入放 platform，不再从 execution 或 triggers 路由取公共依赖 |
| 会话队列和群历史缺少匹配索引 | 增加 channel/identity/conversation/status/time 和 channel/conversation/status/time 组合索引 |
| 网关审计使用未注册来源值 | 统一使用 platform 来源，保留 gateway 分类，避免创建通道和绑定身份时事务回滚；增加真实数据库回归用例 |

## 分类与保留边界

| 模块 | 审查结论 |
| --- | --- |
| agents | 配置、资源注册及原生 SDK 组装；不拥有持久实例。AgentManager 仍用于宿主配置目录，不能当无用代码删除 |
| execution | 实例、Session、Run、沙箱和效果回执；不导入 Temporal。模型授权包装是平台控制，不是另一个模型 SDK |
| workflows | 发布、绑定和图执行；Temporal 适配集中 engines/temporal。节点账本与 execution 效果账本拥有不同实体，保留各自事务 |
| users | 当前身份、平台角色及额度；外部群角色不能转换为平台角色 |
| gateway | 根目录只放合同/端口/包入口；api、channels、identity、routing、messaging、storage、adapters 按职责分类；不保留旧路径包装 |
| plugins / SDK | 安装、注册、代际与打包；MCP 连接/工具协议继续由原生 Agents SDK 提供 |
| resources | FileStorage/LocalDirectoryStorage 是存储能力边界；目录授权与沙箱执行各有所有者。GatewayMedia 可注入 FileStorage |
| platform | 审计、账单、评估、通用目标端口及依赖；跨模块实际调用由 bootstrap 组合 |
| bootstrap / temporal / shared | 进程装配、公共 Temporal 设施与基础工具分层；资源模块不反向导入 bootstrap |

Session 的本地与 Temporal 适配是 I/O 边界，不另建一套 Session 协议。
沙箱的 `python3 -I -c` 仅装载 `runtime/helpers/` 内可审查的固定辅助程序，
用于原生 SDK 所需的文件与权限操作，不执行从提示词拼接出来的框架管理脚本。
此次未为减少文件数量强行合并不同所有者、权限或事务生命周期的模块。

## 校验与发布限制

当前工作区静态校验结果：

| 检查 | 结果 |
| --- | --- |
| Ruff 格式与规则 | 源码、SDK、新迁移和新增合同用例通过 |
| 服务端及 SDK mypy | 357 个源文件通过；SDK 独立 9 个源文件通过 |
| import-linter | 服务端 11 条边界规则通过；SDK 独立边界通过 |
| 架构审计 | 348 个服务端源文件、56 个唯一表所有者通过 |
| Compute 依赖生成物 | export_compute_requirements.py --check 通过 |
| Git 空白与失效导入路径 | diff --check 通过；未发现已删除网关路径与旧端口路径的源码引用 |

- 服务端及 SDK 全量 Ruff、mypy、import-linter 与架构审计属于静态校验，不能证明厂商协议或分布式恢复行为。
- 用户随后授权验收，已执行本地与服务器测试。命令、结果与未覆盖范围见 [验收记录](acceptance.md)。
- `0006_gateway_processing` 已在服务器隔离测试数据库执行，空库迁移链和 ORM 一致性通过；不代表生产数据库已经升级。
- Temporal 当前持久 Agent 的普通/流式执行和同版本历史重放已通过；存量历史跨版本重放、取消、审批恢复和 Worker 重启组合流程仍需验收。存量历史未完成重放核验时先排空旧执行再升级。
- QQ/钉钉凭据探测、实际收发、QQ 流式结束/重连和厂商主动消息额度尚未在本轮真实验收。

因此，此报告说明已经完成的源码调整和审查边界，不是完整功能验收或 Release 许可结论。
