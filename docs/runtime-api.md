# 运行与 HTTP API

本文描述当前实现。新增管理、调度/触发器、通知与运维接口见[平台补齐记录](platform-completion.md)及运行实例`/openapi.json`；最新权限与恢复语义见[代码审查修复记录](review-fixes.md)。HTTP 应用入口是 `opsmesh.entrypoints.http.app.create_http_app(application)`；本地账号、业务数据与执行状态由真实模块服务持久化。接口前缀为 `/api/v1`，启动后 `/docs` 提供 FastAPI 交互文档，`/openapi.json` 提供实际请求和响应结构。

HTTP 路由位于 `src/opsmesh/entrypoints/http/routes/`，按 accounts、agents、models、sessions、runs、capabilities、resource_sources、workspaces、memory、bots、scheduler、management、usage 分文件。HTTP 表述、认证依赖、状态码及 SSE 属于统一入口适配层；业务规则和持久化仍由对应模块服务拥有。路由通过 `application.services` 调用公开服务，不跨模块导入 `internal`。这与早期目标树把每个 HTTP 路由放在 `modules/*/internal/http.py` 的布局不同，当前选择便于统一维护入口约束。

## 初始化与启动

需要 Python 3.11+ 与仓库约定的 uv 版本。在仓库根目录：

```bash
uv sync --extra openai
uv run opsmesh init --admin admin
uv run opsmesh check
uv run opsmesh serve
```

`init` 交互读取管理员密码，也可以从部署环境中的 `OPSMESH_ADMIN_PASSWORD` 读取。密码长度至少 12 个字符；初始化管理员要求空用户数据库。`migrate` 执行显式数据库迁移。`serve` 和 `worker` 只校验已有数据库结构，不自动建表；未初始化时启动失败。

默认监听 `127.0.0.1:8000`，数据放在 `.opsmesh`，使用 SQLite。`OPSMESH_DATA_DIRECTORY` 和 `OPSMESH_DATABASE_URL` 可以覆盖路径和数据库连接；支持异步 PostgreSQL。密钥由部署环境提供，模型、Bot、MCP 配置只保存 `env:NAME` 引用。

默认 `OPSMESH_WORKER_ENABLED=true`，网关进程同时驱动持久任务。若网关与后台进程分离，网关进程设为 `false`，独立 `opsmesh worker` 进程设为 `true`，两者使用相同数据库、资源存储和插件包。Run 执行模式由 `OPSMESH_RUN_WORKER_MODE` 决定：`inline`、`subprocess` 或 `docker`；后两者仍需相应运行环境和配置。隔离 Run Worker 不启动平台定时器。默认 inline 模式本身不是操作系统隔离，Agent 原生沙箱是另一层能力。

## curl：登录、创建并运行已发布 Agent

下面使用 Bash、curl 和 jq；Windows PowerShell 可使用 `curl.exe` 并调整变量语法，或直接使用 `/docs`。启动服务后在另一终端运行。示例模型名称由部署者设置，不保证任意账号可用；需要服务进程的 `OPENAI_API_KEY` 环境变量。

```bash
API=http://127.0.0.1:8000/api/v1
read -r -s -p 'Admin password: ' ADMIN_PASSWORD
printf '\n'
TOKEN=$(jq -n --arg password "$ADMIN_PASSWORD" \
  '{username:"admin",password:$password}' | \
  curl -fsS "$API/auth/login" -H 'Content-Type: application/json' --data-binary @- | \
  jq -r .token)
unset ADMIN_PASSWORD

curl -fsS "$API/auth/me" -H "Authorization: Bearer $TOKEN"

# Set MODEL_ID to an available model identifier for this deployment.
read -r -p 'Model identifier: ' MODEL_ID
jq -n --arg model "$MODEL_ID" \
  '{id:"default",model:$model,secret_ref:"env:OPENAI_API_KEY"}' | \
  curl -fsS "$API/models" -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' --data-binary @-

curl -fsS "$API/agents" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"id":"assistant","name":"Assistant","backend":"openai_agents","model_profile_ref":"default@1","instructions":"Answer clearly and briefly."}'

curl -fsS "$API/agents/assistant/publish" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' --data '{"expected_revision":1}'

SESSION_ID=$(curl -fsS "$API/sessions" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' --data '{"agent_ref":"assistant@1"}' | jq -r .id)

RUN_ID=$(jq -n --arg session "$SESSION_ID" \
  '{session_id:$session,agent_ref:"assistant@1",input:"Hello",idempotency_key:"hello-1"}' | \
  curl -fsS "$API/runs" -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' --data-binary @- | jq -r .id)

curl -fNsS "$API/runs/$RUN_ID/events" -H "Authorization: Bearer $TOKEN"
curl -fsS "$API/runs/$RUN_ID" -H "Authorization: Bearer $TOKEN"
curl -fsS "$API/sessions/$SESSION_ID/messages" -H "Authorization: Bearer $TOKEN"
```

