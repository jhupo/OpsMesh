# 独立插件服务接入

状态：2026-09-20。平台与 SDK 已通过本地接入流程验证；钉钉插件通过模拟渠道流程。
插件中心已推送至现有远程，SDK 0.4.0 已通过 GitHub Actions 发布；真实模板导入及钉钉消息联调
尚未验收，整个接入任务未完成。

| 验收项 | 当前证据与剩余事项 |
| --- | --- |
| 平台安装凭据、用户授权、消息隔离 | 相关产品流程及 PostgreSQL 验证通过 |
| 插件中心单仓库 | sdk/、plugins/dingtalk/ 已推送到 jhupo/opsmesh-plugin-sdk-python，保留原远程名称 |
| SDK 与插件独立分发 | SDK 0.4.0/插件 0.2.0 的 wheel/sdist、严格元数据检查、独立临时环境安装及三套模板资源检查通过；未执行正式发布 |
| 钉钉卡片资源 | UI、映射、预览已分文件且进入插件包；尚无真实设计器导入证据 |
| 钉钉消息与回调 | 模拟流程通过；真实应用、模板、账号及模型端到端待验收 |
| 平台切换新 SDK 来源 | pyproject/uv.lock 已固定 GitHub SDK 0.4.0 发布 wheel 与 SHA-256，安装验证通过 |

需要运营者提供本机密钥配置位置、测试应用/账号和模板；插件发布还需可写 Actions Secrets 的凭据。
JSON 结构校验、模拟渠道流程、打包成功都不能代替真实钉钉验收。
用户提供的 Linux 虚拟机已确认 Docker 可用；按用户要求仅安装 GitHub 发布包/镜像，
不在虚拟机编译。桥接网络 DNS 曾阻止本地包镜像构建，该构建已停止，失败容器已清理。
当前尚未执行新版发布镜像验收，不以 Docker 替身流程充当容器验收。
代码依赖方向检查 8 项通过；历史 `app-layout-2026-09-14` 快照核对仍失败，
其目标清单未纳入已有插件/授权等新模块。本次不把旧快照改成“已验证当前架构”的证据。

## 本次完整交付范围（2026-09-19 用户确认）

以下全部属于当前任务，子项提交不能作为整个任务结束的理由：

- [x] 插件中心远程目录迁移及平台 SDK 固定发布来源切换；远程仓库保留原名称。
- [ ] 图片、文件、语音接收：平台和 SDK 合同、官方下载、大小/类型/配额、租户私有文件存储、
  授权引用及 OpenAI/Claude 输入映射已实现；仍需钉钉真实媒体消息与模型验收。
- [ ] 群回复：指定群接收人、官方成员检查和实时任务权限已接入插件模拟流程；
  真实钉钉接收人可见性及退群效果待验收。
- [ ] 卡片内审批：API/SDK 与模拟卡片流程已接通，真实钉钉按钮验收待进行。
  引用真实待审批项、重鉴权、幂等决策和现有任务恢复；禁止渠道身份直接授权。
- [ ] 多种卡片与模板选择：任务、审批及结果视图，独立 UI/映射/样例，打包交付。
- [ ] 平台托管插件进程：复用隔离运行时，显式部署/启停/升级/恢复、资源/网络限制及审计；
  API、Worker 和 PostgreSQL 恢复流程已实现并验证，真实容器与发布镜像尚未验收。
  配置刷新不冒充进程热加载，不在 API/Worker 导入插件代码。
- [x] 与现有文本、流式、工具状态、去重和恢复组合的相关产品流程验证：平台消息流程 2 项、
  分发/调度流程 2 项、PostgreSQL 托管进程流程 1 项、插件私发/群发与媒体组合 8 项通过。
  渠道、模型和 Docker 使用替身，此项不包含下面的真实外部验收。
- [ ] 真实钉钉模板导入、发布、收发、按钮及模型工作流端到端验收。

真实联调及发布镜像运行仍待验收；本地实现与验收证据按上表分开记录。

## 边界

- 平台：安装专用凭据、绑定校验、用户授权、私有存储、脱敏日志、消息入口。
- SDK：HTTPX 客户端、数据合同、card.json 参数映射；不依赖平台源码或渠道 SDK。
- 独立钉钉插件：官方 dingtalk-stream/OpenAPI SDK、消息/卡片适配、回调及平台私有存储中的投递恢复。
- 插件服务凭据不是用户身份，不能访问普通用户 API；用户权限由管理员绑定的 sender 决定。
- 不允许任意 SQL、平台数据库连接、宿主机执行或动态插件导入。

## 验收

- [x] 安装范围、权限声明、凭据发放/轮换/撤销；generation 变更、停用、失信后拒绝访问。
- [x] 私有 JSON 存储的 CAS、容量限制、配置引用和日志脱敏。
- [x] SDK 客户端与 card.json 合同、wheel/sdist 构建、独立安装。
- [x] 独立钉钉仓库、官方 SDK、入站去重、持久恢复、卡片更新和回调重鉴权。
- [x] 平台 SDK 接入流程（SQLite/PostgreSQL），不同执行用户、撤权、轮换和重放。
- [x] 插件模拟渠道流程：私发、跨发送人拒绝、重复回调、失败恢复及终态投递失败重试。
- [ ] 真实钉钉接收→真实模型/专家/知识与记忆→真实卡片更新及按钮回调联调。

