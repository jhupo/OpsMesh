# 多用户资源权限

状态：后端实现，2026-09-18。适用于 API、外部插件消息和后台执行；前端权限配置页面不在本次范围内。

## 权限边界

一次操作必须同时满足有效账号、工作区成员、工作区角色、令牌范围、资源动作授权以及原有 Agent/工具/运行时策略。资源授权不能授予工作区成员资格，也不能突破角色或令牌上限；模型、插件输入中的用户级别、队列中的用户 ID 均不能增加权限。

新建资源默认归创建者私有；owner/admin 在令牌允许的范围内管理工作区资源。普通成员需要明确授权。共享团队不会共享其他用户的任务、模型会话或私有记忆。资源不存在和无权访问的受保护 ID 入口统一拒绝；列表和数量在 SQL 分页前过滤。

| 动作 | 含义 | 工作区上限 |
| --- | --- | --- |
| `read` | 列表、详情、内容、下载、输出、事件 | `read` |
| `invoke` | 使用团队、专家、能力、MCP、技能、编排、运行空间 | `write` |
| `update` / `delete` | 修改或删除资源 | `write` |
| `share` | 管理明确的用户授权 | `write` |
| `control` | 取消、控制任务或团队执行 | `operate` |
| `approve` | 处理审批 | `approve` |

动作相互独立：能看不等于能调用，能调用不等于能修改，能修改不等于能分享。授予者必须具备 `share`，且不能授出自己没有的动作。原有管理接口还会要求 `manage_capability`、`manage_runtime` 或 `admin`。

## 资源归属

资源类型包括 `team`、`agent`、`project`、`domain_project`、`domain_item`、`task`、`capability`、`mcp_server`、`mcp_tool`、`skill`、`session`、`thread`、`workflow`、`file`、`knowledge`、`memory`、`automation`、`runtime_space`、`runtime`、`provider`、`webhook`、`schedule`。

- Run、步骤、消息、审批、artifact 和任务输出沿所属任务鉴权。
- 项目文件关联、配置版本和产物索引沿项目鉴权；项目读权限可读关联文件。
- 知识切片沿知识源鉴权；Run 记忆可通过任务读权限获得；记忆更新另查 `update`。
- SDK 会话键包含工作区和实际用户，不续接不同用户的模型历史。
- 团队线程中的任务消息仍按任务过滤。
- 插件信任、凭据、基础设施治理和审计/成本控制面保留管理边界。
- 未声明归属的新工作区表对普通用户默认拒绝，必须添加明确的根或从属规则。

查询过滤由 SQLAlchemy Session 事件和 `with_loader_criteria` 承载；归属与资源写入使用同一个事务。权限表只存固定产品动作，不提供策略代码、跨工作区共享或通配符绕过。

## 配置 API

在 `/api/v1/workspaces/{workspace_id}` 下：

- `GET /access/{kind}/{resource_id}/permissions`：当前用户有效动作。
- `GET /access/{kind}/{resource_id}/grants`：有分享权的用户查看授权。
- `PUT /access/{kind}/{resource_id}/grants/{user_id}`：替换用户授权，body 为 `{"actions":["read","invoke"]}`，空数组撤销。
- `PUT /access/{kind}/{resource_id}/owner`：管理员转移所有者，body 为 `{"user_id":"平台用户 UUID"}`。
- `PUT /automations/{automation_id}/identities/{sender_id}`：管理员绑定或撤销渠道发送者，body 为 `{"user_id":"平台用户 UUID","active":true}`。

修改授权、归属、绑定以及 API 拒绝均产生审计或安全事件。撤销明确授权不会移除所有者身份，需要转移归属或停用成员资格。

## 消息、任务与撤权

插件验证渠道签名后填写 `sender_id`；平台通过 `(workspace, automation, sender_id)` 查管理员绑定。`data.user`、用户名、VIP 等级只是业务输入。自动化创建者/插件凭据只承担消息传输授权，执行身份使用绑定用户。

Task 持久保存 `execution_identity`（用户 ID、令牌 ID、接受时令牌范围，不含令牌密文）。子任务继承原身份，重试不借用操作人的权限；导入任务重新绑定导入者。定时自动化失效后暂停并记录证据，团队队列恢复接受时身份。

执行入口、工具网关、MCP、自托管派发、模型结果落库前和撤销轮询均重新检查权限。排队任务撤权记录失败；运行中撤权通过 SDK 取消并由可信终止逻辑释放资源。事件订阅每次读取和重连重新授权，回复在入队及投递前检查绑定和任务读权限。已发送的外部消息无法撤回。

## 升级与运维

执行 Alembic `upgrade head`，迁移版本为 `0099_resource_authorization`。

- 能证明创建者且成员存在的历史资源回填所有者；无法证明的资源保留管理员管理。
- 历史直接任务及可追溯子任务回填身份；无法证明来源的任务拒绝执行。
- 历史定时事件回填自动化身份；历史渠道事件不猜测发送者，需绑定后重新提交。
- 旧模型会话不自动迁入新用户会话；回滚到 `0098_plugin_distribution` 会删除本次权限数据，生产回滚前须备份。

验证覆盖同工作区双用户创建/列表/分享/拒绝/撤销、消息与定时任务、第二用户流式模型和工具事件、撤权重连、排队撤权 Worker，以及 PostgreSQL 升级/回滚/再次升级。日常只运行相关产品流程和静态/架构检查，不运行全量测试。

本阶段采用固定动作和直接用户授权，使用现有 PostgreSQL 同事务保存；不引入独立策略服务、双写同步或权限缓存。组织树递归继承、跨租户共享、OIDC/SSO 和独立服务账号属于后续范围。