`POST /runs` 返回 `202` 表示已接收，不表示模型执行完成。需要观察持久 Run 状态，成功终态为 `completed`；也可能进入 `waiting_approval`、`failed`、`blocked` 或 `cancelled`。验证使用真实 SQLite 服务与确定性测试后端；此交付没有调用付费模型，以上真实 OpenAI 流程需要部署者配置后执行。

`GET /runs` 支持 `principal_id`、`session_id`、`status`、`limit` 和 `offset`。`limit` 默认 1000，范围 1–1000；`offset` 必须非负。查询先在 SQL 中过滤和分页，再逐项检查调用者权限，因此返回数量可能少于页大小。不会为凑满一页扫描全库。内部后台遍历使用稳定游标，流事件按 Run ID 和 sequence 进行有界查询。

升级现有数据库前停止写入并备份，安装后依次执行 `opsmesh migrate`、`opsmesh check` 再重启。Run 与事件流新增索引投影，旧版程序不能与新版混合写入；详见[迁移 0002](../migrations/0002-runtime-projections.md)。

Agent 的 `revision` 是发布版本；`AgentDefinition.revision` 是并发更新版本，两者不同。创建、编辑、发布后应重新读取 `GET /agents/{id}` 获取后续变更的 `expected_revision`。HTTP 运行会解析已发布版本；草稿不能直接执行。模型配置同样保留不可变 `id@revision`，发布 OpenAI Agent 必须引用具体模型版本。

Run 的 `idempotency_key` 在提交主体范围内防止重复提交（会话属于请求内容）：相同内容重试返回同一 Run，不同内容复用键返回 `409`。每次新的用户输入应使用新的键。HTTP 接受字符串或 `role=user` 的消息列表，不允许注入 SDK 工具结果、系统指令或历史执行对象。

## SSE、审批、取消和恢复

SSE 每条事件包含 `id: <seq>`、`event: <kind>` 和 JSON `data`。序号跨执行 attempt 单调推进，不能假设连续。断线后传最后成功处理的序号：

```bash
curl -fNsS "$API/runs/$RUN_ID/events" \
  -H "Authorization: Bearer $TOKEN" -H 'Last-Event-ID: 1000001'
```

也可以使用 `?after_seq=...`；同时提供时取较大值。终态和等待审批状态会结束当前流。默认心跳为 15 秒，按周期重新校验会话令牌与访问权。断线只结束订阅、释放读取任务，不取消 Run。需要显式取消：

```bash
curl -fsS -X POST "$API/runs/$RUN_ID/cancel" -H "Authorization: Bearer $TOKEN"
curl -fsS "$API/runs/$RUN_ID/approvals" -H "Authorization: Bearer $TOKEN"
```

审批先读取最新 Run 和待审批 ID，再提交 `POST /runs/{id}/approvals/{approval_id}`，JSON 为 `{"approved":true,"expected_revision":当前版本}`。批准和恢复是两个操作：对审批响应中的新版本调用 `POST /runs/{id}/resume`，JSON 为 `{"expected_revision":新版本}`。恢复排队后由 Worker 执行。过期版本返回 `409`，其他用户不能操作本人的 Run，授权角色可以执行授予的管理操作。

`POST /runs/{id}/recover` 使用相同版本请求结构，需要显式 `run:recover` 权限；它只处理租约已过期的遗失 Worker。若已有完整输出但提交未完成，会排队恢复提交；否则进入失败并要求检查外部副作用，不会盲目重放工具调用。它不绕过有效租约，也不是任意重新执行失败 Run 的按钮。

## 当前路由范围

