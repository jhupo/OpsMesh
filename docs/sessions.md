# 会话与沙箱

Agent 是配置实体，Session 是连续对话，Task 是本轮持久执行，Attempt 是执行尝试。人工审批 Interaction 绑定本轮检查点，一个 Task 可以产生多次审批；因此不把 interactions 重命名为 turn。

## 通道绑定

会话键包含 Agent ID、通道 ID、private/group、平台会话 ID；私聊额外包含平台用户映射后的用户 ID。一个 QQ Agent 的私聊、群 A、群 B 各有独立历史和沙箱，同一群的多个已绑定用户使用该群会话。通道消息仍逐条进行身份及 Agent 使用权限检查，群角色不会变成平台权限。

HTTP 默认新建会话，提交 `session_id` 可继续自己的 API 会话。不能通过手动填写其他用户的 ID 或群会话 ID 绕过来源绑定。任务仍归原调用者，额度按原调用者计算。

同一 Session 的任务串行，等待审批时保留当前轮次。错误恢复必须仍处于同一轮次，禁止在较新对话之后重放旧检查点。失效租约、取消、审批过期和任务结束都会释放活跃轮次；无法确认沙箱持久化结果则保留 dirty 标记。

## 原生历史

默认对象是锁定版本 SDK 的 `EncryptedSession` 包装 `SQLiteSession`，使用用户会话 ID 派生加密密钥。没有再次实现一套 Session。

```python
history = context.session.history
items = await history.get_items(limit=20)
await history.add_items([{"role": "user", "content": "hello"}])
last = await history.pop_item()
await history.clear_session()

result = await Runner.run(agent, value, session=history, context=context)
```

`get_items` 返回按时间排序的最近记录；`pop_item` 删除并返回最后一项，空会话返回 None；`clear_session` 只清除历史。它们不会回滚工具副作用、检查点、沙箱文件或任务。业务插件只应在没有 Runner 正在读写历史时直接修改历史。

保留期配置为 `OPSMESH_SESSION_TTL_SECONDS`，默认一年；SDK 会在读取时跳过过期记录，不代表自动物理删除文件。插件可自行选用其他原生 Session，需承担相同的隔离与保留职责。

## 目录

```text
data/users/<owner-id>/agents/<agent-id>/
  .opsmesh/
    sessions/<session-id>/
      .opsmesh/
        session/
          history.sqlite             # 加密 SDK 历史
          snapshots/<session-id>.tar # 原生沙箱快照
```

原生 Manifest 在沙箱中创建 `/workspace/input`、`output`、`.opsmesh/session`、`.opsmesh/skills`、`.opsmesh/cache`，根路径可由 `OPSMESH_SANDBOX_ROOT` 配置。宿主历史和快照不会作为输入文件上传到沙箱。每个会话持有独立目录、状态和后端 Session，不通过 cwd 模拟隔离。

`GET /v1/sessions` 查看可管理的会话，详情显示 active_task_id、revision、sandbox_dirty。文件管理使用 resources 的公共路由，不再提供 Session 专用文件路由。所有者与管理员可管理目录；同组使用 Agent 的权限不包含目录访问权限。

## 公共文件接口

所有接口统一接收 `kind=workspace|sandbox`、`root_id` 和相对路径 `path`。普通工作区的 root_id 是 Workspace ID，沙箱的 root_id 是 Session ID。服务端验证所有权后定位实际目录，不接受服务器绝对路径。

| 方法与路径 | 行为 |
| --- | --- |
| `GET /v1/files` | 列出目录，path 可为空以列根目录 |
| `GET /v1/file` | 下载文件 |
| `PUT /v1/file` | multipart 字段 content 写入或覆盖文件，自动创建父目录 |
| `DELETE /v1/file` | 删除文件或递归删除目录，不允许删除根目录 |
| `POST /v1/directory` | 创建目录和父目录 |

例如 `GET /v1/files?kind=sandbox&root_id=<session-id>&path=output`。宿主工作区直接使用文件系统，容器沙箱通过注册的原生客户端访问同一沙箱；这不建立宿主副本，也不需要 input/output 回传。沙箱管理要求会话空闲且已完成初始化；文件操作期间阻止执行、关闭和 Agent 删除。修改会增加沙箱版本，使旧检查点失效。

## 沙箱生命周期

```python
async with context.session.sandbox(client, options, manifest=manifest) as sandbox:
    result = await Runner.run(
        agent,
        value,
        context=context,
        session=context.session.history,
        run_config=RunConfig(sandbox=sandbox),
    )
```

宿主通过后端客户端的 create/resume 建立 caller-owned 原生会话，提供独立 LocalSnapshot，结束时持久化快照、关闭连接并停止运行中的后端，随后保存加密 session_state；不会调用 client.delete 删除持久资源。恢复时后端可重连已有环境，环境丢失时能否从快照重建取决于所选原生后端。外部进程、套接字和任意 Python 栈不能由快照恢复。

初始化 Manifest 只用于新沙箱，恢复使用保存的状态。需要变更初始化布局或后端时，应关闭会话并新建，不能静默丢弃已有工作。input/output 是沙箱内的普通目录，不做双向复制。其他目录和技能初始化由插件使用原生 Manifest/Skills 能力声明。插件在 setup 中通过 `ctx.sandbox(backend, factory)` 声明客户端工厂，运行与文件管理共用该后端。

沙箱启动后、执行模型和工具前先保存后端会话标识，完整清理成功后才清除 dirty 标记。若初始化在保存标识前中断，宿主拒绝自动新建替代沙箱；需要关闭该会话后新建。保存的标识不代表进程硬退出前的所有文件都已进入快照，恢复边界仍由原生后端和插件检查点决定。

文件接口限制单文件大小并检查用户存储额度；快照及可信插件直接写盘仍需部署侧硬配额。清理成功后即保存当前沙箱状态，再报告超额，用户仍可删除文件释放空间。进程硬退出或清理失败可能留下待恢复的后端资源；关闭会话保留它们，管理员应按后端资源 ID 显式回收，不把关闭会话伪装成已经删除远端资源。

SDK 概念参考：[官方 Sandbox Agents 文档](https://developers.openai.com/api/docs/guides/agents/sandboxes)。本实现接口以锁定的 openai-agents 0.22.3 源码为准，未运行真实沙箱验收。
