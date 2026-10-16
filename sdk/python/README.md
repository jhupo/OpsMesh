# OpsMesh SDK

`opsmesh-sdk` 是 OpsMesh 平台的独立 Python 客户端与扩展合同包。外部客户端、
扩展包和开发工具只依赖本包；它不导入服务端 `opsmesh`，服务端也不导入它。

## 安装

```bash
pip install opsmesh-sdk
```

包提供 `opsmesh_sdk` 导入名和 `opsmesh-sdk` 命令行工具。

## 平台客户端

登录获得 Session 后，用 `X-Session-ID` 访问平台 API。业务分组客户端
（`identity`、`execution`、`governance` 等）由平台 OpenAPI 生成，随对应服务端
版本一起交付；生成约定见 `codegen.toml`。

```python
from opsmesh_sdk import PlatformClient

async with PlatformClient("https://opsmesh.example.com", session_id=session_id) as client:
    ...
```

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
- 宿主能力（state、secrets、models、jobs、events、observability）经
  `InvocationContext` 的受限分组访问，委托身份由宿主附加，不可伪造。

## 开发工具

```bash
opsmesh-sdk validate ./my-package   # 校验 manifest 与声明
opsmesh-sdk build ./my-package      # 导出 schema、计算摘要并打包
```

构建只打包声明与源码，不执行包代码。

## 依赖合同

本包只依赖 `httpx`、`pydantic` 与 `jsonschema`；不依赖服务端框架、数据库或
Temporal。SDK 内部分层（`transport` 不导入生成的业务客户端、`extensions`
只依赖纯合同、宿主客户端不导入调用上下文）由 import-linter 合同守护。
