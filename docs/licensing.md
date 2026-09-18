# 许可证与分发边界

状态：当前许可声明，2026-09-18。

| 范围 | SPDX 标识 | 正文 |
| --- | --- | --- |
| OpsMesh 平台、backend、部署及构建脚本 | LGPL-3.0-only | 根目录 `LICENSE`、`COPYING` |
| `operator/`、`runtime/` 的独立 Python 包 | LGPL-3.0-only | 各包目录 `LICENSE`、`COPYING` |
| 外部 opsmesh-plugin-sdk 依赖（不在本仓库维护） | Apache-2.0 | 独立仓库及安装包内的 LICENSE |

未单独标注的本仓库内容采用平台许可。LGPL 指定第 3 版，不自动授权后续版本。
LGPL 是 GPL 第 3 版附加权限的组合，分发时须同时保留 LGPL 和 GPL 正文。

## 商业使用

两种许可证都允许商业使用。平台不是 AGPL，也没有额外添加网络服务源码公开条款。
分发修改后的平台时，须按 LGPL/GPL 适用条款提供对应源码并保留许可和修改声明。
闭源应用可以在满足 LGPL 组合作品条件的情况下使用平台，包括适用的修改、重新组合或
替换库的权利；LGPL 不等于可以无条件闭源分发平台本身。

外部插件仅依赖 Apache-2.0 SDK、通过 API 与平台通信时，SDK 不要求插件源码公开。
复制平台代码到插件中则须另行遵守该代码的 LGPL 条件。Apache 分发须保留许可证、
适用版权及 NOTICE 声明，并标注修改；它不授予项目商标权。第三方依赖适用各自许可证。
许可证全文优先于本页摘要：
[GNU LGPL v3](https://www.gnu.org/licenses/lgpl-3.0.html)、
[GNU GPL v3](https://www.gnu.org/licenses/gpl-3.0.html)、
[Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0)。

## 历史版权与发布包

保留原有 `Copyright (c) 2026 Amber Chiang`，不把原作者署名替换为维护者 Git 用户名。
之前采用 MIT 发布的代码，其原始版权和完整许可声明保存在 `LICENSE-MIT`。
本次许可变更不会撤销此前版本已授予的 MIT 权利，也不改变第三方组件的许可。

各 Python 包的 `pyproject.toml` 声明独立的 SPDX 标识及许可文件，wheel 和 sdist
随包包含正文、NOTICE 和历史 MIT 声明。独立 CLI/server 包在顶层保留平台许可文件，
并保留已安装依赖的 dist-info；容器通过安装的包元数据携带对应许可证。
SDK 已迁往 [独立仓库](https://github.com/jhupo/opsmesh-plugin-sdk-python)，本仓库不再保留
SDK 源码与 Apache 许可证副本。平台分发中实际包含的 SDK 安装包仍保留其自有许可证。
LICENSE-MIT 是历史代码的归属与授权声明，不是本平台的新主许可证；MIT 原文要求在
其代码的副本或实质部分保留版权和授权声明，因此不作为“过期文件”删除。
