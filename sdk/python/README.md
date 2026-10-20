# OpsMesh SDK

`opsmesh-sdk` 是 OpsMesh 平台的独立 Python 客户端与扩展合同包。外部客户端、
扩展包和开发工具只依赖本包；它不导入服务端 `opsmesh`。服务端复用本包的纯值与扩展合同。

## 安装

```bash
pip install opsmesh-sdk
```

包提供 `opsmesh_sdk` 导入名和 `opsmesh-sdk` 命令行工具。

包可声明 `ExecutionPolicy(mode="managed_worker", max_concurrency=2)`；管理员安装后，
实例所有者通过 `extensions.deployments` 显式启动和停止。进程保留模块资源，每次
Invocation 仍有独立 context、deadline 和宿主 RPC。`ExtensionExports.worker_startup`
与 `worker_shutdown` 管理连接等资源，接收不到永久平台授权。常驻入口是 SDK 的
`opsmesh_sdk.runtime.managed`，单次入口为 `opsmesh_sdk.runtime.runner`，均由服务端在
隔离容器中启动，不应在 API 中 import 包。当前平台不控制运行网络，采用底层默认行为；
effects 和宿主权限声明不等于网络隔离。

## 平台客户端

登录获得 Session 后，用 `X-Session-ID` 访问平台 API。业务分组客户端由平台
OpenAPI 生成，随对应服务端版本一起交付；生成约定见 `codegen.toml`。当前已
生成 `identity`、`capabilities`、`resources`、`extensions`、
`orchestration`、`channels` 和 `platform` 分组：

```python
from opsmesh_sdk import PlatformClient
from opsmesh_sdk.api.identity import LoginRequest, create_identity_client

async with PlatformClient("https://opsmesh.example.com") as anonymous:
    login = await create_identity_client(anonymous).auth.login(
        LoginRequest(email=email, password=password)
    )
session_id = str(login.session_token)

async with PlatformClient("https://opsmesh.example.com", session_id=session_id) as client:
    identity = client.group("identity")
    profile = await identity.users.read_current()
    quota = await identity.quotas.read_current()
    sessions = await identity.auth.sessions.list()
```

- `identity`：`auth`（登录/注销/改密/Session）、`users`（资料/角色/头像）、
  `quotas`（Token/存储/并发/环境数量额度）、`invitations`（一次性平台邀请，无邮件投递）；
- `orchestration`：用户组装定义/修订、能力绑定、使用配置/邀请、私有 Session/Run、流程、环境、MCP 和触发；
- `capabilities`：定义、不可变版本、包及 `publication_reviews`（提交/本人记录/撤回、管理员列表/决定）；
  用户版本公开需逐版本审核，`publish` 仍可仅用于作者私有使用。通过
  `create_capabilities_client(client).publication_reviews` 调用，具体顺序见
  [公开审核说明](../../docs/capability-publication-guide.md)。
- `platform.audit`：审计/安全记录及幂等追加；`platform.approvals` 和 `platform.policies` 提供通用审批与操作策略。
- 扩展通知合同位于 `opsmesh_sdk.extensions.events`，不提供通用平台事件分派。
- `channels`：账号/连接、身份映射、固定流程路由、Inbox/Delivery 与未知发送核对；
- `platform`：管理员模型连接、加密 Key、逐用户模型授权、运维回执/排空、轮换和健康。

生成物位于 `src/opsmesh_sdk/api/`，服务端 API 变更后由
`scripts/generate_sdk_api.py` 重新生成并做一致性检查，不手改。

## 扩展合同

扩展包声明 manifest，不声明业务实现细节：

```python
from opsmesh_sdk import EffectKind, PackageManifest, validate_manifest

manifest = validate_manifest(
    {
        "package_name": "document-knowledge",
        "version": "1.0.0",
        "description": "Knowledge operations",
        "operations": [
            {
                "export_id": "search",
                "description": "Search libraries",
                "effects": ["read"],
                "permissions": ["resources.files.read"],
            }
        ],
        "permissions": [
            {"name": "resources.files.read", "reason": "read library files"},
        ],
    }
)
```

- `declare_operation` 声明带 effects、输入/输出 schema 的 operation；
- `Continue` / `Patch` / `Block` 表达钩子决策，补丁字段按阶段白名单校验；
- 宿主能力（state、models、jobs、events、observability）经
  `InvocationContext` 的受限分组访问，委托身份由宿主附加，不可伪造。
- `ExecutionPolicy.mode` 为 `sandbox` 或 `managed_worker`；常驻模式的
  `max_concurrency` 为 1–64。常驻部署须通过生成的 `extensions.deployments`
  客户端显式创建/停止，调用仍使用同一套 Invocation API 和效果回执。
- SDK 不自行扩大权限或获取平台凭据；模型调用由宿主按当前用户授权和额度执行，
  管理员模型 Key 不进入扩展上下文。
- 工厂返回 `ExtensionExports`，可选 `worker_startup` / `worker_shutdown` 是
  无参数异步资源生命周期函数。它们没有宿主上下文；每个操作从当前 Invocation
  获取独立的 `InvocationContext`。常驻状态应显式存入宿主 state，不能依赖进程不退出。

## 开发工具

`sources` 负责可配置 GitHub Release 发现、受限下载、摘要校验及通过公开 API 导入。
`composition.parameters` 提供有界参数展开，`composition.templates` 读取固定版本包中的
模板并创建用户定义；服务端重新校验当前授权、固定版本和实际连接，不信任客户端展开结果。

```bash
opsmesh-sdk validate ./my-package/manifest.json   # 校验 manifest 与声明
opsmesh-sdk build ./my-package --target ./my-package.zip
opsmesh-sdk test ./my-package/manifest.json       # 运行声明的合同检查
```

`validate` 和 `test` 接收 manifest 文件路径，`build` 接收包源码目录并需要
`--target` 指定产物路径。构建只打包声明与源码，不执行包代码。

## 依赖合同

本包只依赖 `httpx`、`pydantic` 与 `jsonschema`；不依赖服务端框架、数据库或
Temporal。SDK 内部分层（`transport` 不导入生成的业务客户端、`extensions`
只依赖纯合同、宿主客户端不导入调用上下文）由 import-linter 合同守护。
