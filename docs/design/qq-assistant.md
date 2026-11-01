# QQ 助手：参考实现与平台接入

范围：QQ 群助手、人物技能、群会话与知识、主动参与、媒体处理。不包含手机模拟器、B站账号操作和可视化房间。保留 QQ 官方机器人接入，不重新引入 OneBot。

参考 [dafeiyu-astrbot](https://github.com/whyever77/dafeiyu-astrbot)，本次阅读固定提交 `7ee692a7479aa2774fa41ff54390ad8bef46d8d3` 的 README、persona、face_convert、schedule_awareness、指令与部署文档。它组合了 AstrBot、NapCat、独立模型服务和插件；不能将 README 上所有能力视为单个插件已经完成的功能。

## 对应关系与验收范围

| 用户能力 | OpsMesh 归属 | 当前事实 |
|---|---|---|
| 自然接话、承接追问、多人聊天 | 示例 AGENTS.md + 平台群会话 | 默认积极回应；明确与别人对话时旁听；普通文本输出，无 respond/text schema |
| 人物风格 | 平台 Skill 绑定 | gentle-sister 保持上游八个文件，提示词不重复写工具参数和返回格式 |
| 批次合并和新消息排队 | 平台 bots Inbox + application/conversations | PostgreSQL 持久接收、每群串行；一轮结束立即合并已审核积压，不限制固定条数，只按估算上下文预算分块；新消息进入下一批 |
| 识别不同成员 | 公共 transcript 序列化 | 群内稳定匿名标识，跨批次和主动聊天不重排；不是已验证昵称或平台权限 |
| 连贯上下文 | 平台 Session | 群和发布版本共用会话；不同群隔离；版本更新开始新会话代际 |
| 可检索群问答 | 平台 Knowledge | 成功投递的问答按来源幂等记录为非权威原始资料；不是自动提炼过的长期人物记忆 |
| 长期偏好、自动整理与遗忘 | 平台 Memory | 声明群 namespace，已投递问答才追加来源并请求后台生成；后续 Run 读取整理内容，权限、保留和遗忘使用平台 API |
| 主动参与 | 平台 conversation activity | 近期活动、日配额、冷却、免打扰、聊天优先、重启后额度保留；不是无限主动刷屏 |
| 提醒与日程 | 平台 Scheduling | 已有持久任务 API；尚未给本例 Agent 装配会话内受限日程工具，不读取调度数据库注入提示词 |
| 风险和广告审核 | 示例 Jev 工具 + 平台 Policy | 先审核再聊天，输出也审核；撤回按 QQ 真实确认结果报告 |
| 图片、语音、视频接收与发送 | QQ Adapter + MessageSegment | 保留 MIME 类型；群聊/C2C 先上传再发送；受控协议测试覆盖，未宣称全部真实媒体格式已验收 |
| 看图 | application/conversations/media + 平台 Run | 限定 QQ 图片域名和张数，复用同一发布 Agent 的隔离分析会话，禁用工具；文字描述经 Jev 审核后进入群聊批次；描述不是完整像素级风险检测 |
| 听语音、理解视频 | 外部媒体服务 | 尚未配置可用 ASR、视频抽帧与音轨链路；保留原附件，进入人工复核，不能把媒体传输当成理解 |
| 语音条、音色、会话语音开关 | 媒体模型 + 群级配置 | 尚未实现；现有网关 `/audio/speech` 和 `/audio/transcriptions` 实测均返回 404，须补可调用服务后验收 |
| 原生 QQ 表情、戳一戳 | 具体 Adapter 能力 | 参考项目依赖 NapCat；不能假定官方 API 支持同一消息段和事件 |

## 接入原则

入口仍为 `examples/qq_community/main.py`，通过平台公开创建、发布、路由、权限和工具接口组装。示例仅拥有角色规则和 Jev 业务策略，不保存第二份 Agent、知识库、模型密钥或机器人路由。新增通用能力进入所属平台模块，业务策略通过技能、工具或插件消费它。

图片附件和分析结果保存在平台 Inbox 的作用域状态中，先持久化再确认收件；重试复用 Run 和分析结果。普通文字与图片采用独立处理通道，慢图片不会阻塞文字审核。仅向模型提交明确允许的 HTTPS 图片域名及有限张数，不下载或执行附件；模型供应商负责获取 URL。地址不可访问、混合未支持附件、分析失败或空结果均转人工复核。附件及描述跟随已清理的收件箱记录按保留期删除；未完成消息和待捕获问答仍保留来源。

分析使用同一发布 Agent 的独立短会话，RunOverrides 只能将 tool_choice 收紧为 none，不能强制开启工具。分析 Run 不自动回群，不写入群聊天历史；描述明确标记为不可信、可能不完整，再经过 Jev 后交给群聊 Agent。所有模型调用沿用平台模型凭据、授权、沙箱、用量和追踪。未来语音、视频的下载仍须增加大小、时长和网络限制。

长期记忆独立于原始知识记录：限定当前群，保留来源，使用平台纠错、删除、保留期和预算。会话内日程工具尚待接入，应通过带授权的公开 Scheduling API 按当前群管理，不能照搬参考插件中直接查询 AstrBot SQLite 的方式。

2026-11-01 已授权网关验证：`gpt-6.1-sol` Responses 图片输入成功识别合成红色图片，HTTP 200。图片应用链有受控 Run、独立审核通道与真实持久队列测试；真实群图片的端到端验收应另记结果，不能用合成图片测试替代。语音两接口 HTTP 404，不能用聊天模型名称冒充可用 ASR/TTS。请求结构核对 [OpenAI 图片文档](https://developers.openai.com/api/docs/guides/images-vision)、[转写文档](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create) 和 [语音合成文档](https://developers.openai.com/api/docs/guides/text-to-speech)。

## 本次验收

队列后续修正：移除聊天固定 24 条限制，领取当前已审核积压中可放入估算上下文预算的全部消息；完成一轮立即继续消费，不重复等待合并窗口。只有从空闲转为有消息时等待一次。上下文超出部分仍在队列，图片描述和事件信息也计入预算。审核仍逐条并发，以保留准确撤回目标。本轮相关回归 249 passed、1 skipped，另在服务器真实 PostgreSQL 的独立测试收件箱验证第一轮期间入队的 60 条短消息在下一轮合并处理；使用受控模型和投递替身，不向 QQ 发送测试消息。该测试不等于任意长度的 60 条消息都能放进同一模型请求。

2026-11-01 已更新授权 QQ 测试服务器。真实群文字经过收件、Jev、群 Agent、输出审核及 QQ 确认投递；连续消息的聊天 Run 使用同一个群 Session。已投递来源触发的 Memory 提取、合并和发布任务有 succeeded 记录。普通聊天不设置输出 schema，测试文字无需 @ 也能得到回复。

图片另通过已部署的平台 Run 实测：同一发布 Agent、Docker 沙箱、tool_choice=none，合成红色图片识别成功，记录一个模型请求。用户选择先验文字，真实群图片仍待验收。图片/音频/视频上传发送有受控协议测试，不宣称所有真实格式都已发送成功。

本轮回归 985 passed、31 skipped；跳过项包括显式 PostgreSQL/Redis/S3/Docker/Kubernetes/付费模型集成或缺少可选依赖，并非全部环境测试通过。Ruff、mypy（387 个源码文件）、源码边界/发行包检查和构建通过。授权服务器上的真实 PostgreSQL 收件、Docker Agent、模型与 Jev 调用及 QQ 投递另外实测。
