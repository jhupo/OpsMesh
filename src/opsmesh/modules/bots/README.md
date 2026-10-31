# bots

机器人账号、外部身份映射、连接、Agent绑定和消息投递；这些是平台核心能力，不依赖业务插件加载。

已实现机器人账号、外部身份映射、Agent路由、投递状态和幂等处理。厂商协议独立位于 `opsmesh.infrastructure.bots`，通过框架 `OpsMeshBotAdapter`、`BotAdapterRegistry` 注入；本模块不导入 QQ、钉钉或 OneBot 实现。当前提供钉钉应用机器人、QQ 官方机器人及独立 OneBot 兼容实现。

`BotRoute`持久保存`(bot_id, channel_id) → agent_revision`。`BotAccounts`提供绑定/替换、单项读取、按Bot列出与解绑；所有入口都经服务检查Bot权限。绑定服务还通过组装层注入的Agent目录解析可用发布版本并检查绑定者的`agent:run`权限；名称解析后保存固定版本，后续发布不会自动修改旧绑定。入站消息仍以映射用户重新授权，管理员配置绑定不替代用户执行权限。

HTTP与`Depends(get_bots)`使用同一服务。没有加载聊天插件时，已绑定频道仍通过应用分发器创建Run；Matcher可以先消费特定命令。解绑只影响后来查找绑定的消息，不取消已提交Run，并发替换与解绑使用存储版本检查。当前没有Bot默认Agent、线程继承或复杂条件路由。

身份绑定属于认证关系管理：除`bots.manage`外，调用者还必须拥有目标本地用户上的`accounts:bot_identity:manage`。替换需要同时获准管理原目标和新目标，解绑检查原目标；管理员身份只允许平台管理员绑定、替换或解绑，通配角色授权也不能绕过这一限制。当前接口是显式委托管理，不提供未经外部账号归属验证的自助绑定。

Bot配置创建、更新在解析凭据之前逐一检查`secret:use`，即使只更换URL并沿用原密钥引用也需要授权。修改已有Bot配置还需有权管理该Bot的全部现有身份映射，以防通过改Webhook/连接地址冒用已绑定用户。配置与身份写入在数据库中锁定同一Bot账号并验证已授权快照：并发新增身份或修改配置会使旧授权请求冲突重试，不会绕过检查。普通消息发送者使用管理员已配置的Bot，不需要直接获得其密钥使用权限。

Agent路由的创建、替换与解绑也检查Bot全体身份映射的目标管理权限，避免将管理员的合法入站消息导向未经其授权管理者设置的Agent。路由写入检查已授权身份快照，身份写入反向检查路由快照，并在同一账号行锁下提交，防止并发绕过；快照包含版本与目标，删除后用相同ID重新建立不同目标也不能复用旧授权。读取路由和普通消息发送仍只使用各自的Bot权限。

`BotAccount` 只保存 `id`、`adapter`、`self_id`、`settings`、`credentials`、`enabled`、`revision`。协议设置由适配器自己的模型校验；所有凭据都是具名 `env:` 引用。已有账号的 adapter 和 self_id 不可替换，新机器人须显式创建账号。旧 OneBot 持久行在读取时兼容转换，显式更新后保存新结构。

`internal/service.py` 组装适配器生命周期；`ingress.py` 统一身份映射、Agent路由和入站去重；`delivery.py` 负责消息/文件的权限、幂等和回执；`management.py` 与 `configuration_store.py` 保持账号/身份/绑定的授权快照事务。SDK签名、token、WebSocket和消息编解码均由独立适配器负责。

通用 Webhook 入口为 `/api/v1/callbacks/bots/{id}`，从持久账号选择已注册适配器验签并返回协议ACK；钉钉使用 Stream 主动连接进入相同接收链。迟到的旧配置连接不能派发事件。协议回包与内部 `InboundReceipt` 分别管理，避免将内部结果直接返回给 QQ 等外部协议。

`MessageDelivery.upload_file` 仍通过已授权的工作区读取内容，最多16MiB；当前只有显式启用 NapCat 的兼容适配器实现该能力，QQ/钉钉未实现的文件能力返回明确错误。详见[完整机器人适配器设计](../../../../docs/design/bot-adapters.md)。

公开调用合同位于`api.py`，实现归`internal/`。跨模块只依赖公开合同，`bootstrap/`负责具体实现的组装。当前支持状态与测试证据见[SDK覆盖表](../../../../docs/design/sdk-coverage.md)，职责设计见[架构设计](../../../../docs/design/architecture.md)。
