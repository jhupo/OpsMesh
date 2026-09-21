# Frontend 架构

状态：应用骨架与身份认证垂直切片已实现  
更新：2026-09-21

## 基线

`frontend/` 基于 [`satnaing/shadcn-admin`](https://github.com/satnaing/shadcn-admin) 的
`e16c87f` 基线重建，但不保留模板的 Clerk 接入、假用户、任务、聊天、应用、设置演示、
示例数据、单组件测试或 Netlify/GitHub 配置。保留的是经过收敛的应用骨架：

- React 19、TypeScript、Vite 和 Tailwind CSS v4；
- TanStack Router 的文件路由和 TanStack Query 的服务端状态边界；
- shadcn/ui `new-york`、Radix primitives、语义 CSS 变量和 Lucide 图标；
- 响应式侧边栏、默认跟随系统并可切换浅色/深色的动画主题按钮、全局错误边界和请求进度；
- 独立 API 传输边界和可扩展国际化注册表。
- 密码登录、当前用户校验、受保护路由、当前令牌撤销和 401/403/404/500/503 状态页。

保留的上游源码依照 MIT 授权，声明位于
`frontend/licenses/shadcn-admin-MIT.txt`；OpsMesh 修改仍遵循仓库的 LGPL-3.0-only。
主题按钮通过 `components.json` 从 Shadcn Space 注册表导入并适配为受控组件，其 MIT
声明位于 `frontend/licenses/shadcn-space-MIT.txt`。Shadcn Space 只作为源码注册表，
没有形成第二套运行时组件体系。

## 目录和依赖方向

```text
src/
├── api/          # 公共后端传输、错误 envelope；不承载产品策略
├── components/
│   ├── ui/       # shadcn CLI 管理的源组件
│   └── layout/   # 应用外壳、导航、响应式布局
├── context/      # 主题和布局等全局视觉状态
├── features/     # 完整产品切片及其页面组合
├── hooks/        # 跨组件且稳定的 React hooks
├── i18n/         # 语言注册、检测、持久化和翻译资源
├── lib/          # 小型前端无关工具
├── routes/       # 薄路由：解析 URL 并组合 feature
├── styles/       # 全局样式和语义设计 token
└── main.tsx      # Query、Router、Theme 的应用组合根
```

依赖方向是 `routes -> features -> api/components`。路由不实现业务规则，组件不直接读取
后端内部模型，前端只消费公共 API。服务端状态使用 TanStack Query；局部交互状态留在
feature 内。新增 UI 原语必须通过 `frontend/components.json` 和 shadcn CLI 管理。

## 多语言决策

对比了 `shadcn-admin` 的两个开放 PR：

| 方案 | 范围 | 状态（评审时） | 结论 |
| --- | --- | --- | --- |
| [PR #307](https://github.com/satnaing/shadcn-admin/pull/307) | 中文、英文；按页面拆分翻译资源 | 与主分支冲突 | 资源分组清楚，但检测、持久化和扩展范围较小 |
| [PR #311](https://github.com/satnaing/shadcn-admin/pull/311) | 九种语言；浏览器检测、本地持久化、统一语言元数据 | 可合并 | 采用其运行机制和扩展模型 |

OpsMesh 没有复制任何开放 PR 的完整补丁，而是采用 PR #311 的结构：`i18next`、
`react-i18next`、`i18next-browser-languagedetector`、浏览器语言检测、`localStorage`
持久化和统一语言注册表。当前只公开完整维护的 `zh-CN` 与 `en-US`，不会把未翻译语言
显示为已支持。新增语言时必须同时注册语言元数据并补齐所有用户可见文案。

## 身份认证边界

登录页只接受公共 `/auth/login` 合同返回的令牌；令牌保存在标签页级
`sessionStorage`，不会进入持久化浏览器存储。受保护路由在渲染前通过 `/auth/me`
校验当前用户；服务端返回 401 时清除会话并回到登录页。主动退出先撤销当前令牌，再清理
浏览器状态。API 客户端只负责传输、认证头和稳定错误 envelope，不在前端复制后端授权
策略。

2026-09-21 已通过本地 Vite 代理及 SSH 隧道连接服务器安装版，实际验证登录、
`/auth/me`、刷新后保留会话、退出时撤销令牌及再次登录。服务器 API 仅绑定回环地址，
开发连接需保持 SSH 隧道运行；连接方式见 `frontend/README.md`。首页统计仍为占位展示。

## 当前边界

应用外壳采用左侧导航、右侧顶部栏与主内容区的布局。顶部栏由 `AppLayout` 统一挂载，
从左到右排列主题、语言、提醒、消息和当前用户菜单；页面不再各自创建顶部栏。
用户菜单复用真实认证用户及退出流程。提醒和消息目前仅保留禁用入口，尚未接入收件箱
或未读数据，不展示模拟消息及计数。

当前已完成应用外壳、路由、主题、语言切换和身份认证垂直切片。工作空间选择、Agent/团队、
编排、审批、执行、观测和插件管理页面尚未连接公共 API，不能描述为已实现。下一阶段应
选择一个完整产品流，从后端合同、Query client、权限拒绝、加载/空/错误状态到用户可见
结果一次完成，而不是恢复模板演示页。
