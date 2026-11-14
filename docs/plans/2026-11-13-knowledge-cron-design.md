# Knowledge 与 Cron 官方插件设计

状态：实现与定向验证已完成，外部集成验收单列。实现基线：`8e0c529`，日期：2026-11-13。当前公共接口以各包 README 和通道契约为准。

本方案新增两个独立官方包，沿用现有框架、database、filesystem 与 execution 的职责。

chat/sandbox 已采用 channels 统一消息入口，会话操作走命令；复用 Matcher 的 `send/finish/pause/reject`，支持多次回复、临时 Matcher 续接及通道对话隔离。knowledge 和 cron 公开 Python 对象及 Matcher 声明接口；管理命令由调用插件组合并授权，不自动开放。知识库全库重建使用持久化目标指纹和索引代次恢复。真实 PostgreSQL/pgvector 测试需显式配置隔离环境；真实模型、机器人、Langfuse 和 Linux 产物另行验收。

## 1. 结论与边界

| 插件 | 负责 | 不负责 |
|---|---|---|
| execution | 原生 Runner 接入、框架回调、Trace 导出 | 知识库、定时计划、用户额度、会话业务 |
| knowledge | 文本文档、切分、向量索引、检索、可选命令、原生 SDK 工具 | Agent 构造、聊天会话、模型供应商、通用任务平台 |
| cron | 定时计划、触发、框架处理器适配、计划启停 | 智能体选择、提示词、机器人发送策略、可靠工作流 |
| database | PostgreSQL 连接、事务入口、插件迁移 | 上述插件的数据模型和查询逻辑 |
| filesystem | 插件文件空间、文件操作与路径边界 | 文档目录、知识库归属、向量和索引状态 |
| 框架 | 插件加载、配置、生命周期、调用持有、事件、Matcher、Dependent | 模型推理、向量检索、cron 表达式计算 |

```text
功能插件
├── require("knowledge") ── database + filesystem
├── require("cron") ─────── database
├── require("execution") ── Agents SDK + tracing
└── require("channels") ─── HTTP / QQ / DingTalk

以上插件共同使用框架的归属、依赖和生命周期。
knowledge 和 cron 之间没有强制依赖。
cron 不依赖 execution；需要执行 Agent 的处理器自行调用 execution。
```

发行包为 `opsmesh-knowledge`、`opsmesh-cron`；导入路径为 `opsmesh.plugins.knowledge`、`opsmesh.plugins.cron`。使用现有 entry point 提供短名，跟随项目 `0.5.x` 发行约定。

## 2. execution 的 Langfuse 现状

### 2.1 已有支持

当前代码链路：

```text
execution.run / execution.stream
→ 原生 Runner 与 SDK Trace/Span
→ OpenInference
→ execution 独占的 OTel TracerProvider
→ OTLP HTTP
→ Langfuse
```

核对位置：`packages/execution/src/_tracing/`、`runner.py`、`docs/langfuse-tracing.md`。

| 能力 | 当前情况 |
|---|---|
| Agent、模型、工具等运行追踪 | 接收 SDK/OpenInference 实际生成的 span，不复制一套执行事件 |
| Token、耗时、异常 | 使用上游 span 中已有的信息；供应商未返回用量时不能补造 |
| Users / Sessions | 支持通过 `TraceContext.user_id/session_id` 关联 |
| 标签和插件归属 | 已传播 tags、plugin_id、sdk_trace_id、environment |
| 流式执行 | 观测作用域覆盖消费与清理 |
| 敏感输入输出 | 默认限制采集；不等于自动清洗任意自定义 metadata |
| 成本 | 依赖 Langfuse 模型价格配置与识别，不能保证自定义模型自动计价 |
| Prompts / Skills / Playground | 不由 execution 管理；启用追踪不会自动上传或启用这些内容 |
| 本地诊断 | 有目的地及有界摘要；不能据此证明远端已经入库 |

