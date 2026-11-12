# Langfuse 追踪边界

execution 负责观测原生 SDK 执行，不创建用户、会话或提示词业务中心。
`TraceContext` 仅提供观测维度；智能体接收的业务 `context` 保持原样。

## 导出与生命周期

链路为 `Runner → SDK Trace/Span → OpenInference → 独立 OTel TracerProvider → Langfuse OTLP`。
保持 SDK 和 OpenTelemetry 各自的原生 trace ID，通过
`langfuse.trace.metadata.sdk_trace_id` 关联；不能把两者视为同一个 ID。

桥接使用独立的 exporter 和 provider，不修改进程环境变量，不替换全局 OTel
provider，也不缓存 Langfuse 客户端。这样不会复用 Langfuse 5.2.0 内部按公钥
缓存的已关闭 ResourceManager。发现已由其他组件启用的 OpenInference
instrumentor 时，启动报配置冲突，不接管或关闭别人的资源。

每个原生 span 开始时，通过 Langfuse 的公开 `propagate_attributes` 写入用户、
会话、标签、插件 ID 和 SDK trace ID。这里不镜像 SDK 事件，也不创建第二套 span。
导出地址为配置的 `base_url` 加 `/api/public/otel/v1/traces`，使用 Basic Auth
和 `x-langfuse-ingestion-version: 4`。安装失败释放已创建资源；停用时先移除
instrumentor，再限时 flush 和关闭导出器。网络或 Langfuse 查询失败不会被修改
服务端查询超时的方式掩盖。

失败回滚有一个明确的版本绑定：OpenTelemetry 在 `_instrument` 完成后才设置安装
标记，因此中途失败时公开 `uninstrument()` 不执行清理。桥接仅在这个失败分支
调用锁定 OpenInference 的 `_uninstrument()`，撤回已注册的 processor 和补丁；
测试覆盖了“注册完成后抛错”的路径。升级依赖时必须复核这一调用。

运行配置先通过 `dataclasses.replace` 复制，原对象不修改。
`trace_include_sensitive_data` 的实际值为宿主设置与调用设置的逻辑与；
`tracing_disabled=True` 始终生效。OpenInference 同时按宿主限制隐藏输入和输出。
这不是对插件自行添加的任意 trace metadata 的内容审查，插件仍应避免写入密钥。

## 用户与会话

普通执行通过以下观测参数关联 Langfuse Users 和 Sessions：

```python
result = await execution.run(
    agent,
    message,
    context=user_context,
    session=conversation_session,
    trace_context=execution.TraceContext(
        user_id=user_context.id,
        session_id=conversation_id,
        tags=("chat",),
    ),
)
```

示例中的用户 ID、对话 ID 和 SDK Session 均由调用插件提供；execution 不生成业务
会话。未提供 `TraceContext.session_id` 时可使用 `RunConfig.group_id` 作为观测
分组。它们不会自动创建本地会话索引。缺省维度保持缺省，不从任意字典猜测用户。

`stream()` 同样接受 `trace_context`。调用者必须在它的异步上下文中消费和关闭
事件流，使原生 trace 生命周期覆盖流式执行及清理。

## 审批恢复与锁定版本限制

恢复 `RunState` 时 execution 不预先构造 trace，也不读取 SDK 私有的
`_trace_state` 或序列化整个状态来提取 ID。已有外层 trace 时继续复用它；否则
由 SDK 自行决定恢复的 trace。在执行前和执行结束后，观测回调中的 trace ID
因此可能为 `None`，不能填入一个伪造的 ID。

当前锁定的 OpenInference 2.8.0 存在已通过原生审批测试确认的边界：SDK 在同进程
恢复原 trace 时使用 `ReattachedTrace`，不重复发送 trace start；OpenInference
已在首次执行结束时移除根 span 映射，因此可能丢弃该次恢复的后续 span。
原生审批和工具仍能正常完成。这一行为不代表 Langfuse 服务端没有收到初始请求。

如果调用插件需要完整记录每次审批继续的观测，可以显式选择新的 trace，并保持
稳定的 session 分组。该选择由调用者作出，execution 不默默覆盖 SDK 恢复语义：

```python
from agents import RunConfig, gen_trace_id

state.approve(interruption)
result = await execution.run(
    agent,
    state,
    run_config=RunConfig(
        trace_id=gen_trace_id(),
        group_id=conversation_id,
    ),
    trace_context=execution.TraceContext(
        user_id=user_id,
        session_id=conversation_id,
    ),
)
```

该方案产生新的 trace，Langfuse 中仍属于同一 Session。确定性测试分别验证了
原生沿用 trace 和显式新 trace 两种路径。升级 SDK 或 OpenInference 时需重跑这组
兼容性测试，不能假设上游恢复机制始终相同。

## 其他 Langfuse 页面

Users 和 Sessions 根据已导出 span 的属性汇总，无需单独创建用户表。
Prompts、Skills 和 Playground 是 Langfuse 自己的管理能力，不会因为启用 execution
追踪自动接管插件的提示词、技能文件或模型配置。插件需要使用这些能力时，应显式
获取对应内容、关联 prompt/skill 元数据；Playground 的模型连接由 Langfuse 项目
设置管理。此桥接只负责追踪，不自动上传本地技能或模型凭据。

协议依据：[Langfuse OpenTelemetry 接入与属性映射](https://langfuse.com/integrations/native/opentelemetry)。
