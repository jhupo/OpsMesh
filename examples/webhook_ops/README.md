# Webhook Ops 完整示例

本示例展示如何在不依赖任何外部三方服务的前提下，用 OpsMesh 构建一个基于 FastAPI 的 HTTP Webhook 事件网关。

## 目录结构

```text
examples/webhook_ops/
├── main.py                  # 四步启动入口：init -> register_adapter -> load_from_toml -> run
├── pyproject.toml           # 插件声明（启用内置 single_session 与本地 plugins/）
├── adapter.py               # 自定义 WebhookAdapter / WebhookGateway / WebhookEvent / WebhookMessage
└── plugins/
    ├── ops_deploy.py        # /deploy 交互式多轮命令（SUPERUSER + to_me + Depends + got/reject）
    └── alert_observer.py    # notice 告警事件监听器（MatcherGroup + gateway.invoke）
```

## 运行方式

在 `examples/webhook_ops` 目录下执行：

```bash
cd examples/webhook_ops
python main.py
```

## 交互测试

### 1. 触发多轮 `/deploy` 命令

```bash
# 第一步：不带参数触发，返回引导提示
curl -X POST http://127.0.0.1:8080/webhook/events \
  -H "Content-Type: application/json" \
  -d '{"event_type":"message","sender_id":"ops_root","channel_id":"ops","content":"/deploy"}'

# 第二步：输入非法服务名，触发 reject 重试
curl -X POST http://127.0.0.1:8080/webhook/events \
  -H "Content-Type: application/json" \
  -d '{"event_type":"message","sender_id":"ops_root","channel_id":"ops","content":"unknown"}'

# 第三步：输入合法服务名，完成发布
curl -X POST http://127.0.0.1:8080/webhook/events \
  -H "Content-Type: application/json" \
  -d '{"event_type":"message","sender_id":"ops_root","channel_id":"ops","content":"api"}'
```

### 2. 推送 `notice` 告警事件

```bash
curl -X POST http://127.0.0.1:8080/webhook/events \
  -H "Content-Type: application/json" \
  -d '{"event_type":"notice","sender_id":"monitor","channel_id":"alerts","content":"cpu > 90%"}'
```