这与 Langfuse 的 [Agents SDK 集成方式](https://langfuse.com/integrations/frameworks/openai-agents)一致。当前 OTLP 使用独立 provider，并按[官方协议](https://langfuse.com/integrations/native/opentelemetry)发送实时摄入 header。

### 2.2 导出与关闭契约

1. **关闭失败后的资源状态。** `LangfuseBridge.shutdown()` 在确认释放后清空引用。仍在关闭的后台线程保留可等待对象，后续停止复用同一个任务；未确认释放则向生命周期抛出失败。普通导出网络失败不改变 Agent 结果。
2. **导出结果诊断。** 原生 exporter 的薄代理记录最后结果、批次计数和时间，错误只保留固定代号，不保存异常文本或敏感 span。导出成功仅表示接收端接受请求，服务端查询健康仍需单独验证。
3. **审批继续的版本限制。** 当前锁定 OpenInference 对 SDK `ReattachedTrace` 有丢失恢复 span 的已知边界。调用插件需要完整观测时，显式为继续运行指定新的 SDK trace ID，保留同一 session/group ID；execution 不静默改写恢复语义。升级依赖时定向验证。

本次未访问部署服务器、未发送真实模型请求，不能用本次源码核对认定线上 Langfuse 健康。

## 3. Knowledge 插件

### 3.1 对外提供什么

提供知识库和文档的创建、查询、更新、删除，文档索引状态与重建，自然语言检索、原文读取，以及可以直接加入 `Agent.tools` 的原生工具。

输入支持 UTF-8 纯文本、`.txt`、`.md`。Markdown 作为文本解析，不执行代码块或 HTML。PDF、Word、网页抓取、ZIP 安装不属于本接口。

knowledge 的主要消费入口是 Agent.tools 中的原生 SDK 工具。插件同时提供直接调用的 Python 对象；调用插件可组合并授权自己的管理命令。工具调用不经过 Matcher；管理命令与通知也不会自动进入模型输入。

channels 负责认证、文件接收、协议解析和入口绑定，将 HTTP 或机器人输入转换成框架事件/消息；调用插件选择开放哪些管理操作及其访问范围，不重复编写知识库业务处理。knowledge 接收经过校验的文本或受控附件，不依赖 FastAPI，不自行监听端口。

### 3.2 模型与切分

固定使用 `BAAI/bge-small-zh-v1.5`，通过 SentenceTransformers 加载，复用 tokenizer 和编码实现。模型权重锁定到明确 revision，记录权重校验信息，不在首次查询时隐式联网下载。

- 向量为 512 维，数据库使用 `vector(512)`。
- 以 tokenizer token 数控制长度，模型最大序列长度 512；查询提示和特殊 token 计入限制。
- 默认每块约 400 个内容 token，重叠约 64 token；这是本项目可调默认值，不是模型强制规则。
- 优先在段落和句子边界切分，超长段落按 token 切分；保留规范化文本中的字符起止位置。
- 文档不添加检索提示；查询使用模型建议的中文检索提示，向量归一化。
- 空查询拒绝；过长查询明确报长度错误，避免静默截断掉用户意图。
- CPU 编码放入受限后台执行器，默认单工作槽；批次大小、线程数、待处理容量有上限，避免阻塞事件循环。

模型能力与用法依据 [BGE 官方模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5)。相似度不是正确率，不承诺一个通用阈值适用于所有知识库。

模型、tokenizer、revision、归一化与切分参数组成索引指纹。参数改变要求重建索引；不能将不同指纹的向量混合搜索。这是索引一致性要求，不引入面向用户的版本中心。

启动时校验当前编码器与已有库指纹；不匹配的库显示需要重建，并拒绝使用新查询向量搜索旧索引。重建期间同样保持不可检索，完成后再切换库的索引指纹。

### 3.3 数据和文件

使用 SQLAlchemy `Mapped` 模型及 `pgvector.sqlalchemy.Vector`。迁移由 knowledge 自己声明，连接和迁移执行复用 database。

| 表 | 主要字段及作用 |
|---|---|
| `knowledge_bases` | `id`, `owner_plugin`, `name`, `description`, `active_profile`, `target_profile`, `index_epoch`, `index_status`, `metadata`, 时间字段 |
| `knowledge_documents` | `id`, `knowledge_base_id`, `name`, `media_type`, `source_path`, `content_hash`, `text`, `metadata`, `status`, `index_key`, `index_epoch`, `indexed_profile`, 索引领取/重试字段、错误摘要、时间字段 |
| `knowledge_chunks` | `id`, `document_id`, `ordinal`, `text`, `start_offset`, `end_offset`, `token_count`, `embedding`, `index_key` |

约束：文档必须归属知识库；分块的 `(document_id, ordinal)` 唯一；删除知识库/文档同步删除相应分块。列表使用有界分页。必要索引包括知识库归属、文档所属库与状态、分块所属文档，以及向量索引。

`index_key` 是每次索引的随机标识，用来拒绝过期 worker 的发布和识别陈旧引用。不是内容历史版本，也没有发布审批流程。

```text
<filesystem root>/
├── data/opsmesh.plugins.knowledge/
│   └── sources/<knowledge_base_id>/<document_id>/<index_key>.txt
└── cache/opsmesh.plugins.knowledge/
    └── models/<model_revision>/
```

原始文件保存原始字节；PG 的 `text` 保存规范化后的可检索文本，支持事务内的原文读取与片段定位。规范化仅处理编码标记和换行，不进行会改变语义的字符替换。

文件空间必须在 knowledge 自己的插件作用域中取得并保存。SDK 工具执行时所在作用域可能是调用插件，不能因此把文档写进智能体插件目录。用户文件名只作展示，不直接参与路径拼接。

文件系统与 PostgreSQL 没有共同事务：先原子发布新文件，再提交文档记录；失败删除此次文件。启动维护只清理超过安全时间且无数据库引用的暂存/孤立文件。删除先取消数据库可见性，再清理文件；文件清理失败不能让已删除内容重新进入搜索。

### 3.4 索引流程

```text
接收文本/文件
→ 校验编码、大小和内容
→ 保存原文与文档记录（pending）
→ knowledge 自有索引 worker 领取
→ 切分、批量编码
→ 事务内替换全部分块并标记 ready
```

状态为 `pending → indexing → ready / failed`。新增/更新返回文档 ID 和当前状态，不阻塞到整个文档编码完成。

领取使用短事务与 `FOR UPDATE SKIP LOCKED`。文档记录保存领取标识、租约、重试次数和下次重试时间；编码期间不占有数据库事务。长任务续租，发布时再次校验 `index_key` 与领取标识。worker 崩溃后租约到期可重新领取；旧 worker 不能覆盖新结果。

更新文档后立即退出检索，直到新索引全部完成；失败保留明确状态，不返回旧内容伪装成新内容。删除与重建也使旧领取失效。暂时性数据库/推理故障有界重试，格式和长度错误不自动重试。

这是知识库自身的索引任务，不新增通用 jobs 插件、Redis 队列或执行工作流。默认一个 worker；数据库领取机制保证进程重启恢复和并发调用一致性。

插件停止先拒绝新增索引任务，停止领取，等待已接收查询和编码。取消协程不能终止推理线程，因此线程实际完成前仍持有模型和依赖资源，不能仅凭 `wait_for` 超时就卸载模型。编码器只保留待处理数量和模型并发限制；同步模型调用复用框架的取消安全 `run_sync`，不再维护第二套线程限制和取消等待协议。

全库重建明确采用以下协议：

1. `active_profile` 记录最近完成的编码配置，`target_profile` 记录本次目标配置，配置包含明确模型 revision；`index_epoch` 是此次全库重建的随机代次，`index_status` 为 ready/rebuilding/failed。
2. 发起重建时，在锁定知识库记录的短事务内写入目标配置、新代次和 rebuilding 状态，并将现有文档重新置为 pending、分配新 index_key。重复请求同一目标且正在重建时返回现有任务状态，不重复重置工作。
3. worker 领取时固定目标指纹、库代次和文档 index_key。发布时在事务内核对三者及领取标识；任何一个改变都丢弃旧结果。成功写入文档的 indexed_profile，不能仅凭 ready 判断编码配置。
4. 重建期间新增和更新文档也在同一知识库锁规则下取得当前目标代次；删除文档从完成条件中移除。最终检查在库记录锁下确认所有有效文档为当前代次、目标指纹且 ready，才将 active_profile 切换为目标并开放检索。空库可以直接完成。
5. 任一文档最终失败时库保持不可检索并显示失败原因；显式重试继续当前目标代次。重启读取数据库状态恢复 pending 或租约过期的工作，不把部分 ready 误判为全库完成。
6. 查询要求库为 ready、查询编码器指纹与 active_profile 相同，且命中文档指纹一致。配置变更不能自动解锁旧索引；没有本次目标模型资源则停止索引并给出配置错误。

这些字段用于并发与恢复，不保存历史版本，也不引入发布流程。编码和分块仍在事务外执行。

### 3.5 检索和引用

```text
query → 查询编码 → 指定知识库内向量排序
      → ready 文档中的片段 → 原文、标题、位置、引用 ID
```

使用余弦距离。SQL 必须先包含库范围和文档状态条件，再排序与限制；禁止全库召回后在 Python 中过滤归属。

小规模知识库使用精确检索；HNSW 可作为配置选择。启用 HNSW 时需要验证过滤后的召回数量，采用 pgvector 支持的迭代扫描，并限定事务级搜索设置及扫描预算。不能承诺 ANN 一定返回 top-k 或一定有 k 条记录。依据：[pgvector 官方文档](https://github.com/pgvector/pgvector)。

使用迭代扫描时要求 pgvector 0.8 或更高版本；部署锁定具体扩展版本。精确模式显式查询库范围内的数据，不假定建了 HNSW 索引之后优化器仍会自动选择精确扫描。

返回 `SearchHit`：文档 ID、分块 ID、标题、原文片段、字符位置、距离、索引标识。限制结果数量及总文本长度。没有有效结果时返回空集合，不生成答案。

`read_document` 支持范围读取；带引用标识读取时验证索引是否仍一致，内容已替换则返回明确的陈旧引用错误。智能体可以直接用搜索片段回答，也可以继续读取上下文。

### 3.6 公共对象和工具

| 接口 | 职责 |
|---|---|
| `knowledge.create(...)`、`knowledge.list(...)` | 创建和列出调用插件所属的知识库 |
| `await knowledge.get(id)` | 读取现有知识库并取得 `KnowledgeBase`，不存在则报错，不隐式创建 |
| `KnowledgeBase.update(...) / delete()` | 修改元信息、删除知识库 |
| `KnowledgeBase.documents.add(...) / import_file(...)` | 写入文本或文件，返回待索引状态 |
| `KnowledgeBase.documents.list(...) / get(id)` | 元信息和索引状态 |
| `KnowledgeBase.documents.update(...) / delete(id)` | 内容修改与删除 |
| `KnowledgeBase.documents.reindex(id)` | 显式重新提交索引 |
| `KnowledgeBase.search(query, ...)` | 自然语言检索，返回原文片段和引用 |
| `KnowledgeBase.documents.read(id, ...)` | 原文范围读取 |
| `tools` | 只读原生 SDK 工具列表 |
| `management_tools` | 单独提供的文档管理工具列表 |

`KnowledgeBase` 表示已经选定的知识库，内部持有库 ID 和受控资源访问入口，不长期持有数据库 Session。每次数据库操作使用独立短作用域。

命名按对象与操作区分：`knowledge` 是插件命名空间，`KnowledgeBase` 是知识库，`documents` 是该库的文档集合；方法使用 create/get/search/add/read 等实际动作。`knowledge_bases.py` 是模块名而非操作名。文档集合持有所属知识库范围，负责文档查询和写入，不是另一层通用注册中心。不再用含义宽泛的 library，也不在明确的对象命名空间中重复拼接资源名。向量编码与 pgvector 查询是内部实现；面向调用者的对象是知识库。

工具采用原生 `@function_tool`，英文 Google 风格 docstring 描述参数与结果。闭包绑定库句柄，模型只填写 query、document_id 等业务参数；没有向模型暴露数据库连接、任意路径或归属切换参数。

默认工具：`search_knowledge`、`read_document`、`list_documents`。可选管理工具：添加、替换、删除、重建文档；默认要求 SDK 原生审批，调用插件处理原生中断。添加成功仅意味着接受索引，不意味着已可搜索。

为同一 Agent 挂载多个库时，工具名使用明确的库别名前缀，启动时拒绝重复名称。业务入口负责用户授权和选择可用库，插件保留库归属与绑定范围校验，不新增 IAM。

调用形态（在功能插件启动完成资源准备后）：

```python
from agents import Agent
from opsmesh import on_ready, require

knowledge = require("knowledge")

agent: Agent | None = None


@on_ready
async def prepare() -> None:
    """Resolve the configured knowledge base after provider readiness."""
    global agent
    knowledge_base = await knowledge.get(knowledge_base_id)
    agent = Agent(
        name="Knowledge Assistant",
        instructions="Search the knowledge base and cite document titles.",
        tools=knowledge_base.tools,
    )
```

这里 `knowledge_base_id` 来自调用插件配置。用户/会话信息仍可正常放在 Agent context 中，但知识库工具不要求 context 携带数据库、编码器或授权回调。

上面的 await 发生在调用插件的 `on_ready` 处理器中，此时依赖提供方已进入 READY。宿主的全局 `on_startup` 阶段尚不满足调用准入，不能在那里调用知识库公共接口。聊天时调用插件将用户输入传给 execution.run/stream；模型从 Agent.tools 的名称、说明和参数 schema 中选择工具，原生 Runner 执行工具并把返回的原文片段交回模型。模型据此生成回答。这条链路不需要 `on_document_indexed` 或 `.handle()`。

```text
用户消息 → 框架消息处理器 → execution → 原生 Runner/Agent
                                      → knowledge 原生工具
                                      → 检索结果返回模型
                                      → 模型回答 → 原通道回复
```

### 3.7 统一消息入口、命令和回复

HTTP 是消息传输方式。功能插件不需要为新建、查询、清理等每项业务定义 GET/POST/DELETE 路由。chat/sandbox 及可选管理命令采用如下链路：

```text
HTTP / QQ / DingTalk
→ channels 认证、目标解析、Message + Event + 可信 Context
→ Framework Rule / Permission / Matcher / Dependent
→ on_command 或 on_message 处理器
→ 公共功能对象或 execution
→ 处理器通过 Matcher.send/finish 回复
→ channels 按原通道输出
```

#### 统一 HTTP 消息协议

channels 定义唯一的消息请求规则和入口：

```http
POST /plugins/{plugin_id}/messages
Authorization: Bearer <channel credential>
Content-Type: application/json
```

请求体使用 channels 的 `MessageRequest`：

完整字段、回复 envelope、SSE 和续接规则以[通道与执行回调](../channels-and-callbacks.md#通道对话标识与多轮续接)为统一契约。本方案不再另定义回复类型或一套命令分发。

```json
{"message": "/sessions --limit 50 --offset 0"}
```

多模态请求使用有序消息段，附件只能引用 channels 受控上传得到的 `attachment_id`：

```json
{
  "message": [
    {"type": "text", "text": "请检索这份文件"},
    {"type": "file", "attachment_id": "0123456789abcdef0123456789abcdef"}
  ]
}
```

协议规则由 channels 统一维护：正文只接受 message 和可选 conversation_id。后者是客户端生成的规范 UUID 区分符，不是 SDK Session ID 或授权凭据；未传时本次请求生成新值并在响应中返回。身份、组、角色、来源通道、账号和附件拥有者由认证与适配器注入，不能被正文覆盖。未知字段、空文本、非法消息段、未知附件和超限请求在进入框架前拒绝。

HTTP、QQ、钉钉都转换成相同的 Framework `Event`、`Message`、可信 `Context` 和附件引用。HTTP 调用者也必须遵循这套消息协议，不为 knowledge、chat、cron 或其他业务增加自己的 body 模型、HTTP 路由或 `HTTPBody` 参数。

插件通过统一消息入口声明允许进入的本插件 Matcher。channels 只负责把消息交给框架；不接收任意 Python 函数名、Matcher ID、导入路径或插件自定义路由。插件加载和停止时，入口声明随插件归属登记和撤销；插件未 ready 时请求返回通道级不可用结果。

普通文本进入 `on_message`；命令文本由框架命令规则进入 `on_command`。会话选择、知识库查询、计划管理等业务参数使用命令参数或消息段表达，由业务 Matcher 解析；HTTP 协议本身不理解 `session_id`、知识库 ID 或计划 ID。

例如客户端向同一个地址发送：

```json
{"message": "/sessions --limit 50 --offset 0"}
```

后续请求为 `{"conversation_id":"d250759b-8d9a-4c3a-a1af-397292fc835d","message":"解释一下这段内容"}`。客户端保留第一次使用或响应返回的 conversation_id，每个窗口独立选择该值。channels 将它与协议、连接、目标插件及认证用户组合为 Event.get_session_id() 的稳定作用域；聊天插件按该作用域映射 SDK Session。SDK 会话 ID 不从 UserContext 获取，也不作为 HTTP 顶层字段注入；切换使用 `/use <id>` 并由聊天插件校验归属。

文本命令中的标题、筛选、分页等由一次明确的参数解析处理。文档正文和文件保留为消息附件，使用 channels 已有受控上传与附件解析能力，不把整份文档拼成命令字符串。上传接口属于通用传输；有效附件应在调用作用域内被 knowledge 保存，后台索引不持有临时上传句柄。

#### Matcher 回复与控制流

NoneBot2 的设计适合作为这里的参考：`on_*` 返回 Matcher，处理器通过当前 Matcher 的 `send()` 回复；`finish()` 发送可选消息后抛出结束控制异常；`pause()` 和 `reject()` 发送提示并改变后续事件的处理位置。处理器的 Python 返回值不是回复协议，不能依赖隐式返回值形成通道响应。

OpsMesh 当前 Matcher 已提供 `send/finish/pause/reject`，通过 Channel ContextVar 发送，并由现有控制异常改变流程。但当前 HTTPEvent 只有一次 reply 槽，APIMessageEvent 没有可用的 get_session_id，入口只接受显式绑定的 Matcher。这三处必须修改，不能只换示例的调用名称。不增加 MessageReply 返回值中心，也不扫描 handler 返回值。

```python
from typing import Annotated

from opsmesh import on_command
from opsmesh.adapters import Message
from opsmesh.matchers import Matcher
from opsmesh.params import CommandArg

search_command = on_command("knowledge", block=True)


@search_command.handle()
async def search(matcher: Matcher, arguments: Annotated[Message, CommandArg()]) -> None:
    """Search the bound knowledge base and finish with a reply."""
    hits = await knowledge_base.search(arguments.extract_plain_text())
    await matcher.finish(format_search_results(hits))
```

约定如下：

- `matcher.send(message)` 在当前事件的回复范围发送消息，可多次调用；发送结果不是 HTTP 业务结果，也不保证客户端已读。独立后台通知须显式选择目标 Channel，不能使用已退出的 Matcher 上下文。
- `matcher.finish(message)` 发送可选消息并结束当前 Matcher；后面的 handler 不再执行。它不直接关闭 HTTP，也不向 Channel 发送额外“最终消息”标记。
- `matcher.pause(prompt)` 发送提示，等待后续事件并继续下一个 handler；`matcher.reject(prompt)` 发送提示，等待新事件后重新执行当前 handler。它们用于多轮参数收集，不是异常转 HTTP 状态码的替代品。
- 普通 handler 返回值被忽略。需要回复就显式调用 `send/finish`；无回复的管理操作可以只修改状态并 `finish()`。
- 普通 send 表示完整消息，非流式 HTTP 在依赖有效期内物化并编码消息数据，清理成功后返回 envelope；SSE 每次 send 输出独立 message 事件，请求清理成功后才输出唯一 done。失败输出安全 error 后关闭；无消息也返回 conversation_id，不返回 204。通道限制累计输出或使用有界缓冲，关闭后禁止发送。
- 未匹配命令、参数错误、拒绝访问和插件停止由框架/通道产生对应安全结果；未知命令不能交给模型猜测执行。取消和 Matcher 控制异常保持原语义。
- 消息生产及附件读取必须在依赖有效期内完成；非流式响应可在清理后发送已物化的数据，不能再访问已关闭资源。流式持有执行和发送资源，先关闭源执行再清理依赖，最后发送成功 done。finish 不跳过清理，清理失败不能报告成功。

续接沿用框架临时 Matcher，补齐原始 Matcher 关联即可，不用模块名或行号代替关联。等待范围复用 Event.get_session_id 和 Permission；入口通过已有 matcher_filter 仅放行绑定来源的有效续接。每次请求重新认证和检查准入，同范围消费由 channels 串行控制。waiting 使用现有 DispatchResult.suspended。state 不持有网络请求或执行资源。复用过期检查与注册清理；停止先关准入、排空调用，再清理该插件临时等待，保留静态声明，避免排空期间新建的等待遗漏。资源停止再启动也不能恢复旧等待，撤销和进程重启同样不恢复；不另建代次登记表或等待管理器。

模型增量不逐段调用普通 send。channels 通过现有 Channel.call 扩展通道输出操作，在调用者的 execution.stream 作用域内消费其转换好的异步文本迭代器；SDK 事件解释仍由调用插件承担。SSE 为同一回答发送带 message_id 的 start/delta/end，增量带有序 sequence；完整消息各自独立。非流式 HTTP 和机器人默认有界聚合后输出完整消息，机器人仅在验证平台支持后采用限频更新。回答结束标记不等于请求 done；失败保留部分输出但不标成功。完整线协议和资源顺序以通道文档为准。

未知命令不能依赖 on_message 的优先级自然排除：普通聊天增加非命令 Rule，命令链末尾增加阻断兜底，复用框架的命令前缀与解析结果。权限不通过的已知命令也不得落入模型。存在续接时由续接优先消费，交互需要取消命令则自行声明其行为。

结构化回复拟扩展现有 MessageSegment.structured(value)，不新建业务返回值注册中心。插件同时提供可读文本和一个 JSON 对象；HTTP 编码为消息项的 content/data，机器人仅呈现可读内容。data 是出站专用段，不能从请求注入。完整错误、输出限制和流式生命周期遵循通道文档。

命令不必经过模型。Agent 工具调用是另一条链路，工具返回值由 Agents SDK Runner 送回模型，不调用 Matcher 的 `send/finish`。

#### knowledge 的入口与归属

知识库主要提供原生工具与 Python 对象。可选 `/knowledge ...` 命令声明在 knowledge 自己的插件作用域，并通过它自己的统一消息入口或显式选择的机器人连接开放，不挂到其他插件的路由下。

其他插件需要在自身命令中组合知识库功能时，解析一次参数并调用已取得的 KnowledgeBase；这是应用组合，不复制检索和存储实现，也不重新分发到 knowledge Matcher。模型调用则直接使用 Agent.tools。

知识库 owner_plugin 从创建时的可信插件作用域确定；KnowledgeBase 句柄固定资源范围。knowledge 自有管理命令默认只能管理其明确绑定的库范围，不拥有所有插件的库。要管理其他插件的知识库，由对应拥有者明确提供受限访问入口，不接受请求正文指定 owner_plugin 来提升访问范围。

因此不需要放宽 channels 的跨插件 Matcher 校验。默认不开放文档删除等管理命令，也不让 knowledge 抢占普通聊天消息。

### 3.8 对外装饰器与通知

提供装饰器使用体验，但复用框架 Matcher 的 `.handle()`；不增加另一套 decorator registry、依赖解析器或通用 `@knowledge.action`。

| 需求 | 对外方式 |
|---|---|
| 自定义命令或消息处理 | 框架 `on_command` / `on_message` 返回 Matcher，再使用 `.handle()` |
| 现成知识库操作 | 直接调用公共对象或原生工具；人工管理由调用插件组合命令 |
| 文档索引状态 | 默认由文档状态查询提供；只有明确应用通知需求时才增加订阅入口 |
| 定时处理 | `cron.on_cron/on_interval/on_date/on_schedule` 返回 Matcher |
| LLM 可调用工具 | 原生 SDK `@function_tool`，不提供重复包装 |

如需增加知识库通知接口，只补充事件类型及库范围匹配。`rule`、`permission`、依赖注入和处理器清理由框架负责；订阅归属记录为声明该订阅的插件，并在加载失败/撤销时统一清理。

原示例中的 `indexed.handle()` 仅能让 Python 订阅函数收到索引完成事件，不会把事件交给 Agent，也不会使一个函数自动成为 SDK 工具。它用于“通知上传者文档已就绪”等应用行为，不能作为智能体知识检索的示例；当前设计不把这类订阅作为必需公共 API。

如果明确需要索引完成/失败通知，使用提交后的框架事件，按库范围订阅，复用既有 `.handle()`，不另建 callback 列表。通知是进程内尽力交付：订阅异常不回滚索引，插件停止后不调用旧函数，重启不补发。默认读取文档状态即可满足上传进度查询。

如果某个应用希望索引完成后让智能体主动分析文档，由该应用的处理器明确选择 Agent、输入、身份、会话和回复目标，再调用 execution；knowledge 不自动向共享 Agent 或任意对话追加消息。

不预先为每个 CRUD 增加 before/after 装饰器。前置授权使用框架 Permission/Rule/Depends；只有确实需要订阅的业务事实才增加事件便利接口。

### 3.9 文件职责

```text
packages/knowledge/
├── pyproject.toml / README.md
├── src/
│   ├── __init__.py       # 元数据和公共导出
│   ├── config.py         # 配置声明
│   ├── lifecycle.py      # 模型、文件空间、worker 的启动与关闭
│   ├── knowledge_bases.py      # KnowledgeBase 与库操作
│   ├── documents.py      # 文档写入、读取、替换、删除
│   ├── text.py           # 文本规范化、token 切分和位置
│   ├── embeddings.py     # BGE 模型与有界编码
│   ├── indexing.py       # 领取、续租、发布、失败恢复
│   ├── search.py         # pgvector 查询和结果约束
│   ├── tools.py          # 原生 SDK 工具
│   ├── models.py         # 三张 ORM 表
│   ├── schemas.py        # 文档、片段等公共数据结构
│   ├── errors.py
│   └── migrations/
└── provision_model.py    # 独立模型准备脚本，非宿主入口
```

不为单一编码器先建 provider 抽象体系，不再分 catalog/artifacts/registry/assembly；这些文件分别对应实际功能。

测试位于仓库 `tests/unit/test_knowledge_*.py`。模型准备脚本随源码包提供；插件运行时不联网补齐模型。

### 3.10 chat/sandbox 示例调整

chat/sandbox 使用统一消息入口及共享命令处理，chat 的 http.py 和旧业务路由已移除；客户端同时迁移到消息 envelope、conversation_id 与 SSE 协议。

| 会话功能 | 统一命令 | 是否调用模型 |
|---|---|---|
| 新建 | `/new [name]` | 否 |
| 分页列表 | `/sessions --limit N --offset N` | 否 |
| 查询指定会话 | `/session <id>` | 否 |
| 校验并选择会话 | `/use <id>` | 否 |
| 当前会话 | `/session` | 否 |
| 读取历史 | `/history` | 否 |
| 清理历史 | `/clear` | 否 |
| 删除指定会话 | `/delete <id>` | 否 |
| 对话 | 普通消息或 `/chat ...` | 是 |

`/new`、`/sessions`、`/session <id>`、`/use`、`/delete` 不依赖一个自动新建会话的依赖函数。只有需要当前会话的命令才解析当前选择；普通聊天没有选择时可按既定策略创建。避免查看列表就产生空会话。

`/use` 校验访问权后更新聊天插件按 Event.get_session_id() 保存的选择映射，再通过 matcher.finish 发送可读文本和 data.session_id。HTTP 客户端后续仍传同一 conversation_id，不需要传 SDK Session ID；机器人沿用平台提供的对话范围。映射需要跨重启保留时存入聊天插件数据库，不放入 channels 或 UserContext。相同范围的选择和调用串行，不同窗口相互独立；若两个范围选中同一个 SDK Session，其模型执行并发由聊天插件协调。

示例文件职责：

```text
example_chat/
├── __init__.py       # 依赖加载、元数据和一次统一消息入口声明
├── agent.py          # 原生 Agent 和 tools
├── messages.py       # 普通消息处理
├── commands.py       # 会话命令解析与回复构造
├── handlers.py       # 协议无关的会话选择依赖
├── conversation.py   # 对话执行
├── sessions.py       # 会话操作
├── schemas.py        # 聊天输入和结构化结果
└── ...
```

`handlers.py` 不再判断 HTTPEvent，不读取 request.path_params/query_params，不抛 HTTPError；通道将协议输入转成公共消息与经校验的插件数据，聊天依赖负责会话选择和归属。命令和普通消息处理器通过注入的 `Matcher` 调用 `send/finish/pause/reject`，不依赖 handler 返回值，也不通过 `respond/reject` 按 HTTP/机器人分别判断。

本地客户端的新建、列表、选择和删除均向统一 messages 地址发命令，并读取结构化 data，不解析人类可读文本。删除旧 `/chat` 和 `/sessions` 路径依赖；上传继续调用 channels 通用附件接口。chat、sandbox、客户端、文档及定向测试一并更新，避免示例之间接口不同。

知识库接入在 Agent 构造时设置 tools，不通过索引完成通知向模型推送内容。默认只读工具无需工具审批；若展示管理工具，示例必须另外实现原生 SDK 审批继续，不能忽略现有 ApprovalUnsupported 边界。

上述入口、channels 适配和框架续接已按本方案实施；README 与包内公共导出是当前使用契约，本文保留设计边界和验收要求。

实施按依赖顺序完成，不先删除仍被客户端使用的入口：

1. channels 定义 MessageRequest 的 conversation_id、消息结构化段、JSON/SSE 编码及输出限制；替换单次回复槽，分发完成后统一收尾。
2. 框架只补临时 Matcher 的原始关联；核对现有注册清理后补齐停止时的临时等待清理。复用 matcher_filter、Permission、DispatchResult，channels 提供稳定 Event.get_session_id 与同范围准入；不新建会话管理器、代次登记或 dispatch。
3. chat/sandbox 按通道范围维护 SDK Session 选择，命令与普通消息采用 Matcher 回复，补齐未知命令兜底和权限拒绝边界。
4. 客户端同步迁移统一入口、conversation_id 和消息列表/SSE 读取；在同一变更中移除 http.py 及旧业务路由，不添加兼容转发。
5. 执行本文统一消息入口的定向验证，再同步对外使用示例；knowledge/cron 复用已确定的消息契约。

#### 框架改动边界

| 位置 | 实施范围 |
|---|---|
| Matcher | 补原始 Matcher 关联，多次续接保持关联；send/finish/pause/reject 语义不变 |
| 插件生命周期与注册清理 | 先核对既有机制，仅补停止排空后清理临时续接的缺口，保留静态声明 |
| dispatch / DispatchResult | 复用 matcher_filter、suspended、errors；不新建结果或分发系统 |
| Event / Permission / Dependent | 复用现有抽象与每次调用作用域；稳定对话标识由 channels 的事件实现 |
| channels | 入口认证、JSON/SSE、完整消息与增量适配、结构化消息、附件物化和同范围准入 |
| 聊天插件与客户端 | SDK Session 选择及并发、命令规则、SDK 增量转换、协议消费 |

HTTP 状态码、conversation_id 字段、消息增量编码及 SDK Session 业务均不进入框架核心。框架是否需要修改某一清理函数，以现有生命周期的定向验证为依据，不因方案提到“等待”就新增管理模块。

## 4. Cron 插件

### 4.1 调度语义

使用 APScheduler 3.x 的 `AsyncIOScheduler` 和原生 cron/interval/date trigger，实施时锁定经过验证的确切版本。不混用 4.x API，不自写表达式解析器。

| 场景 | 约定 |
|---|---|
| cron | 五字段表达式，显式 IANA 时区 |
| interval | 固定间隔和持久化起始锚点，重启不会改变周期基准 |
| date | 指定时间单次触发，启动时已经过期则标记过期 |
| 默认并发 | 同一计划最多一个执行，另设插件总并发上限 |
| 运行期间错过触发 | 使用原生 misfire/coalesce 配置，有界处理 |
| 服务停机期间 | 不补跑；重启后计算未来触发点 |
| 处理器失败 | 记录错误，本次失败；不自动重试外部写入 |
| 超时 | 取消本次异步处理并等待清理；不允许遗留资源逃离生命周期 |

APScheduler 3.x 的 weekday 数字从星期一开始，day 与 weekday 同时指定时需同时满足；推荐 `mon`～`sun`，不将其宣称为与系统 crontab 完全相同。夏令时地区遵循原生 trigger 的墙上时间行为。依据：[CronTrigger](https://apscheduler.readthedocs.io/en/stable/modules/triggers/cron.html)。

本插件是定时触发器，不保证跨崩溃补偿、至少一次或恰好一次执行。需要结算、扣费等可靠流程的插件，应持久化业务任务和幂等键；不能把定时器误当作可靠任务队列。

### 4.2 持久化

PostgreSQL 保存 JSON 计划定义，APScheduler 的 MemoryJobStore 保存当前进程计时对象。重启从定义重建未来计时，不序列化 Python 函数、Agent、闭包或数据库 Session。

不直接使用 APScheduler 3.x 的 SQLAlchemyJobStore：它的同步 engine、对象序列化和运行对象生命周期，与本项目异步 database 插件及热启停的处理器不匹配。这里保存的是插件计划声明，不自行实现 APScheduler JobStore 协议。

一张 `cron_schedules` 表：

- `id`, `namespace`, `owner_plugin`, `name`, `source`（code/dynamic）。
- `definition` JSONB：经 ScheduleDefinition 校验的 handler、trigger、payload、enabled 与执行选项。动态计划省略 `timeout_seconds` 或 `misfire_grace_seconds` 时，在校验并持久化阶段继承 cron 插件配置；代码声明和动态计划使用同一解析规则，显式值优先。已保存值保持稳定，只有历史上缺少字段的记录在恢复时使用当前配置补全。
- `revision` 拒绝过期计时回调；它是并发标识，不保存历史版本。
- `completed`、`expired`、创建和修改时间。
- 唯一约束：`(namespace, owner_plugin, name)`。

调度参数使用有类型的配置校验后存储；不接受从数据库提供任意 Python 导入路径、shell 命令或代码。下一次触发时间从当前 scheduler 获取，不维护第二份独立计时状态机。执行历史默认走日志和框架观察回调，不新增完整任务账本。

宿主 SQLite 继续只保存插件配置和启用意图；定时计划属于 cron 插件自己的 PostgreSQL 数据。

### 4.3 对框架的接入

```text
APScheduler 到期
→ 核对计划启用状态、处理器归属和插件资源代次
→ 取得调用插件及依赖的 plugin_call 占用
→ ScheduledEvent
→ Framework Rule / Matcher / Dependent
→ 业务插件处理函数
```

当前框架通用事件入口是 `on(...)`。`cron.on_cron(...)` 只是创建指定事件 Matcher 并关联计划的便利接口，不增加第二套 handler 调用、权限或依赖解析系统。

对外装饰器统一使用返回 Matcher 的 `.handle()`。不同时提供一套返回 Matcher 的接口和另一套独立执行的 `@cron.job`，避免两套归属、启停及依赖语义。

现有 `dispatch_event` 要求 Channel；cron 内部提供一个没有网络连接的逻辑 Adapter/Channel，作为定时事件来源。它不承诺消息回复，`send` 明确不支持。通知 QQ、钉钉时由处理器使用自己依赖的 channels。

`ScheduledEvent` 仅含 schedule_id、invocation_id、owner_plugin、name、trigger_type、实际触发时间 fired_at、经校验的 payload。它不是聊天消息，没有自动生成的人类用户、群、会话或管理员权限。需要执行身份的处理器通过自己的依赖提供。

Matcher 的归属固定到声明插件，并按计划匹配，不能广播给所有业务处理器。处理结果中的异常必须反馈调度执行结果，不能仅被 dispatch 收集后当作成功。

`invocation_id` 只标识本次调用，不是跨重启的业务幂等键；APScheduler 的原计划触发时间与实际 fired_at 也不能混为一谈。

使用方式：

```python
from opsmesh import require

cron = require("cron")

daily_digest = cron.on_cron(
    "0 9 * * mon-fri",
    name="daily_digest",
    timezone="Asia/Shanghai",
)


@daily_digest.handle()
async def send_digest() -> None:
    """Generate and deliver the scheduled digest."""
    await publish_digest()
```

`publish_digest` 是调用插件自己的函数。需要数据库、Agent 或渠道时继续使用框架 `Depends`；不要求任务函数接收全部事件字段。动态计划和代码声明都通过同一个 `ScheduleDefinition` 校验与默认值解析路径。

### 4.4 声明、在线管理与恢复

| 操作 | 约定 |
|---|---|
| `on_cron / on_interval / on_date` | 导入时声明，启动后才建立实际计划 |
| `on_schedule(name, payload=...)` | 声明可被动态计划引用的 Matcher 与参数类型 |
| `add_schedule(...)` | 引用本插件已声明的 handler，校验 JSON 参数后保存 |
| `list_schedules / get_schedule` | 返回定义、是否实际可运行、阻塞原因和下次触发 |
| `update_schedule` | 先校验并提交，再替换内存计时对象；失败标记待同步并重试协调 |
| `pause_schedule / resume_schedule` | 持久化启用意图，已执行任务不因暂停自动取消 |
| `delete_schedule` | 删除动态计划；代码声明计划通过暂停关闭，避免下次加载又被创建 |
| `reset_schedule` | 明确恢复当前代码声明的默认值 |

计划查询、修改、暂停、恢复等操作可通过 cron 自己声明的 `/cron ...` 命令开放，复用上述函数并返回统一回复。HTTP 和机器人使用同一消息/命令链路，不写每项计划操作的 HTTP 路由。调用插件也可以直接使用公共函数组合自己的命令，不跨插件挂载 cron Matcher。管理命令与 `ScheduledEvent` 到期事件分别匹配，管理一条计划不会因此执行其任务。默认不对机器人开放计划管理命令；只能操作入口明确授权的计划范围，不能靠传入 owner_plugin 越权。

代码声明仅初始化不存在的记录；重启不覆盖用户暂停或在线修改。代码中默认值变化不会悄悄覆盖持久化设置，需显式 reset。处理器被移除时原计划显示 blocked，不能继续调用旧函数，也不自动删除数据。

代码声明的 name 与 handler 键由代码固定，在线更新只能修改触发、参数和执行选项；动态计划可修改名称和目标声明。暂停/恢复在行锁内只更新 enabled，避免覆盖同时发生的其他修改。无效旧定义单独标为 blocked，不阻止其他合法计划同步。

计划保存成功但内存更新失败时，不声称已经生效。短周期协调任务比较持久定义与本地对象并恢复一致性；业务代码通过本插件接口修改，不支持绕过接口直接改表后期待即时触发。

每次触发前重新核对持久记录及其修改标识；计时回调同时绑定声明插件对象和框架的 generation，在查询前、调用准入前核对。停止后重新启动或撤销后重新加载时，旧回调不能进入新资源；同步过程为新代次重建计时器。已暂停、删除或被替换的旧计时对象不能继续提交新执行。数据库暂时不可用时拒绝本次触发并记录原因，不以缓存配置猜测当前启用状态。

无效持久定义仍可通过列表和详情查询：保留 id、name、归属和来源，definition 返回 null，runnable 为 false，blocked_reason 为 invalid definition。动态计划可替换或删除；代码声明可按固定名称恢复默认配置，不依赖损坏定义中的 handler 字段。

插件停用期间，计划 enabled 值不变，但实际停止触发。重新启用时恢复未来计划。计划修改只影响后续触发，已经执行的任务保留自己的不可变参数。

### 4.5 启停与单实例约束

1. cron 启动检查迁移，建立 scheduler，取得当前 namespace 的 PostgreSQL advisory lock。
2. 同一 namespace 只允许一个活跃调度实例；第二个启动明确失败，不默默重复调度。锁使用独占连接，并在 database 池容量中预留；不把 session 锁所在连接归还连接池。
3. 在目标插件进入可调用状态后安装计时器；声明收集不执行任务。
4. 目标插件进入 quiescing 时暂停对应计划，关闭新调用准入；既有执行由框架排空。
5. cron 停止时暂停 scheduler、等待在途处理与依赖清理，再关闭 scheduler 和锁连接。不能假定 APScheduler 的 shutdown 调用替代框架排空。

同步处理器或依赖运行在线程时，超时和取消不会杀死线程。框架在实际线程退出前保留调用及资源，随后传播超时或取消；因此 timeout_seconds 不能承诺强制终止阻塞的 Python 代码。处理器应使用有界 I/O，宿主停止超时仍按未完成清理处理。
6. 锁连接丢失时关闭触发准入并停止本实例调度。该机制防止正常情况下的重复实例，不承诺网络分区下外部副作用恰好一次，业务仍需幂等。

APScheduler 3.x 官方明确不支持多个进程共享 job store 来获得正确的分布式调度，见 [FAQ](https://apscheduler.readthedocs.io/en/stable/faq.html)。本方案不实现集群抢占、选主转移或分布式工作队列。

### 4.6 文件职责

```text
packages/cron/
├── pyproject.toml / README.md
├── src/
│   ├── __init__.py       # 元数据和公共导出
│   ├── config.py         # namespace、时区和执行上限
│   ├── lifecycle.py      # scheduler、锁与启停协调
│   ├── schedules.py      # 计划 CRUD、声明默认值和协调
│   ├── operations.py     # 公共操作、调用归属与同步结果
│   ├── scheduler.py      # APScheduler 对象及触发适配
│   ├── events.py         # ScheduledEvent 和逻辑事件来源
│   ├── declarations.py   # 定时计划声明与框架 Matcher 的关联
│   ├── models.py         # 一张 ORM 表
│   ├── schemas.py        # 有类型的计划定义与查询结果
│   ├── errors.py
│   └── migrations/
└── requirements*.txt     # 框架、插件和外部依赖分别声明
```

不新增框架级定时子系统，也不把每个 helper 拆成一个文件。只有实际私有实现使用 `_`；不统一套 `_internal` 目录。

测试位于仓库 `tests/unit/test_cron_*.py`；同步函数取消与插件资源持有另在 framework 定向测试中验证。

## 5. 配置、发行和观测

两个插件均用 `PluginMetadata.config` 声明配置，保存到现有宿主插件记录。`host.json` 不增加知识库、cron、模型或 Langfuse 字段。

| 插件 | 配置项 |
|---|---|
| knowledge | 固定模型位置/revision、设备、编码批量、线程上限、文档大小上限、切分参数、索引 worker/租约/重试限制、检索模式与结果上限；未提供的分页/检索数量使用插件内安全默认值并受配置上限约束 |
| cron | namespace、默认时区、计划数量与总并发上限、默认超时、misfire 默认值、协调间隔 |

库 ID、任务参数、文档信息属于插件数据，不塞入全局 Settings。database DSN 只由 database 配置，filesystem 根目录只由 filesystem 配置。

PostgreSQL 服务器预先安装 pgvector 扩展，部署时检查并迁移；普通请求不尝试以应用账号安装扩展。knowledge 的模型依赖和权重作为单独插件运行资源发布，保留模型许可证和校验和。PyTorch 等二进制依赖需按 Linux 架构与宿主 Python ABI 构建/验证，不能把它们当作任意平台通用源码插件。

框架安装脚本仍只部署框架；新插件随 Release 提供独立包和相应运行资源，更新插件构建清单，不令所有框架安装默认下载模型。

知识库工具在 Agent 执行期间自动体现为 SDK 工具 span；有必要时在已有 trace 中增加原生 custom span，记录编码耗时、召回数量等有限属性。不创建第二个 Langfuse exporter，不将本地 embedding 的 token 数冒充 LLM 计费用量。普通文档索引使用插件日志，不伪造 Agent 执行。

cron 执行普通任务只记录调度日志/框架观察事件。处理器调用 execution 时可向 TraceContext.tags 和 RunConfig.trace_metadata 添加计划关联信息；不会自动伪造一个 user_id 或 session_id。

## 6. 验收条件

### Langfuse

- run、stream、取消、流提前结束的 trace 作用域和用户维度正确，并发不串联。
- 导出失败不重试 Agent；关闭失败保留资源状态，可重试清理。
- 审批继续的新 trace 分组策略得到确定性测试验证。
- 独立的服务器联调区分接收成功、入库成功与查询成功。

### Knowledge

- 中文、Markdown、空文本、非法编码、超限文件、长段落及 token 边界处理正确。
- 真实 BGE 编码为 512 维且归一化，模型revision/索引指纹一致。
- 在真实 pgvector 上验证检索、库范围过滤、ANN 过滤召回和原文引用。
- 更新/删除与索引并发、租约过期、进程中断后不会发布过期分块；全库重建恢复根据持久化代次和目标指纹判定完成，混合指纹不可查询。
- 文件失败和数据库回滚不产生可见的错误文档；孤立文件可清理。
- 原生工具 schema、审批、错误说明与读取范围正确；停止时推理仍持有依赖。
- HTTP/机器人使用同一命令处理器且只分发一次；附件、分页和可信归属不丢失，保留同插件 Matcher 绑定约束。
- 未绑定的管理操作不可从通道调用；知识库不会默认接管普通聊天消息。
- 确定性模型验证 Agent.tools 能实际调用检索工具并接收原文结果；不能用 Matcher 通知测试代替工具调用测试。

### Cron

- 原生 trigger 的时区、weekday、月份边界、DST、interval 锚点符合文档。
- 重启只恢复未来触发，持久化暂停保留，过期单次任务不补跑。
- 动态计划参数校验、代码默认值与在线覆盖、缺失 handler 状态明确。
- Matcher/Dependent 只调用一次，异常可见，用户和会话不被伪造。
- 并发上限、超时、取消、停止排空、插件资源代次隔离正确。
- 第二个 namespace 相同的实例不能启动；丢锁后停止触发。
- 数据保存与内存计时更新部分失败可以协调恢复，不误报生效。
- 定时装饰入口复用框架 Matcher/Dependent；管理请求与任务触发互不混淆。

### 统一消息入口与示例

- chat/sandbox 不再加载 http.py，不声明业务 REST 路由；未声明入口的插件不会被统一 messages 地址自动暴露。
- 连续 send 两次再 finish：JSON 返回三条有序消息，SSE 返回三个 message 和唯一 done，机器人发送三条；finish() 不带消息时不补空消息，结束后发送失败。
- 空结果也返回 conversation_id；请求省略它会创建新范围，同值在不同用户/插件下隔离，不同客户端窗口不会互相切换 SDK Session。
- pause/reject 生成的临时 Matcher 可在相同入口范围续接；跨用户、插件、窗口不能命中；多次续接保留原始关联。过期失效，停止排空后清理包含排空期间产生的等待，资源重启而未重新导入模块时旧等待仍不能命中；静态 Matcher 保留，撤销继续清理。
- 同范围并发 HTTP 请求返回 409，不重复消费临时 Matcher；不同范围指向同一 SDK Session 时聊天插件的并发策略仍有效。
- MessageSegment.structured() 只允许出站 JSON 对象并要求可读内容；HTTP 客户端取得 ID/分页，机器人不泄露结构化 data；超限、异常与断连均有定向覆盖。
- 完整消息与增量不能混淆：两次普通 send 是两条消息；同一流式回答的 delta 按 message_id/sequence 追加。机器人默认只发送聚合后的回答，不按 token 请求平台；开启更新时遵循平台限频。
- 附件和惰性数据在依赖关闭前物化；普通响应清理失败不发送成功结果。SSE 先关闭源执行再清理依赖，成功后唯一 done；任一步失败均不补成功 done，断连取消源执行。
- 回复数据结构与发送所需资源在处理器依赖清理前使用完毕，取消、断连和流提前关闭保持框架排空规则。
- `/new`、`/sessions`、`/use`、`/session`、`/history`、`/clear`、`/delete` 不调用模型，列表和选择操作不会隐式新建会话。
- `/use` 和所有会话读写检查归属；不同用户/群/账号之间不会共享一个全局当前会话。
- 本地客户端通过统一地址完成连续聊天和全部会话操作，从 data 读取 ID 与分页数据，不解析回复文本。
- 未知命令及权限拒绝均不进入聊天模型；等待续接不被普通命令兜底抢走。参数错误、处理异常、SSE 开始后失败只输出安全错误，不能追加成功 done。
- 旧 REST 路由和兼容分支移除，客户端和文档同步；CommandArg 示例使用当前依赖工厂写法。

只运行受影响的测试；需要 PostgreSQL、模型权重、Langfuse 的集成测试独立标记并明确执行情况，单元测试通过不能代替外部集成验收。
