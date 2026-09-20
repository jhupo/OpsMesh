# GitHub 安全与协作自动化

更新：2026-09-20。本文描述本次 PR 提供的配置；合并前不能称为默认分支已启用。

## 工作流

| 配置 | 触发 | 行为 |
| --- | --- | --- |
| Backend CI | PR、master push | Ruff、类型、架构、相关流程与 PostgreSQL 编排检查；不跑全量测试 |
| CodeQL | PR、master push、周一、手动 | Python 与 GitHub Actions 安全扫描，结果上传 Security / Code scanning |
| Dependency Review | PR | 检查新增依赖漏洞，moderate 及以上失败；不发布 PR 评论 |
| Issue and PR Labels | Issue 新建/重开，PR 新建/重开/更新 | 确保标签存在；Issue 待分类及类型标签；PR 按变更目录分类 |
| Dependabot | 每周 | 提交 uv、GitHub Actions、Dockerfile 与 Compose 镜像更新 PR；不自动合并 |
| Release Publish | 发布 tag | 完整测试门禁通过后构建与发布；不因新增安全配置取消既有门禁 |

`uv` 配置维护根锁文件及其本地 operator/runtime 依赖。独立 SDK 的固定发布 URL 和
SHA-256 仍需要维护者在核实发布内容后更新，不能认为 Dependabot 会自动追踪该 URL。
`docker` 负责根目录 Dockerfile；`docker-compose` 显式覆盖根目录开发环境、
`deploy/server` 生产环境和 `deploy/server/monitoring` 监控栈。由运行时环境变量提供的
OpsMesh 发布镜像仍由签名发布清单和更新器管理，不由 Dependabot 选择版本。
Dependency Review 比较 GitHub 依赖图；它不是容器镜像漏洞扫描，也不能证明外部插件安全。

## 权限与外部贡献

源码检查使用 `pull_request`，不向外部 PR 提供仓库密钥。CodeQL 仅增加上传扫描结果所需的
`security-events: write`。所有新增 Action 固定完整提交 SHA，由 Dependabot 提议更新。

标签流程使用 `pull_request_target`，只运行默认分支上的受信任配置，不 checkout、不安装
依赖、不运行 PR 代码；Issue 标题作为数据读取，不插值到脚本或 shell。不会自动关闭 Issue、
批准或合并 PR。标签采用追加策略，维护者手工设置的标签不会被删除。

## 保护规则与后台设置

所有后续源码修改通过功能分支和 PR 进入 `master`。2026-09-20 读取到活动规则集 `opsmesh`
（23715499），但其 `ref_name.include` 和 `required_status_checks` 都为空，且没有
`pull_request` 规则。维护者需选择目标 `master`（或默认分支），启用必须通过 PR 合并，
并选择必需检查。现有规则集没有绕过者；传统 branch protection 查询另行返回 403，不能
据此断言不存在其他保护。本次不修改或绕过用户已有规则。

本次 PR 的新检查产生记录后，维护者应核对规则是否要求 `backend`、`planning-postgres`、
`CodeQL (python)`、`CodeQL (actions)` 和 `Dependency Review`。CodeQL 执行成功表示扫描完成，
不等于没有安全告警。`opsmesh` 已配置 CodeQL 高危及以上告警和 errors 门禁，需正确指定
目标分支才能覆盖 master。
标签任务不应作为合并门禁。

Dependabot、定时任务、Issues 和 `pull_request_target` 工作流以默认分支配置为准；首次合并后
才开始生效。默认 CodeQL setup 若已启用，应切换为本仓库高级 workflow 配置，避免上传冲突。
GitHub 后台的 dependency graph、Dependabot alerts/security updates、Secret scanning 和
Push protection 独立于 YAML。2026-09-20 已通过仓库 Settings / Advanced Security 页面开启，
并确认各项按钮均显示 Disable。Dependency Review 首次运行因 dependency graph 未开启而失败，
已在开启后重跑。默认 CodeQL setup 状态未核实；若出现默认/高级上传冲突需单独处理。

参考：[CodeQL](https://github.com/github/codeql-action)、
[Dependency Review](https://github.com/actions/dependency-review-action)、
[uv Dependabot](https://docs.astral.sh/uv/guides/integration/dependabot/)、
[PR Labeler](https://github.com/actions/labeler)。