真实钉钉联调需要运营者的应用、机器人和已发布卡片模板。平台流程使用真实应用、任务/Worker
和数据库，模型采用受控替身；插件独立流程使用模拟平台/渠道响应。两种证据分别记录，
不把它们合称真实外部服务端到端验收。未创建正式发布 tag。

## 平台接口与身份

管理员入口均带 `/api/v1/workspaces/{workspace_id}/plugins/{install_id}`：

- POST `/credentials`：permissions、lifetime_hours（1–2160，默认 24）；明文 token 仅返回一次。
  签发时原子撤销所有旧 token，数据库只保存 SHA256；权限必须同时属于已批准 release 和服务支持集合。
- DELETE `/credentials`：撤销该安装的凭据。
- PUT `/configuration`：`expected_revision` 与非敏感 JSON `value`；更新写审计，仅记录版本。

插件入口是 `/api/v1/plugin-runtime/{workspace_id}/{install_id}`，Bearer 使用 `omp_` 凭据：

| 接口 | 所需权限 | 边界 |
| --- | --- | --- |
| GET configuration | configuration.read | 只读配置；不给出平台密钥 |
| GET storage、GET storage/{key} | storage.read | 按安装隔离；列表每页 100 项 |
| PUT/DELETE storage/{key} | storage.write | CAS；配置保留键不可写 |
| POST logs | logs.write | 结构化、脱敏的持久审计 |
| POST permissions | permissions.read | 绑定自动化的发送人对指定资源的实际动作 |
| POST automations/{id}/events | messages.receive | 只能使用当前 release 绑定的入口 |
| GET automations/{id}/events/{event}/[stream] | messages.read | 只能读取同一安装来源的事件 |
| POST automations/{id}/events/{event}/approvals/{approval}/decision | approvals.decide | 真实成员具备审批权限，审批属于事件的任务；同决定幂等、相反决定 409 |
| POST automations/{id}/attachments | attachments.write | multipart metadata/file；绑定发送人、事件和上传槽，文件私有 |

插件不是 AuthenticatedUser，也没有普通 API 的用户令牌权限。平台信任被管理员批准的渠道插件
验证外部来源，再通过 ExternalIdentityBinding 映射到真实成员。昵称、级别、消息 data 不授予权限。
消息受理、排队执行、会话控制、流式读取继续走已有用户授权服务；没有第二套 Agent 执行器。
凭据撤销阻止后续插件请求；之前合法接受的任务仍按已保存的用户身份重新检查执行权限。
安装停用遵循原有插件调度门禁。已经发送的数据无法追溯收回。

私有存储每项 64 KB，每安装 512 项/2 MB；创建 revision=0，修改/删除必须匹配现有 revision，
冲突返回 409。配置也计入容量。插件应清理已完成历史，不把这里当作无限数据库。
拒绝原始密钥；外部密钥使用部署环境引用，平台不提供任意 SQL、数据库连接或凭据导出。
迁移 `0100_plugin_services` 新增 plugin_credentials、plugin_values 以及消息来源安装复合外键；
已在隔离 PostgreSQL 验证升级→回退→升级，并跑通实际任务流。

## 媒体输入与托管进程

SDK 0.4 新增事件附件引用。当前附件用于 start 消息；follow_up 和控制消息不接受附件。
每消息最多 5 个、每文件最多 20 MiB（仍受平台更小上传限制）、模型输入总计最多 20 MiB。
类型按声明 MIME 白名单检查，不把 MIME 检查说成文件内容安全扫描；文本仅 UTF-8、最多
16000 字符且检查嵌入密钥。每安装最多 512 个活跃附件、总计 512 MiB。
相同事件/发送人/槽重复上传返回原引用，内容或元数据变更返回 409。文件沿用平台文件
删除与保留策略；没有把删除投递记录等同于删除用户文件。

上传、受理、派发和模型执行均检查身份/文件权限。执行前再次检查实时外部身份绑定，
正文只在模型边界读取并核对存储摘要。文本和语音转写进入已有上下文预算；图片/PDF 使用
厂商原生多模态输入格式，其实际模型可用性和二进制 token 消耗仍受所选模型约束。
加密 SDK 续接状态上限为 40 MiB，容纳最多 20 MiB 媒体的 Base64 与已有上下文；仍拒绝超限状态。
语音当前使用钉钉识别文本，原音频私有保存；没有转写的音频拒绝，不冒充模型原生语音识别。

管理员通过安装路径 PUT `/deployment` 提交 `expected_revision`、`template_id`、
`platform_url`、`permissions`、`environment`、`desired_state`；GET 查询状态，
POST `/deployment/stop` 请求停止。首次 revision=0；修改必须带当前版本。返回 202 仅表示
持久化受理，实际启动由 Worker 执行。环境值加密保存，查询与审计不返回密钥。
模板必须活跃、镜像固定 `@sha256`，网络策略明确开放；限制沿用模板和运行时配额。
restricted 模式仍需部署者提供已有受管出口网关，平台不会凭一个域名列表自动建立防火墙。

