# OpsMesh 钉钉适配器

独立发行包 `opsmesh-dingtalk`，插件 ID 和导入名均为 `dingtalk`。框架启动前依据元数据向 Driver 注册一个钉钉 Adapter，不加载 channels、QQ 或通用 HTTP 入口。

默认账号列表为空：可以验证安装、启用和适配器注册，不会访问平台。业务插件可在自己的 JSON 里通过 `dependencies.dingtalk` 声明一个专属账号，事件只投递给该插件；框架全局配置 `dingtalk.accounts` 定义共享账号。两处均提供 `client_id` 和 `client_secret_ref`（密钥所在环境变量名），账号不能重复。详见[配置契约](../../docs/framework.md#6-插件配置与声明式连接)。插件在 Driver 就绪时为每个启用账号打开 Stream，连接成功后登记 Gateway；退出时停止任务并关闭连接与客户端。消息回复回到原账号。

已移植机器人回调、平台事件、附件、群操作与卡片发送代码；本地测试覆盖无账号加载和模拟回调，不能替代真实账号的协议验收。部署只使用 wheel，并将运行依赖装入服务环境。
