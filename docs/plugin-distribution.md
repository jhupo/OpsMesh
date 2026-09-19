# 远程插件分发与安装闭环

状态：2026-09-19。目录下载与安装保持数据边界；隔离进程部署由管理员另行批准，
见[插件服务与部署](plugin-service-integration.md)。插件中心远程迁移尚未完成。

## 实施项

- SDK 0.4 开发版的 descriptor v2、目录合同、双层签名、发布命令及分包工作流。
- 工作区来源配置、固定摘要目录同步、不可变候选版本。
- 后台持久化下载请求、租约恢复、安全 HTTPS 拉取与内容校验。
- 预览权限/配置差异、显式安装、升级/回滚及目录撤回。
- 现有产品流程验证；真实 PostgreSQL 迁移与双 Worker 领取验证。

## 设计边界

目录是数据，不包含可执行代码。管理员通过 URL + SHA256 批准精确目录快照，
目录条目再固定 release JSON 的 URL、SHA256、插件 key/version 与发布者 key。
release JSON 包含已签名 manifest、SDK/平台版本范围和发布者签名。
平台既验证目录摘要又验证管理员预先信任的插件发布者；目录不能自授信。
私有来源指工作区私有配置；网络端点必须为公开 HTTPS，不开放内网或 URL 凭据。

下载请求保存 Postgres，Worker 独立维护阶段领取有期限的租约；在网络 I/O 前提交事务，
完成后按租约 token 和来源版本校验再落库。失联请求可在租约过期后重新领取，
失败可显式重试；失败不会改变现有安装。安装继续调用 PluginService，不创建第二套执行引擎。

采用现有 HTTPX 传输、Pydantic 合同、cryptography Ed25519 与 packaging 版本判断。
网络适配器固定解析后的公网 IP，以 HTTPX 的 SNI/Host 保持 TLS 身份校验，逐跳校验重定向，
禁用代理继承，限制响应大小、编码和超时。拒绝下载源码、wheel 或 OCI 执行。

下载成功只产生可审核候选。管理员查看权限/绑定差异后安装，升级使用已有 generation CAS；
目录删除或撤回条目阻止新的安装，不静默卸载正在使用的版本。紧急停止继续使用 disable
或撤销发布者公钥。SDK 和业务插件独立版本、独立 tag；插件中心中 sdk/ 与 plugins/ 分开维护。

## 发布与目录

