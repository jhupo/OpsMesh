# 发布

核心 `opsmesh` 和四个官方插件分别构建 Python 包。采用同步版本：五个 `pyproject.toml` 的版本必须相同，Release tag 使用 `v<version>`。

版本约定：从 `v0.5.0` 起，常规功能与修复均递增为 `0.5.1`、`0.5.2` 等，保持 `0.5.x`。只有明确决定进行大版本调整时才切换版本系列；目录重构或普通功能新增本身不自动升级版本系列。

## Release 附件

一次成功的 tag 发布包含：

- `opsmesh`、`opsmesh-channels`、`opsmesh-execution`、`opsmesh-filesystem`、`opsmesh-database` 各自的 wheel 和源码包。
- Linux x86_64、aarch64 的框架可执行包。
- 同版本、同架构的可选官方插件运行包，内含四个插件、资源和额外依赖。
- `install.sh`、`SHA256SUMS` 及工作流生成的构建来源证明。

wheel 用于普通 Python 环境；可执行宿主使用独立插件运行包。安装器只部署框架。GitHub Release 与 PyPI 是两种发布目标，当前流程不自动向 PyPI 上传。

## 本地构建

在隔离构建环境中安装核心与构建依赖，再构建 Python 包：

```bash
python -m pip install -r requirements.lock -r packaging/requirements.lock
python -m pip install --no-deps --no-build-isolation .
python scripts/build_packages.py --output dist/python
```

输出包含五个 wheel 和五个源码包；构建脚本会检查版本一致性和 wheel 内容边界。

在目标 Linux 架构上继续构建与验收：

```bash
python scripts/build_release.py
python scripts/build_plugins.py
python scripts/smoke_release.py dist/opsmesh/opsmesh --plugin-path dist/plugins/site
```

`build_release.py` 和 `build_plugins.py` 都接受 `--output DIR`，默认 `dist`。后者读取同目录的 `opsmesh/BUILD.json` 和 `python/` 轮子，因此前面的 Python 包构建应显式使用 `--output dist/python`。插件构建使用 `packaging/plugins.lock`，宿主使用 `requirements.lock`，构建工具使用 `packaging/requirements.lock`。

框架解包目录为 `dist/opsmesh`；插件解包目录为 `dist/plugins`，内含 `site/`、构建信息、说明与许可证。两个归档的顶层名称分别对应它们的归档文件名去掉 `.tar.gz`。Linux 插件运行包必须与框架使用同一版本、架构和 Python ABI，不能在 Windows 交叉打包。

发布前审查包内容，确认核心 wheel 没有插件源码或插件依赖，插件 wheel 包含其资源与迁移脚本，源码包不夹带环境、备份、密钥或旧构建结果。wheel／源码包和可执行包都应有各自的安装或运行检查。

## 维护者流程

1. 更新核心与四个插件的版本，以及各插件 `requirements-core.txt` 中匹配的宿主版本；更新 `CHANGELOG.md`，并同步需要变更的锁文件。
2. 在 Pull Request 中通过 CI、Python 包检查以及两种架构的冻结产物验收。
3. 将已审查的提交合并后创建并推送对应 tag，例如 `v0.5.0`。
4. 检查 Release 工作流的版本校验、构建、测试、校验和、来源证明及附件上传结果。
5. 核对五个包和两种架构附件齐全后，使用实际发布附件进行安装验证。

不要通过移动已发布 tag 替换现有包；修复应使用新版本。撤回有缺陷的发布时记录原因，说明宿主与插件的匹配要求和数据迁移影响。

## 工作流边界

`ci.yml` 验证源码、依赖与包边界；`build.yml` 执行 Linux 原生构建和产物验收；`release.yml` 在 tag 与全部版本一致、依赖任务通过后发布附件。工作流文件存在不代表已有成功发布，本地测试也不替代 GitHub runner 的实际结果。

发布仓库为 [jhupo/OpsMesh](https://github.com/jhupo/OpsMesh)。发布前确认 Actions 权限、ARM64 runner 可用性及安全报告渠道。
