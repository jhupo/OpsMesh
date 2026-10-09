# 配置驱动审批

工作空间 `settings.approvals` 是执行与资源审核配置，通过现有工作空间创建／更新 API 保存。
配置修改需要工作空间管理权限；模型凭据必须属于当前工作空间且处于 active 状态。
未配置时默认 allow。allow 只免审批，不免身份认证、资源授权、租户隔离、配额或 Runtime 隔离。

```json
{
  "approvals": {
    "default": "allow",
    "reviewer": "model",
    "opaque_commands": "review",
    "model": {
      "model_provider_credential_id": "当前工作空间的凭据 UUID",
      "model": "Provider 实际支持的模型名称",
      "instructions": "核对用户授权与目标资源。授权明确且操作范围匹配时批准；不匹配时拒绝；证据不足时转人工。",
      "timeout_seconds": 30,
      "on_error": "human"
    },
    "rules": [
      {"id": "publish", "action": "mcp_tool", "name": "publish", "decision": "review"},
      {"id": "push", "action": "runtime_command", "command_prefix": ["git", "push"], "decision": "review"},
      {"id": "force-push", "action": "runtime_command", "command_prefix": ["git", "push", "--force"], "decision": "deny"},
      {"id": "public-skill", "action": "resource", "name": "skill", "visibility": "public", "decision": "review", "reviewer": "human"}
    ]
  }
}
```

示例中的凭据和模型名称必须替换后才能提交。模型并非必需：reviewer=human 时可省略 model。
没有固定审核模型、内置危险词表或按工具名称推断风险的分类器。

## 匹配与决策

- action 支持 model_request、mcp_tool、product_tool、runtime_command、resource。
- name 为精确匹配；MCP 可加 server_id，资源可加 visibility。省略选择器表示匹配该 action 全部操作。
- 命中规则按 deny > review > allow 合并；没有命中时使用 default。
- 多条 review 规则中任何一条指定 human，必须人工审批。
- 工具已有 requires_approval=true 是显式审核要求，仍有效；审核者使用配置。新发现 MCP 默认不强制审批，但仍处于 discovered 状态，必须启用后才能调用。定义变化重新进入 discovered，保留原有显式审批开关。
- 风险等级是注释，不再强制把模型 approve 改为人工审批。平台明确禁用命令或明确阻断标记为高风险的 MCP 仍有效。
- model_request 的 name 为 model.run；默认不审核普通对话、推理或路由。
- 资源创建与市场发布使用 resource 规则；不再仅因 public、stdio、凭据存在等硬编码条件强制审核。

## 命令范围

command_prefix 匹配真实 argv，不是文本包含匹配。支持 sh/bash/zsh/dash 的 -c/-lc 简单线性命令链，拆分后逐个匹配。
配置了命令规则时，无法解析的上述 shell 脚本根据 opaque_commands 转审核或拒绝。
这不是通用脚本静态分析器：Python、PowerShell、cmd、脚本文件及自定义包装程序的内部行为不展开；需要控制它们时，为其实际启动 argv 配置 review/deny，或将 default 设为 review 并逐项放行。

命令等待人工审批时保存 waiting_approval，批准后入队。批准绑定命令记录、Runtime、参数和工作空间审批配置。
执行前重查策略；配置或参数变化不会复用旧批准。禁止的命令不能通过批准绕过。

## 模型审核

审核 Provider 和模型独立配置，可与执行模型不同。发送脱敏后的动作、参数、任务上下文和命中规则，不向审核模型提供执行工具。
返回 approve、reject、needs_human；兼容既有 needs_admin_review 输出。审核结果记录 approval.model_reviewed 审计事件。
超时或 Provider 故障标记 unavailable，按 on_error=human|deny 处理，不冒充明确拒绝，也不自动放行。
模型批准只覆盖当前检查；后续实际执行仍重查授权及配置，可能再次审核。

## 迁移

Alembic 0112 将旧 resource_review 中显式启用的资源审核与 always 模型请求审核转换成人工规则。
不从旧默认名猜测可用模型，不自动填写管理员审核指令。迁移后需要显式配置模型审批。
原始设置保存在迁移备份表；降级恢复旧工作空间设置，会覆盖升级后对此设置的修改。
已有工具 requires_approval 和待审批单不被批量取消。新 API 拒绝旧 resource_review 设置，避免出现保存成功但不生效的配置。

## 本次硬编码排查

移除：输入／工具关键词扫描、Runtime 命令风险词表、内置工具强制审批、固定 codex-auto-review 模型、公开资源必审、SDK approve 被风险等级再次改判。
保留：权限与租户校验、参数 schema、脱敏、不可变审批绑定、Runtime 隔离、平台显式禁用配置。
其他模型默认值（例如记忆 embedding 模型）不属于执行审批，本次没有改动。