| 领域 | 入口与行为 |
|---|---|
| 本地账号 | `/auth/login`、`/auth/logout`、`/auth/me`；`/users` 创建/读取/更新；`/roles`、`/departments` 创建/列出 |
| Agent | `/agents` 创建/列出，`/{id}` 读取/更新，`/{id}/publish` 发布，`/{id}/enabled` 启停，`/agent-revisions/{ref}` 读取及 `/clone` 克隆 |
| 模型 | `/models` 创建/列出，`/{id}` 更新，`/{id@revision}` 读取固定版本 |
| 会话 | `/sessions` 打开/列出，`/{id}` 读取/关闭，`/{id}/messages` 读取历史 |
| 运行 | `/runs` 提交/列出，`/{id}` 状态；`events`、`cancel`、`approvals`、`resume`、`recover` |
| 代码工具 | `/tools` 注册声明/列出，`/{ref}` 读取，`/{ref}/invoke` 通过授权与幂等网关执行 |
| Skill | `/skills` 从已授权工作区目录发布，`/skill-imports/git` 从固定 Git 提交发布，`/skills/{ref}` 读取及 `/{ref}/load` 装载 |
| 脚本工具 | `/scripts` 从已授权工作区发布不可变脚本制品，`/{ref}` 读取；通过 `/tools/{ref}/invoke` 或 Agent 工具绑定执行 |
| MCP | `/mcp` 注册远端 Streamable HTTP 或 SSE 服务，`/{ref}` 读取，`/{ref}/discover` 发现授权工具 |
| 知识 | `/knowledge/documents` 添加文档，`/knowledge/{collection}/search?query=...&mode=lexical\|vector\|hybrid` 检索 |
| 工作区 | `/workspaces` 创建，`/{id}` 读取/删除，`files/{path}` 写入/下载，`materialize`、`snapshots`，`/restore` 从授权快照恢复 |
| 记忆 | `/memory/namespaces` 创建，`/{id}` 读取/遗忘，`content` 读取/更新，`sources`、`generate`、`jobs`、`prune`，`/memory/jobs/{id}` 状态 |
| 机器人 | `/bots` 创建/列出，`/{id}` 读取，`enabled`、`identities`、`routes`、`messages`、`files`；HTTP 签名回调和 Driver 反向 WebSocket 入口 |
| 定时器 | `/schedules` 创建/列出，`/{id}/enabled` 启停持久事件计划 |
| 管理与审计 | `/configuration`、`/configuration/{key}`，`/audit`，受授权保护的 `/metrics` |
| 用量 | `/usage/costs`、`/usage/budget`，`prices`、`resource-prices`、`exchange-rates` 发布计价版本，`reprice/{principal_id}` 补价，`reconcile` 发票行对账 |

不是所有实体都提供删除：发布版本、审计和费用事实需要保留历史，Agent 使用停用，会话使用关闭。按 `/docs` 中实际方法和 DTO 操作。

## Git Skill、脚本工具和检索

`POST /skill-imports/git` 接受 `name`、`version`、`description` 和 `source`，其中 `source.repository` 为 HTTPS 仓库地址，`source.commit` 必须是固定的 40 位提交 SHA，`source.subdirectory` 默认为 `.`。部署者通过 `OPSMESH_SKILL_GIT_ALLOWED_HOSTS=github.com,git.example.com` 配置允许的主机；默认不允许任何远程主机。HTTP 不能导入宿主本地仓库路径。发布需要 `capability:manage`，返回制品 digest、来源仓库和实际提交。所选目录必须包含 `SKILL.md`，导入不执行仓库代码、Git hook 或子模块，拒绝符号链接和越界路径，默认限制 1,000 个文件和 20 MB 内容。相同版本不能覆盖为不同内容。

`POST /scripts` 接受以下结构；先通过工作区文件 API 上传 `main.py` 及需要的 wheel 文件，再发布：

```json
{
  "workspace_id": "script-source",
  "directory": ".",
  "declaration": {
    "name": "double",
    "version": "1",
    "entrypoint": "main.py",
    "input_schema": {
      "type": "object",
      "properties": {"number": {"type": "integer"}},
      "required": ["number"],
      "additionalProperties": false
    },
    "wheels": [],
    "side_effects": false,
    "timeout_seconds": 10
  }
}
```

入口脚本从第一个命令行参数指定的 JSON 文件读取输入，stdout 必须仅输出一个 JSON 值；日志写 stderr。例如 `main.py`：

```python
import json
import sys
from pathlib import Path

arguments = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(json.dumps({"result": arguments["number"] * 2}))
```

