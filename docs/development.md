# 开发说明

## 环境

Python 3.11+，默认3.13；uv 0.13.5。安装Python包不需要Docker、PostgreSQL或模型密钥。首次安装从锁文件获取开发依赖：

```sh
uv sync --locked --dev --extra openai
uv run opsmesh --version
```

可选SDK：`uv sync --locked --dev --extra openai`。只有该extra提供OpenAI SDK；核心包仍可独立安装。全仓类型检查与SDK行为测试使用此extra。`scripts/check_sdk_surface.py`仅检查公开类型，不验证运行语义或调用模型。

## 检查与构建

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
uv run --locked python scripts/check_repository.py
uv build
uv run --locked python scripts/check_sdist.py
```

`check_repository.py`检查文档本地链接、代码块和静态可见的导入边界，覆盖绝对/相对/成员导入及公开API反向依赖；动态import仍需评审。测试覆盖这些边界、安装与CLI、插件/DI/Matcher、HTTP/机器人、持久运行/恢复、SDK替身、工作区并发、记忆、计费和独立Worker。`check_sdist.py`在构建归档中检查本地文档引用。新增功能后增加行为测试，不能将导入成功写成SDK后端验收通过。

`uv build`输出wheel与sdist到`dist/`（Git忽略）。安装包不包含.env、虚拟环境或业务运行数据。修改依赖使用`uv lock`，提交pyproject与uv.lock并说明兼容性；SDK beta行为需要逐项重新验证。

## 代码组织

- `bootstrap`创建实例、挂载HTTP路由、加载插件和资源；它是允许导入模块internal实现的组装根。
- 业务模块只通过对方api/events协作，仓储与ORM表留在所有者internal。
- framework不导入业务模块；SDK类型只在相应backend内。
- 插件导入期登记无IO代码声明，启动时验证冻结并创建资源；模块不反向依赖业务插件。
- 同步回调、异步通知和持久业务提交分别定义，不以“全部事件化”代替必要的返回值与事务。

## 文档与状态

主架构和源码树按实际文件维护。实现一个行为时再添加其代码，不提前堆积成功返回的stub。同步更新路线图、对应设计、测试和变更记录。数据合同/状态格式变化补ADR和迁移。

示例中的目标API应明确标注；只有真实可运行的示例才加入执行测试。真实模型、外部Bot、沙箱和数据库集成测试必须显式开启，并说明环境和费用。临时Redis测试通过 `OPSMESH_TEST_REDIS_INTEGRATION=1` 开启，仅连接测试自己创建的loopback实例。

公共类命名、OpsMesh前缀和文件职责见[命名规范](development-naming.md)；当前真实文件树见[源码目录](reference/source-tree.md)。

## 发布准备

当前没有远程仓库或自动发布工作流。首次公开发布前：设定仓库URL和维护者、启用私密漏洞报告、校验名称可用性、复核许可证、通过CI与实际行为验收。构建工件不自动上传PyPI；不要预填不存在的主页、邮箱或徽章。