SDK 唯一源为 [opsmesh-plugin-sdk-python](https://github.com/jhupo/opsmesh-plugin-sdk-python)。
平台固定完整提交及 uv.lock 摘要，不复制 SDK 实现。SDK 的 `opsmesh-plugin-publish` 命令
产生 SignedPluginRelease：受签 manifest、平台/SDK 版本范围、许可证、源码仓库与 commit。
新插件中心的根 `.github/workflows/release.yml` 按 sdk/v* 或 plugins/<name>/v* 发布；
插件使用 CI 门禁产物构建 OCI 镜像，descriptor v2 签名覆盖 `container_image` 固定摘要。
目录仍是 contract_version=1；描述合同升级到 v2 不增加旧合同兼容分支。
仅 tag 发布，私钥不进 PR。远程地址与平台依赖切换状态以接入文档为准。
SDK 的 wheel/sdist 发布与插件 release.json 发布各自独立，本次没有打 tag 或上架 PyPI。

目录 JSON 合同如下。摘要必须由已审核的准确文件字节计算，不能填写下面的说明文字：

```json
{
  "contract_version": 1,
  "entries": [{
    "plugin_key": "example.connector",
    "version": "1.0.0",
    "publisher_key_id": "publisher-2026",
    "release_url": "https://plugins.example.com/example.connector/1.0.0/release.json",
    "sha256": "替换为 release.json 准确字节的 64 位小写十六进制 SHA256",
    "withdrawn": false
  }]
}
```

公共发布继续经过 Marketplace 审核；新增 sources 是管理员审核的工作区私有入口，不是公共
发布的绕过路径。相同 source/plugin_key/version 的 URL、摘要和发布者不可更改；即使条目
从目录消失，身份仍保留。发布内容错误须发布新版本。独立公共目录运营仓库尚未建立。

## 管理员操作

所有路径带 `/api/v1/workspaces/{workspace_id}/plugins` 前缀，并要求工作区 ADMIN 权限。
先配置远程 MCP、技能、自动化、回复订阅等资源，并从可信渠道确认发布者公钥；通过既有
`/trust-keys` 注册的公钥只授权精确插件身份，目录数据不能自行增加信任。

| 方法与路径 | 输入与结果 |
| --- | --- |
| POST /sources | name、url、sha256、allowed_hosts、enabled；登记审核过的目录快照 |
| GET /sources | 分页查看来源、generation 与 synced_generation |
| PUT /sources/{id} | 完整配置及 expected_generation；禁用也用此入口 |
| POST /sources/{id}/sync | request_key；返回 202 与持久化下载任务 |
| GET /candidates?source_id=… | 分页候选、摘要、撤回状态与下载后的受签描述 |
| POST /candidates/{id}/download | request_key；返回 202，不安装、不激活 |
| GET /downloads 或 /downloads/{id} | queued/fetching/succeeded/failed、尝试次数、脱敏错误码 |
| POST /downloads/{id}/retry | 仅重试失败且来源未改版的请求；仍需当前管理员权限 |
| POST /candidates/{id}/preview | bindings（可先为空）；返回权限增减、Schema、当前/拟绑定配置和 preview_digest |
| POST /candidates/{id}/install | 完整 bindings、approved_permissions、preview_digest、expected_generation |

安装的 preview_digest 必须来自**相同完整 bindings** 的预览。预览后来源、当前安装版本或
资源配置改变会返回 409，必须重新审核；绑定不全/权限未全量批准仍拒绝。升级使用新版本与
独立资源绑定，原版本有依赖时不得退役。回滚走已有 `/plugins/{install_id}/actions` 的
switch_version，不重新覆盖包。预览不是后台自动审批，也不赋予 Agent 额外工具权限。

`request_key` 在工作区内唯一。相同键和相同固定来源版本/候选重复提交返回同一任务；
换目标或来源版本则 409。重复安装不会重复创建 release，返回冲突后查询既有安装确认结果。
编辑来源后必须重新 sync；synced_generation 不匹配时不允许新下载或候选安装。
目录中删除条目或 withdrawn=true 会阻止新下载/安装，不改变已安装或在途任务的既有绑定。

## Worker、安全和恢复

`plugins/distribution.py` 拥有来源/目录/预览策略；`plugins/downloads.py` 拥有下载状态机；
`plugins/transport.py` 是 HTTPX 适配器。现有 Worker maintenance 每轮处理至多一个下载，
Postgres 的 queued 记录就是持久化 inbox，不依赖一次性 Redis 消息。

领取使用 FOR UPDATE SKIP LOCKED，提交 2 分钟租约后才做网络 I/O；保存结果前重新校验
租约 token、来源 generation、当前用户/成员权限和撤回状态。超时或网络失败按 30/60 秒
延迟重试，最多 3 次；进程失联同样有限恢复。明确失败可由管理员 retry；配置变化后应重新
提交新 request_key。错误仅保存固定错误码，不保存网络响应、凭据或异常堆栈。

仅 HTTPS 443、精确 allowed_hosts、公网解析地址；连接固定到已检查 IP，Host/SNI 保留原域名，
禁止环境代理和 URL query/凭据。最多 3 次重定向且每跳重新检查；目录上限 1 MiB、发布描述
256 KiB；仅 identity 编码；HTTP 操作 timeout=5 秒，传输检查 30 秒总预算。DNS 解析仍受
操作系统 resolver 超时约束。GitHub Release 常用带签名 query 的重定向不接受，应使用固定
commit 的 raw.githubusercontent.com 地址或可信静态镜像，不能直接填 GitHub HTML 页面。

下载先比对精确字节 SHA256，再验证目录身份、内外签名、已装平台/SDK 的兼容范围。JSON
经 Pydantic 校验与敏感字段拒绝后才存入数据库。下载和预览都不执行 manifest 的任何代码。
来源、下载、重试和安装操作复用已有审计服务；公钥撤销及插件禁用复用实时执行门禁。

## 迁移与验证

`0098_plugin_distribution` 新增 sources、candidates、downloads，包含租户复合外键、版本及
请求幂等唯一约束、状态检查和领取索引。降级删除这三张分发记录表，不删除既有插件安装；
降级前停 Worker 并备份分发数据。

真实空 PostgreSQL 18 升级发现旧 `0088` revision 超过 Alembic 默认 32 字符列；该迁移现先
将 version_num 扩至 128，再让 Alembic 记录版本。此元数据宽度在降级时保留，避免版本登记
再次截断；没有改变任何历史 revision ID 或增加兼容入口。

验证复用 `test_configured_automation_admits_workflow_and_delivers_reply` 的消息/定时两条完整
流程：创建团队和编排→目录→下载→预览审批→安装→入站→任务终态→签名回复→升级回退。
包括损坏字节、私网重定向、失联恢复、重复提交、跨工作区、预览过期、权限缺失、目录撤回、
公钥撤销。消息流程另在真实迁移后的 PostgreSQL 上执行，双 Worker 验证同一任务只下载一次；
迁移从空库到 head、0098 降级和重新升级通过。HTTP/渠道使用模拟传输，未声称真实渠道验收。
只执行相关流程及 Ruff、mypy、架构/编译检查；未执行全量 pytest。

## 范围以外

目录分发不包含 Web 页面、私网/带凭据的分发服务或公共目录运营。业务插件在独立插件中心维护；
进程托管由显式部署 API 负责，不通过下载目录隐式执行。进程替换与配置刷新分别处理，
不提供 API/Worker 内的 Python 热加载。当前真实运行验收状态见插件服务接入文档。