一个安装绑定一个独占 persistent 运行时，不进 Agent 池、不接受 Agent 绑定或交互命令。
复用现有非 root、只读根文件系统、资源限制、卷、租约和清理实现；插件通过镜像入口运行，
不在 API/Worker 宿主进程 import。PostgreSQL 安装级锁串行化配置与维护；容器创建前持久化
运行时身份，丢失创建响应后按名称、租户标签和镜像恢复。单次失败只保存脱敏错误码。
凭据有效期 24 小时，剩余一小时内通过销毁重建轮换；外部主动撤销不会自动重新发凭据。
失败/非正常重启累计最多 8 次，需要管理员重新提交部署配置重试。
停止、安装停用、发布者失信、操作者失权或模板策略变化触发下一次维护清理；清理失败保留
状态并重试，不宣称容器已删除。安装 generation 改变后需要重新明确批准部署配置。
进程更新是串行替换，存在短暂停机；渠道重投递与平台持久状态用于恢复，不承诺零停机。
`running` 表示容器进程正在运行，不代表已验证钉钉连接、应用权限或卡片渲染正常。

迁移 `0101_plugin_deployments` 已验证升级→回退→升级。受影响的 PostgreSQL 产品流程
验证受理、失联创建恢复、服务凭据、停止与信任撤销；Docker 调用使用替身，不代表真实容器验收。
插件中心发布流程以门禁 wheel 和锁定依赖构建镜像，生成来源证明，并把镜像摘要放入
SDK 0.4 的受签 release descriptor v2。目录下载不会自动部署，管理员必须另行审核运行模板。

## 独立仓库与模板

平台依赖已切换到 GitHub `sdk/v0.4.0` 发布的 wheel，SHA-256 为
`8a34d4fa69675520dfb4a57d69ad0ecc3c981b77483d68be4550a0361dfb8461`，不复制源码。
新的本地维护目录为 `../opsmesh-plugin-center/sdk` 和
`../opsmesh-plugin-center/plugins/dingtalk`，根目录统一开发锁和 CI，各包独立版本与产物。
当前版本分别为 SDK 0.4.0、钉钉插件 0.2.0；平台已从 GitHub 安装 SDK wheel 验证新合同。
旧 SDK/钉钉目录暂保留迁移前副本，不应并行维护两套实现。
插件没有硬编码订单业务：领导、日志专家、工具、知识和记忆由平台工作流配置。

钉钉插件的 `card-templates/task/v1/card.json` 保存 UI 设计器数据，当前未验证实际导入。
同目录 `mapping.json` 才是 OpsMesh 的数据合同：channel、已发布 template_id、变量及按钮映射；
`preview.json` 是不含真实用户数据的预览参数。
文本/状态映射到厂商变量；不执行模板代码。按钮支持 pause/resume/cancel 与 approve/reject；
审批按钮携带当前 approvalId，平台重新校验安装权限、成员审批权限及事件与任务归属。
task、approval、result 三套 UI/映射/预览位于各自 v1 目录，仍待真实设计器验收。
钉钉回调的 `content.cardPrivateData.params.action` 对应模板 actions 键；卡片、会话、原发送人及
实时权限必须匹配。默认群输入的结果私发给原员工；显式 group_recipients 模式使用固定名单，
每次发送前检查官方群成员和每人的任务 read 权限，名单变化拒绝继续向旧卡片追加内容。
所有模式禁用转发；真实群可见性需单独验收。
模板原生 UI 仍需在钉钉设计器创建和发布；不是导入映射文件就自动创建云端模板。

投递游标在成功更新卡片后保存；终态消息失败仍可重试。记录使用 CAS 租约，进程重启恢复，
超过有限重试后保留 blocked 状态供处理。配置每 30 秒刷新，但既有卡片保留原模板快照。
身份映射/授权撤销在下一次读取或控制请求生效，不依赖配置刷新周期。

## 依赖决策

复用官方 [dingtalk-stream](https://github.com/open-dingtalk/dingtalk-stream-sdk-python)
处理 Stream 连接、消息与卡片回调；卡片与 OAuth 复用官方 alibabacloud-dingtalk OpenAPI SDK。
Stream 的卡片便捷方法会吞掉部分错误，连接请求缺少超时且会捕获取消信号，因此不将其返回值
当作投递成功：卡片用有显式超时的 OpenAPI 方法；Stream 在受监督子进程运行，心跳超时后重启，
自动重启最多三次。没有重写钉钉协议或 monkeypatch SDK。
OpenAPI wheel 约 2.2 MiB，只在独立插件环境安装，不进入平台或 SDK 依赖。
平台授权和存储使用现有 SQLAlchemy、PostgreSQL 和脱敏/审计服务，SDK 继续使用 HTTPX/Pydantic。