发布要求 `capability:manage` 和源工作区读取权限，自动生成 `double@1` 工具声明。执行使用 `POST /tools/double@1/invoke`，请求 `{"arguments":{"number":21},"call_id":"double-1"}`；也可在 Agent 的 `tools` 中绑定同一 `tool_ref` 和输入 schema。执行仍检查 `tool:invoke`，相同调用 ID 的重试沿用工具网关幂等语义。发布后的文件由 digest 固定，修改源工作区不会改变已发布版本。

脚本默认使用 Docker 执行环境，镜像由 `OPSMESH_SCRIPT_EXECUTION_IMAGE` 指定，默认 `python:3.13-slim`。每次执行创建独立虚拟环境，仅安装制品中列出的 wheel，使用 `--no-index --no-deps` 并检查依赖；不会在线拉取缺失依赖。选择 `OPSMESH_SCRIPT_EXECUTION_PROVIDER=local` 还必须设置 `OPSMESH_ALLOW_TRUSTED_LOCAL=true`，该模式只用于可信代码，Python venv 本身不提供操作系统隔离。Debian/Ubuntu 使用系统 Python 时，需要安装对应版本的 `python3-venv` / `python3.12-venv` 以提供 `ensurepip`；仅有 uv 创建的项目环境不能替代此系统组件。

MCP 注册的 `transport` 可以是 `streamable_http` 或 `sse`，`execution_location` 必须为 `remote`，`allowed_tools` 明确列出允许使用的工具。`url` 不能包含凭据、查询参数或 fragment；认证头通过 `header_secret_refs` 引用部署环境，例如 `{"Authorization":"env:MCP_AUTHORIZATION"}`，环境变量提供完整头值。注册成功只保存配置，`/{ref}/discover` 才实际连接服务；发现和调用都重新检查能力授权。模型供应商托管的 MCP 是 Agent 的 `hosted_tools` 中 `type="mcp"` 的独立能力，执行前另查 `hosted_tool:use`、`mcp:use` 和 `secret:use`，不假装经过本地 MCP 工具网关。

知识检索支持 `mode=lexical` 的 BM25、`mode=vector` 的余弦向量检索，以及默认 `mode=hybrid` 的 RRF 合并。响应包含 `score`、`lexical_score` 和 `vector_score`，集合权限在计算向量前检查。默认 provider 是确定性本地特征哈希，不代表预训练语义模型；受信任插件可通过 `get_embedding_registry().register_embedding_provider(provider, default=True)` 注册真正的语义向量 provider，启动后冻结注册。provider 的版本 ID 与维度定义独立向量空间，文档向量持久化保存。

## 实时记忆与费用

创建 `/memory/namespaces` 时设置 `policy.live_update=true`，并在发布的 Agent 中绑定 `memory_namespace_id`，运行会得到 `read_memory` 和 `update_memory` 工具。`read_memory` 返回当前 summary、index 和 namespace revision；`update_memory` 要求 `summary`、`index`、`expected_revision`、`idempotency_key`。所有者和 contributors 可以更新，readers 只能读取，服务每次调用重新验证访问权和策略。审批恢复保持工具身份稳定，每个执行 attempt 单独持有引用，旧 attempt 的清理不会撤销新 attempt 仍使用的工具。

HTTP 实时更新使用 `PUT /memory/namespaces/{id}/content`，结构为 `{"candidate":{"summary":"...","index":"..."},"expected_revision":1,"idempotency_key":"edit-1"}`。并发版本冲突返回 `409`，应重新读取 namespace 与 content 后决定修改。`POST /memory/namespaces/{id}/prune` 按 namespace 的 `source_retention_seconds`、`job_retention_seconds` 清理符合保留策略的来源和任务元数据，不把正在使用的记忆制品当作普通过期文件删除。异步 `generate` 仍是另一条提取、归并、发布链路，其模型调用通过普通 Agent Run 计量。

`POST /usage/prices` 发布模型版本的每百万输入、输出、缓存输入 token 单价；`POST /usage/resource-prices` 发布 `resource_key`、`unit`、`amount_per_unit`；`POST /usage/exchange-rates` 发布 `base_currency`、`quote_currency`、`rate`。三者均使用不可变 `id` 和 `effective_at`，请求发生时选择当时生效的版本，费用使用 Decimal。金额、单位和汇率由部署者配置，不自动同步供应商报价。资源价格只对已采集的用量事实生效，不代表所有容器、存储和供应商成本都已自动采集。

