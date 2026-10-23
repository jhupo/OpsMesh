# 工程与交付合同

## 所有者与入口

`scripts/README.md` 列工程入口；`.github/workflows` 编排 CI 与发行；`deploy/cli` 管理主机安装、升级、备份、恢复；`deploy/images` 和 local/server/temporal 分别提供镜像及部署拓扑。

## 不变量

- 锁文件是可重现构建依据。插件 SDK、运维 CLI、服务端分别打包；插件 SDK 不能依赖服务端业务模块。
- 正式部署只有 Docker（`compose`）和本机安装（`systemd`）；统一通过 `bootstrap/cli.py` 的 `opsmesh-server` 命令启动，源码运行属于开发流程。
- 初始迁移及后续迁移不可因源码清理删除。当前 head 由 Alembic 图读取，不能写死为初始版本。发布策略必须包含当前迁移的直接前驱，回滚策略不能声称未经证明的可逆性。
- `bootstrap/schema.py` 只枚举已知退役表。autogenerate 仍检查当前表及未知反射表；不屏蔽所有 metadata 以外的对象。
- 备份停止入口后仍须检查执行/编排 Run、效果回执、沙箱操作和未关闭环境，不能只检查旧 execution 表。快照包含数据库、存储、密钥和必要 Temporal 状态。
- 发布保留摘要、attestation、版本/迁移策略、原生包迁址、Compose/systemd、恢复和防篡改检查；不能因清理过期场景把剩余检查描述成全产品验收。
- `agents` 与 `gateway` 的指定公开包导出允许存在；普通包 initializer 为空标记。架构检查仍禁止业务反向依赖 bootstrap、私有跨模块访问及导入环。
- CI 受影响测试映射要与当前模块依赖方向一致。被删除测试不应通过空集合悄悄失去其余所属模块覆盖。

## 校验与缺口

用户未明确要求时只运行静态检查。具体可用场景及重构后尚未补齐的集成覆盖见 `tests/README.md`。真实 PG、Temporal、Docker、外部 MCP 与模型结果不能由源码扫描代替。
