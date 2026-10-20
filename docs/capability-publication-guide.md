# 能力公开审核说明

本文说明当前公开 API 的使用顺序。设计依据为 [V4 第 0.5 节](source-refactor-plan-v4.md)，
实际测试与未验证范围见[统一编排交付记录](agent-orchestration-delivery.md)。
以下路径省略默认 `/api/v1` 前缀，普通客户端使用 `X-Session-ID` 登录凭据。

## 发布与公开

1. `POST /capabilities/definitions` 创建能力，通过 `declaration.type` 区分
   tool/mcp/skill/channel/extension；需要包的类型通过版本包接口上传。
2. `POST /capabilities/definitions/{definition_id}/versions/{version_number}/publish`
   静态校验并冻结版本。响应中的 `version_id` 是安装和能力绑定使用的 UUID。
   发布后作者可私有使用，不会自动公开、安装或执行包。
3. `POST /capabilities/definitions/{definition_id}/versions/{version_number}/publication-review`
   提交 `{"submission_note":"审核说明"}`，取得 `review_id`、精确版本及内容摘要。
4. 管理员查询审核列表，读取目标版本和包，提交批准或拒绝决定。
5. 批准后定义成为 public，其他用户可读取和绑定该已批准版本。新版本须重新审核。
   可执行包仍须由管理员单独安装，审核本身不会运行上传代码。

## 审核接口

| 方法与路径 | 权限与用途 |
| --- | --- |
| `GET /capabilities/publication-reviews/mine?limit=50&offset=0` | 本人提交记录 |
| `GET /capabilities/publication-reviews/{review_id}` | 提交者或管理员读取 |
| `POST /capabilities/publication-reviews/{review_id}/withdraw` | 作者撤回 pending 请求 |
| `GET /capabilities/publication-reviews/admin?status=pending&limit=50&offset=0` | 管理员列表；状态支持 pending/approved/rejected/cancelled |
| `POST /capabilities/publication-reviews/admin/{review_id}/decision` | 管理员提交 `{"decision":"approved","decision_note":"通过原因"}`，或 rejected |
| `GET /capabilities/definitions?owned_only=true` | 本人创建的能力；可叠加 type/source/visibility 过滤 |

拒绝或撤回后可再次提交，同版本只允许一个 pending 请求。相同决定和理由重试幂等，
不同决定或理由返回 409。转私有、禁用或删除取消 pending 请求；重复旧批准不会重新公开
已转私有的定义。目录名称和描述可编辑，审核内容固定到不可变版本。

## 访问与唯一事实

公开访问同时要求定义为 public、版本已 published、用户来源版本有 approved 决定。
定义公开不会开放草稿。版本列表、详情、包下载、UUID 绑定、模板导入、通道账号和扩展
使用都检查精确版本权限。作者和管理员可检查草稿；使用配置的私有委托不公开全平台目录。
平台来源 `source=system` 的发布版本与用户版本分别处理，草稿同样受保护。

`platform.approvals` 唯一拥有决定，`capabilities` 验证和应用版本可见性。bootstrap 注入
目标校验及决定应用，按 definition → approval 锁序在同一 PG 事务保存决定、可见性和审计。
不复制审批状态机，也不通过发布审核授予运行或资源权限。

## SDK 与完整合同

使用 `create_capabilities_client(client).publication_reviews` 的 `submit`、`mine`、`read`、
`withdraw`、`admin_list`、`decide`。请求 DTO 为 `PublicationReviewSubmission` 和
`PublicationReviewDecision`。

完整请求、响应及包接口见生成的[能力 API](api/capabilities.md)和
[公开审核 API](api/capabilities-publication-reviews.md)，组装与运行见
[专家创建与执行说明](expert-creation-workflow.md)。本文不作为真实模型、MCP、Docker 或发行验收证据。