缺价格或汇率的成本为 `unpriced`，没有可信测量的结果为 `unavailable`，两者都不记成零费用。补齐适用于原发生时间的价格后，管理者调用 `POST /usage/reprice/{principal_id}`；累计 token 用量按已保存的增量计费，响应 ID 与资源调用 ID 防止重复入账。已结束且全部关联费用已明确、币种一致的 `pending_pricing` 预留会结算释放；仍在运行或仍有未知费用的预留继续保留。

模型已撤回也可为其历史版本发布价格并补结算。该操作读取历史模型定义，不恢复该版本的推理可用性；不存在的版本仍拒绝补价。

`POST /usage/reconcile` 接受 `provider`、`invoice_id`、`line_id`、`principal_id`、`call_id`、`amount`、`currency`，对比已记录成本，返回 matched、difference、missing_usage、unpriced 或 currency_mismatch。外币账单按原币金额对比，记录来源金额和汇率版本；对账本身不会再次扣费。价格、汇率、补价、对账需要 `usage:manage`；用户可以读取本人的成本和预算，读取他人需要 `usage:read`。

## 权限与入口约束

所有业务路由使用 Bearer 本地会话；Bot 回调使用协议签名而非用户令牌。管理员拥有平台管理权限，普通用户需要授予 `agent:create`、`agent:run`、`model:use`、`workspace:create` 等操作。用户可以读取、更新、发布自己定义的 Agent，并访问、审批、取消自己的 Run；工具、模型、资源仍独立鉴权。列表按所有者或授权过滤。部门和角色采用本地 RBAC/属性检查，没有 OIDC/企业 SSO。

用户、角色、部门管理路由要求 `accounts:manage`；模型、能力、配置、价格和其他用户用量同样要求对应管理权限。授权错误不会带出密码、令牌或服务堆栈。登录有 IP 限流，已认证请求有主体限流。默认请求体上限由 `OPSMESH_MAX_HTTP_BODY_BYTES` 控制。

修改请求先记录审计意图，持久化失败时不执行变更；响应结束后记录结果。结果日志与业务事务并非一个跨服务原子事务：极端情况下可能只留下意图，结果写入失败会记录服务端日志。审计不保存请求密码、原始令牌或完整模型对话。

HTTP 只接受声明式数据：工具实现必须已通过插件注册，或来自已授权发布的不可变脚本制品，不能提交任意 Python 导入路径；`resource:` 内部运行期闭包不能通过 HTTP 重绑。模型、机器人和 MCP 密钥使用 `env:NAME` 引用。HTTP 不创建 host/stdio MCP 进程，也不接受原生沙箱宿主路径、恢复状态、manifest、provider options 或 `run_as`。这些需要受信任部署代码配置。

工作区路径受目录边界检查；下载按附件返回。HTTP materialize 使用内联内容，不允许凭全局 blob digest 读取其他资源。快照恢复先验证源工作区权限，再分配目标所有者。删除工作区保留 ID 所有权，防止其他主体抢占历史标识。

同一主体向同一目标恢复同一快照时可安全重试。未完成的恢复使用私有暂存目录，对普通读取不可见；目录发布后若数据库确认失败，重试完成确认，不覆盖已有文件。其他主体或快照不能复用已分配目标，未完成恢复保留源快照引用。

记忆 namespace 与会话历史、Run checkpoint、原生 SandboxSessionState、工作区快照是不同数据。`generate` 只提交持久记忆任务，不等于生成成功；模型配置、用量与失败应通过任务状态检查。HTTP source 关联的 Run 和会话必须匹配并可访问。

## 机器人和插件

平台通过独立适配器支持钉钉 Stream、QQ 官方签名 Webhook。`GET /bot-adapters` 返回启用实现及其设置 Schema、凭据名称与能力。创建通用 BotAccount（adapter/self_id/settings/credentials）后，通过 `/bots/{id}/identities` 绑定外部用户到本地主体，通过 `/bots/{id}/routes` 将该协议的频道目标绑定到已发布 Agent。配置示例与边界见[机器人适配器](design/bot-adapters.md)。账号、映射、路由与发送幂等状态持久化保存；已处理事件重复到达不会重复派发，每次仍检查本地主体和权限。

