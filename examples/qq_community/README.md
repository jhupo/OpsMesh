# QQ 群助手

一个沙箱 Agent，绑定一个人物技能、一个自定义 Jev 工具和平台知识库。QQ Bot 是消息入口，通过平台 BotRoute 绑定 Agent，不负责构造或拥有 Agent。

```text
qq_community/
├── main.py                  # 唯一入口：声明资源、注册工具、绑定网关
├── AGENTS.md                # 示例自己的角色、群聊参与和表达规则
├── tools/
│   ├── __init__.py
│   └── jev.py               # Jev 风险判断、供应商响应解析、策略适配
├── skills/gentle-sister/     # 完整的上游人物技能及出处
└── README.md
```

没有 `.env.example`、YAML 项目配置、工作区副本或本地知识库目录。`main.application(...)` 接收创建参数；平台创建 Agent 工作区，只发布明确选中的技能目录，不把示例工程上传到沙箱。`AGENTS.md` 的文字作为 Agent 的 instructions 提交，Python 工具在宿主进程注册。

人物技能来自 [gentle-mature-skill](https://github.com/zhangzhanglaila/gentle-mature-skill)，固定提交 `dcbac204722679aa953ffd0990d91a30f5539000`。上游八个文件保持原文，摘要见 `skills/gentle-sister/UPSTREAM.md`。输出协议、审核规则和平台配置均未写入人物技能。

## Python 调用

框架扩展使用 `opsmesh.framework` 的 Driver、Adapter、Matcher 和 Depends。创建平台应用使用包根目录的 `create_application`，不直接依赖 bootstrap 或 CLI。

```python
async with application(
    principal_id=service_user_id,
    model_profile_ref="community-model@1",
    qq_app_id=app_id,
    qq_app_secret=app_secret,
    group_openid=group_openid,
    bot_member_openid=bot_member_openid,
    jev_api_key=jev_api_key,
    jev_proxy=proxy_url,
    platform={"data_directory": "/var/lib/opsmesh"},
) as app:
    result = await app.publish(actor_id=administrator_id)
```

参数由调用方提供。QQ 密钥绑定为进程内 `runtime:` 引用，不写入 BotAccount、Agent 或工作区；每次启动应用时由调用方再次传入。模型名称、base URL 和凭据由平台模型管理独立配置，示例只接收已发布模型引用。

更新已有部署时可传入 `resource_id`，复用原 Agent、工作区、知识集合和角色的标识，避免因为更换启动入口而创建空的新知识库。默认值为 `qq-community`。

先通过平台初始化/管理接口配置模型，并创建非管理员服务用户，使它持有 `qq-community` 角色。应用声明会发布该角色的精确资源权限，不自行创建用户，也不把管理员身份委派给群成员。`principal_id` 必须是平台用户 ID，`group_openid` 是 QQ 开放平台的群标识，不能用普通 QQ 群号替代。

## 命令行

在仓库根目录执行 `uv sync --locked --extra openai`。数据库初始化和迁移仍使用平台 `opsmesh init` / `opsmesh migrate`，示例启动不隐式初始化数据库。

`run(application)` 提供统一的 publish/serve 命令。命令行的 `--config` 只是把外部 JSON 对象作为函数关键字参数传入，不是第二套项目描述格式。可把实际部署参数放在仓库外、限制读取权限，例如 `/etc/opsmesh/qq-community.json`：

```json
{
  "principal_id": "平台服务用户ID",
  "model_profile_ref": "community-model@1",
  "qq_app_id": "QQ应用ID",
  "qq_app_secret": "由部署方填写",
  "group_openid": "群openid",
  "bot_member_openid": "机器人在该群的成员openid",
  "jev_api_key": "由部署方填写",
  "jev_proxy": "http://127.0.0.1:7890",
  "platform": {"data_directory": "/var/lib/opsmesh"}
}
```

```sh
uv run python examples/qq_community/main.py publish --config /etc/opsmesh/qq-community.json
uv run python examples/qq_community/main.py serve --config /etc/opsmesh/qq-community.json
```

publish 需要平台登录并调用带授权的公开资源服务；重复发布相同声明保留版本。serve 读取平台当前已发布的 BotRoute 和 Agent，启动 HTTP、机器人连接和工作器，不重新发布本地声明，也不读取另一份绑定 JSON。修改代码或声明时先停止消费者，再发布并启动；未完成批次和知识写入会阻止重配。

Docker 需要可用并预先拉取 `python:3.12-slim`。Agent 在断网 Docker 沙箱中运行；Jev 在宿主进程通过传入的代理请求供应商，密钥和工具源码不会复制进沙箱。本例的 Python 函数工具要求 inline Run worker，这与 Agent 使用 Docker 沙箱是两个独立配置。

## 处理链路

QQ Adapter → Bot 身份与群路由 → 持久 Inbox → Jev 审核 → 每群合并批次 → 路由绑定的单 Agent → 输出审核与投递 → 平台知识库与 Memory。

图片先由同一 Agent 的隔离分析 Run 提取文字描述，禁用工具调用，描述经 Jev 审核再进入群聊；图片分析与普通消息审核互不阻塞。只接受明确允许的 QQ 图片域名，失败或不支持的附件进入人工复核。现有网关视觉输入已验证；语音转写和合成接口均返回 404，语音、视频理解和语音回复尚未可用。

审核通过的消息全部进入聊天 Agent，默认积极接话，只有明确不是对自己说的才旁听；Jev 不判断聊天回复概率。Agent 直接输出回复正文，不设置 JSON 输出 schema；空正文表示保持安静。新消息进入下一批，同一群及发布版本共用一个 Session。定时主动参与也复用该会话和平台调度机制。

每轮聊天结束，立即合并当时所有已审核的积压消息，不按 24 条或逐条调用 Agent。只有超过估算上下文预算才分成下一批，超出的消息继续保留。空闲转忙时有短暂合并等待，已有积压时不重复等待；Jev 仍逐条并发审核，以便准确定位需撤回的消息。

Agent 通过 `knowledge_collections` 绑定平台资源，运行时提供知识检索。已审核并成功投递的问答由平台接口幂等记录，保留来源；同时提交当前群的 Memory namespace，由平台后台任务提炼和合并，后续对话读取记忆。提炼模型使用传入的模型档案，正常计量用量与成本；原始记忆来源和任务保留 30 天，可通过平台 Memory API 更改策略或遗忘。没有本地知识镜像或示例自己的第二个整理 Agent。自动撤回受 QQ 权限和消息时限约束，未知审核结果交给人工核查。

HTTP 创建的 Agent 可绑定相同技能、工具和知识资源。Python 工具代码需先部署并注册；HTTP 资源创建接口不执行上传的 Python。