绑定是平台`modules/bots`的核心功能，没有加载聊天插件也能触发默认Agent调用链。每个绑定固定一个发布版本；同一Bot可为不同频道选择不同Agent，同一Agent可被多个Bot使用。服务层统一校验`bots.manage`、Agent可用性和绑定者的`agent:run`权限，插件与HTTP入口共享相同规则。

身份绑定额外要求针对目标 principal ID 的 `accounts:bot_identity:manage`，替换或解绑也检查原身份；映射平台管理员要求调用方本身为管理员。对已有身份映射的 Bot 修改连接配置或路由，同样检查这些身份，授权和写入之间的并发变化会被拒绝。Bot 密钥引用在解析前校验 `secret:use`；修改地址并沿用原密钥也需要该权限。只使用 `secret:` 权限命名空间，不转换旧别名。普通发送仍使用 `bots.send`，不会要求消息发送者具有身份管理或密钥使用权限。

| 方法与路径（前缀 `/api/v1`） | 功能 |
| --- | --- |
| `PUT /bots/{id}/routes` | 创建或替换绑定，body为`{"channel_id":"group:123","agent_revision":"assistant@1"}` |
| `GET /bots/{id}/routes` | 列出该机器人全部绑定，要求`bots.read` |
| `GET /bots/{id}/routes/{channel_id}` | 查询指定频道的绑定 |
| `DELETE /bots/{id}/routes/{channel_id}` | 解绑，要求`bots.manage`；不取消已提交的Run |

传Agent名称时由服务解析并保存当时的发布版本；后续发布不会静默升级旧绑定。当前没有机器人级默认Agent或复杂条件路由。未绑定频道不会进入默认Agent链，但可被已加载的Matcher处理。插件可使用`Depends(get_bots)`取得同一`BotAccounts`服务；持久任务使用`Depends(get_scheduling)`取得`modules/scheduling/api.py`的服务。

机器人回调统一使用 `/api/v1/callbacks/bots/{id}`。QQ 支持签名 Webhook 和 Gateway，钉钉使用 Stream；配置项见[机器人适配器](design/bot-adapters.md)。

发送消息携带 `idempotency_key`。确定未发送的瞬态连接失败或限流可以安全重试；本地不支持或供应商明确拒绝保留原错误，同 key 不重复调用，修正配置或内容后使用新 key。读超时、取消和无法判断的协议错误标记 `DELIVERY_UNKNOWN`，不会静默重发。QQ 被动回复可带 `message_id` 与 `delivery_sequence`；协议认证和发送限制见相应适配器文档。测试只使用受控服务和虚构凭据，不连接生产机器人。

`POST /bots/{id}/files` 是协议可选扩展入口：先检查工作区读取权，再检查 `bots.send`，只接受受控工作区路径和安全文件名，上限 16 MiB。当前 QQ 与钉钉未声明文件上传能力，调用会明确返回不支持；第三方适配器可实现 `upload_file`。

[可运行插件示例](../examples/custom_plugin/README.md) 展示 NoneBot 风格 import 注册、元数据、Matcher、Depends、yield 清理、AgentFactory、代码工具、运行 hook 和护栏。`get_run_context()` 只在执行上下文中有效；普通事件处理器使用 `DispatchContext`，不应假定每个事件都有 Bot 或 Run。

网关、后台 Worker 和 Run Worker 必须加载同版本代码包。导入期注册函数与声明，启动后冻结普通代码注册。平台资源编译器拥有受限 `register_runtime` 入口，在 Run 范围生成 Skill/MCP/Knowledge/Memory 工具闭包。插件禁用阻止新调用；正在执行的代码与任意外部副作用不支持自动回滚，升级和失败导入恢复需要重启。这里参考 NoneBot 的插件组织与行为，不声明可以不经迁移直接加载任意 NoneBot 插件。

### 插件的 HTTP / WebSocket 服务端入口

`OpsMeshNetworkDriver` 同时提供 `HTTPClient`、`WebSocketClient`、`HTTPServer`、`WebSocketServer`。插件或 Adapter 通过 `get_driver()` 取得实例，调用 `setup_http_server(HTTPServerSetup(...))` / `setup_websocket_server(WebSocketServerSetup(...))` 声明入口；类型检查时先用 `isinstance(driver, HTTPServer)` 或 `WebSocketServer` 确认能力。声明只保存 async handler，不在导入时监听端口。

`HTTPServerSetup(path, methods, handler)` 的 `methods` 是 `frozenset[str]`，HTTP handler 签名为 `async (HTTPRequest) -> HTTPResponse`。`WebSocketServerSetup(path, handler)` 的 handler 为 `async (HTTPRequest, WebSocketServerConnection) -> None`；先检查升级请求，再显式调用 `await connection.accept()`，之后使用 `send`、`receive`、`close`。请求 URL、headers 和 HTTP 原始 body 可用于协议验签。

HTTP 应用在 `application.start()` 完成、插件和 startup hook 全部加载后，通过 `framework.drivers.asgi.mount_driver_routes` 挂载入口并冻结路由表。startup 注册保留插件归属；导入失败撤销注册，重复路径/方法拒绝启动，`GET` 同时占用 `HEAD`。当前只接受明确的绝对路径，例如 `/plugins/example/callback`，不接受根路径、通配符或路径参数。`/api`、`/docs`、`/openapi`、`/openapi.json`、`/health`、`/metrics`、`/redoc` 及其子路径保留给宿主；挂载时还检查宿主已有路由冲突。

这些入口使用宿主 HTTP body 上限、请求 ID 与结果审计，但**不自动获得业务 Bearer 鉴权或用户身份**。插件/Adapter 自行验证平台签名、令牌、WebSocket 帧尺寸和业务授权，并通过公开服务执行需要审计的操作。插件禁用返回 HTTP 403、拒绝新 WebSocket，并在现有 WebSocket 的后续收发处检查状态；已经开始的 HTTP handler 不会被强行回滚。Driver 停止时关闭入站 WebSocket。该能力已通过本地 ASGI HTTP/WS 回调、禁用、冲突、失败回滚、生命周期和平台 body 上限测试，尚未连接真实远端机器人。

## 验证

```bash
uv run pytest tests/http tests/framework tests/bots
uv run python examples/custom_plugin/demo.py
```

HTTP 测试覆盖真实账号与 SQLite 持久化、跨用户权限、发布版本、幂等提交、审批、工作区恢复、记忆隔离、代码与密钥边界、Bot 签名及重复事件、审计失败前置拦截、SSE 续接与取消/令牌吊销清理。扩展测试实际创建临时 Git 仓库、安装离线 wheel 并执行发布脚本，启动本地 MCP SSE、QQ/钉钉 服务，验证文件上传、断线重连和重复事件。真实 SDK 使用确定性 Model，包含实时记忆审批恢复和并发请求计量回归，没有调用付费模型或发送真实机器人消息。依赖实际 Docker daemon、数据库部署或远端服务的 opt-in 测试需要单独启用并准备环境，默认测试通过不代表这些部署集成已验收。

## 单次 Run 配置

`POST /api/v1/runs` 可附加 `overrides`，发布版本保持不变：

```json
{
  "session_id": "服务端会话ID",
  "agent_ref": "assistant@1",
  "input": "分析项目",
  "idempotency_key": "analysis-001",
  "overrides": {
    "workspace_id": "已授权工作区ID",
    "model_settings": {"max_tokens": 2048, "temperature": 0.2}
  }
}
```

沙箱 Agent 也可改用 `{"sandbox":{"snapshot_ref":"服务端快照ID"}}` 或 `{"sandbox":{"session_ref":"已有沙箱Run ID"}}`；它们与 `workspace_id` 互斥。前者以保存文件创建新沙箱，后者恢复已登记 session；不接受客户端原生状态、宿主绝对路径或凭据。提交和实际执行分别检查资源权限，来源引用不会回退到会话上的其他工作区。

模型设置按模型档案→Agent→Run合并。Run仅开放类型化采样、token/timeout/reasoning/retry等字段；不开放模型地址、认证header、原始SDK对象或任意工具选择。返回的 `agent` 是本次有效配置，另保存 `published_agent_ref`、发布/有效配置指纹和 `overrides`；审批恢复使用此冻结配置。相同幂等键但覆盖内容变化返回409，追踪父span变化不改变业务请求身份。

子Agent独立沙箱的产物记录位于Run的 `metadata.delegated_artifacts`，包含工具调用ID、Agent版本、工作区与快照引用；通过同一工作区API授权下载。父Run暂停和终态前都会持久化这些引用。
